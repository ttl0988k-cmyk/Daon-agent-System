# -*- coding: utf-8 -*-
"""GitHub 검색: 카글에서 돌아가는 영상생성 노트북 찾기.
1) 시스템 브라우저(9222)에 새 탭으로 GitHub 검색 화면 표시 (대표님 공유 화면)
2) GitHub Search API로 구조화된 결과 수집
"""
import json, urllib.request, urllib.parse, time, sys

sys.stdout.reconfigure(encoding="utf-8")

# ---------- 1. 시스템 브라우저에 검색 화면 띄우기 ----------
browser_ok = False
try:
    from playwright.sync_api import sync_playwright
    pw_ctx = sync_playwright().start()
    browser = pw_ctx.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = browser.contexts[0]
    page = ctx.new_page()
    page.goto(
        "https://github.com/search?q=video+generation+kaggle+notebook&type=repositories&s=stars&o=desc",
        timeout=30000,
    )
    browser_ok = True
    print(f"[BROWSER] 새 탭 열림: {page.url}")
except Exception as e:
    print(f"[BROWSER] 실패(무시하고 API로 진행): {e}")

# ---------- 2. GitHub Search API ----------
def api(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "raon-search",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

queries = [
    "video generation kaggle",
    "kaggle notebook video diffusion",
    "text-to-video kaggle",
    "image-to-video kaggle notebook",
    "video generation notebook gpu",
]
seen = {}
for q in queries:
    try:
        d = api("https://api.github.com/search/repositories?q="
                + urllib.parse.quote(q) + "&sort=stars&order=desc&per_page=10")
        print(f"[API] '{q}' -> {d.get('total_count', 0)}개")
        for r in d.get("items", []):
            key = r["full_name"]
            if key not in seen or r["stargazers_count"] > seen[key]["stars"]:
                seen[key] = {
                    "stars": r["stargazers_count"],
                    "desc": (r.get("description") or "")[:130],
                    "url": r["html_url"],
                    "updated": r["updated_at"][:10],
                    "lang": r.get("language"),
                }
        time.sleep(1.2)
    except Exception as e:
        print(f"[API] ERR '{q}': {e}")

top = sorted(seen.items(), key=lambda kv: -kv[1]["stars"])
print(f"\n=== 통합 결과 {len(top)}개 저장소 (스타순) ===")
for name, info in top[:25]:
    print(f"{info['stars']:>6}* | {name} | {info['lang']} | upd:{info['updated']}")
    print(f"         {info['desc']}")
    print(f"         {info['url']}")

with open("search_results.json", "w", encoding="utf-8") as f:
    json.dump(seen, f, ensure_ascii=False, indent=1)
print("\n[저장] search_results.json")
