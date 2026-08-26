# -*- coding: utf-8 -*-
"""Q1-4: API 호출 오류(404/503 등) UI 실시간 중계 파이프라인 정적 검증 프로브.

검증 대상 3계층:
  1. hermes-agent/run_agent.py
     - AIAgent.__init__ 에 api_error_callback 파라미터 존재 (:842 부근)
     - self.api_error_callback 저장 (:1024 부근)
     - OpenAI 호환 API 예외 블록에서 콜백 호출 + 예외 삼킴 (:8858~8863 부근)
  2. api/api/streaming.py
     - on_api_error 정의 + 스로틀(15초/8회) + put('apierror', ...) 발행
     - AIAgent 생성부에 api_error_callback=on_api_error 연결
  3. static/modules/chat.js
     - sse.addEventListener('apierror', ...) 리스너 정확히 2개 (레거시+메인)
     - apierror 핸들러 내부에 finish()/finishStream() 호출이 없어야 함
       (스트림을 끊으면 재시도 중 경고 의미가 상실됨)

실행: PYTHONIOENCODING=utf-8 python _probe/q14_apierror_relay_probe.py
출력: ASCII 전용 안전(한국어 메시지는 UTF-8 강제 시에만 출력).
"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS = "[PASS]"
FAIL = "[FAIL]"

failures = []


def check(name, cond, detail=""):
    if cond:
        print("%s %s" % (PASS, name))
    else:
        print("%s %s %s" % (FAIL, name, detail))
        failures.append(name)


# ── 1. run_agent.py ──────────────────────────────────────────────────────────
with open("hermes-agent/run_agent.py", "rb") as f:
    ra = f.read().decode("utf-8", "replace")
ra_lines = ra.splitlines()

def find_line(lines, pattern):
    for i, ln in enumerate(lines):
        if re.search(pattern, ln):
            return i + 1  # 1-based
    return None

l_param = find_line(ra_lines, r"api_error_callback:\s*callable\s*=\s*None")
check("run_agent: __init__ param exists", l_param is not None,
      "(pattern 'api_error_callback: callable = None')")

l_store = find_line(ra_lines, r"self\.api_error_callback\s*=\s*api_error_callback")
check("run_agent: callback stored on self", l_store is not None)

# 예외 블록 내 호출: try/except 로 감싸져 있어야 함
m_call = re.search(
    r"if self\.api_error_callback:\s*\n"
    r"\s*try:\s*\n"
    r"\s*self\.api_error_callback\(error_msg\)\s*\n"
    r"\s*except Exception:\s*\n"
    r"\s*logger\.debug\(", ra)
check("run_agent: exception-swallowing invocation present", m_call is not None)

# ── 2. streaming.py ──────────────────────────────────────────────────────────
with open("api/api/streaming.py", "rb") as f:
    st = f.read().decode("utf-8", "replace")
st_lines = st.splitlines()

l_def = find_line(st_lines, r"def on_api_error\(error_msg\):")
check("streaming: on_api_error defined", l_def is not None)

l_throttle_15 = find_line(st_lines, r"<\s*15\.0")
check("streaming: 15s same-message throttle", l_throttle_15 is not None)

l_cap8 = find_line(st_lines, r"_api_err_state\.get\('count', 0\)\s*>=\s*8")
check("streaming: max 8 per stream cap", l_cap8 is not None)

l_put = find_line(st_lines, r"put\('apierror',\s*\{")
check("streaming: put('apierror', ...) emitted", l_put is not None)

l_msg500 = find_line(st_lines, r"'message':\s*error_msg\[:500\]")
check("streaming: message truncated to 500 chars", l_msg500 is not None)

l_wire = find_line(st_lines, r"api_error_callback=on_api_error,")
check("streaming: wired into AIAgent constructor", l_wire is not None)

# on_api_error 본문이 put 이후 finish/finishStream 을 부르지 않는지
if l_def and l_put:
    body = "\n".join(st_lines[l_def - 1 : l_put + 6])
    check("streaming: on_api_error never finishes the stream",
          "finishStream" not in body and "finish(" not in body.replace("finishStream", ""))

# ── 3. chat.js ───────────────────────────────────────────────────────────────
with open("static/modules/chat.js", "rb") as f:
    cj_bytes = f.read()
cj = cj_bytes.decode("utf-8", "replace")

n_listeners = cj.count("addEventListener('apierror'")
check("chat.js: exactly 2 apierror listeners", n_listeners == 2,
      "(found %d)" % n_listeners)

# 각 apierror 핸들러 블록에서 finish 계열 미호출 확인
blocks = []
for m in re.finditer(r"addEventListener\('apierror'", cj):
    start = m.start()
    # 균형 잡힌 중괄호 블록 추출
    i = cj.index("{", m.end())
    depth = 0
    j = i
    while j < len(cj):
        if cj[j] == "{":
            depth += 1
        elif cj[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    blocks.append(cj[i:j])

for idx, blk in enumerate(blocks):
    bad = re.search(r"\b(finishStream|finish)\s*\(", blk)
    check("chat.js: apierror handler #%d does not finish stream" % (idx + 1),
          bad is None, "(matched '%s')" % bad.group(0) if bad else "")
    has_warn = ("API" in blk) or ("warn" in blk) or ("insertAdjacentHTML" in blk) \
               or ("appendChild" in blk)
    check("chat.js: apierror handler #%d renders a warning" % (idx + 1), has_warn)

# 개행 무결성: 원본 특유의 \r\r\r\n 종결자가 유지되는지
n_crcrcrlf = cj_bytes.count(b"\r\r\r\n")
n_lf = cj_bytes.count(b"\n")
check("chat.js: dominant EOL still \\r\\r\\r\\n",
      n_crcrcrlf * 10 >= n_lf * 9,
      "(crcrcrlf=%d lf=%d)" % (n_crcrcrlf, n_lf))

# ── 요약 ─────────────────────────────────────────────────────────────────────
print("")
if failures:
    print("[RESULT] FAILED (%d): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("[RESULT] ALL CHECKS PASSED")
sys.exit(0)
