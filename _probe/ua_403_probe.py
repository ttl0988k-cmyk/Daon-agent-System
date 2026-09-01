# -*- coding: utf-8 -*-
"""urllib 기본 UA vs 커스텀 UA: opencode.ai /v1/models 403 원인 규명 프로브.

requests(200 OK)와 urllib(403)의 차이 = User-Agent 헤더 여부.
"""
import json
import pathlib
import urllib.request

_pkg = json.loads(pathlib.Path(
    r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'
).read_text(encoding='utf-8'))
KEY = _pkg['providers']['opencode-go']['api_key']
URL = _pkg['providers']['opencode-go'].get('base_url', 'https://opencode.ai/zen/go/v1').rstrip('/') + '/models'

CASES = {
    'no-ua (urllib default)': {},
    'ua=daon-agent/1.0': {'User-Agent': 'daon-agent/1.0'},
    'ua=Mozilla/5.0': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
}

for name, extra in CASES.items():
    headers = {'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
    headers.update(extra)
    req = urllib.request.Request(URL, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        print(f"PASS 200 | {name:26s} | models={len(body.get('data', []))}")
    except urllib.error.HTTPError as e:
        print(f"FAIL {e.code} | {name:26s} | {e.read().decode('utf-8', 'replace')[:120]!r}")
    except Exception as e:
        print(f"ERR    | {name:26s} | {type(e).__name__}: {e}")
