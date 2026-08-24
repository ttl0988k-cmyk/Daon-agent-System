# -*- coding: utf-8 -*-
"""설치 앱 UI에 최신 chat.js/styles.css 로드 확인 + 새로고침."""
import json
import sys
import time
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CDP = "http://127.0.0.1:9222"


def find_ui_target():
    targets = json.load(urllib.request.urlopen(CDP + "/json/list"))
    for t in targets:
        if t.get("type") == "page" and "127.0.0.1:9090" in t.get("url", ""):
            return t
    return None


def main():
    tgt = find_ui_target()
    if not tgt:
        print("UI target not found")
        sys.exit(1)
    ws = websocket.create_connection(tgt["webSocketDebuggerUrl"], timeout=25)
    _id = [0]

    def ev(expr, await_promise=False):
        _id[0] += 1
        params = {"expression": expr, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate",
                            "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                res = m.get("result", {})
                if "exceptionDetails" in res:
                    return "EXC"
                return res.get("result", {}).get("value")

    def reload():
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": "Page.reload"}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                return

    reload()
    time.sleep(5)
    # 초 카운터 변수(말풍선 경과 시간) 존재 여부는 소스 fetch로 판정
    print("CHAT-SRC    :", json.dumps(ev(
        "(async()=>{const r=await fetch('/static/modules/chat.js?nocache='+Date.now());"
        "const t=await r.text();"
        "return [t.includes('_statusTimer'),"
        "t.includes('_toolGroupCard'),"
        "t.includes('_freezeAnswerSegment')];})()", await_promise=True)))
    print("CSS-SRC     :", json.dumps(ev(
        "(async()=>{const r=await fetch('/static/styles.css?nocache='+Date.now());"
        "const t=await r.text();"
        "return [t.length,"
        "t.includes('#htmlPreviewContainer.active')];})()", await_promise=True)))
    ws.close()


if __name__ == "__main__":
    main()
