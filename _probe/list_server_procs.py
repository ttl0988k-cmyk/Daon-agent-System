import psutil

print("Checking server processes:")
for p in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
    try:
        pname = (p.info['name'] or '').lower()
        if 'server' in pname:
            pid = p.info['pid']
            cmd = ' '.join(p.info['cmdline'] or [])
            print(f"PID {pid}: {cmd}")
    except Exception:
        pass
