"""갭 A 기능 프로브: 챗 경로 도구(dynamic_harness_tool)의 수용 기준 배선 검증.

실제 LLM/하네스 실행 없이 다음을 검증한다:
1. 도구 스키마에 acceptance_criteria 파라미터 + 사전 점검 체크리스트 존재
2. _ensure_acceptance_criteria: precomputed 부착 / LLM 폴백 / 이미 부착 / fail-open
3. registry 핸들러가 acceptance_criteria를 execute_dynamic_harness로 전달하는지
"""
import sys
import types

sys.path.insert(0, "api")
sys.path.insert(0, "hermes-agent")

# tools.registry를 페이크로 주입해 무거운 의존성 격리
fake_registry_mod = types.ModuleType("tools.registry")
_registered = {}

class _FakeRegistry:
    def register(self, **kw):
        _registered[kw.get("name")] = kw

fake_registry_mod.registry = _FakeRegistry()
fake_registry_mod.tool_error = lambda msg: {"error": msg}
fake_registry_mod.tool_result = lambda **kw: kw
sys.modules["tools.registry"] = fake_registry_mod

import api.dynamic.clarifier as clar  # noqa: E402
from api.dynamic.clarifier import parse_acceptance_criteria  # noqa: E402
import tools.dynamic_harness_tool as dht  # noqa: E402

# ── 1. 스키마 검증 ──
schema = dht.DYNAMIC_HARNESS_SCHEMA
props = schema["parameters"]["properties"]
assert "acceptance_criteria" in props, "스키마에 acceptance_criteria 파라미터 누락"
assert props["acceptance_criteria"]["type"] == "array"
assert "PRE-FLIGHT CHECKLIST" in schema["description"], "도구 설명에 사전 점검 체크리스트 누락"
assert "acceptance_criteria" in schema["description"]
print("1. schema acceptance_criteria + checklist OK")

# ── 2. _ensure_acceptance_criteria 시맨틱 ──
task_base = "대시보드 웹앱을 만들어줘"
criteria = ["차트 3종이 렌더링되어야 한다", "데이터 새로고침 버튼이 동작해야 한다"]

# (a) precomputed 우선 부착 (LLM 호출 없이)
calls = []
clar._call_direct = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(RuntimeError("no LLM"))
out = dht._ensure_acceptance_criteria(task_base, None, criteria)
assert parse_acceptance_criteria(out) == criteria, parse_acceptance_criteria(out)
assert calls == [], "precomputed 경로에서는 LLM 호출이 없어야 한다"
# (b) 이미 부착된 task는 변경 없음
out2 = dht._ensure_acceptance_criteria(out, None, None)
assert out2 == out
# (c) 기준 없음 + LLM 실패 → 일반 폴백 부착
out3 = dht._ensure_acceptance_criteria(task_base, None, None)
assert parse_acceptance_criteria(out3) == ["원본 요청이 산출물에 완전히 구현되어 있어야 한다."]
# (d) clarifier 임포트 불가 상황 → fail-open(원본 반환)
saved = sys.modules.get("api.dynamic.clarifier")
sys.modules["api.dynamic.clarifier"] = None
try:
    out4 = dht._ensure_acceptance_criteria(task_base, None, criteria)
    assert out4 == task_base, "fail-open 시 원본 task를 반환해야 한다"
finally:
    if saved is not None:
        sys.modules["api.dynamic.clarifier"] = saved
    else:
        del sys.modules["api.dynamic.clarifier"]
print("2. _ensure_acceptance_criteria semantics OK")

# ── 3. registry 핸들러 배선 검증 ──
entry = _registered.get("execute_dynamic_harness")
assert entry is not None, "execute_dynamic_harness 미등록"
assert entry.get("toolset") == "dynamic_harness"

captured = {}
def _fake_exec(**kw):
    captured.update(kw)
    return "ok"
dht.execute_dynamic_harness = _fake_exec
entry["handler"]({"task": "테스트 작업", "preferred_model": "MiniMax-M3",
                  "skills": ["tester"], "acceptance_criteria": ["조건A", "조건B"]})
assert captured.get("task") == "테스트 작업"
assert captured.get("forced_skills") == ["tester"]
assert captured.get("acceptance_criteria") == ["조건A", "조건B"], captured
# 빈 목록 → None 정규화
captured.clear()
entry["handler"]({"task": "테스트 작업", "acceptance_criteria": []})
assert captured.get("acceptance_criteria") is None
print("3. registry handler wiring OK")

print("ALL GAP-A PROBES PASSED")
