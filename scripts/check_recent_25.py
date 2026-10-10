import os, json, time
p = os.path.expanduser('~/.hermes/profiles/raon/sessions')
files = [os.path.join(p, f) for f in os.listdir(p) if f.endswith('.json') and not f.startswith('_') and 'request_dump' not in f and 'checkpoint' not in f]
files.sort(key=os.path.getmtime, reverse=True)
for f in files[:25]:
    mt = time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(f)))
    try:
        with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
            d = json.load(fp)
        msgs = [m for m in d.get('messages', []) if m.get('role') == 'user']
        real = [m['content'] for m in msgs if not any(x in str(m.get('content')) for x in ['프로필 정보를 추출', '기존 기억', '간결하게 요약'])]
        if real:
            m_name = d.get('model')
            p_name = d.get('provider')
            print(f"[{mt}] {os.path.basename(f)} | model={m_name} | prov={p_name} | msg={real[-1][:60]}")
    except Exception as e:
        pass
