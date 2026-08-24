# -*- coding: utf-8 -*-
"""4차 CDP 진단: 실행 중인 UI에 로드된 CSS 규칙 덤프.
htmlPreviewContainer/monacoContainer 관련 규칙이 실제로 어떻게
파싱되어 있는지(@media 감싸짐, 후손 선택자, 누락 등) 확인한다."""
import json
import sys
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
    ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id[0]:
            res = msg.get("result", {}).get("result", {})
            if res.get("type") == "string":
                return res.get("value")
            return json.dumps(res.get("value"), ensure_ascii=False, default=str)

# 1) htmlPreview*/monacoContainer 관련 모든 규칙 덤프 (@media 포함 재귀)
print("== RELATED RULES ==")
print(ev("""
(function(){
  var out = [];
  function walk(rules, sheetName, media) {
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i];
      if (r.type === 4) {
        walk(r.cssRules, sheetName, r.conditionText);
      } else if (r.selectorText &&
                 (r.selectorText.indexOf('htmlPreview') !== -1 ||
                  r.selectorText.indexOf('monacoContainer') !== -1)) {
        out.push((sheetName || 'inline') + (media ? ' [@media ' + media + ']' : '')
                 + ' :: ' + r.selectorText + ' { ' + r.style.cssText + ' }');
      }
    }
  }
  for (var s = 0; s < document.styleSheets.length; s++) {
    var sheet = document.styleSheets[s];
    try { walk(sheet.cssRules, sheet.href, null); }
    catch (e) { out.push('SHEET-ERR ' + sheet.href + ': ' + e.message); }
  }
  return out.join('\\n') || 'NO RULES FOUND';
})()
"""))

# 2) 시트 목록 + 규칙 수 (파싱 실패 흔적)
print("== SHEETS ==")
print(ev("""
(function(){
  var out = [];
  for (var s = 0; s < document.styleSheets.length; s++) {
    var sh = document.styleSheets[s];
    try { out.push((sh.href || 'inline') + ' rules=' + sh.cssRules.length); }
    catch (e) { out.push((sh.href || 'inline') + ' ERR=' + e.message); }
  }
  return out.join('\\n');
})()
"""))

# 3) 현재 상태 재확인
print("HAS-ACTIVE:", ev("document.getElementById('htmlPreviewContainer').classList.contains('active')"))
print("COMPUTED:", ev("(function(){var cs=getComputedStyle(document.getElementById('htmlPreviewContainer'));return 'display='+cs.display+' pos='+cs.position+' z='+cs.zIndex;})()"))

ws.close()
