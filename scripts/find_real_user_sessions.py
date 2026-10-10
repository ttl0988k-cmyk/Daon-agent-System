# -*- coding: utf-8 -*-
import os
import glob
import time
import json
from datetime import datetime

search_dirs = [
    os.path.expanduser(r'~/.hermes/profiles/raon/sessions'),
    os.path.expanduser(r'~/.hermes/profiles/다온(응대)/sessions'),
    os.path.expanduser(r'~/.hermes/profiles/빌(개발)/sessions'),
    os.path.expanduser(r'~/.hermes/profiles/셜록(검수)/sessions'),
    os.path.expanduser(r'~/.hermes/profiles/토니(기획)/sessions'),
    os.path.expanduser(r'~/.hermes/profiles/프라다(디자인)/sessions'),
    os.path.expanduser(r'~/.hermes/sessions'),
    os.path.expandvars(r'%LOCALAPPDATA%\DAON Agent System\data\sessions'),
    r'c:\daon\Daon agent System\data\sessions',
    os.path.expanduser(r'~/.hermes/webui/sessions'),
    r'C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources\data\sessions'
]

oct8_start = datetime(2026, 10, 8, 0, 0, 0).timestamp()
real_user_sessions = []
seen_texts = set()

for d in search_dirs:
    if not os.path.exists(d):
        continue
    for f in glob.glob(os.path.join(d, '*.json')):
        if os.path.basename(f).startswith('_'):
            continue
        if 'request_dump' in f or 'checkpoint' in f:
            continue
        mtime = os.path.getmtime(f)
        if mtime < oct8_start:
            continue
        
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
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
                # Avoid exact duplicates across directories
                if sig not in seen_texts:
                    seen_texts.add(sig)
                    title = data.get('title') or data.get('name') or user_texts[0][:25]
                    real_user_sessions.append({
                        'file': f,
                        'mtime': mtime,
                        'title': title,
                        'user_texts': user_texts,
                        'msg_count': len(msgs),
                        'raw_data': data
                    })
        except Exception:
            pass

print(f"Total Unique Real User Sessions found: {len(real_user_sessions)}")
real_user_sessions.sort(key=lambda x: x['mtime'], reverse=True)

for idx, item in enumerate(real_user_sessions[:40]):
    tstr = time.strftime('%m-%d %H:%M', time.localtime(item['mtime']))
    print(f"[{idx+1}] [{tstr}] {item['title']} | Msgs: {item['msg_count']} | {item['user_texts'][0][:50]} (File: {os.path.basename(item['file'])})")
