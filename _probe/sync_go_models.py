# -*- coding: utf-8 -*-
"""Sync OpenCode Go model list (live catalog, 33 models) into both custom_providers.json copies."""
import json
import pathlib

IDS = [
    'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp', 'deepseek-v4-pro',
    'glm-5', 'glm-5.1', 'glm-5.2', 'glm-5.3', 'glm-5.3-flash',
    'gpt-5.6-luna', 'grok-4.5', 'grok-4.6',
    'hy3', 'hy3-preview', 'hy4-preview',
    'kimi-k2.5', 'kimi-k2.6', 'kimi-k2.7-code', 'kimi-k3',
    'longcat-2.0',
    'mimo-v2-omni', 'mimo-v2-pro', 'mimo-v2.5', 'mimo-v2.5-pro',
    'minimax-m2.5', 'minimax-m2.7', 'minimax-m3',
    'muse-spark-1.2-contributor',
    'qwen3.5-plus', 'qwen3.6-plus', 'qwen3.7-max', 'qwen3.7-plus',
    'qwen3.8-flash', 'qwen3.8-max',
]
MODELS = [{'id': i, 'label': i, 'type': 'chat'} for i in IDS]

PATHS = [
    pathlib.Path('data/custom_providers.json'),
    pathlib.Path(r'C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json'),
]

for fp in PATHS:
    d = json.loads(fp.read_text(encoding='utf-8'))
    d.setdefault('presets', {}).setdefault('opencode-go', {})['models'] = MODELS
    prov = d.get('providers', {}).get('opencode-go')
    if prov is not None:
        prov['models'] = MODELS
    fp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('updated', fp)
