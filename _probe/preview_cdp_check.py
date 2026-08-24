# -*- coding: utf-8 -*-
"""실행 중인 DAON 앱(Electron)의 메인 UI 페이지를 CDP로 읽기 전용 진단한다.
- /json/list에서 127.0.0.1:9090 UI 페이지 타깃 탐색
- Runtime.evaluate로 toggleHtmlPreview/refreshHtmlPreviewFrame 존재,
  htmlPreviewContainer 상태, window.onerror 흔적 확인
페이지를 새로고침하거나 조작하지 않는다."""
import json
import urllib.request

import websocket  # websocket-client

CDP = "http://127.0.0.1:9222"

with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
    targets = json.loads(r.read().decode("utf-8"))

ui = None
for t in targets:
    url = t.get("url", "")
    if "127.0.0.1:9090" in url and t.get("type") == "page":
        ui = t
        break

print("TARGETS:", [(t.get("type"), t.get("url", "")[:60]) for t in targets])
if not ui:
    print("UI PAGE NOT FOUND — main window may not be attached to this CDP")
    raise SystemExit(0)

print("UI TARGET:", ui.get("url"))
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
            return res.get("value")

checks = {
    "toggleHtmlPreview": "typeof toggleHtmlPreview",
    "togglePreview": "typeof togglePreview",
    "refreshHtmlPreviewFrame": "typeof refreshHtmlPreviewFrame",
    "btn_exists": "!!document.getElementById('previewHtmlBtn')",
    "btn_display": "(document.getElementById('previewHtmlBtn')||{}).style ? document.getElementById('previewHtmlBtn').style.display : 'no-btn'",
    "container_exists": "!!document.getElementById('htmlPreviewContainer')",
    "iframe_exists": "!!document.getElementById('htmlPreview')",
    "monaco_loaded": "typeof monaco !== 'undefined' && !!monaco.editor",
    "editor_created": "!!(window.State && State.editor)",
    "active_tab_ext": "(window.State && getActiveTab && getActiveTab()) ? (getActiveTab().name || '') : 'NO-TAB'",
    "preview_active_class": "document.getElementById('htmlPreviewContainer') ? document.getElementById('htmlPreviewContainer').className : 'no-container'",
    "monaco_container_class": "document.getElementById('monacoContainer') ? document.getElementById('monacoContainer').className : 'no-monaco'",
}
for name, expr in checks.items():
    try:
        print(f"{name}: {ev(expr)}")
    except Exception as e:  # noqa: BLE001
        print(f"{name}: EVAL-ERROR {e}")

ws.close()
