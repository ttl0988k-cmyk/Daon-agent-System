# -*- coding: utf-8 -*-
"""Unit-check resolve_opencode_route against the live-verified surface matrix."""
import pathlib
import sys

src = pathlib.Path('api/api/managers/model_manager.py').read_text(encoding='utf-8')
start = src.index('_OPENCODE_PROVIDER_ALIASES')
end = src.index('class ModelManager')
ns = {}
exec('from typing import List, Optional, Tuple\n' + src[start:end], ns)
route = ns['resolve_opencode_route']
norm_p = ns['normalize_opencode_provider']
norm_m = ns['normalize_opencode_model_id']

cases = [
    # (provider, model, expected_api_mode)
    ('opencode-go', 'gpt-5.6-luna', 'codex_responses'),
    ('opencode-go', 'grok-4.6', 'codex_responses'),
    ('opencode-go', 'grok-4.5', 'codex_responses'),
    ('opencode-go', 'minimax-m3', 'anthropic_messages'),
    ('opencode-go', 'minimax-m2.7', 'anthropic_messages'),
    ('opencode-go', 'glm-5.3-flash', 'chat_completions'),
    ('opencode-go', 'kimi-k2.5', 'chat_completions'),
    ('opencode-go', 'qwen3.8-max', 'chat_completions'),
    ('opencode-go', 'opencode-go/grok-4.6', 'codex_responses'),
    ('opencode go', 'gpt-5.6-luna', 'codex_responses'),
    ('opencode-zen', 'gpt-5.5', 'codex_responses'),
    ('opencode-zen', 'claude-sonnet-4-5', 'anthropic_messages'),
    ('opencode-zen', 'glm-5', 'chat_completions'),
    ('deepseek', 'deepseek-chat', None),
]
fails = 0
for p, m, want in cases:
    mode, url = route(p, m, None)
    ok = mode == want
    fails += 0 if ok else 1
    print(f"{'OK ' if ok else 'FAIL'} {p:14} {m:26} -> {str(mode):18} {url}")

# base_url /v1-strip checks
_, u1 = route('opencode-go', 'minimax-m3', None)
_, u2 = route('opencode-go', 'grok-4.6', None)
assert not u1.endswith('/v1'), u1
assert u2.endswith('/go/v1'), u2
print('anthropic url (stripped):', u1)
print('responses  url (kept /v1):', u2)
print('FAILS =', fails)
sys.exit(1 if fails else 0)
