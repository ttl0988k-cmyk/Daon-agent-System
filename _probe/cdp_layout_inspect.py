"""
[레이아웃 + 사이드바 바인딩 실측]
1. 주요 패널들의 위치/크기 덤프 (레이아웃 붕괴 확인)
2. 사이드바 nav-tab 버튼의 이벤트 바인딩 상태
3. 클릭 시뮬레이션으로 실제 동작 확인
4. 전역 에러 캡처 시작
"""
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

JS = """
() => {
  const out = { panels: [], navTabs: [], clickTest: null, errors: [] };
  // 1) 주요 패널 rect 덤프
  const panelSels = ['#sidebar', '.sidebar', '.left-panel', '.right-panel', '.center-panel',
    '#chatMessages', '#monacoContainer', '#welcomeCanvas', '.main-content', '#canvasArea',
    '#browserViewWrap', '#chatPanel', '.chat-panel'];
  panelSels.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    out.panels.push({ sel: sel, rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      display: cs.display, position: cs.position, visibility: cs.visibility });
  });
  // 2) 사이드바 nav-tab 버튼 바인딩 확인
  document.querySelectorAll('.nav-tab').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    out.navTabs.push({
      i: i, text: (el.textContent || '').trim().substring(0, 10),
      hasOnclickAttr: !!el.getAttribute('onclick'),
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    });
  });
  // 3) 첫 번째 nav-tab 클릭 시뮬레이션 (실제 동작 확인)
  const firstTab = document.querySelector('.nav-tab');
  if (firstTab) {
    const before = document.querySelectorAll('.nav-tab.active').length;
    try { firstTab.click(); out.clickTest = { clicked: true, activeCountBefore: before }; }
    catch (e) { out.clickTest = { clicked: true, error: e.message }; }
  }
  // 4) 전역 에러 캡처 시작 (이후 5초간 발생하는 에러)
  window.__capturedErrors = [];
  window.onerror = function (msg, src, line, col) {
    window.__capturedErrors.push(String(msg).substring(0, 120) + ' @' + line);
  };
  return out;
}
"""

JS2 = """
() => {
  // 5초 후 호출: 캡처된 에러 + 클릭 후 활성 상태 변화 확인
  const out = { capturedErrors: window.__capturedErrors || [], activeTabs: [] };
  document.querySelectorAll('.nav-tab.active').forEach((el) => {
    out.activeTabs.push((el.textContent || '').trim().substring(0, 10));
  });
  // 패널 표시 상태 재확인
  const panelSels = ['#chatMessages', '#monacoContainer', '.right-panel', '#sidebar'];
  out.panelsAfter = [];
  panelSels.forEach(sel => {
    const el = document.querySelector(sel);
    if (!el) return;
    const r = el.getBoundingClientRect();
    out.panelsAfter.push({ sel: sel, w: Math.round(r.width), h: Math.round(r.height), display: getComputedStyle(el).display });
  });
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
                if "127.0.0.1:9090" in u and "devtools" not in u:
                    target = p
        if target is None:
            print("!! 메인 UI 페이지 없음")
            return
        r1 = target.evaluate(JS)
        print("=== 1) 패널 레이아웃 ===")
        print(json.dumps(r1.get("panels", []), ensure_ascii=False, indent=1))
        print("\n=== 2) 사이드바 nav-tab 바인딩 ===")
        for t in r1.get("navTabs", []):
            print(f"  [{t['i']}] '{t['text']}' onclick속성={t['hasOnclickAttr']} rect={t['rect']}")
        print("\n=== 3) 클릭 시뮬레이션 ===")
        print(json.dumps(r1.get("clickTest"), ensure_ascii=False))
        import time
        time.sleep(2)
        r2 = target.evaluate(JS2)
        print("\n=== 4) 클릭 후 상태 + 캡처된 에러 ===")
        print(json.dumps(r2, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
