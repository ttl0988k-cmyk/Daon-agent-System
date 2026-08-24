# -*- coding: utf-8 -*-
"""미리보기 이미지 재작성 수정 E2E 검증.

1) UI 새로고침 → 수정본 로드 확인
2) blue-hour/index.html 탭 오픈 → 미리보기 켬
3) iframe 내부의 img 요소 naturalWidth(실제 로드 폭) 검사
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
        "(typeof refreshHtmlPreviewFrame==='function') && "
        "String(refreshHtmlPreviewFrame).includes('_rewriteLocalAssetUrls')"))

    print("OPEN-TAB    :", ev(
        "openFileInTab('" + HTML_PATH + "')", await_promise=True))
    time.sleep(1.2)
    ev("togglePreview()")
    time.sleep(3.5)  # 이미지 로드 대기

    print("PREVIEW-DISP:", ev(
        "getComputedStyle(document.getElementById('htmlPreviewContainer')).display"))
    print("SRCDOC-RW   :", ev(
        "String(document.getElementById('htmlPreview').srcdoc).split('/api/file/raw').length - 1",
    ), "rewritten refs")
    print("IMG-RESULT  :", json.dumps(ev("""
      (function(){
        var f=document.getElementById('htmlPreview');
        var d=f.contentDocument; if(!d) return 'NO-DOC';
        var imgs=d.querySelectorAll('img');
        var ok=0,bad=0,detail=[];
        imgs.forEach(function(im){
          if(im.naturalWidth>0){ok++;}else{bad++;detail.push(im.getAttribute('src').slice(0,80));}
        });
        var hero=getComputedStyle(d.querySelector('.hero .bg')||d.body).backgroundImage;
        return {total:imgs.length, loaded:ok, failed:bad,
                badSrc:detail.slice(0,3),
                heroBg:(hero||'').slice(0,120)};
      })()
    """)))

    ws.close()


if __name__ == "__main__":
    main()
