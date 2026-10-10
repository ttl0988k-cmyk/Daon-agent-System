# -*- coding: utf-8 -*-
"""
Restore all real user conversation sessions (especially Oct 8 and 9 sessions)
into _index.json so they appear at the very top of Recent Threads in UI.
"""
import os
import glob
import time
import json
import shutil

dirs = [
    r'c:\daon\Daon agent System\data\sessions',
    os.path.expandvars(r'%LOCALAPPDATA%\DAON Agent System\data\sessions')
]

# Collect all real sessions with messages > 0
all_valid_sessions = {}

for d in dirs:
    if not os.path.exists(d):
        continue
    for f in glob.glob(os.path.join(d, '*.json')):
        if os.path.basename(f).startswith('_'):
            continue
        sid = os.path.basename(f)[:-5]
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                data = json.load(fp)
            msgs = data.get('messages') or []
            if len(msgs) == 0:
                continue
                
            mtime = os.path.getmtime(f)
            title = data.get('title') or data.get('name') or '새 세션'
            # If title is generic, make a good title from first user message
            if title in ['Untitled', '새 세션', 'No Title']:
                for m in msgs:
                    if m.get('role') == 'user':
                        c = m.get('content', '')
                        c_str = c if isinstance(c, str) else str(c)
                        if c_str.strip():
                            title = c_str.strip()[:35]
                            break
                            
            entry = {
                'session_id': sid,
                'title': title,
                'workspace': data.get('workspace') or r'c:\daon\Daon agent System',
                'model': data.get('model') or 'deepseek-v4.1-flash',
                'message_count': len(msgs),
                'created_at': data.get('created_at') or mtime,
                'updated_at': data.get('updated_at') or mtime,
                'pinned': False,
                'archived': False,
                'project_id': None,
                'profile': data.get('profile') or 'raon',
                'input_tokens': data.get('input_tokens') or 0,
                'output_tokens': data.get('output_tokens') or 0,
                'surface': 'webui'
            }
            
            if sid not in all_valid_sessions or mtime > all_valid_sessions[sid]['updated_at']:
                all_valid_sessions[sid] = entry
        except Exception:
            pass

# Sort by updated_at descending (latest on top)
sorted_entries = sorted(all_valid_sessions.values(), key=lambda s: s.get('updated_at', 0) or 0, reverse=True)

print(f"Total restored sessions with real messages: {len(sorted_entries)}")
print("\nTop 15 restored sessions:")
for idx, s in enumerate(sorted_entries[:15]):
    tstr = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.get('updated_at', 0)))
    print(f"  [{idx+1}] [{tstr}] {s['title']} (msgs: {s['message_count']}) [ID: {s['session_id'][:10]}]")

# Write to both index files
for d in dirs:
    idx_p = os.path.join(d, '_index.json')
    with open(idx_p, 'w', encoding='utf-8') as fp:
        json.dump(sorted_entries, fp, indent=2, ensure_ascii=False)
    print(f"Updated {idx_p} with {len(sorted_entries)} sessions!")

# Also ensure all json files exist in both directories
for sid, entry in all_valid_sessions.items():
    src1 = os.path.join(dirs[0], f"{sid}.json")
    src2 = os.path.join(dirs[1], f"{sid}.json")
    if os.path.exists(src1) and not os.path.exists(src2):
        shutil.copy2(src1, src2)
    elif os.path.exists(src2) and not os.path.exists(src1):
        shutil.copy2(src2, src1)

print("\nAll session files completely synchronized across both directories!")
