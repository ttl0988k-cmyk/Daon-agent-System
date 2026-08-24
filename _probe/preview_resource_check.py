# -*- coding: utf-8 -*-
"""미리보기 회귀 진단: 라이브 서버(9090)가 서빙하는 index.html의
모든 로컬 리소스 참조를 HEAD 요청으로 검증해 404/누락을 찾는다."""
import re
import urllib.request

BASE = "http://127.0.0.1:9090"

def fetch(url, timeout=6):
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

html = fetch(BASE + "/").decode("utf-8", errors="replace")
refs = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', html)
local = sorted({r for r in refs if r.startswith("/") or r.startswith("./")})
print("TOTAL LOCAL REFS:", len(local))

bad = []
for u in local:
    full = u[1:] if u.startswith("/") else u[2:]
    try:
        fetch(BASE + "/" + full)
    except Exception as e:  # noqa: BLE001
        bad.append((u, str(e)))
        print("FAIL:", u, "->", e)

if not bad:
    print("ALL RESOURCES OK")

# 미리보기 관련 핵심 심볼이 라이브 JS에 존재하는지 재확인
checks = {
    "/static/modules/editor.js": ["toggleHtmlPreview", "refreshHtmlPreviewFrame", "togglePreview"],
    "/static/modules/core.js": ["initMonaco"],
}
for path, symbols in checks.items():
    try:
        body = fetch(BASE + path).decode("utf-8", errors="replace")
        for s in symbols:
            print(("OK  " if s in body else "MISS"), path, "::", s)
    except Exception as e:  # noqa: BLE001
        print("FETCH FAIL:", path, e)

# index.html에 preview 컨테이너/버튼 존재 확인
for token in ['id="previewHtmlBtn"', 'id="htmlPreviewContainer"', 'id="htmlPreview"',
              'id="monacoContainer"', "toggleHtmlPreview()"]:
    print(("OK  " if token in html else "MISS"), "index.html ::", token)
