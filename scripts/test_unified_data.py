# -*- coding: utf-8 -*-
import urllib.request
import json

def test():
    base = "http://127.0.0.1:9090"
    
    # 1. Test Providers
    req = urllib.request.urlopen(f"{base}/api/providers")
    prov_data = json.loads(req.read().decode('utf-8'))
    print("=== PROVIDERS ===")
    print("Registered Providers:", list(prov_data.get('providers', {}).keys()))
    print("Presets:", list(prov_data.get('presets', {}).keys()))
    
    # 2. Test Sessions
    req = urllib.request.urlopen(f"{base}/api/sessions")
    sess_data = json.loads(req.read().decode('utf-8'))
    sessions = sess_data.get('sessions', [])
    print(f"\n=== SESSIONS (Count: {len(sessions)}) ===")
    for s in sessions[:8]:
        print(f"  [{s.get('session_id')}] {s.get('title')} (msgs: {s.get('message_count')})")
        
    # Test first session fetch
    if sessions:
        first_id = sessions[0]['session_id']
        s_req = urllib.request.urlopen(f"{base}/api/session?session_id={first_id}")
        s_res = json.loads(s_req.read().decode('utf-8'))
        print(f"\nFetched first session [{first_id}]:", s_res.get('session', {}).get('title'), "OK!")
        
    # 3. Test Workspaces
    req = urllib.request.urlopen(f"{base}/api/workspaces")
    ws_data = json.loads(req.read().decode('utf-8'))
    print("\n=== WORKSPACES ===")
    print("Active:", ws_data.get('active'))
    print("Workspaces:", [w.get('name') or w.get('path') for w in ws_data.get('workspaces', [])])

if __name__ == '__main__':
    test()
