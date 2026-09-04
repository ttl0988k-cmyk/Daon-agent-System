"""
Daon Agent System — Playwright Browser Automation routes.

All browser operations run on a dedicated background thread to avoid Playwright's
greenlet thread-binding limitation ("cannot switch to a different thread").

POST /api/browser/navigate   — navigate to URL
POST /api/browser/sync_url   — sync URL (Electron: IPC already navigated; AI connects via CDP)
POST /api/browser/snapshot   — get accessibility snapshot
POST /api/browser/click      — click element
POST /api/browser/type       — type text
POST /api/browser/screenshot — take screenshot
POST /api/browser/execute    — execute JavaScript
POST /api/browser/close      — close browser
GET  /api/browser/status     — browser status
GET  /api/browser/recommend  — AI recommends next action
"""
import logging
import base64
import threading
import queue
import os
import json as _json
import time

from api.helpers import j, j_ok, j_err

_logger = logging.getLogger(__name__)


# ── Browser Worker Thread ──
# Playwright Page objects are bound to the thread that created them.
# We run ALL browser operations on a single dedicated thread.

_browser_worker = None
_browser_worker_lock = threading.Lock()
_browser_task_queue = queue.Queue()
_browser_result_queue = queue.Queue()
_BROWSER_WORKER_STOP = object()  # sentinel to stop the worker thread

# Cached state accessible from any thread (read-only after worker sets them)
_last_url = ""
_browser_active = False

# ── 참조번호(ref) 안정화 ──
# 스냅샷 시점의 대화형 요소 목록을 서버에 보관한다. click/type/fill은 이 목록에서
# 해당 ref의 식별자(id/name/텍스트/href)를 찾아 "셀렉터 우선"으로 원소를 찾고,
# 못 찾을 때만 기존 위치 기반 재계산으로 폴백한다. DOM이 살짝 변해도 ref가
# 다른 원소를 가리키는 사고를 막는다.
_ref_store_lock = threading.Lock()
_ref_store: dict = {}          # ref('e0') → {tag,text,href,type,placeholder,id,name}
_REF_STORE_MAX = 400


# ── 큰 iframe 감지 (2026-08-28) ──
# upsampler.co처럼 생성기/플레이어가 iframe으로 임베드된 사이트에서는
# iframe이 페이지 레이아웃에 묻혀 잘 안 보인다. iframe URL을 직접 열면
# 전체 화면으로 사용할 수 있다. 에이전트가 판단할 수 있도록 주요 iframe
# (400x300 이상) 목록을 navigate/open/snapshot 결과에 포함한다.
# 광고/분석용 작은 iframe은 제외되므로 자동 새 탭 폭탄 위험이 없다.
_IFRAME_DETECT_JS = """
(() => {
  try {
    const frames = Array.from(document.querySelectorAll('iframe'));
    return frames
      .map(f => ({ src: f.src || '', width: f.clientWidth || 0, height: f.clientHeight || 0 }))
      .filter(f => f.src && f.src.startsWith('http') && f.height >= 300 && f.width >= 400)
      .sort((a, b) => (b.width * b.height) - (a.width * a.height))
      .slice(0, 3);
  } catch (e) { return []; }
})()
"""


_EXTRACT_INTERACTIVE_JS = """
(() => {
    const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],[role="checkbox"],[role="radio"],[role="menuitem"],[role="tab"],[contenteditable="true"],details,summary';
    const allEls = document.querySelectorAll(interactive);
    const results = [];
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;

    allEls.forEach((el) => {
        // 1. Basic geometry & size filter
        const rect = el.getBoundingClientRect();
        if (rect.width <= 2 || rect.height <= 2) return;

        // 2. Computed style checks (Browser-Use Visibility Filter)
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.05) return;
        if (style.pointerEvents === 'none') return;
        if (el.disabled) return;

        // 3. Occlusion check: Is center point of element covered by an overlay/modal?
        const cx = Math.min(Math.max(rect.left + rect.width / 2, 0), vw - 1);
        const cy = Math.min(Math.max(rect.top + rect.height / 2, 0), vh - 1);
        const topEl = document.elementFromPoint(cx, cy);
        let isVisible = topEl && (el === topEl || el.contains(topEl) || topEl.contains(el));

        // Fallback offset check if center is covered by child icon/badge
        if (!isVisible && rect.width > 12 && rect.height > 12) {
            const p2 = document.elementFromPoint(rect.left + 6, rect.top + 6);
            if (p2 && (el === p2 || el.contains(p2) || p2.contains(el))) {
                isVisible = true;
            }
        }
        if (!isVisible) return;

        // 4. Extract rich metadata
        const label = (
            el.getAttribute('aria-label') ||
            el.getAttribute('title') ||
            el.placeholder ||
            (el.innerText || el.textContent || '').trim().substring(0, 150)
        );

        results.push({
            ref: 'e' + results.length,
            tag: el.tagName.toLowerCase(),
            text: label,
            href: el.href || null,
            type: el.type || null,
            placeholder: el.placeholder || null,
            id: el.id || null,
            name: el.getAttribute('name') || null,
            className: el.className || null,
            rect: {
                x: Math.round(rect.left),
                y: Math.round(rect.top),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            },
            in_viewport: (rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw)
        });
    });
    return results;
})()
"""

_SOM_INJECT_JS = """
(elements) => {
    const OVERLAY_ID = '__daon_som_overlay__';
    let existing = document.getElementById(OVERLAY_ID);
    if (existing) existing.remove();

    const container = document.createElement('div');
    container.id = OVERLAY_ID;
    container.style.position = 'fixed';
    container.style.top = '0';
    container.style.left = '0';
    container.style.width = '100vw';
    container.style.height = '100vh';
    container.style.pointerEvents = 'none';
    container.style.zIndex = '2147483647';
    document.body.appendChild(container);

    const colors = ['#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6'];

    (elements || []).forEach((item, idx) => {
        if (!item.in_viewport) return;
        const r = item.rect;
        if (!r || r.width <= 0 || r.height <= 0) return;
        const color = colors[idx % colors.length];

        const box = document.createElement('div');
        box.style.position = 'fixed';
        box.style.left = r.x + 'px';
        box.style.top = r.y + 'px';
        box.style.width = r.width + 'px';
        box.style.height = r.height + 'px';
        box.style.border = '2px solid ' + color;
        box.style.boxSizing = 'border-box';
        box.style.pointerEvents = 'none';

        const badge = document.createElement('span');
        badge.innerText = item.ref;
        badge.style.position = 'absolute';
        badge.style.top = '-14px';
        badge.style.left = '-2px';
        badge.style.backgroundColor = color;
        badge.style.color = '#ffffff';
        badge.style.fontSize = '10px';
        badge.style.fontWeight = 'bold';
        badge.style.padding = '0 3px';
        badge.style.borderRadius = '2px';
        badge.style.lineHeight = '14px';
        badge.style.fontFamily = 'monospace, sans-serif';

        box.appendChild(badge);
        container.appendChild(box);
    });
}
"""

_SOM_CLEANUP_JS = """
(() => {
    const existing = document.getElementById('__daon_som_overlay__');
    if (existing) existing.remove();
})()
"""


def _detect_iframes(page):
    """페이지의 주요 iframe 목록을 반환한다 (실패 시 빈 리스트 — 순수 부가)."""
    try:
        return page.evaluate(_IFRAME_DETECT_JS) or []
    except Exception:
        return []


def _store_refs(elements) -> None:
    """스냅샷/recommend 결과의 elements를 ref 저장소에 반영한다."""
    if not isinstance(elements, list):
        return
    with _ref_store_lock:
        _ref_store.clear()
        for el in elements:
            try:
                ref = str(el.get("ref", ""))
                if not ref:
                    continue
                _ref_store[ref] = {
                    "tag": el.get("tag") or "",
                    "text": (el.get("text") or "")[:120],
                    "href": el.get("href"),
                    "type": el.get("type"),
                    "placeholder": el.get("placeholder"),
                    "id": el.get("id"),
                    "name": el.get("name"),
                    "rect": el.get("rect"),
                    "in_viewport": el.get("in_viewport", True),
                }
            except Exception:
                continue
        # 상한 방어(비대한 페이지): 오래된 항목부터 잘라낸다.
        while len(_ref_store) > _REF_STORE_MAX:
            _ref_store.pop(next(iter(_ref_store)))


def _get_stored_ref(ref: str):
    with _ref_store_lock:
        return dict(_ref_store.get(ref) or {})
# AI requested navigate — frontend polls /api/browser/status and auto-opens the browser view.
# TTL 기반: 탭 유무와 무관하게 navigate/open 실행 시 항상 설정하고, _PENDING_TTL 초 후 자동 만료.
# 첫-navigate 타임아웃 수정 후 백엔드는 블로킹 없이 즉시 pending 응답하므로, 프론트 5초 폴링이
# 뷰를 만들고 이동할 시간 여유를 주기 위해 TTL을 15초로 확보한다. (동일 URL 반복은 프론트가
# "새 pending"으로만 1회 처리하므로 길어도 부작용 없음)
_pending_url = ""
_pending_url_ts = 0.0
_PENDING_TTL = 15.0

# CDP 재연결 백오프: Electron이 준비되지 않았을 때 폴링마다 connect_over_cdp를
# 다시 시도하면 asyncio 소켓 예외가 반복되고 서버 스레드가 소진되어
# 다른 API(/api/approval/respond 등)가 15초 타임아웃에 걸린다.
# 실패 직후 _CDP_RETRY_COOLDOWN 초간은 CDP 연결 시도를 건너뛰고 즉시 실패 처리한다.
_CDP_RETRY_COOLDOWN = 5.0
_last_cdp_attempt = 0.0  # worker 전용, _ensure_browser 내에서만 접근

# CDP 연결 하드 타임아웃(ms). connect_over_cdp를 타임아웃 없이 호출하면
# 드라이버/엔드포인트가 비정상일 때 Playwright _sync() 루프가 GIL을 쥔 채
# 무한 spin하며 서버 전체를 마비시킨다(py-spy 스택 덤프로 확인).
_CDP_CONNECT_TIMEOUT_MS = 10000
# CDP 연결이 연속으로 이 횟수만큼 실패하면 Playwright 드라이버 자체가
# 망가진 것으로 보고 폐기 후 재생성한다(좀비 드라이버 재발 방지).
_CDP_MAX_CONSECUTIVE_FAILURES = 3
_cdp_fail_streak = 0  # worker 전용, _ensure_browser 내에서만 접근

# ── status 폴링 적체 방지 ──
# 워커가 정체됐을 때 /api/browser/status 폴링(프론트 5초 주기)이 큐에 쌓여
# 서버 스레드마다 35초씩 블로킹되는 것을 막는다. 워커가 바쁘거나 이미
# status 태스크가 대기 중이면 큐에 넣지 않고 캐시 상태를 즉시 반환한다.
_status_gate_lock = threading.Lock()
_pending_status_tasks = 0
_worker_task_start_ts = 0.0    # 워커가 현재 처리 중인 태스크 시작 시각(0=유휴)
_WORKER_STUCK_THRESHOLD = 5.0  # 한 태스크가 이보다 오래 걸리면 워커 정체로 판정
_STATUS_WAIT_TIMEOUT = 6.0     # status 폴링은 워커 결과를 최대 이만큼만 기다림


def _set_pending(url: str) -> None:
    """AI가 요청한 navigate URL을 기록해 프론트 폴링이 브라우저 뷰를 자동으로 열게 한다."""
    global _pending_url, _pending_url_ts
    _pending_url = url
    _pending_url_ts = time.time()


def _get_pending() -> str:
    """TTL 내의 pending URL을 반환한다. TTL이 지났으면 비운다."""
    global _pending_url, _pending_url_ts
    if not _pending_url:
        return ""
    if (time.time() - _pending_url_ts) > _PENDING_TTL:
        _pending_url = ""
        _pending_url_ts = 0.0
        return ""
    return _pending_url


def _clear_pending() -> None:
    """pending URL을 즉시 비운다(오류 발생 시)."""
    global _pending_url, _pending_url_ts
    _pending_url = ""
    _pending_url_ts = 0.0

def _browser_worker_loop():
    """Dedicated thread: runs all Playwright operations sequentially."""
    global _last_url, _browser_active, _pending_url, _worker_task_start_ts

    browser = None
    browser_page = None
    pw = None  # sync_playwright instance

    def _find_system_chrome():
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for p in chrome_candidates:
            if os.path.isfile(p):
                return p, None
        try:
            import subprocess
            result = subprocess.run(
                ['reg', 'query', r'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe', '/ve'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if 'REG_SZ' in line or 'REG_EXPAND_SZ' in line:
                    parts = line.strip().split('    ')
                    if len(parts) >= 3:
                        candidate = parts[-1].strip()
                        if os.path.isfile(candidate):
                            return candidate, None
        except Exception:
            pass
        return None, None

    def _cdp_endpoint_ready() -> bool:
        """CDP HTTP 엔드포인트를 raw HTTP로 신속 점검(3초).

        Playwright를 건드리기 전에 9222가 응답하는지 확인한다. 무응답이면
        connect_over_cdp 자체를 건너뛰어 드라이버/엔드포인트 비정상이
        워커를 hang 시키는 것을 차단한다.
        """
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3) as resp:
                return 200 <= int(resp.status) < 300
        except Exception:
            return False

    # ── Session & Tab Page Mapping ──
    _session_pages = {}  # session_id -> Page
    _tab_pages = {}      # tab_id -> Page

    def _get_valid_pages():
        """Return all valid (non-UI, non-DevTools) pages from browser contexts."""
        if browser is None:
            return []
        pages = []
        try:
            for ctx in browser.contexts:
                for p in ctx.pages:
                    try:
                        if p.is_closed():
                            continue
                        u = (p.url or "").strip()
                        if u.startswith("http://127.0.0.1") or u.startswith("devtools://") or u.startswith("chrome://") or u.startswith("devtools:"):
                            continue
                        pages.append(p)
                    except Exception:
                        continue
        except Exception:
            pass
        return pages

    def _clean_stale_pages():
        """Remove closed or invalid pages from mapping dictionaries."""
        dead_sessions = [sid for sid, p in _session_pages.items() if not p or p.is_closed()]
        for sid in dead_sessions:
            _session_pages.pop(sid, None)
        dead_tabs = [tid for tid, p in _tab_pages.items() if not p or p.is_closed()]
        for tid in dead_tabs:
            _tab_pages.pop(tid, None)

    def _ensure_browser(session_id: str = None, tab_id: str = None):
        """Connect to Electron's WebContentsView via CDP and return the appropriate Page.

        Supports multi-session isolation: if session_id is provided, routes to that
        session's designated page. Background agents running in separate sessions
        control separate tabs simultaneously without interference.
        """
        nonlocal browser, browser_page, pw
        global _last_cdp_attempt, _cdp_fail_streak

        _clean_stale_pages()

        # Check if requested session already has a live page
        if session_id and session_id in _session_pages:
            p = _session_pages[session_id]
            try:
                if not p.is_closed():
                    p.title()
                    return p, None
            except Exception:
                _session_pages.pop(session_id, None)

        # Check if requested tab already has a live page
        if tab_id and tab_id in _tab_pages:
            p = _tab_pages[tab_id]
            try:
                if not p.is_closed():
                    p.title()
                    return p, None
            except Exception:
                _tab_pages.pop(tab_id, None)

        # Check existing connection
        if browser is not None:
            valid_pages = _get_valid_pages()
            if valid_pages:
                chosen = None
                if session_id:
                    # Find a page not yet assigned to another session
                    assigned_pages = set(_session_pages.values())
                    for p in valid_pages:
                        if p not in assigned_pages:
                            chosen = p
                            break
                    if not chosen:
                        chosen = valid_pages[-1]  # fallback: use last page
                    _session_pages[session_id] = chosen
                elif tab_id:
                    assigned_tabs = set(_tab_pages.values())
                    for p in valid_pages:
                        if p not in assigned_tabs:
                            chosen = p
                            break
                    if not chosen:
                        chosen = valid_pages[0]
                    _tab_pages[tab_id] = chosen
                else:
                    if browser_page and not browser_page.is_closed() and browser_page in valid_pages:
                        chosen = browser_page
                    else:
                        # Pick first page with real URL, else fallback
                        real = [p for p in valid_pages if (p.url or "").strip() not in ("about:blank", "")]
                        chosen = real[0] if real else valid_pages[0]
                    browser_page = chosen

                return chosen, None
            else:
                browser = None
                browser_page = None

        # CDP retry cooldown
        now = time.time()
        if now - _last_cdp_attempt < _CDP_RETRY_COOLDOWN:
            return None, "Electron CDP not ready yet (cooldown). 브라우저 뷰를 열고 페이지를 로드한 후 다시 시도하세요."

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None, "Playwright not installed. Run: pip install playwright && playwright install chromium"

        if pw is None:
            try:
                pw = sync_playwright().start()
            except Exception as e:
                return None, f"Failed to start Playwright: {str(e)}"

        try:
            if not _cdp_endpoint_ready():
                raise ConnectionError("CDP endpoint 127.0.0.1:9222 not reachable (pre-check)")
            _logger.info("Attempting CDP connection to DAON browser at 127.0.0.1:9222")
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=_CDP_CONNECT_TIMEOUT_MS)
            _cdp_fail_streak = 0

            valid_pages = _get_valid_pages()
            if not valid_pages:
                return None, "Electron 내부 브라우저 탭이 아직 없습니다. 브라우저 뷰를 열고 페이지를 로드한 후 다시 시도하세요."

            chosen = None
            if session_id:
                _session_pages[session_id] = valid_pages[-1]
                chosen = valid_pages[-1]
            elif tab_id:
                _tab_pages[tab_id] = valid_pages[0]
                chosen = valid_pages[0]
            else:
                real = [p for p in valid_pages if (p.url or "").strip() not in ("about:blank", "")]
                chosen = real[0] if real else valid_pages[0]
                browser_page = chosen

            _last_cdp_attempt = time.time()
            _logger.info("Connected to browser tab (total: %d): %s", len(valid_pages), chosen.url)
            return chosen, None
        except Exception as e:
            _logger.warning("Electron CDP connection failed: %s", str(e))
            # CDP 연결 실패 시각 기록 → 다음 _CDP_RETRY_COOLDOWN 초간 재시도 방지
            _last_cdp_attempt = time.time()
            _cdp_fail_streak += 1
            # 반복 실패 시 Playwright 드라이버 자체가 망가진 것으로 의심 →
            # 폐기 후 재생성한다. (좀비 드라이버에 대한 반복 호출은 다시
            # 무한 hang에 빠질 수 있으므로 인스턴스 자체를 새로 만든다.)
            if _cdp_fail_streak >= _CDP_MAX_CONSECUTIVE_FAILURES:
                _logger.warning("CDP connect failed %d times in a row - recreating Playwright driver", _cdp_fail_streak)
                _cdp_fail_streak = 0
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    pass
                browser = None
                browser_page = None
                try:
                    if pw is not None:
                        pw.stop()
                except Exception:
                    pass
                pw = None
            # In Electron mode (BROWSER_CDP_URL is set), do NOT fall back to headless —
            # user and AI must share the same WebContentsView page.
            if os.environ.get("BROWSER_CDP_URL"):
                _logger.error("Electron CDP mode active — refusing headless fallback. Retry later.")
                return None, "Electron 브라우저 탭이 아직 준비되지 않았습니다. 브라우저 뷰를 열고 페이지를 로드한 후 다시 시도하세요."

            # Non-Electron mode: fallback to local headless
            # 1) Try Playwright's bundled Chromium first (most reliable headless)
            try:
                browser = pw.chromium.launch(headless=True)
                browser_page = browser.new_page()
                _logger.info("Launched headless browser (Playwright bundled Chromium)")
                return browser_page, None
            except Exception as bundled_err:
                _logger.warning("Bundled Chromium failed: %s — trying system Chrome", bundled_err)

            # 2) Fallback: system Chrome with explicit headless args
            try:
                chrome_path, _ = _find_system_chrome()
                if chrome_path:
                    browser = pw.chromium.launch(
                        executable_path=chrome_path,
                        headless=True,
                        args=['--headless=new', '--disable-gpu', '--no-sandbox'],
                    )
                    browser_page = browser.new_page()
                    _logger.info("Launched headless browser (system Chrome: %s)", chrome_path)
                    return browser_page, None
                return None, "No browser available. Run: playwright install chromium"
            except Exception as inner_e:
                return None, f"Failed to connect to Electron CDP: {str(e)} and headless fallback failed: {inner_e}"

    def _worker_click(target_page, target_ref):
        stored = _get_stored_ref(target_ref)
        ident_js = _json.dumps(stored or {})
        click_js = f"""
        (() => {{
            const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],details,summary';
            const stored = {ident_js};
            let target = null;

            // 1순위: 스냅샷 시점 식별자(id/name/href/텍스트)로 정확히 찾기
            if (stored && (stored.id || stored.name || stored.href || stored.text)) {{
                const els = Array.from(document.querySelectorAll(interactive));
                target = els.find(el => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return false;
                    if (stored.id && el.id === stored.id) return true;
                    if (stored.name && el.getAttribute('name') === stored.name) return true;
                    if (stored.href && el.href && el.href === stored.href) return true;
                    if (stored.text && !stored.href &&
                        (el.getAttribute('aria-label') || el.textContent || '').trim().substring(0, 120) === stored.text) return true;
                    return false;
                }}) || null;
            }}

            // 2순위(폴백): 기존 방식 — 실행 시점 위치 재계산
            if (!target) {{
                const els = document.querySelectorAll(interactive);
                const filtered = [];
                els.forEach((el) => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return;
                    filtered.push({{el, ref: 'e' + filtered.length}});
                }});
                target = (filtered.find(f => f.ref === '{target_ref}') || {{}}).el || null;
            }}

            if (target) {{
                target.scrollIntoView({{block: 'center'}});
                target.click();
                return {{clicked: true, ref: '{target_ref}', tag: target.tagName.toLowerCase(), by: 'stored-or-positional'}};
            }}
            return {{clicked: false, ref: '{target_ref}', error: 'Element not found'}};
        }})()
        """
        res = target_page.evaluate(click_js)
        target_page.wait_for_timeout(300)
        return res

    def _worker_type(target_page, target_ref, target_text):
        stored = _get_stored_ref(target_ref)
        ident_js = _json.dumps(stored or {})
        type_js = f"""
        (() => {{
            const interactive = 'input,textarea,[contenteditable="true"],[role="textbox"]';
            let target = null;
            const stored = {ident_js};

            if (stored && (stored.id || stored.name || stored.placeholder)) {{
                const els = Array.from(document.querySelectorAll(interactive));
                target = els.find(el => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return false;
                    if (stored.id && el.id === stored.id) return true;
                    if (stored.name && el.getAttribute('name') === stored.name) return true;
                    if (stored.placeholder && el.placeholder === stored.placeholder) return true;
                    return false;
                }}) || null;
            }}

            if (!target) {{
                const els = document.querySelectorAll(interactive);
                const filtered = [];
                els.forEach((el) => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return;
                    filtered.push({{el, ref: 'e' + filtered.length}});
                }});
                target = (filtered.find(f => f.ref === '{target_ref}') || {{}}).el || null;
            }}

            if (target) {{
                target.scrollIntoView({{block: 'center'}});
                target.focus();
                target.value = {_json.dumps(target_text)};
                target.dispatchEvent(new Event('input', {{bubbles: true}}));
                target.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{typed: true, ref: '{target_ref}', by: 'stored-or-positional'}};
            }}
            // Fallback: type into focused element
            const active = document.activeElement;
            if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {{
                active.value = {_json.dumps(target_text)};
                active.dispatchEvent(new Event('input', {{bubbles: true}}));
                return {{typed: true, ref: 'active', tag: active.tagName.toLowerCase()}};
            }}
            return {{typed: false, ref: '{target_ref}', error: 'No editable element found'}};
        }})()
        """
        res = target_page.evaluate(type_js)
        target_page.wait_for_timeout(200)
        return res

    # ── Main dispatch loop ──
    _logger.info("Browser worker thread started")
    while True:
        try:
            task = _browser_task_queue.get(timeout=1)
        except queue.Empty:
            continue

        if task is _BROWSER_WORKER_STOP:
            _logger.info("Browser worker received stop signal")
            break

        action = task.get("action", "")
        result_id = task.get("_result_id", -1)
        req_sid = task.get("session_id")
        req_tid = task.get("tab_id")

        # 만료된 status 태스크: 요청자가 이미 타임아웃으로 포기하고 떠났으므로
        # 실행을 건너뛴다. (워커가 일시 정체됐다가 복구된 후, 쌓인 status 폴링을
        # 뒤늦게 몰아서 실행하는 것을 방지)
        _task_expires_at = task.get("_expires_at") or 0
        if action == "status" and _task_expires_at and time.time() > _task_expires_at:
            continue

        _worker_task_start_ts = time.time()
        try:
            if action == "status":
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err, "status": "disconnected"})
                else:
                    _browser_active = True
                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "connected",
                        "url": page.url,
                        "title": page.title(),
                    })

            elif action == "sync_url":
                # Electron mode: URL was already navigated by IPC.
                # AI connects to the SAME page via CDP — just record the URL.
                url = task.get("url", "")
                _last_url = url
                _browser_active = True
                _browser_result_queue.put({
                    "_result_id": result_id,
                    "status": "ok",
                    "url": url,
                    "synced": True,
                })

            elif action == "navigate":
                url = task.get("url", "about:blank")
                # TTL pending: 탭 유무와 무관하게 항상 설정 → 프론트 폴링이 에디터 뒤 브라우저 뷰를
                # 자동으로 앞으로 가져온다. (이전엔 탭 없음 분기에서만 설정되어, 이미 뷰가 존재하지만
                # 숨겨진 경우 폴링이 토글을 잡지 못하는 race가 있었다.)
                _set_pending(url)
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    # In Electron mode, no browser tab yet — signal frontend to auto-open.
                    # 백엔드 블로킹 제거: 이전엔 10초 대기 루프(20×0.5s)가 _CDP_RETRY_COOLDOWN(5s)과
                    # 충돌해 실질 재시도가 1~2회뿐이었고, 10초 후 실패하면서 curl 8초 타임아웃이 먼저 발생했다.
                    # 이제 pending_url은 TTL 동안 유지되고 프론트 5초 폴링(_autoOpenBrowserPoll)이
                    # 뷰를 자동 생성/네비게이션하므로, 즉시 pending 응답을 반환한다.
                    # "탭 없음"뿐 아니라 CDP cooldown/미준비 오류도 pending으로 처리 — 어느 쪽이든
                    # 프론트가 뷰를 만들면 해결되므로 pending을 지우면 안 된다.
                    if os.environ.get("BROWSER_CDP_URL") and any(
                        k in str(err).lower() for k in ("tab", "cooldown", "not ready", "cdp connection failed")
                    ):
                        _logger.info("No browser view ready — respond pending, frontend auto-open will create view (url=%s)", url)
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "pending",
                            "url": url,
                            "message": "브라우저 뷰를 여는 중입니다. 잠시 후 자동으로 열리고 이동합니다.",
                        })
                    else:
                        _clear_pending()
                        _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _last_url = page.url
                    _browser_active = True
                    _set_pending(page.url)  # 탭 존재(숨김) 케이스: navigate 완료 후에도 TTL 연장
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "title": page.title(),
                        "iframes": _detect_iframes(page),
                    })

            elif action == "snapshot":
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # Get accessibility snapshot from Playwright
                    try:
                        snapshot = page.accessibility.snapshot()

                        # Build text-based snapshot from accessibility tree (agent-browser compatible)
                        def _a11y_to_text(node, indent=0):
                            lines = []
                            if node is None:
                                return ""
                            prefix = "  " * indent
                            role = node.get("role", "unknown")
                            name = (node.get("name") or "").strip()
                            value = node.get("value", "")
                            if name and value:
                                label = f"{name}={value}"
                            elif name:
                                label = name
                            elif value:
                                label = str(value)
                            else:
                                label = ""
                            line = f"{prefix}[{role}]"
                            if label:
                                line += f" {label}"
                            lines.append(line)
                            for child in node.get("children", []) or []:
                                lines.append(_a11y_to_text(child, indent + 1))
                            return "\n".join(filter(None, lines))

                        snapshot_text = _a11y_to_text(snapshot) if snapshot else ""

                        # [2026-08-31 캡챠 감지] 스냅샷에 캡챠/차단 패턴이 있으면
                        # 에이전트가 인지하고 사용자에게 직접 해결을 요청할 수 있게
                        # 스냅샷 앞에 경고를 삽입한다.
                        try:
                            _captcha_patterns = ['captcha', 'challenge', 'verify you are human',
                                                 'are you a robot', 'confirm you are human',
                                                 '보안 확인', '자동입력 방지', 'access denied',
                                                 'unusual traffic', 'blocked']
                            _lower = snapshot_text.lower()
                            _detected = [p for p in _captcha_patterns if p in _lower]
                            if _detected:
                                snapshot_text = (
                                    "[CAPTCHA/차단 감지됨 - 패턴: " + ', '.join(_detected) + "]\n"
                                    "[사용자에게 알리고, 내부 브라우저에서 사용자가 직접 해결하도록 요청하세요. "
                                    "해결될 때까지 다른 도구 실행을 잠시 멈추는 것이 좋습니다.]\n\n"
                                    + snapshot_text
                                )
                        except Exception:
                            pass

                        # Get interactive elements via Browser-Use style visibility filter
                        elements = page.evaluate(_EXTRACT_INTERACTIVE_JS) or []
                        # Build refs dict (agent-browser compatible format)
                        refs = {}
                        for el in elements:
                            refs[el.get("ref", "")] = el

                        # ref 저장소 갱신 — 이후 click/type/fill이 위치 재계산 대신
                        # 스냅샷 시점 식별자로 원소를 찾는다.
                        _store_refs(elements)

                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "title": page.title(),
                            "snapshot": snapshot_text or page.inner_text('body')[:10000],
                            "refs": refs,
                            "elements": elements,
                            "iframes": _detect_iframes(page),
                        })
                    except Exception as snap_err:
                        # Fallback: just get page text
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "title": page.title(),
                            "snapshot": page.inner_text('body')[:10000] if hasattr(page, 'inner_text') else "",
                            "refs": {},
                            "elements": [],
                            "snapshot_error": str(snap_err),
                        })



            elif action == "click":
                ref = task.get("ref", "")
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    result = _worker_click(page, ref)
                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "click_result": result,
                    })

            elif action == "type":
                ref = task.get("ref", "")
                text = task.get("text", "")
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    result = _worker_type(page, ref, text)
                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "type_result": result,
                    })

            elif action == "screenshot":
                labeled = bool(task.get("labeled", False))
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    try:
                        if labeled:
                            # Browser-Use style Set-of-Marks visual overlay
                            elements = page.evaluate(_EXTRACT_INTERACTIVE_JS) or []
                            _store_refs(elements)
                            page.evaluate(_SOM_INJECT_JS, elements)

                        screenshot_bytes = page.screenshot(type="png", full_page=False)
                        image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "image_base64": image_b64,
                            "labeled": labeled,
                        })
                    finally:
                        if labeled:
                            try:
                                page.evaluate(_SOM_CLEANUP_JS)
                            except Exception:
                                pass

            elif action == "batch":
                sub_actions = task.get("actions", [])
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    batch_results = []
                    all_ok = True
                    for sub in sub_actions:
                        act = (sub.get("action") or "").lower()
                        if act == "click":
                            r = _worker_click(page, sub.get("ref", ""))
                            batch_results.append({"action": "click", "result": r})
                            if not r.get("clicked"):
                                all_ok = False
                                break
                        elif act in ("type", "fill"):
                            r = _worker_type(page, sub.get("ref", ""), sub.get("text", ""))
                            batch_results.append({"action": act, "result": r})
                            if not r.get("typed"):
                                all_ok = False
                                break
                        elif act == "wait":
                            ms = min(max(int(sub.get("ms", 500)), 50), 10000)
                            page.wait_for_timeout(ms)
                            batch_results.append({"action": "wait", "ms": ms, "status": "ok"})
                        elif act == "press":
                            key = sub.get("key", "Enter")
                            page.keyboard.press(key)
                            page.wait_for_timeout(200)
                            batch_results.append({"action": "press", "key": key, "status": "ok"})
                        elif act == "scroll":
                            direction = sub.get("direction", "down")
                            px = int(sub.get("pixels", 500))
                            dy = px if direction == "down" else -px
                            page.evaluate(f"window.scrollBy({{top: {dy}, behavior: 'smooth'}})")
                            page.wait_for_timeout(300)
                            batch_results.append({"action": "scroll", "direction": direction, "pixels": px, "status": "ok"})
                        else:
                            batch_results.append({"action": act, "error": f"Unsupported batch action: {act}"})
                            all_ok = False
                            break

                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok" if all_ok else "partial",
                        "url": page.url,
                        "results": batch_results,
                    })

            elif action == "execute":
                expression = task.get("expression", "")
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    result = page.evaluate(expression)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "result": result,
                    })

            elif action == "evaluate":
                # Alias for execute
                expression = task.get("expression", "")
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    result = page.evaluate(expression)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "result": result,
                    })

            elif action == "close":
                if browser_page:
                    try:
                        browser_page.close()
                    except Exception:
                        pass
                    browser_page = None
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                    browser = None
                if pw:
                    try:
                        pw.stop()
                    except Exception:
                        pass
                    pw = None
                _browser_active = False
                _last_url = ""
                _browser_result_queue.put({
                    "_result_id": result_id,
                    "status": "ok",
                })

            elif action == "recommend":
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # Get interactive elements via Browser-Use style visibility filter
                    elements = page.evaluate(_EXTRACT_INTERACTIVE_JS) or []
                    _store_refs(elements)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "title": page.title(),
                        "recommendations": elements,
                    })

            elif action == "scroll":
                direction = task.get("direction", "down")
                px_raw = task.get("pixels", "500")
                try:
                    px = int(px_raw)
                except (ValueError, TypeError):
                    px = 500
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.evaluate(f"window.scrollBy(0, {px if direction == 'down' else -px})")
                    page.wait_for_timeout(300)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "scrolled": direction,
                    })

            elif action == "back":
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.go_back(wait_until="domcontentloaded", timeout=15000)
                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                    })

            elif action == "forward":
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.go_forward(wait_until="domcontentloaded", timeout=15000)
                    _last_url = page.url
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                    })

            elif action == "tabs":
                # CDP로 연결된 브라우저의 열린 탭 목록을 조회한다.
                # 메인 UI(http://127.0.0.1)와 DevTools 내부 페이지는 제외 —
                # _ensure_browser의 페이지 선택 기준과 동일하게 유지한다.
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    tabs_list = []
                    for ctx in (browser.contexts if browser is not None else []):
                        for p in ctx.pages:
                            url = (p.url or "").strip()
                            if url.startswith("http://127.0.0.1"):
                                continue  # skip main UI
                            if url.startswith("devtools://") or url.startswith("chrome://") or url.startswith("devtools:"):
                                continue  # skip internal DevTools
                            try:
                                title = p.title()
                            except Exception:
                                title = ""
                            tabs_list.append({
                                "index": len(tabs_list),
                                "url": url,
                                "title": title,
                                "active": p is page,
                            })
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "tabs": tabs_list,
                        "active_url": page.url,
                    })

            elif action == "switch_tab":
                # 에이전트의 활성 탭을 전환한다. browser_page를 대상 page 객체로
                # 교체하면 이후 snapshot/click 등 모든 도구가 새 탭에서 동작한다.
                # Electron 모드에서 new_page()는 새 BrowserWindow를 만들므로 여기서도
                # 절대 호출하지 않는다 — 기존에 열린 탭 사이에서만 전환한다.
                target_index = task.get("index")
                target_url = (task.get("url") or "").strip()
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    candidates = []
                    for ctx in (browser.contexts if browser is not None else []):
                        for p in ctx.pages:
                            url = (p.url or "").strip()
                            if url.startswith("http://127.0.0.1"):
                                continue  # skip main UI
                            if url.startswith("devtools://") or url.startswith("chrome://") or url.startswith("devtools:"):
                                continue  # skip internal DevTools
                            candidates.append(p)
                    new_page = None
                    if target_index is not None:
                        try:
                            ti = int(target_index)
                            if 0 <= ti < len(candidates):
                                new_page = candidates[ti]
                        except (ValueError, TypeError):
                            pass
                    elif target_url:
                        for p in candidates:
                            if (p.url or "").strip() == target_url:
                                new_page = p
                                break
                    if new_page is None:
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "error": "지정한 탭을 찾을 수 없습니다. browser_tabs로 목록을 먼저 확인하세요.",
                        })
                    else:
                        browser_page = new_page
                        _last_url = new_page.url
                        _browser_active = True
                        try:
                            new_title = new_page.title()
                        except Exception:
                            new_title = ""
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": new_page.url,
                            "title": new_title,
                        })

            elif action == "press":
                key = task.get("key", "")
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.keyboard.press(key)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "pressed": key,
                    })

            elif action == "fill":
                ref = task.get("ref", "")
                text = task.get("text", "")
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # 스냅샷 시점에 저장한 ref 정보(id/name/placeholder)로 먼저 식별자
                    # 매칭을 시도하고, 실패 시에만 위치 기반 재구성으로 폴백한다.
                    # (스냅샷 이후 DOM이 살짝 변해도 올바른 요소를 찾도록)
                    stored = _get_stored_ref(ref)
                    ident_js = _json.dumps(stored or {})
                    fill_js = f"""
                    (() => {{
                        const interactive = 'input,textarea,[contenteditable="true"],[role="textbox"]';
                        const els = document.querySelectorAll(interactive);
                        const visible = [];
                        els.forEach((el) => {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            visible.push(el);
                        }});
                        const ident = {ident_js};
                        let target = null;
                        if (ident && (ident.id || ident.name || ident.placeholder)) {{
                            target = visible.find((el) => {{
                                if (ident.id && el.id === ident.id) return true;
                                if (ident.name && el.getAttribute('name') === ident.name) return true;
                                if (ident.placeholder && el.placeholder === ident.placeholder) return true;
                                return false;
                            }});
                        }}
                        if (!target) {{
                            const filtered = visible.map((el, i) => ({{el, ref: 'e' + i}}));
                            const posTarget = filtered.find(f => f.ref === '{ref}');
                            if (posTarget) target = posTarget.el;
                        }}
                        if (target) {{
                            target.scrollIntoView({{block: 'center'}});
                            target.focus();
                            target.value = {_json.dumps(text)};
                            target.dispatchEvent(new Event('input', {{bubbles: true}}));
                            target.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return {{filled: true, ref: '{ref}', tag: target.tagName.toLowerCase(), by: 'stored-or-positional'}};
                        }}
                        const active = document.activeElement;
                        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {{
                            active.value = {_json.dumps(text)};
                            active.dispatchEvent(new Event('input', {{bubbles: true}}));
                            return {{filled: true, ref: 'active', tag: active.tagName.toLowerCase()}};
                        }}
                        return {{filled: false, ref: '{ref}', error: 'No editable element found'}};
                    }})()
                    """
                    result = page.evaluate(fill_js)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "fill_result": result,
                    })

            elif action == "open":
                url = task.get("url", "about:blank")
                # TTL pending: navigate와 동일하게 탭 유무와 무관하게 항상 설정
                _set_pending(url)
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    # navigate와 동일: 10초 블로킹 루프 제거, 즉시 pending 응답.
                    # pending_url은 TTL 동안 유지되고 프론트 5초 폴링이 뷰를 자동 생성/이동한다.
                    # "탭 없음"뿐 아니라 CDP cooldown/미준비 오류도 pending으로 처리한다.
                    if os.environ.get("BROWSER_CDP_URL") and any(
                        k in str(err).lower() for k in ("tab", "cooldown", "not ready", "cdp connection failed")
                    ):
                        _logger.info("No browser view ready — respond pending, frontend auto-open will create view (url=%s)", url)
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "pending",
                            "url": url,
                            "data": {"url": url, "message": "브라우저 뷰를 여는 중입니다. 잠시 후 자동으로 열리고 이동합니다."},
                        })
                    else:
                        _clear_pending()
                        _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _last_url = page.url
                    _browser_active = True
                    _set_pending(page.url)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"url": page.url, "title": page.title(), "iframes": _detect_iframes(page)},
                    })

            elif action == "get_images":
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    images_js = """
                    (() => {
                        const imgs = document.querySelectorAll('img[src]');
                        return Array.from(imgs).map(img => ({
                            src: img.src,
                            alt: img.alt || '',
                            width: img.naturalWidth || 0,
                            height: img.naturalHeight || 0,
                        }));
                    })()
                    """
                    images = page.evaluate(images_js)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "images": images,
                    })

            elif action == "console":
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"messages": []},
                    })

            elif action == "errors":
                page, err = _ensure_browser(session_id=req_sid, tab_id=req_tid)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"errors": []},
                    })

            elif action == "record":
                _browser_result_queue.put({
                    "_result_id": result_id,
                    "status": "ok",
                })

            elif action in ("grid", "sessions"):
                _ensure_browser()
                valid_pages = _get_valid_pages()
                tab_list = []
                for idx, p in enumerate(valid_pages):
                    try:
                        p_url = (p.url or "").strip()
                        p_title = ""
                        try:
                            p_title = p.title() or p_url or f"Tab {idx+1}"
                        except Exception:
                            p_title = p_url or f"Tab {idx+1}"
                        matching_sid = ""
                        for sid, sp in _session_pages.items():
                            if sp == p:
                                matching_sid = sid
                                break
                        thumb_b64 = ""
                        try:
                            thumb_bytes = p.screenshot(type="jpeg", quality=40)
                            thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(thumb_bytes).decode("utf-8")
                        except Exception:
                            pass
                        tab_list.append({
                            "id": f"tab{idx+1}",
                            "index": idx,
                            "session_id": matching_sid,
                            "url": p_url,
                            "title": p_title,
                            "thumbnail": thumb_b64,
                            "active": (p is browser_page),
                        })
                    except Exception as pe:
                        _logger.debug("Error inspecting page %d: %s", idx, pe)
                _browser_result_queue.put({
                    "_result_id": result_id,
                    "status": "ok",
                    "tabs": tab_list,
                    "count": len(tab_list),
                })

            elif action == "focus":
                target_idx = task.get("index")
                target_sid = task.get("session_id")
                target_tid = task.get("tab_id")
                target_url = (task.get("url") or "").strip()
                _ensure_browser()
                valid_pages = _get_valid_pages()
                target_p = None
                if target_sid and target_sid in _session_pages:
                    target_p = _session_pages[target_sid]
                elif target_idx is not None:
                    try:
                        idx_num = int(target_idx)
                        if 0 <= idx_num < len(valid_pages):
                            target_p = valid_pages[idx_num]
                    except Exception:
                        pass
                elif target_url:
                    for p in valid_pages:
                        if (p.url or "").strip() == target_url:
                            target_p = p
                            break
                if target_p and not target_p.is_closed():
                    browser_page = target_p
                    _last_url = target_p.url
                    try:
                        f_title = target_p.title()
                    except Exception:
                        f_title = ""
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": target_p.url,
                        "title": f_title,
                    })
                else:
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "error": "대상 탭을 찾을 수 없습니다.",
                    })

            elif action == "close_tab":
                target_idx = task.get("index")
                target_sid = task.get("session_id")
                _ensure_browser()
                valid_pages = _get_valid_pages()
                target_p = None
                if target_sid and target_sid in _session_pages:
                    target_p = _session_pages.pop(target_sid, None)
                elif target_idx is not None:
                    try:
                        idx_num = int(target_idx)
                        if 0 <= idx_num < len(valid_pages):
                            target_p = valid_pages[idx_num]
                    except Exception:
                        pass
                if target_p:
                    try:
                        target_p.close()
                    except Exception:
                        pass
                    _clean_stale_pages()
                    _browser_result_queue.put({"_result_id": result_id, "status": "ok"})
                else:
                    _browser_result_queue.put({"_result_id": result_id, "error": "닫을 탭을 찾을 수 없습니다."})

            else:
                _browser_result_queue.put({
                    "_result_id": result_id,
                    "error": f"Unknown action: {action}",
                })

        except Exception as e:
            _logger.exception("Browser worker error during action=%s", action)
            _browser_result_queue.put({
                "_result_id": result_id,
                "error": str(e),
            })

        # 태스크 1건 완료(성공/실패 무관) — busy 마커 해제. 이 마커가 설정된 채
        # 오래 남아 있으면 status 폴링은 큐에 쌓이지 않고 캐시 상태를 반환한다.
        _worker_task_start_ts = 0.0

    # Cleanup
    if browser_page:
        try:
            browser_page.close()
        except Exception:
            pass
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    if pw:
        try:
            pw.stop()
        except Exception:
            pass
    _browser_active = False
    _last_url = ""
    _logger.info("Browser worker thread stopped")


def _start_browser_worker():
    """Start the browser worker thread if not already running."""
    global _browser_worker, _worker_task_start_ts
    with _browser_worker_lock:
        if _browser_worker is None or not _browser_worker.is_alive():
            _worker_task_start_ts = 0.0  # 이전 워커가 죽으며 남긴 stale busy 마커 리셋
            _browser_worker = threading.Thread(
                target=_browser_worker_loop,
                name="browser-worker",
                daemon=True,
            )
            _browser_worker.start()
            _logger.info("Browser worker thread started (PID-like id: %s)", _browser_worker.ident)


def _submit_task(action: str, wait_timeout: float = 35.0, **kwargs) -> dict:
    """Submit a task to the browser worker and wait for the result."""
    _start_browser_worker()

    result_id = int(time.time() * 1000000)  # unique ID
    task = {"action": action, "_result_id": result_id, **kwargs}
    _browser_task_queue.put(task)

    try:
        result = _browser_result_queue.get(timeout=wait_timeout)
        # Drain any stale results that don't match our ID
        attempts = 0
        while result.get("_result_id") != result_id and attempts < 20:
            _logger.debug("Discarding stale result (expected %s, got %s)", result_id, result.get("_result_id"))
            try:
                result = _browser_result_queue.get(timeout=2)
            except queue.Empty:
                return {"error": "Timeout waiting for matching result"}
            attempts += 1
        if result.get("_result_id") != result_id:
            return {"error": "Failed to get matching result from browser worker"}
        return result
    except queue.Empty:
        return {"error": f"Browser operation timed out ({wait_timeout:.0f}s)"}


# ── Route Handlers ──

def handle_get_browser_status(handler, parsed):
    """GET /api/browser/status — return current browser status."""
    global _pending_status_tasks
    now = time.time()
    worker_stuck = _worker_task_start_ts > 0 and (now - _worker_task_start_ts) > _WORKER_STUCK_THRESHOLD
    with _status_gate_lock:
        if worker_stuck or _pending_status_tasks > 0:
            return j_ok(handler, {
                "status": "connected" if _browser_active else "disconnected",
                "url": _last_url or "",
                "title": "",
                "pending_url": _get_pending(),
                "worker_busy": True,
            })
        _pending_status_tasks += 1
    try:
        result = _submit_task(
            "status",
            wait_timeout=_STATUS_WAIT_TIMEOUT,
            _expires_at=time.time() + _STATUS_WAIT_TIMEOUT + 2,
        )
    finally:
        with _status_gate_lock:
            _pending_status_tasks -= 1
    if "error" in result:
        return j_ok(handler, {
            "status": "disconnected",
            "url": "",
            "error": result["error"],
            "pending_url": _get_pending(),
        })
    return j_ok(handler, {
        "status": result.get("status", "unknown"),
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "pending_url": _get_pending(),
    })


def handle_get_browser_recommend(handler, parsed):
    """GET /api/browser/recommend — get AI-actionable element recommendations."""
    result = _submit_task("recommend")
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "recommendations": result.get("recommendations", []),
    })


def handle_get_browser_grid(handler, parsed):
    """GET /api/browser/grid (or /api/browser/sessions) — return all open tabs with thumbnails and session info."""
    result = _submit_task("grid", wait_timeout=15.0)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "tabs": result.get("tabs", []),
        "count": result.get("count", 0),
    })


def handle_post_browser_focus(handler, body: dict):
    """POST /api/browser/focus — focus a specific tab/session."""
    body = body or {}
    index = body.get("index")
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    url = (body.get("url") or "").strip()
    result = _submit_task("focus", index=index, session_id=session_id, tab_id=tab_id, url=url)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
    })


def handle_post_browser_close_tab(handler, body: dict):
    """POST /api/browser/close_tab — close a specific tab/session."""
    body = body or {}
    index = body.get("index")
    session_id = body.get("session_id")
    result = _submit_task("close_tab", index=index, session_id=session_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {"status": "ok"})


def handle_post_browser_sync_url(handler, body: dict):
    """POST /api/browser/sync_url — sync URL (Electron: IPC already navigated)."""
    url = (body or {}).get("url", "")
    if not url:
        return j_err(handler, "Missing 'url' field")
    result = _submit_task("sync_url", url=url)
    return j_ok(handler, {
        "url": result.get("url", url),
        "synced": True,
    })


def handle_post_browser_navigate(handler, body: dict):
    """POST /api/browser/navigate — navigate browser to a URL."""
    body = body or {}
    url = body.get("url", "")
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    if not url:
        return j_err(handler, "Missing 'url' field")
    result = _submit_task("navigate", url=url, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    resp = {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
    }
    iframes = result.get("iframes") or []
    if iframes:
        resp["iframes"] = iframes
        resp["hint"] = (
            f"이 페이지는 큰 iframe {len(iframes)}개를 포함합니다. "
            "생성기/플레이어가 잘 안 보이면 iframe URL을 browser_navigate로 직접 여세요."
        )
    return j_ok(handler, resp)


def handle_post_browser_snapshot(handler, body: dict):
    """POST /api/browser/snapshot — get accessibility snapshot + interactive elements."""
    body = body or {}
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    result = _submit_task("snapshot", session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "dom": result.get("dom", ""),
        "elements": result.get("elements", []),
        "text": result.get("text", ""),
    })


def handle_post_browser_click(handler, body: dict):
    """POST /api/browser/click — click an element by ref."""
    body = body or {}
    ref = body.get("ref", "")
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    if not ref:
        return j_err(handler, "Missing 'ref' field")
    result = _submit_task("click", ref=ref, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "click_result": result.get("click_result", {}),
    })


def handle_post_browser_type(handler, body: dict):
    """POST /api/browser/type — type text into an element."""
    body = body or {}
    ref = body.get("ref", "")
    text = body.get("text", "")
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    if not ref:
        return j_err(handler, "Missing 'ref' field")
    result = _submit_task("type", ref=ref, text=text, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "type_result": result.get("type_result", {}),
    })


def handle_post_browser_screenshot(handler, body: dict):
    """POST /api/browser/screenshot — capture a screenshot (base64 PNG). Supports labeled=True for Set-of-Marks."""
    body = body or {}
    labeled = bool(body.get("labeled", False))
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    result = _submit_task("screenshot", labeled=labeled, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "image_base64": result.get("image_base64", ""),
        "labeled": result.get("labeled", False),
    })


def handle_post_browser_batch(handler, body: dict):
    """POST /api/browser/batch — execute a sequence of browser actions sequentially."""
    body = body or {}
    actions = body.get("actions", [])
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    if not isinstance(actions, list) or not actions:
        return j_err(handler, "Missing or invalid 'actions' list")
    result = _submit_task("batch", actions=actions, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "status": result.get("status", "ok"),
        "url": result.get("url", ""),
        "results": result.get("results", []),
    })


def handle_post_browser_execute(handler, body: dict):
    """POST /api/browser/execute — execute arbitrary JavaScript in the page."""
    body = body or {}
    expression = body.get("expression", "")
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    if not expression:
        return j_err(handler, "Missing 'expression' field")
    result = _submit_task("execute", expression=expression, session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "result": result.get("result", None),
    })


def handle_post_browser_close(handler, body: dict):
    """POST /api/browser/close — close the browser and stop Playwright."""
    result = _submit_task("close")
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {"status": "closed"})


def handle_post_browser_back(handler, body: dict):
    """POST /api/browser/back — go back in browser history."""
    body = body or {}
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    result = _submit_task("back", session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
    })


def handle_post_browser_forward(handler, body: dict):
    """POST /api/browser/forward — go forward in browser history."""
    body = body or {}
    session_id = body.get("session_id")
    tab_id = body.get("tab_id")
    result = _submit_task("forward", session_id=session_id, tab_id=tab_id)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
    })


def handle_post_browser_tabs(handler, body: dict):
    """POST /api/browser/tabs — list open browser tabs (CDP pages)."""
    result = _submit_task("tabs")
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "tabs": result.get("tabs", []),
        "active_url": result.get("active_url", ""),
    })


def handle_post_browser_switch_tab(handler, body: dict):
    """POST /api/browser/switch_tab — switch the agent's active tab (by index or url)."""
    body = body or {}
    index = body.get("index")
    url = (body.get("url") or "").strip()
    if index is None and not url:
        return j_err(handler, "Provide 'index' or 'url' of the tab to switch to")
    result = _submit_task("switch_tab", index=index, url=url)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
    })


# ── Browser Proxy (iframe bypass) ──

def handle_get_browser_proxy(handler, parsed):
    """GET /api/browser/proxy?url=ENCODED_URL — fetch page, strip X-Frame-Options.
    
    Many sites send X-Frame-Options: DENY / SAMEORIGIN or CSP frame-ancestors
    which block iframe embedding. This endpoint acts as a server-side relay:
    fetches the target page, removes blocking headers, and serves it to the iframe.
    
    Works in non-Electron (dev) mode only. Electron uses WebContentsView + CDP.
    """
    import urllib.parse as _up
    try:
        import requests as _requests
    except ImportError:
        handler.send_error_json("requests library not available", 500)
        return True

    qs = _up.parse_qs(parsed.query)
    target_url = (qs.get('url', [''])[0] or '').strip()
    if not target_url:
        handler.send_error_json("Missing 'url' query parameter", 400)
        return True

    # Security: only allow http/https
    if not target_url.startswith(('http://', 'https://')):
        handler.send_error_json("Only http/https URLs are allowed", 400)
        return True

    try:
        resp = _requests.get(
            target_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
            },
            timeout=15,
            allow_redirects=True,
            stream=False,
        )

        # Strip headers that block iframe embedding + encoding headers
        # (requests auto-decompresses, so Content-Encoding must NOT be forwarded)
        blocked_headers = {
            'x-frame-options', 'content-security-policy',
            'x-content-security-policy', 'x-webkit-csp',
            'content-encoding', 'transfer-encoding',
        }
        response_headers = []
        content_type = resp.headers.get('Content-Type', 'text/html')
        for key, val in resp.headers.items():
            if key.lower() not in blocked_headers:
                response_headers.append((key, val))
        # Ensure correct content-type
        if 'text/html' in content_type.lower():
            response_headers = [(k, v) for k, v in response_headers if k.lower() != 'content-type']
            response_headers.append(('Content-Type', 'text/html; charset=utf-8'))

        content = resp.content
        # Inject <base> tag so relative URLs resolve to original domain
        if b'<head' in content.lower() and b'<base' not in content.lower():
            import re as _re
            base_tag = f'<base href="{target_url}">'.encode('utf-8')
            content = _re.sub(b'(<head[^>]*>)', b'\\1' + base_tag, content, count=1, flags=_re.IGNORECASE)

        # Set Content-Length so the browser detects end-of-body correctly
        response_headers.append(('Content-Length', str(len(content))))

        handler.send_response(resp.status_code)
        for key, val in response_headers:
            try:
                handler.send_header(key, val)
            except Exception:
                pass  # skip headers that fail encoding
        handler.end_headers()

        handler.wfile.write(content)
        return True

    except _requests.exceptions.Timeout:
        handler.send_error_json("Request to target URL timed out", 504)
        return True
    except _requests.exceptions.ConnectionError:
        handler.send_error_json("Could not connect to target URL", 502)
        return True
    except Exception as e:
        handler.send_error_json(f"Proxy error: {str(e)}", 500)
        return True
