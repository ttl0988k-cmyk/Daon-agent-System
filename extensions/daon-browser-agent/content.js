/**
 * DAON Browser Agent - Content Script
 * 웹페이지 내부에서 실행되며 DOM 정보를 수집하고(Sensor) 마우스/키보드 액션을 대행합니다(Actuator).
 */

(function () {
  // 특수 내부 프레임 방어 (about:blank 또는 data: iframe에서는 동작하지 않음)
  if (!window.location.href || window.location.href === 'about:blank' || window.location.href.startsWith('data:')) {
    return;
  }
  if (window.__daonContentScriptLoaded) return;
  window.__daonContentScriptLoaded = true;

  console.log('[DAON Agent] Content script active on:', window.location.href);

  let activeBadge = null;
  let activeHighlightEl = null;

  // ── 시각적 피드백 (하이라이트 및 가상 뱃지) ─────────────────────────────────
  function showFeedback(element, text = 'DAON 작업 중') {
    clearFeedback();
    if (!element) return;

    element.classList.add('daon-agent-highlight');
    activeHighlightEl = element;

    const rect = element.getBoundingClientRect();
    const badge = document.createElement('div');
    badge.className = 'daon-agent-badge';
    badge.innerHTML = `<span style="font-size:12px;">🤖</span> <span>${text}</span>`;
    badge.style.left = `${window.scrollX + rect.left}px`;
    badge.style.top = `${window.scrollY + rect.top}px`;

    document.body.appendChild(badge);
    activeBadge = badge;

    setTimeout(clearFeedback, 2500);
  }

  function clearFeedback() {
    if (activeHighlightEl) {
      activeHighlightEl.classList.remove('daon-agent-highlight');
      activeHighlightEl = null;
    }
    if (activeBadge && activeBadge.parentNode) {
      activeBadge.parentNode.removeChild(activeBadge);
      activeBadge = null;
    }
  }

  // ── 검색 가능한 도큐먼트 수집 (메인 프레임 + 동일 출처 iframe/프레임 탐색) ────
  function getSearchableDocuments() {
    const docs = [document];
    try {
      const iframes = Array.from(document.querySelectorAll('iframe, frame'));
      for (const f of iframes) {
        try {
          const doc = f.contentDocument || f.contentWindow?.document;
          if (doc && doc.body) {
            docs.push(doc);
          }
        } catch (e) {
          // 크로스 오리진 iframe은 보안상 직접 접근 불가 (manifest all_frames 로 보완)
        }
      }
    } catch (e) {}
    return docs;
  }

  // ── 요소 탐색 헬퍼 (CSS 셀렉터 + 시맨틱 텍스트 매칭 + nth 다중 일치 지원) ──
  function findElement(query, nth = 1) {
    if (!query) return null;
    query = query.trim();
    nth = Math.max(1, parseInt(nth) || 1);

    const docs = getSearchableDocuments();
    const matches = [];

    for (const doc of docs) {
      // 1. 직접 CSS 셀렉터 시도
      try {
        const els = Array.from(doc.querySelectorAll(query));
        for (const el of els) {
          if (!matches.includes(el)) matches.push(el);
        }
      } catch (e) {
        // 잘못된 셀렉터 구문이면 텍스트 탐색으로 폴백
      }

      // 2. ID 또는 Name 시도
      const elId = doc.getElementById(query);
      if (elId && !matches.includes(elId)) matches.push(elId);

      const elNames = Array.from(doc.querySelectorAll(`[name="${query}"]`));
      for (const el of elNames) {
        if (!matches.includes(el)) matches.push(el);
      }

      // 3. Placeholder, Aria-Label, Title 시도
      const attrEls = Array.from(doc.querySelectorAll(
        `[placeholder*="${query}" i], [aria-label*="${query}" i], [title*="${query}" i]`
      ));
      for (const el of attrEls) {
        if (!matches.includes(el)) matches.push(el);
      }

      // 4. 버튼/링크 텍스트 내용으로 탐색
      const candidates = Array.from(doc.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"]'));
      for (const c of candidates) {
        const text = (c.innerText || c.textContent || c.value || '').trim();
        if (text.toLowerCase().includes(query.toLowerCase())) {
          if (!matches.includes(c)) matches.push(c);
        }
      }

      // 5. 일반 텍스트 매칭
      const allTextEls = Array.from(doc.querySelectorAll('span, div, p, label, li, td, th, h1, h2, h3, h4, em, strong'));
      for (const c of allTextEls) {
        if (c.children.length === 0 && (c.textContent || '').trim().toLowerCase().includes(query.toLowerCase())) {
          if (!matches.includes(c)) matches.push(c);
        }
      }
    }

    // ⚠️ 2026-09-10 패치: type 속성이 아예 없는 input은 input[type=text] 셀렉터에 매칭되지 않음
    // (HTML 기본값이 text여도 DOM에 속성이 없으면 안 잡힘 — 네이버 검색창 사례)
    // → 쿼리에 input이 포함되어 있고 매칭 실패 시 visible 텍스트 입력창 폴백 탐색
    if (matches.length === 0 && /\binput\b/i.test(query)) {
      for (const doc of docs) {
        const inputs = Array.from(doc.querySelectorAll(
          'input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="image"]), textarea'
        ));
        for (const el of inputs) {
          if (el.offsetParent !== null || el.offsetHeight > 0) {
            if (!matches.includes(el)) matches.push(el);
          }
        }
      }
    }


    if (matches.length === 0) return null;
    return matches[nth - 1] || matches[0];
  }

  // ── React/Vue 호환 타이핑 (Synthetic Event Dispatcher) ─────────────────────
  function simulateTyping(element, text) {
    element.focus();

    // React/Vue 내부 상태 갱신을 위해 prototype setter 우회
    const proto = Object.getPrototypeOf(element);
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set ||
                   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set ||
                   Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;

    if (setter) {
      setter.call(element, text);
    } else {
      element.value = text;
    }

    // 표준 이벤트 시퀀스 발생 (keydown→input→change→keyup, React/Vue 모두 커버)
    element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 }));
    element.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
    element.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, cancelable: true, key: 'Enter' }));

    // Enter 키 의미(=제출)는 form.requestSubmit 폴백으로 확정 (네이버 등 keydown submit 사이트 대응)
    try {
      const form = element.closest('form');
      if (form && typeof form.requestSubmit === 'function') {
        form.requestSubmit();
      }
    } catch (e) { /* form 없으면 무시 */ }

    return element.value === text;
  }

  // ── 마우스 클릭 시뮬레이션 ───────────────────────────────────────────────
  function simulateClick(element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });

    const mouseEvents = ['mouseenter', 'mouseover', 'mousedown', 'mouseup', 'click'];
    const rect = element.getBoundingClientRect();
    const clientX = rect.left + rect.width / 2;
    const clientY = rect.top + rect.height / 2;

    mouseEvents.forEach(eventType => {
      const event = new MouseEvent(eventType, {
        view: window,
        bubbles: true,
        cancelable: true,
        clientX,
        clientY
      });
      element.dispatchEvent(event);
    });

    if (typeof element.focus === 'function') element.focus();
    if (typeof element.click === 'function') element.click();
  }

  // ── 마우스 호버(Hover) 시뮬레이션 ────────────────────────────────────────
  function simulateHover(element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const rect = element.getBoundingClientRect();
    const clientX = rect.left + rect.width / 2;
    const clientY = rect.top + rect.height / 2;

    const mouseEvents = ['mouseenter', 'mouseover', 'mousemove'];
    mouseEvents.forEach(eventType => {
      element.dispatchEvent(new MouseEvent(eventType, {
        view: window,
        bubbles: true,
        cancelable: true,
        clientX,
        clientY
      }));
    });
  }

  // ── 키보드 입력(Key Press) 시뮬레이션 ─────────────────────────────────────
  function simulateKeyPress(element, key = 'Enter') {
    const target = element || document.activeElement || document.body;
    if (typeof target.focus === 'function') target.focus();

    const keyEvents = ['keydown', 'keypress', 'keyup'];
    keyEvents.forEach(type => {
      target.dispatchEvent(new KeyboardEvent(type, {
        key: key,
        code: key === 'Enter' ? 'Enter' : (key === 'Escape' ? 'Escape' : (key === 'Tab' ? 'Tab' : key)),
        bubbles: true,
        cancelable: true,
        view: window
      }));
    });

    if (key.toLowerCase() === 'enter') {
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
        const form = target.closest('form');
        if (form) {
          try {
            if (form.requestSubmit) form.requestSubmit();
            else form.submit();
          } catch (e) {}
        }
      }
    }
  }

  // ── 페이지 컨텍스트 추출 (Sensor — 메인 + iframe 통합) ───────────────────
  function extractPageContext() {
    const title = document.title || '';
    const url = window.location.href;
    const selection = window.getSelection()?.toString()?.trim() || '';

    // 메타 설명 태그
    const metaDesc = document.querySelector('meta[name="description"]')?.getAttribute('content') || '';

    const docs = getSearchableDocuments();
    let combinedText = '';
    const buttons = [];
    const inputs = [];

    for (const doc of docs) {
      if (!doc.body) continue;
      const clone = doc.body.cloneNode(true);
      const unwanted = clone.querySelectorAll('script, style, noscript, svg, nav, footer');
      unwanted.forEach(n => n.remove());

      let text = clone.innerText || clone.textContent || '';
      text = text.replace(/\s+/g, ' ').trim();
      if (text) {
        combinedText += (combinedText ? '\n\n' : '') + text;
      }

      // 주요 대화형 요소 수집
      Array.from(doc.querySelectorAll('button, [role="button"], input[type="submit"], a.btn, a[role="button"]'))
        .map(b => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim())
        .filter(t => t.length > 0 && t.length < 30)
        .forEach(t => { if (!buttons.includes(t)) buttons.push(t); });

      Array.from(doc.querySelectorAll('input:not([type="hidden"]), textarea, select'))
        .map(i => i.placeholder || i.name || i.id || i.getAttribute('aria-label') || '')
        .filter(t => t.length > 0 && t.length < 40)
        .forEach(t => { if (!inputs.includes(t)) inputs.push(t); });
    }

    if (combinedText.length > 6000) {
      combinedText = combinedText.substring(0, 6000) + '... (이하 생략)';
    }

    return {
      title,
      url,
      metaDesc,
      selectedText: selection,
      bodyText: combinedText,
      interactive: {
        buttons: buttons.slice(0, 25),
        inputs: inputs.slice(0, 25)
      }
    };
  }

  // ── 대화형 요소 스냅샷 추출 (Agent Grounding — 메인 + iframe 통합) ────────
  function extractInteractiveSnapshot() {
    const docs = getSearchableDocuments();
    const items = [];
    let count = 0;

    for (const doc of docs) {
      if (count >= 50) break;
      const elements = Array.from(doc.querySelectorAll('a, button, input, select, textarea, [role="button"]'));
      for (const el of elements) {
        if (count >= 50) break;
        const rect = el.getBoundingClientRect();
        const isVisible = rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden';
        if (!isVisible) continue;

        const tag = el.tagName.toLowerCase();
        const type = el.type || '';
        const text = (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim();
        const id = el.id ? `#${el.id}` : '';
        const name = el.getAttribute('name') ? `[name="${el.getAttribute('name')}"]` : '';

        items.push({
          index: ++count,
          tag,
          type,
          text: text.slice(0, 40),
          selector: id || name || (el.className ? `.${el.className.split(' ')[0]}` : tag)
        });
      }
    }

    return items;
  }

  // ── 메시지 리스너 (사이드패널 ↔ 컨텐츠 스크립트) ─────────────────────────
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    try {
      switch (request.action) {
        case 'GET_PAGE_CONTEXT': {
          if (window !== window.top) {
            return false; // 서브프레임은 전체 페이지 컨텍스트 응답에서 제외 (메인 프레임만 응답)
          }
          const ctx = extractPageContext();
          sendResponse({ ok: true, data: ctx });
          break;
        }

        case 'GET_PAGE_SNAPSHOT': {
          if (window !== window.top) {
            return false; // 서브프레임은 전체 스냅샷 응답에서 제외
          }
          const snapshot = extractInteractiveSnapshot();
          sendResponse({ ok: true, data: snapshot });
          break;
        }

        case 'ACT_CLICK': {
          const el = findElement(request.target || request.selector, request.nth || 1);
          if (!el) {
            sendResponse({ ok: false, error: `요소를 찾을 수 없습니다: "${request.target || request.selector}" (nth: ${request.nth || 1})` });
            return;
          }
          showFeedback(el, `클릭: ${request.target || '버튼'}`);
          simulateClick(el);
          sendResponse({
            ok: true,
            message: `클릭 완료: <${el.tagName.toLowerCase()}> "${(el.innerText || el.value || '').trim().slice(0, 30)}"`,
            url: window.location.href
          });
          break;
        }

        case 'ACT_HOVER': {
          const el = findElement(request.target || request.selector, request.nth || 1);
          if (!el) {
            sendResponse({ ok: false, error: `요소를 찾을 수 없습니다: "${request.target || request.selector}" (nth: ${request.nth || 1})` });
            return;
          }
          showFeedback(el, `호버: ${request.target || '요소'}`);
          simulateHover(el);
          sendResponse({
            ok: true,
            message: `호버(Mouse Over) 완료: <${el.tagName.toLowerCase()}> "${(el.innerText || el.value || '').trim().slice(0, 30)}"`,
            url: window.location.href
          });
          break;
        }

        case 'ACT_PRESS_KEY': {
          const key = request.key || 'Enter';
          let el = request.target ? findElement(request.target, request.nth || 1) : null;
          if (el) {
            showFeedback(el, `키: ${key}`);
          }
          simulateKeyPress(el, key);
          sendResponse({
            ok: true,
            message: `키 입력 완료: [${key}]${el ? ` (대상: <${el.tagName.toLowerCase()}>)` : ''}`
          });
          break;
        }

        case 'ACT_TYPE': {
          const el = findElement(request.target || request.selector, request.nth || 1);
          if (!el) {
            sendResponse({ ok: false, error: `입력 필드를 찾을 수 없습니다: "${request.target || request.selector}"` });
            return;
          }
          showFeedback(el, `입력: "${request.text}"`);
          const verified = simulateTyping(el, request.text);
          sendResponse({
            ok: true,
            verified,
            value: el.value,
            message: `입력 완료: "${request.text}" (검증: ${verified ? '성공' : '경고'})`
          });
          break;
        }

        case 'ACT_SCROLL': {
          const direction = request.direction || 'down';
          const amount = request.amount || (window.innerHeight * 0.7);
          const top = direction === 'down' ? amount : (direction === 'up' ? -amount : 0);
          window.scrollBy({ top, behavior: 'smooth' });
          sendResponse({ ok: true, scrollY: window.scrollY });
          break;
        }

        default:
          sendResponse({ ok: false, error: `알 수 없는 액션: ${request.action}` });
      }
    } catch (err) {
      console.error('[DAON Agent Content Script Error]', err);
      sendResponse({ ok: false, error: err.message });
    }
    return true; // 비동기 응답 지원
  });

})();
