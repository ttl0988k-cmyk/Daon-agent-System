# -*- coding: utf-8 -*-
import os
import time
import json
from datetime import datetime

search_dirs = [
    os.path.expanduser(r'~/.hermes/profiles/raon/sessions'),
    os.path.expandvars(r'%LOCALAPPDATA%\DAON Agent System\data\sessions'),
    r'c:\daon\Daon agent System\data\sessions',
    os.path.expanduser(r'~/.hermes/webui/sessions'),
    os.path.expanduser(r'~/.hermes/sessions')
]

oct8_start = datetime(2026, 10, 8, 0, 0, 0).timestamp()
real_user_sessions = []
seen_texts = set()

for d in search_dirs:
    if not os.path.exists(d):
        continue
    try:
        with os.scandir(d) as it:
            for entry in it:
                if not entry.name.endswith('.json') or entry.name.startswith('_'):
                    continue
                if 'request_dump' in entry.name or 'checkpoint' in entry.name:
                    continue
                stat = entry.stat()
                if stat.st_mtime < oct8_start:
                    continue
                
                # Check contents
                try:
                    with open(entry.path, 'r', encoding='utf-8', errors='ignore') as fp:
                        data = json.load(fp)
                    msgs = data.get('messages') or []
                    user_texts = []
                    for m in msgs:
                        if m.get('role') == 'user':
                            c = m.get('content', '')
                            c_str = c if isinstance(c, str) else str(c)
                            if not any(skip in c_str for skip in ['다음 대화를 3~5문장', '프로필 정보를 추출', '기존 기억', '장기적으로 기억', '다음 대화에서 사용자의']):
                                user_texts.append(c_str)
                    if user_texts:
                        sig = user_texts[0][:50]
                        if sig not in seen_texts:
                            seen_texts.add(sig)
                            title = data.get('title') or data.get('name') or user_texts[0][:25]
                            real_user_sessions.append({
                                'path': entry.path,
                                'mtime': stat.st_mtime,
                                'title': title,
                                'user_texts': user_texts,
                                'msg_count': len(msgs),
                                'raw_data': data
                            })
                except Exception:
                    pass
    except Exception:
        pass

print(f"Total Unique Real User Sessions found: {len(real_user_sessions)}")
real_user_sessions.sort(key=lambda x: x['mtime'], reverse=True)

out_file = r"c:\daon\Daon agent System\scripts\recent_found_sessions.json"
with open(out_file, 'w', encoding='utf-8') as f:
    json.dump([{
        'path': s['path'],
        'mtime': s['mtime'],
        'date': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s['mtime'])),
        'title': s['title'],
        'user_preview': s['user_texts'][0][:80] if s['user_texts'] else '',
        'msg_count': s['msg_count']
    } for s in real_user_sessions], f, indent=2, ensure_ascii=False)

for idx, item in enumerate(real_user_sessions[:50]):
    tstr = time.strftime('%m-%d %H:%M', time.localtime(item['mtime']))
    print(f"[{idx+1}] [{tstr}] {item['title']} | Msgs: {item['msg_count']} | {item['user_texts'][0][:50]}")
