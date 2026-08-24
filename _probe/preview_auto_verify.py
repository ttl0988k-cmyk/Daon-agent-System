# -*- coding: utf-8 -*-
"""에이전트 자동 미리보기 기능 E2E 검증.

1) UI 새로고침 → 수정본 로드 확인
2) createTabWithContent()로 HTML 파일 쓰기 시뮬레이션 (에이전트 write_file과
   동일한 프론트 경로) → 미리보기가 자동으로 켜지는지 확인
3) 비-HTML 파일은 미리보기가 켜지지 않는지 확인
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

    ws = websocket.create_connection(tgt["webSocketDebuggerUrl"], timeout=30)
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

    reload()
    time.sleep(5)
    print("FIX-loaded  :", ev(
        "(typeof _autoPreviewIfHtml==='function') && "
        "String(createTabWithContent).includes('_autoPreviewIfHtml')"))

    # 시나리오 1: 에이전트가 HTML을 씀 (createTabWithContent 경로)
    ev("toggleBrowserView().then(()=>toggleBrowserView())" if False else "0")
    print("HTML-TAB    :", ev(
        "openFileInTab('" + HTML_PATH + "')", await_promise=True))
    time.sleep(1.2)
    # 미리보기 끄고 시작 (깨끗한 상태에서)
    ev("if(document.getElementById('htmlPreviewContainer').classList.contains('active')) togglePreview()")
    time.sleep(0.4)
    print("BEFORE-OFF  :", ev(
        "document.getElementById('htmlPreviewContainer').classList.contains('active')"))
    # 에이전트 재작성 시뮬레이션: 같은 파일 내용으로 createTabWithContent 호출
    ev("(function(){var t=getActiveTab(); createTabWithContent(t.path, t.content);})()")
    time.sleep(1.0)
    print("AUTO-PREVIEW:", ev(
        "document.getElementById('htmlPreviewContainer').classList.contains('active')"),
        "(should be True)")
    print("DISPLAY     :", ev(
        "getComputedStyle(document.getElementById('htmlPreviewContainer')).display"))

    # 시나리오 2: 비-HTML 파일은 자동 미리보기 안 함
    ev("_closeAllPreviews()")
    js_txt = "data:text/plain,console.log(1)"
    ev("createTabWithContent('C:/daon/tmp-test.js', 'console.log(1);')")
    time.sleep(0.6)
    print("JS-TAB      :", ev(
        "document.getElementById('htmlPreviewContainer').classList.contains('active')"),
        "(should be False)")

    ws.close()


if __name__ == "__main__":
    main()
