# -*- coding: utf-8 -*-
"""OpenCode Go 라이브 라우팅 검증 프로브.

Go 구독($10/mo)의 모든 모델은 단일 base_url(https://opencode.ai/zen/go/v1)
뒤에 있으며, API 표면만 모델마다 다르다 (2026-09 실측):
  - minimax-*        -> Anthropic Messages (/v1/messages)
  - gpt-* / grok-*   -> Responses API (/v1/responses) 전용
  - 그 외(glm, kimi, qwen, mimo...) -> chat/completions
"""
import json
import pathlib
import sys

import requests

# 하드코딩 금지: 설치본 custom_providers.json에서 키를 읽어온다.
_pkg = json.loads(pathlib.Path(
    r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'
).read_text(encoding='utf-8'))
KEY = _pkg['providers']['opencode-go']['api_key']
GO = _pkg['providers']['opencode-go'].get('base_url', 'https://opencode.ai/zen/go/v1').rstrip('/')

results = []

# 1) chat_completions 표면: gpt-5.6-luna / grok-4.6
for model in ("gpt-5.6-luna", "grok-4.6"):
    try:
        r = requests.post(
            GO + "/chat/completions",
            headers={"Authorization": "Bearer " + KEY,
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                  "max_tokens": 16},
            timeout=90,
        )
        ok = r.status_code == 200
        body = ""
        if ok:
            try:
                body = r.json()["choices"][0]["message"]["content"][:40]
            except Exception:
                body = "<unparsed>"
        else:
            body = r.text[:200]
        results.append((f"chat_completions/{model}", "PASS" if ok else "FAIL",
                        f"HTTP {r.status_code} :: {body}"))
    except Exception as e:
        results.append((f"chat_completions/{model}", "ERROR", repr(e)))

# 2) anthropic_messages 표면: minimax-m3 (SDK가 /v1/messages를 붙이므로
#    base는 /v1 제거 후 + /v1/messages == GO + "/messages" 와 동일)
try:
    r = requests.post(
        GO + "/messages",
        headers={"x-api-key": KEY,
                 "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        json={"model": "minimax-m3",
              "max_tokens": 16,
              "messages": [{"role": "user", "content": "Reply with exactly: OK"}]},
        timeout=90,
    )
    ok = r.status_code == 200
    body = ""
    if ok:
        try:
            body = "".join(b.get("text", "") for b in r.json().get("content", []))[:40]
        except Exception:
            body = "<unparsed>"
    else:
        body = r.text[:200]
    results.append(("anthropic_messages/minimax-m3", "PASS" if ok else "FAIL",
                    f"HTTP {r.status_code} :: {body}"))
except Exception as e:
    results.append(("anthropic_messages/minimax-m3", "ERROR", repr(e)))

width = max(len(n) for n, _, _ in results)
for name, status, detail in results:
    print(f"{name:<{width}}  {status:<5}  {detail}")

sys.exit(0 if all(s == "PASS" for _, s, _ in results) else 1)
