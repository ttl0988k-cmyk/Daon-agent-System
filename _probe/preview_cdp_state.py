# -*- coding: utf-8 -*-
"""2차 CDP 진단: UI 상태 심층 확인 (읽기 전용)."""
import json
import urllib.request

import websocket

CDP = "http://127.0.0.1:9222"

with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
    targets = json.loads(r.read().decode("utf-8"))

ui = next((t for t in targets if "127.0.0.1:9090" in t.get("url", "") and t.get("type") == "page"), None)
if not ui:
    print("UI PAGE NOT FOUND")
    raise SystemExit(0)

ws = websocket.create_connection(ui["webSocketDebuggerUrl"], timeout=8)
_id = [0]

def ev(expr):
    _id[0] += 1
    ws.send(json.dumps({
        "id": _id[0], "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True},
    }))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            res = msg.get("result", {}).get("result", {})
            if res.get("type") == "string":
                return res.get("value")
            return json.dumps(res.get("value"), ensure_ascii=False, default=str)

checks = {
    "state_keys": "Object.keys(window.State || {}).join(',')",
    "tabs_len": "(window.State && Array.isArray(State.tabs)) ? State.tabs.length : 'no-tabs-array'",
    "tabs_names": "(window.State && Array.isArray(State.tabs)) ? State.tabs.map(t=>t.name+':'+t.ext).join('|') : '-'",
    "active_index": "(window.State && 'activeTabIndex' in State) ? String(State.activeTabIndex) : 'no-field'",
    "typeof_getActiveTab": "typeof getActiveTab",
    "getActiveTab_try": "(function(){ try { var t = getActiveTab(); return t ? ('TAB:'+t.name) : 'NULL'; } catch(e){ return 'THROW:'+e.message; } })()",
    "welcome_display": "document.getElementById('welcomeCanvas') ? document.getElementById('welcomeCanvas').style.display : 'no-el'",
    "monaco_style_display": "document.getElementById('monacoContainer') ? document.getElementById('monacoContainer').style.display : 'no-el'",
    "preview_cs_display": "(function(){ var el=document.getElementById('htmlPreviewContainer'); if(!el) return 'no-el'; var cs=getComputedStyle(el); return cs.display+'/'+cs.position+'/'+el.offsetWidth+'x'+el.offsetHeight; })()",
    "preview_parent_chain": "(function(){ var el=document.getElementById('htmlPreviewContainer'); var out=[]; var n=el; for(var i=0;i<6&&n;i++){ var cs=getComputedStyle(n); out.push(n.id||n.className||n.tagName+':disp='+cs.display); n=n.parentElement; } return out.join(' <- '); })()",
    "beginner_mode": "typeof isBeginnerMode !== 'undefined' ? String(isBeginnerMode()) : (document.body.className || 'unknown')",
    "body_class": "document.body.className",
    "editor_area_visible": "(function(){ var ea=document.querySelector('.editor-area'); if(!ea) return 'no-el'; var cs=getComputedStyle(ea); return cs.display+'/'+ea.offsetWidth+'x'+ea.offsetHeight; })()",
    "canvas_area_box": "(function(){ var ca=document.querySelector('.canvas-area'); if(!ca) return 'no-el'; return ca.offsetWidth+'x'+ca.offsetHeight; })()",
    "active_file_path": "(document.getElementById('activeFilePath')||{}).textContent",
    "srcdoc_now": "(function(){ var f=document.getElementById('htmlPreview'); return f && f.srcdoc ? ('len='+f.srcdoc.length) : 'empty'; })()",
    "last_error": "window.__daonLastError || 'none'",
}
for name, expr in checks.items():
    try:
        print(f"{name}: {ev(expr)}")
    except Exception as e:  # noqa: BLE001
        print(f"{name}: EVAL-ERROR {e}")

ws.close()
