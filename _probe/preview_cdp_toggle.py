# -*- coding: utf-8 -*-
"""3차 CDP 진단: 미리보기 토글 재현 + MutationObserver로 .active 클래스
변화를 감시해 '누가 끄는지' 특정한다. (비파괴: 미리보기 토글만 재현)"""
import json
import time
import urllib.request

import websocket

CDP = "http://127.0.0.1:9222"

with urllib.request.urlopen(CDP + "/json/list", timeout=5) as r:
    targets = json.loads(r.read().decode("utf-8"))

ui = next((t for t in targets if "127.0.0.1:9090" in t.get("url", "") and t.get("type") == "page"), None)
if not ui:
    print("UI PAGE NOT FOUND")
    raise SystemExit(0)

ws = websocket.create_connection(ui["webSocketDebuggerUrl"], timeout=10)
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

# 1) 관찰자 설치: htmlPreviewContainer / monacoContainer 클래스 변화 기록
ev("""
window.__clsLog = [];
(function(){
  var ids = ['htmlPreviewContainer','mdPreviewContainer','imgPreviewContainer','monacoContainer'];
  ids.forEach(function(id){
    var el = document.getElementById(id);
    if (!el) return;
    new MutationObserver(function(muts){
      muts.forEach(function(m){
        window.__clsLog.push(Date.now() + ' ' + id + ' class="' + el.className + '"');
      });
    }).observe(el, {attributes:true, attributeFilter:['class','style']});
  });
})();
'observer-installed'
""")

# 2) 토글 직전 상태
print("BEFORE:", ev("(function(){var el=document.getElementById('htmlPreviewContainer');return el.className+' | '+getComputedStyle(el).display;})()"))

# 3) 토글 실행 (버튼 onclick과 동일한 함수)
print("TOGGLE:", ev("toggleHtmlPreview(); 'called'"))

# 4) 직후 상태
print("AFTER-0ms:", ev("(function(){var el=document.getElementById('htmlPreviewContainer');return el.className+' | '+getComputedStyle(el).display;})()"))
print("MONACO-AFTER:", ev("document.getElementById('monacoContainer').className"))

# 5) 1초 대기 후 상태 (지연으로 끄는 코드 탐지)
time.sleep(1.0)
print("AFTER-1s:", ev("(function(){var el=document.getElementById('htmlPreviewContainer');return el.className+' | '+getComputedStyle(el).display;})()"))

# 6) 클래스 변화 로그
print("CLS-LOG:")
log = ev("JSON.stringify(window.__clsLog || [])")
try:
    for line in json.loads(log):
        print("  ", line)
except Exception:
    print("  raw:", log)

# 7) iframe 내부 렌더 상태 (srcdoc 문서가 실제로 파싱됐는지)
print("IFRAME-DOC:", ev("(function(){try{var f=document.getElementById('htmlPreview');return 'readyState='+f.contentDocument.readyState+' title='+(f.contentDocument.title||'')+' bodyLen='+f.contentDocument.body.innerHTML.length;}catch(e){return 'ERR:'+e.message;}})()"))

ws.close()
