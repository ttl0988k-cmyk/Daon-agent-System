# -*- coding: utf-8 -*-
"""refresh_provider_models 단위 검증 (dev 소스, 임시 providers 파일).

1) api.config.STATE_DIR을 temp로 리다이렉트
2) add_custom_provider로 opencode-go 등록(키 포함) 후 models 초기화
3) refresh_provider_models('opencode-go') 호출 -> 33개 반환 + 저장 확인
"""
import sys, os, json, tempfile, pathlib, io

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api'))

# Windows console encoding guard
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

tmp = tempfile.mkdtemp(prefix='daon_refresh_probe_')
os.environ['DAON_STATE_DIR'] = tmp  # may be ignored; patch below regardless

import api.config as cfg  # noqa: E402
print('STATE_DIR before patch:', cfg.STATE_DIR)
cfg.STATE_DIR = pathlib.Path(tmp)

# NOTE: `import api.managers.model_manager as mm` binds the singleton INSTANCE
# (package attribute `model_manager` is shadowed by the instance), not the module.
# Use importlib to force the real module object so module-level helpers resolve.
import importlib  # noqa: E402
mm = importlib.import_module('api.managers.model_manager')
# model_manager resolves path via _get_custom_providers_path() -> STATE_DIR at call time
print('providers path:', mm._get_custom_providers_path())
assert str(cfg.STATE_DIR) in str(mm._get_custom_providers_path()), 'STATE_DIR redirect failed'

KEY = os.environ.get('OPENCODE_GO_API_KEY', '').strip()
if not KEY:
    # fallback: read from installed data (do NOT print)
    inst = pathlib.Path(os.environ['LOCALAPPDATA']) / 'DAON Agent System' / 'data' / 'custom_providers.json'
    KEY = json.loads(inst.read_text(encoding='utf-8'))['providers']['opencode-go']['api_key']
print('key loaded:', bool(KEY), 'len', len(KEY))

mgr = mm.model_manager

# 1) register with empty models
res = mgr.add_custom_provider('opencode-go', KEY, 'https://opencode.ai/zen/go/v1', models=[])
print('ADD_OK', res.get('success'))

# force stored models to [] (add may have auto-fetched; that itself proves UA fix)
data = mm._load_custom_providers()
providers = data.get('providers', {})
providers['opencode-go']['models'] = []
mm._save_custom_providers(providers)
print('models reset to 0')

# 2) refresh
out = mgr.refresh_provider_models('opencode-go')
print('REFRESH count =', out['count'])

# 3) verify persisted
saved = mm._load_custom_providers()['providers']['opencode-go']['models']
ids = {m.get('id') for m in saved}
print('PERSISTED =', len(saved), 'HAS gpt-5.6-luna =', 'gpt-5.6-luna' in ids, 'HAS grok-4.6 =', 'grok-4.6' in ids)

# 4) error paths
try:
    mgr.refresh_provider_models('no-such-provider')
    print('FAIL: expected KeyError')
except KeyError:
    print('PASS: KeyError for unknown provider')

RESULT = 'PASS' if out['count'] == 33 and len(saved) == 33 else 'FAIL'
print('RESULT:', RESULT)
