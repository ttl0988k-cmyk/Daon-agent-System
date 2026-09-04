import psutil
import datetime
import urllib.request
import json
import os

print("=== 1. Check Running DAON Processes ===")
found_procs = []
for p in psutil.process_iter(['pid', 'name', 'exe', 'create_time', 'cmdline']):
    try:
        pname = (p.info['name'] or '').lower()
        if 'daon' in pname or 'server' in pname or 'electron' in pname:
            ctime = datetime.datetime.fromtimestamp(p.info['create_time']).strftime('%Y-%m-%d %H:%M:%S')
            exe = p.info['exe'] or 'Unknown'
            cmd = ' '.join(p.info['cmdline'] or [])
            found_procs.append((p.info['pid'], p.info['name'], ctime, exe, cmd))
            print(f"PID: {p.info['pid']}, Name: {p.info['name']}, Started: {ctime}")
            print(f"  Path: {exe}")
            print(f"  Cmd: {cmd[:140]}...")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

if not found_procs:
    print("No running DAON processes detected.")

print("\n=== 2. Check File Last Modified Times ===")
files_to_check = [
    r"C:\daon\DAON-Portable\DAON Agent System.exe",
    r"C:\daon\DAON-Portable\resources\server.exe",
    r"C:\daon\DAON-Portable\resources\static\modules\browser_ai.js",
    r"C:\daon\Daon agent System\dist\server.exe",
    r"C:\Users\ttl09\AppData\Local\Programs\DAON Agent System\resources\server.exe"
]
for f in files_to_check:
    if os.path.exists(f):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M:%S')
        size = os.path.getsize(f)
        print(f"{f}\n  -> Modified: {mtime}, Size: {size:,} bytes")
    else:
        print(f"{f}\n  -> NOT FOUND")

print("\n=== 3. Test Running Server API (port 9090) ===")
try:
    req = urllib.request.Request("http://127.0.0.1:9090/health")
    with urllib.request.urlopen(req, timeout=3) as resp:
        body = resp.read().decode('utf-8')
        print(f"GET /health -> Code {resp.status}, Body: {body}")
except Exception as e:
    print("GET /health failed:", e)

try:
    req = urllib.request.Request("http://127.0.0.1:9090/api/browser/grid")
    with urllib.request.urlopen(req, timeout=3) as resp:
        body = resp.read().decode('utf-8')
        data = json.loads(body)
        print(f"GET /api/browser/grid -> Code {resp.status}, ok: {data.get('ok')}, grid_items: {len(data.get('grid', []))}")
except Exception as e:
    print("GET /api/browser/grid failed:", e)
