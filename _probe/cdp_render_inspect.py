"""
[렌더링 실측 프로브] CDP로 메인 UI의 도구 카드 렌더링 상태를 직접 검사한다.

- DOM에는 tool-group-card가 존재하는데 화면에 안 보이는 문제의 원인을 찾는다.
- 각 카드의 outerHTML / computed style(display, height, visibility, overflow) /
  부모 체인 / summary 텍스트를 덤프한다.
"""
import json
import sys
from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=10000)
        # 메인 UI 페이지 찾기
        target = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                u = (p.url or "")
                print(f"[CDP page] {u}")
                if "127.0.0.1:9090" in u and "devtools" not in u:
                    target = p
        if target is None:
            print("!! 메인 UI 페이지를 CDP에서 찾지 못함")
            return
        print(f"\n=== 메인 UI: {target.url} ===\n")

        js = """
        () => {
          const out = { cards: [], chatInfo: null, cssCheck: {} };
          const box = document.getElementById('chatMessages');
          if (!box) { out.chatInfo = 'chatMessages 없음!'; return out; }
          const r = box.getBoundingClientRect();
          out.chatInfo = {
            scrollHeight: box.scrollHeight, clientHeight: box.clientHeight,
            scrollTop: box.scrollTop, rect: {x: r.x, y: r.y, w: r.width, h: r.height},
            display: getComputedStyle(box).display,
          };
          const cards = box.querySelectorAll('.tool-group-card');
          cards.forEach((c, i) => {
            const cr = c.getBoundingClientRect();
            const cs = getComputedStyle(c);
            const sum = c.querySelector('summary');
            out.cards.push({
              idx: i,
              open: c.open,
              rect: {x: Math.round(cr.x), y: Math.round(cr.y), w: Math.round(cr.width), h: Math.round(cr.height)},
              display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
              height: cs.height, overflow: cs.overflow,
              summaryText: sum ? sum.textContent.trim().substring(0, 80) : '(summary 없음)',
              itemCount: c.querySelectorAll('.tool-group-item').length,
              parentClass: c.parentElement ? c.parentElement.className : '?',
              inDomOffsetParent: !!c.offsetParent,
            });
          });
          // styles.css 로드 확인: tool-group-card 클래스가 정의된 스타일시트 검색
          let found = false;
          for (const sheet of document.styleSheets) {
            try {
              for (const rule of sheet.cssRules) {
                if (rule.selectorText && rule.selectorText.includes('.tool-group-card')) { found = true; break; }
              }
            } catch (_) {}
            if (found) break;
          }
          out.cssCheck.toolGroupCardRule = found;
          out.cssCheck.sheets = document.styleSheets.length;
          return out;
        }
        """
        result = target.evaluate(js)
        print(json.dumps(result, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
