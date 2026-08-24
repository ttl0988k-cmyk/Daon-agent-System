# -*- coding: utf-8 -*-
"""미리보기 수정본 배포 후 E2E 재검증 프로브.

1) UI 페이지를 새로고침해 수정된 editor.js / browser_ai.js를 다시 로드
2) 수정 코드가 실제 로드됐는지 함수 소스 문자열로 확인
3) HTML 파일을 탭으로 열고 togglePreview() → computed display 검증
"""
import json
import sys
import time
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CDP = "http://127.0.0.1:9222"
HTML_PATH = "C:/daon/cafe/blue-hour/index.html"


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
                    return "EXC: " + str(
                        res["exceptionDetails"].get("exception", {}).get(
                            "description", "?"))[:300]
                return res.get("result", {}).get("value")

    def reload():
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": "Page.reload"}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                return

    # 1) 새로고침 → 수정본 로드
    reload()
    time.sleep(5)
    print("URL-NOW     :", ev("location.href"))

    # 2) 수정 코드 로드 확인
    print("FIX-editor  :", ev(
        "(typeof togglePreview==='function') && "
        "String(togglePreview).includes(\"style.display = ''\")"))
    print("FIX-bai     :", ev(
        "(typeof toggleBrowserView==='function') && "
        "String(toggleBrowserView).includes(\"htmlPreview.style.display = ''\")"))

    # 3) 잔재 재현: 브라우저 뷰 열었다 닫기 → 과거에는 인라인 display:none 남음
    ev("toggleBrowserView()")
    time.sleep(0.8)
    ev("toggleBrowserView()")
    time.sleep(0.5)
    print("RESIDUE     :", ev(
        "document.getElementById('htmlPreviewContainer').style.display || '(clean)'"))

    # 4) E2E: 파일 탭 오픈 → 미리보기 토글
    print("OPEN-TAB    :", ev(
        "openFileInTab('" + HTML_PATH + "')", await_promise=True))
    time.sleep(1.5)
    print("ACTIVE-TAB  :", ev("(getActiveTab()||{}).name"))

    ev("togglePreview()")
    time.sleep(0.6)
    el = "document.getElementById('htmlPreviewContainer')"
    print("ON  class   :", ev(el + ".className"))
    print("ON  display :", ev("getComputedStyle(" + el + ").display"))
    print("ON  srclen  :", ev(
        "(document.getElementById('htmlPreview').srcdoc||'').length"))

    ev("togglePreview()")
    time.sleep(0.3)
    print("OFF display :", ev("getComputedStyle(" + el + ").display"))

    ev("togglePreview()")
    time.sleep(0.3)
    print("RE  display :", ev("getComputedStyle(" + el + ").display"))

    ws.close()


if __name__ == "__main__":
    main()
