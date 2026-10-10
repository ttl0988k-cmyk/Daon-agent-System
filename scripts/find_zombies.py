# -*- coding: utf-8 -*-
import psutil

print("=== Scanning Processes ===")
for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
    try:
        name = (proc.info['name'] or '').lower()
        if 'python' in name or 'server' in name or 'daon' in name or 'electron' in name or 'cmd' in name:
            cmd = " ".join(proc.info['cmdline'] or [])
            if any(k in cmd.lower() for k in ['daon', 'server', 'hermes', 'laya', 'whisper', '9090', '8088', '8099']):
                try:
                    conns = [c.laddr.port for c in proc.connections() if c.status == 'LISTEN']
                except Exception:
                    conns = []
                print(f"PID {proc.info['pid']} ({proc.info['name']}): Ports={conns} | Cmd={cmd[:120]}")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
