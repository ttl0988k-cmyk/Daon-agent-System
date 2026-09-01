# -*- coding: utf-8 -*-
"""OpenCode Go 카탈로그(/models) 엔드포인트 실측 프로브.

auto-fetch(fetch_models_from_provider)가 base_url + '/models'를 쓰는데,
Go 구독에서 이 경로가 실제로 JSON을 주는지 404/HTML인지 확인한다.
키 파일에 하드코딩 금지: 설치본 custom_providers.json에서 로드.
"""
import json
import pathlib

import requests

_pkg = json.loads(pathlib.Path(
    r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'
).read_text(encoding='utf-8'))
KEY = _pkg['providers']['opencode-go']['api_key']
GO = _pkg['providers']['opencode-go'].get('base_url', 'https://opencode.ai/zen/go/v1').rstrip('/')

CANDIDATES = [
    GO + '/models',
    GO.rsplit('/v1', 1)[0] + '/models',          # https://opencode.ai/zen/models
    'https://opencode.ai/zen/go/models',
    'https://opencode.ai/zen/go/v1/models',
]

for url in CANDIDATES:
    for auth in ('bearer', 'xapikey'):
        headers = {'User-Agent': 'daon-probe/1.0'}
        if auth == 'bearer':
            headers['Authorization'] = 'Bearer ' + KEY
        else:
            headers['x-api-key'] = KEY
        try:
            r = requests.get(url, headers=headers, timeout=30)
            ctype = r.headers.get('content-type', '')
            note = ''
            n = 0
            if 'json' in ctype:
                try:
                    j = r.json()
                    data = j.get('data') or j.get('models') or []
                    n = len(data) if isinstance(data, list) else 0
                    note = 'json_keys=' + ','.join(list(j)[:5]) if isinstance(j, dict) else 'json-list'
                except Exception as e:
                    note = 'json-parse-fail ' + str(e)[:60]
            else:
                note = 'non-json body[:60]=' + repr(r.text[:60])
            print(f"{r.status_code} | {auth:7s} | {url} | ct={ctype[:30]} | models={n} | {note}")
        except Exception as e:
            print(f"ERR  | {auth:7s} | {url} | {type(e).__name__}: {str(e)[:80]}")
