import urllib.request
import re
import sys

base = 'http://127.0.0.1:9090'
to_check = ['/static/v2/js/app.js?v=20261008_2225']
checked = set()

print("Checking JS modules...")
while to_check:
    rel = to_check.pop(0)
    if rel in checked:
        continue
    checked.add(rel)
    
    if rel.startswith('/'):
        url = base + rel
    else:
        url = base + '/static/v2/js/' + rel
        
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            data = resp.read().decode('utf-8', errors='replace')
            ct = resp.headers.get('Content-Type')
            print(f"[{resp.status}] ({ct}) {rel} ({len(data)} chars)")
            
            # extract imports
            matches = re.findall(r'(?:from\s+[\'"]([^\'"]+)[\'"]|import\s+[\'"]([^\'"]+)[\'"])', data)
            for m in matches:
                target = m[0] or m[1]
                if target and (target.startswith('.') or target.endswith('.js')):
                    to_check.append(target)
    except Exception as e:
        print(f"FAILED: {rel} -> {e}")
