# -*- coding: utf-8 -*-
import re

for filepath in ['index.html', 'static/v2/index.html']:
    print(f"=== {filepath} ===")
    with open(filepath, 'r', encoding='utf-8') as f:
        text = f.read()

    for mid in ['workspace-modal', 'settings-modal', 'new-agent-modal', 'sidebar-backdrop', 'agent-mode-select-btn', 'autonomous-mode-btn', 'chat-voice-btn', 'auto-tts-toggle-btn']:
        pattern = rf'id="{mid}"'
        found = pattern in text
        print(f"  {mid}: {found}")
