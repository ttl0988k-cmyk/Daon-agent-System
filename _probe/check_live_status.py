# -*- coding: utf-8 -*-
import json
import sys
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NO_PROXY = {"http": None, "https": None}
targets = requests.get("http://127.0.0.1:9222/json", proxies=NO_PROXY, timeout=5).json()
page_tgt = None
for t in targets:
    if t.get("type") == "page" and "127.0.0.1:9090" in t.get("url", ""):
        page_tgt = t
        break

if not page_tgt:
    print("Page target not found")
    sys.exit(1)

ws = websocket.create_connection(page_tgt["webSocketDebuggerUrl"], timeout=10)
_id = [0]


def ev(expr):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            res = m.get("result", {})
            if "exceptionDetails" in res:
                return "EXC: " + str(res["exceptionDetails"])
            return res.get("result", {}).get("value")


print("=== Live UI Debate State ===")
print("debateIsActive:", ev("typeof debateIsActive !== 'undefined' ? debateIsActive : 'undefined'"))
print("debateStatusText:", ev("document.getElementById('debateStatusText') ? document.getElementById('debateStatusText').textContent : null"))
print("debateNextBtn visible:", ev("document.getElementById('debateNextBtn') ? document.getElementById('debateNextBtn').style.display : null"))
print("currentStreamId:", ev("State ? State.currentStreamId : null"))
print("messages count in debateMessages:", ev("document.getElementById('debateMessages') ? document.getElementById('debateMessages').children.length : 0"))
print("debateMessages innerHTML preview:", str(ev("document.getElementById('debateMessages') ? document.getElementById('debateMessages').innerText : ''"))[:500])

ws.close()
