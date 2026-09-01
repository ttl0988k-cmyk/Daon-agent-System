# -*- coding: utf-8 -*-
"""fetch_models_from_provider 엔드투엔드 테스트 (실제 라우트와 동일 코드 경로).

UI의 '모델 자동 감지' 버튼 -> POST /api/providers/fetch-models ->
model_manager.fetch_models_from_provider(base_url, api_key) 흐름을 재현한다.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'api'))

_pkg = json.loads(pathlib.Path(
    r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'
).read_text(encoding='utf-8'))
KEY = _pkg['providers']['opencode-go']['api_key']
BASE = _pkg['providers']['opencode-go'].get('base_url', 'https://opencode.ai/zen/go/v1')

from api.managers.model_manager import model_manager  # noqa: E402

try:
    models = model_manager.fetch_models_from_provider(BASE, KEY)
    print('RESULT_COUNT =', len(models))
    ids = [m['id'] for m in models]
    print('HAS gpt-5.6-luna :', 'gpt-5.6-luna' in ids)
    print('HAS grok-4.6     :', 'grok-4.6' in ids)
    print('SAMPLE           :', json.dumps(models[:3], ensure_ascii=False))
    types = sorted({m.get('type', '?') for m in models})
    print('TYPES            :', types)
except Exception as e:
    print('FAILED:', type(e).__name__, e)
