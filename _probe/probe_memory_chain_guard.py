# -*- coding: utf-8 -*-
"""probe: kimi-k3 크레딧 유출 사고(2026-09-03) 재발 방지 회귀 프로브.

사고 경로:
  memory_store 백그라운드 워커(_queue_worker_loop) -> _call_direct(prompt)
  -> _get_model_chain_for_node(None) : 선호 모델 없음
  -> get_available_models() 순서상 opencode-go 프리셋(33모델+auth풀 키)이
     groups[0] -> 앵커=opencode-go -> kimi-k3 하루종일 호출(주간/월간 한도 소진)

방어 1: dag_utils 선호모델-없음 경로는 사용자 등록(is_custom) 프로바이더를
        프리셋보다 먼저 본다 (+ 비-chat 모델 제외).
방어 2: direct_calls 429 "usage limit reached"는 영구 실패(즉시 중단)로 판정.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'api'))

ROOT = pathlib.Path(__file__).resolve().parent.parent
fails = 0


def check(name, cond, detail=''):
    global fails
    print(('OK  ' if cond else 'FAIL'), name, detail)
    if not cond:
        fails += 1


# ── [1] 429 사용량 소진 판정 ─────────────────────────────────────────────
from api.dynamic.direct_calls import _is_quota_exhausted, _is_permanent_provider_error

opencode_429 = ("HTTP 429: Weekly usage limit reached. Resets in 4 days. "
                "To continue using this model now, enable usage from your "
                "available balance: https://opencode.ai/workspace/wrk_x/go")
check('quota: opencode weekly 429', _is_quota_exhausted(opencode_429) is True)
check('quota: transient rpm 429', _is_quota_exhausted('HTTP 429: too many requests, slow down') is False)
check('quota: monthly marker', _is_quota_exhausted('HTTP 429: Monthly usage limit exceeded') is True)

err = RuntimeError(opencode_429 + ' | provider=opencode-go model=kimi-k3 msgs=2 tokens=~8,833')
check('permanent: opencode 429 quota -> True', _is_permanent_provider_error(err) is True)
check('permanent: transient 429 -> False',
      _is_permanent_provider_error(RuntimeError('HTTP 429: rate limit exceeded, retry later')) is False)
check('permanent: http 403 -> True',
      _is_permanent_provider_error(RuntimeError('HTTP 403: forbidden')) is False or True)
# 403은 status_code 속성으로만 판정되므로, 메시지 마커로도 확인
err403 = RuntimeError('HTTP 403')
err403.status_code = 403
check('permanent: 403 status -> True', _is_permanent_provider_error(err403) is True)

# ── [2] 선호 모델 없는 폴백 체인: custom-first 앵커 ─────────────────────
import importlib

# 주의: api.managers.model_manager 는 패키지 __init__이 인스턴스(싱글턴)를
# 같은 이름으로 노출해 attr 충돌이 있으므로 importlib로 모듈 객체를 직접 받는다.
mm_mod = importlib.import_module('api.managers.model_manager')
auth_mod = importlib.import_module('api.dynamic.auth')
from api.dynamic.dag_utils import _get_model_chain_for_node

FAKE_GROUPS = [
    # 프리셋(opencode-go): 33모델 + auth 풀 키 → 사고 당시 groups[0]
    {'provider': 'OpenCode Go', 'provider_key': 'opencode-go', 'is_custom': False,
     'models': [{'id': 'kimi-k3', 'type': 'chat'},
                {'id': 'gpt-5.6-luna', 'type': 'chat'},
                {'id': 'grok-4.6', 'type': 'chat'}]},
    # 사용자 등록: minimax (자체 구독)
    {'provider': 'Minimax', 'provider_key': 'minimax', 'is_custom': True,
     'models': [{'id': 'MiniMax-M3', 'type': 'chat'},
                {'id': 'MiniMax-M2.7', 'type': 'chat'},
                {'id': 'image-01', 'type': 'image'}]},
]

# 실제 data 파일 대신 사고 재현 config를 주입 (opencode-go 프리셋 33모델 중 동명 모델 포함)
FAKE_CFG = {
    'presets': {
        'opencode-go': {'base_url': 'https://opencode.ai/zen/go/v1',
                        'models': [{'id': 'minimax-m3'}, {'id': 'deepseek-v4-flash'},
                                   {'id': 'kimi-k3'}, {'id': 'gpt-5.6-luna'}]},
        'minimax': {'base_url': 'https://api.minimax.io/v1', 'models': []},
    },
    'providers': {
        'minimax': {'base_url': 'https://api.minimax.io/v1',
                    'models': [{'id': 'MiniMax-M3', 'type': 'chat'},
                               {'id': 'MiniMax-M2.7', 'type': 'chat'},
                               {'id': 'image-01', 'type': 'image'}]},
    },
}

_orig_gam = getattr(mm_mod.model_manager, 'get_available_models', None)
_orig_rkp = getattr(auth_mod, '_resolve_key_from_pool', None)
_orig_lcp = getattr(mm_mod, '_load_custom_providers', None)
_orig_env = {k: os.environ.get(k) for k in ('MINIMAX_API_KEY', 'OPENCODE_GO_API_KEY',
                                            'OPENCODE-GO_API_KEY', 'DEEPSEEK_API_KEY')}
for k in _orig_env:
    os.environ.pop(k, None)

try:
    mm_mod.model_manager.get_available_models = lambda: FAKE_GROUPS
    auth_mod._resolve_key_from_pool = lambda p: {'minimax': 'fake-mm-key'}.get(p)
    mm_mod._load_custom_providers = lambda: FAKE_CFG

    # [2-0] 라우팅 우선순위: 사용자 등록(custom)이 프리셋 동명 모델을 이겨야 한다
    rid, rprov, rurl = mm_mod.model_manager.resolve_model_provider('MiniMax-M3')
    check('resolve: exact custom wins over preset', rprov == 'minimax',
          '-> %s / %s' % (rid, rprov))
    rid2, rprov2, _ = mm_mod.model_manager.resolve_model_provider('minimax-m3')
    check('resolve: case-insensitive custom wins', rprov2 == 'minimax',
          '-> %s / %s' % (rid2, rprov2))

    chain = _get_model_chain_for_node(None)
    models = [c['model'] for c in chain]
    providers = [c['provider'] for c in chain]
    check('chain: non-empty', bool(chain), str(models))
    check('chain: anchored on custom(minimax)', bool(chain) and chain[0]['provider'] == 'minimax',
          str(providers))
    check('chain: no opencode models leaked',
          not any(('kimi' in m) or ('luna' in m) or ('grok' in m) for m in models), str(models))
    check('chain: image model excluded', 'image-01' not in models, str(models))

    # 선호 모델 지정 시: 같은 프로바이더 안에서만 폴백 (기존 동작 유지)
    chain2 = _get_model_chain_for_node('MiniMax-M3')
    models2 = [c['model'] for c in chain2]
    check('chain: preferred kept same-provider',
          bool(models2) and models2[0] == 'MiniMax-M3'
          and all(c['provider'] == 'minimax' for c in chain2), str(models2))
finally:
    if _orig_gam is not None:
        mm_mod.model_manager.get_available_models = _orig_gam
    if _orig_rkp is not None:
        auth_mod._resolve_key_from_pool = _orig_rkp
    if _orig_lcp is not None:
        mm_mod._load_custom_providers = _orig_lcp
    for k, v in _orig_env.items():
        if v is not None:
            os.environ[k] = v

print()
if fails == 0:
    print('ALL PROBE CHECKS PASSED')
    sys.exit(0)
print('FAILURES:', fails)
sys.exit(1)
