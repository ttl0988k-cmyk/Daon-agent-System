"""
[클릭 흡수 범인 실측 v2] CDP elementFromPoint로 화면 요소들의 클릭을
실제로 누가 받는지 확인한다. 단순화된 JS로 문법 에러 제거.
"""
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

JS = """
() => {
  const out = {};
  out.alive = {
    switchPanel: typeof switchPanel,
    renderMessages: typeof renderMessages,
    addFiles: typeof addFiles,
    storeInit: typeof storeInit
  };
  const results = [];
  const box = document.getElementById('chatMessages');
  if (box) {
    const r = box.getBoundingClientRect();
    const cx = Math.round(r.x + r.width / 2);
    const cy = Math.round(r.y + r.height / 2);
    const hit = document.elementFromPoint(cx, cy);
    results.push({ where: 'chatMessages-center', cx: cx, cy: cy,
      hit: hit ? hit.tagName + '|' + String(hit.className).substring(0, 50) : 'null' });
  }
  const btns = document.querySelectorAll('button, .icon-btn, [onclick]');
  const seen = {};
  let count = 0;
  for (let i = 0; i < btns.length && count < 14; i++) {
    const el = btns[i];
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) continue;
    const cx = Math.round(r.x + r.width / 2);
    const cy = Math.round(r.y + r.height / 2);
    const key = cx + ':' + cy;
    if (seen[key]) continue;
    seen[key] = 1;
    const hit = document.elementFromPoint(cx, cy);
    const inSelf = (hit === el) || el.contains(hit);
    results.push({ where: (el.textContent || '').trim().substring(0, 15) || el.tagName,
      cx: cx, cy: cy,
      hit: hit ? hit.tagName + '|' + String(hit.className).substring(0, 40) : 'null',
      ok: inSelf });
    count++;
  }
  out.hits = results;
  let okCount = 0;
  for (let i = 0; i < results.length; i++) { if (results[i].ok === true) okCount++; }
  out.summary = { total: results.length, clickReachesApp: okCount };
  return out;
}
"""

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=10000)
        target = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                u = (p.url or "")
                print(f"[CDP page] {u}")
                if "127.0.0.1:9090" in u and "devtools" not in u:
                    target = p
        if target is None:
            print("!! 메인 UI 페이지 없음")
            return
        result = target.evaluate(JS)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        s = result.get("summary", {})
        total = s.get("total", 0)
        ok = s.get("clickReachesApp", 0)
        print("\n=== 판정 ===")
        if total and ok == total:
            print("✅ 모든 클릭이 앱 요소에 도달 — 앱 내부 문제 (이벤트 바인딩 확인 필요)")
        elif ok == 0:
            print("❌ 클릭이 전혀 앱에 도달하지 않음 — 투명 오버레이/다른 창이 덮고 있음")
        else:
            print(f"⚠️ 일부만 도달 ({ok}/{total}) — 부분적으로 가려진 상태")

if __name__ == "__main__":
    main()
