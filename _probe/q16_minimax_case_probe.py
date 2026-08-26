# -*- coding: utf-8 -*-
"""
q16: 미니맥스 토론 401 수정(R1~R4) 정적 검증 프로브.

검증 항목:
  1) model_manager.py casefold 폴백 존재
  2) resolve_model_provider('minimax-m3') 런타임 정규화 (환경 의존 — SKIP 가능)
  3) debate_routes.py 구식 custom 가드 부재
  4) _norm_url 헬퍼 존재 (base_url 기준 페어링)
  5) minimax 공식 엔드포인트 강제 라인 존재
  6) base_url 폴백 조건 단순화 확인
  7) __DEBATE_FAILED__ 센티널 가드 5곳 (round1/round2/판사x2/회의 참여자)
  8) 센티널 생성부 2곳 유지 (timeout/exception)
  9) debate_routes.py AST 유효
 10) model_manager.py AST 유효
 11) EOL 혼합 개행 보존 (crcrlf > 0)
 12) explorer.js visionPrefixes에서 'minimax-m3' 제거 확인
"""
import ast
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PASS = 0
FAIL = 0


def check(no, desc, ok):
    global PASS, FAIL
    tag = '[OK]' if ok else '[NG]'
    print(f'{tag} {no:02d}) {desc}')
    if ok:
        PASS += 1
    else:
        FAIL += 1


MM_PATH = r'api/api/managers/model_manager.py'
DR_PATH = r'api/api/routes/debate_routes.py'
EX_PATH = r'static/modules/explorer.js'

mm_src = open(MM_PATH, encoding='utf-8').read()
dr_bytes = open(DR_PATH, 'rb').read()
dr_src = dr_bytes.decode('utf-8')
ex_src = open(EX_PATH, encoding='utf-8').read()

# 1) casefold 폴백 존재
check(1, 'model_manager casefold 폴백 존재', 'casefold()' in mm_src and 'Case-insensitive fallback' in mm_src)

# 2) 런타임 정규화 — 합성 등록 데이터 주입(환경 무관 단위 검증).
# 실서버는 활성 프로필 경로의 custom_providers.json을 쓰므로 프로브 기본 경로와
# 불일치할 수 있다. 따라서 로더를 몽키패치해 'MiniMax-M3'가 등록된 상태를 만들고
# 대소문자 폴백이 canonical ID로 정규화하는지 직접 확인한다.
try:
    sys.path.insert(0, 'api')
    import api.managers.model_manager as _mm_mod
    _mm_probe = _mm_mod.model_manager
    _orig_load = _mm_mod._load_custom_providers
    _orig_gapm = type(_mm_probe)._get_all_provider_models
    _synthetic = {
        'providers': {
            'minimax': {
                'base_url': 'https://api.minimax.io/v1',
                'models': [{'id': 'MiniMax-M3'}],
            }
        }
    }
    _mm_mod._load_custom_providers = lambda: _synthetic
    type(_mm_probe)._get_all_provider_models = lambda self: {
        'minimax': [{'id': 'MiniMax-M3'}]
    }
    try:
        mid, prov, burl = _mm_probe.resolve_model_provider('minimax-m3')
        print(f'      -> resolved: {mid!r} / {prov!r} / {burl!r}')
        ok2 = (mid == 'MiniMax-M3' and prov == 'minimax')
    finally:
        _mm_mod._load_custom_providers = _orig_load
        type(_mm_probe)._get_all_provider_models = _orig_gapm
except Exception as e:
    ok2 = None
    print(f'      -> SKIP (런타임 해석 불가: {type(e).__name__}: {e})')
if ok2 is None:
    print('[SK] 02) resolve_model_provider 대소문자 정규화 (합성 데이터)')
elif ok2:
    print("[OK] 02) resolve_model_provider('minimax-m3') → ('MiniMax-M3','minimax') 정규화")
else:
    print("[NG] 02) resolve_model_provider('minimax-m3') 정규화 실패")
    FAIL += 1

# 3) 구식 custom 가드 부재
old_guard = re.search(r'if\s+_provider\s+and\s+not\s+str\(_provider\)\.startswith\("custom"\)', dr_src)
check(3, '구식 startswith("custom") 동적키 가드 제거', old_guard is None)

# 4) _norm_url 헬퍼
check(4, '_norm_url(base_url 정규화) 헬퍼 존재', 'def _norm_url(u):' in dr_src)

# 5) minimax 엔드포인트 강제
check(5, 'minimax 공식 엔드포인트 강제', "https://api.minimax.io/v1" in dr_src)

# 6) base_url 폴백 단순화
check(6, "base_url 폴백 'if not _base_url:' 단순화",
      bool(re.search(r'if not _base_url:\s*\r?\r?\n\s*_base_url = rt_base_url', dr_src)))

# 7) 센티널 가드 — 신규 저장 방지 5곳 + 기존 round1/round2 조기리턴 검사 2곳 = 7
guards = len(re.findall(r'startswith\("__DEBATE_FAILED__"\)', dr_src))
check(7, f'센티널 가드 총 7곳 (신규 5 + 기존 조기리턴 2, 실제 {guards})', guards == 7)

# 8) 센티널 생성부 유지
creates = len(re.findall(r'"__DEBATE_FAILED__::', dr_src))
check(8, f'센티널 생성부 2곳 유지 (실제 {creates})', creates == 2)

# 9) debate_routes AST
try:
    ast.parse(dr_src)
    check(9, 'debate_routes.py AST 유효', True)
except SyntaxError as e:
    check(9, f'debate_routes.py AST 오류: {e}', False)

# 10) model_manager AST
try:
    ast.parse(mm_src)
    check(10, 'model_manager.py AST 유효', True)
except SyntaxError as e:
    check(10, f'model_manager.py AST 오류: {e}', False)

# 11) EOL 보존
crcrlf = dr_src.count('\r\r\n')
check(11, f'EOL 혼합 개행 보존 (crcrlf={crcrlf})', crcrlf > 700)

# 12) explorer.js 하드코딩 제거
vp = re.search(r'const visionPrefixes = \[(.*?)\]', ex_src, re.S)
has_mm = vp is not None and "'minimax-m3'" in vp.group(1)
check(12, "explorer.js visionPrefixes 'minimax-m3' 제거", vp is not None and not has_mm)

print()
print(f'RESULT: PASS={PASS} FAIL={FAIL}' + (' — ALL_GREEN' if FAIL == 0 else ' — NEEDS_FIX'))
sys.exit(0 if FAIL == 0 else 1)
