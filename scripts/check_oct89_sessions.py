# -*- coding: utf-8 -*-
import os
import glob
import time
import json
from datetime import datetime

oct8_start = datetime(2026, 10, 8, 0, 0, 0).timestamp()

dirs = [
    r'c:\daon\Daon agent System\data\sessions',
    os.path.expandvars(r'%LOCALAPPDATA%\DAON Agent System\data\sessions')
]

found = []
seen = set()

for d in dirs:
    if not os.path.exists(d):
        continue
    for f in glob.glob(os.path.join(d, '*.json')):
        if os.path.basename(f).startswith('_'):
            continue
        mtime = os.path.getmtime(f)
        if mtime >= oct8_start:
            sid = os.path.basename(f)[:-5]
            if sid not in seen:
                seen.add(sid)
                try:
                    with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                        data = json.load(fp)
                    title = data.get('title') or 'Untitled'
                    msgs = data.get('messages') or []
                    first_msg = ''
                    for m in msgs:
                        if m.get('role') == 'user':
                            c = m.get('content', '')
                            first_msg = c[:60] if isinstance(c, str) else str(c)[:60]
                            break
                    found.append((f, mtime, title, len(msgs), first_msg, sid))
                except Exception:
                    pass

found.sort(key=lambda x: x[1], reverse=True)
print(f"Total {len(found)} sessions found in data/sessions from Oct 8-9:\n")
for f, mtime, title, count, first_msg, sid in found:
    tstr = time.strftime('%m-%d %H:%M', time.localtime(mtime))
    print(f"[{tstr}] ID:{sid} | \"{title}\" (msgs:{count}) | {first_msg}")
