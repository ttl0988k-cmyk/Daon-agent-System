# -*- coding: utf-8 -*-
"""Go 구독 모델별 API 표면 매트릭스 실측: chat/messages/responses 3종 비교."""
import json
import pathlib
import sys

import requests

# API 키는 커스텀 프로바이더 저장소에서 읽어온다 (플레인텍스트 하드코딩 금지).
_pkg = json.loads(pathlib.Path(
    r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'
).read_text(encoding='utf-8'))
KEY = _pkg['providers']['opencode-go']['api_key']
GO = _pkg['providers']['opencode-go'].get('base_url', 'https://opencode.ai/zen/go/v1').rstrip('/')

MODELS = ["gpt-5.6-luna", "grok-4.6", "grok-4.5", "glm-5.3-flash", "kimi-k2.5"]


def try_chat(model):
    return requests.post(
        GO + "/chat/completions",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
              "max_tokens": 16},
        timeout=120,
    )


def try_messages(model):
    return requests.post(
        GO + "/messages",
        headers={"x-api-key": KEY, "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"},
        json={"model": model, "max_tokens": 16,
              "messages": [{"role": "user", "content": "Reply with exactly: OK"}]},
        timeout=120,
    )


def try_responses(model):
    return requests.post(
        GO + "/responses",
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
        json={"model": model, "input": "Reply with exactly: OK",
              "max_output_tokens": 16, "store": False},
        timeout=120,
    )


def summarize(r):
    if r.status_code == 200:
        return "PASS"
    txt = r.text[:120].replace("\n", " ")
    return f"HTTP {r.status_code} :: {txt}"


for model in MODELS:
    row = [model]
    for label, fn in (("chat", try_chat), ("messages", try_messages),
                      ("responses", try_responses)):
        try:
            row.append(f"{label}={summarize(fn(model))}")
        except Exception as e:
            row.append(f"{label}=ERROR {e!r}")
    print(" | ".join(row), flush=True)
