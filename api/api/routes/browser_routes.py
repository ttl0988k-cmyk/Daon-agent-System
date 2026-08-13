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
_pending_url = ""  # AI requested navigate but no browser tab yet — frontend should auto-open

# CDP 재연결 백오프: Electron이 준비되지 않았을 때 폴링마다 connect_over_cdp를
# 다시 시도하면 asyncio 소켓 예외가 반복되고 서버 스레드가 소진되어
# 다른 API(/api/approval/respond 등)가 15초 타임아웃에 걸린다.
# 실패 직후 _CDP_RETRY_COOLDOWN 초간은 CDP 연결 시도를 건너뛰고 즉시 실패 처리한다.
_CDP_RETRY_COOLDOWN = 5.0
_last_cdp_attempt = 0.0  # worker 전용, _ensure_browser 내에서만 접근

# ── CDP 자동 재시작 요청 ──
# 기본 CDP는 OFF(구글 로그인 정상화). browser_* 도구가 호출됐는데 포트 9222가
# 닫혀 있으면 이 플래그 파일을 생성해 Electron에게 "CDP 켜고 재시작해달라"고
# 요청한다. Electron은 2초 폴링으로 감지해 --remote-debugging-port=9222 로
# 자동 재실행한다. (브라우저를 쓰지 않는 구글 로그인 등은 재시작이 일어나지 않는다.)
_CDP_RESTART_FLAG_PATH = os.environ.get(
    "DAON_CDP_RESTART_FLAG",
    os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "DAON Agent System",
        "restart-for-cdp.flag",
    ),
)
_cdp_restart_requested = False  # 세션당 한 번만 요청하도록 가드


def _request_cdp_restart() -> bool:
    """Create the restart flag file (once per session) so Electron ensures DAON Chrome (CDP 9222) is running."""
    global _cdp_restart_requested
    if _cdp_restart_requested:
        return False
    _cdp_restart_requested = True
    try:
        flag_dir = os.path.dirname(_CDP_RESTART_FLAG_PATH)
        if flag_dir:
            os.makedirs(flag_dir, exist_ok=True)
        with open(_CDP_RESTART_FLAG_PATH, "w", encoding="utf-8") as f:
            f.write("restart-with-cdp")
        _logger.warning(
            "[CDP] Browser tool called but CDP(9222) is OFF — restart flag written at %s. "
            "Electron will ensure DAON Chrome is running on --remote-debugging-port=9222.",
            _CDP_RESTART_FLAG_PATH,
        )
        return True
    except Exception as e:
        _logger.warning("[CDP] Failed to write restart flag: %s", e)
        return False


def _browser_worker_loop():
    """Dedicated thread: runs all Playwright operations sequentially."""
    global _last_url, _browser_active, _pending_url

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

    def _ensure_browser(auto_restart: bool = False):
        """Connect to DAON 전용 Chrome (real Chrome.exe, CDP 9222) via Playwright.

        내장 Electron WebContentsView 대신, DAON이 띄운 '진짜 Chrome.exe'의 전용
        프로필에 --remote-debugging-port=9222 로 연결한다. 사용자가 이 Chrome 창에서
        로그인하면 세션이 전용 프로필에 저장되고, 에이전트(browser_*)가 CDP로
        같은 세션을 공유·제어한다.

        auto_restart=True 일 때 CDP(9222)가 꺼져 있으면(연결 실패) 재시작 요청
        플래그를 써서 Electron이 DAON Chrome을 (재)실행하게 한다. status 폴링
        (5초 간격)은 auto_restart=False 로 호출해, 브라우저를 쓰지 않는 동안에는
        재실행이 일어나지 않도록 한다.
        """
        nonlocal browser, browser_page, pw
        global _last_cdp_attempt
        if browser is not None:
            try:
                browser_page.title()
                return browser_page, None
            except Exception:
                browser = None
                browser_page = None

        # CDP 재연결 백오프: 최근 실패 후 쿨다운 동안에는 연결 시도를 건너뛰고
        # 즉시 "준비 안 됨"으로 응답한다. 그래야 /api/browser/status 폴링이
        # 반복적인 connect_over_cdp 실패(asyncio socket.send 예외)로 서버를
        # 압박하지 않는다.
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
            # Connect to DAON 전용 Chrome / 내부 WebContentsView remote debugging port
            _logger.info("Attempting CDP connection to DAON browser at localhost:9222")
            browser = pw.chromium.connect_over_cdp("http://localhost:9222")

            # Find a usable browser page.
            # DAON 내부 공유 브라우저(WebContentsView, 파티션 persist:daon-shared-browser)
            # 는 브라우저 패널이 열리면 실제 URL(예: https://www.google.com)로 CDP 타겟이
            # 노출된다. Electron CDP에는 여러 context(파티션)가 있을 수 있으므로 모든
            # context 를 순회하며, 메인 UI(http://127.0.0.1:xxxx)와 about:blank(기본 빈
            # 페이지)는 건너뛰고 실제 URL을 가진 페이지를 우선 선택한다.
            # IMPORTANT (구버전 Electron BrowserView): NEVER call new_page() —
            # it spawned a new BrowserWindow taking over the screen.
            # 현재는 DAON 전용 Chrome.exe(CDP 9222) 방식이면 new_page()가 Chrome의
            # 실제 새 탭을 만들어 같은 프로필/세션을 공유한다. 단, Electron 모드
            # (BROWSER_CDP_URL 설정, 내부 WebContentsView)에서는 new_page()가 새
            # BrowserWindow를 만들어 화면을 가로채므로 절대 호출하지 않는다 — 이때는
            # frontend가 pending_url 로 내부 패널을 자동 생성하도록 안내한다.
            contexts = browser.contexts
            target_page = None
            fallback_page = None  # 실 URL이 없어도 쓸 수 있는 마지막 비-UI 페이지
            for ctx in contexts:
                for p in ctx.pages:
                    url = (p.url or "").strip()
                    _logger.debug("CDP page: %s", url)
                    if url.startswith("http://127.0.0.1"):
                        continue  # skip main UI
                    if url == "about:blank" or url == "":
                        fallback_page = fallback_page or p
                        continue  # remember but don't stop — keep looking for a real URL
                    target_page = p
                    break
                if target_page:
                    break

            if target_page:
                browser_page = target_page
                _last_cdp_attempt = time.time()  # 연결 성공 → 백오프 기준 리셋
                _logger.info("Connected to existing browser tab: %s", browser_page.url)
            elif fallback_page:
                browser_page = fallback_page
                _last_cdp_attempt = time.time()
                _logger.info("Using fallback CDP page (about:blank): %s", browser_page.url)
            else:
                if os.environ.get("BROWSER_CDP_URL"):
                    # Electron 모드(내부 WebContentsView): new_page()는 새 BrowserWindow를
                    # 만들어 화면을 가로채므로 금지. frontend가 pending_url 로 내부 패널을
                    # 자동 생성하도록 안내한다.
                    return None, "Electron 내부 브라우저 탭이 아직 없습니다. 브라우저 뷰를 열고 페이지를 로드한 후 다시 시도하세요."
                # DAON 전용 Chrome이 떠 있어도 시작 URL이 없어 탭 0개인 경우
                # (예: CDP 플래그 폴링이 기본 URL 없이 실행). 이때는 CDP 상에서
                # 실제 새 탭을 만들어 제어 대상으로 삼는다. 진짜 Chrome 탭이므로
                # 같은 프로필/세션을 공유하며 사용자 화면에도 보이는 창이 된다.
                try:
                    ctx = contexts[0] if contexts else browser.new_context()
                    browser_page = ctx.new_page()
                    browser_page.goto("https://www.google.com", timeout=30000)
                    _last_cdp_attempt = time.time()
                    _logger.info("No CDP pages — created a new DAON Chrome tab: %s", browser_page.url)
                except Exception as np_e:
                    _logger.warning("Failed to create a new CDP tab: %s", str(np_e))
                    return None, "Electron CDP connected but no pages available, and creating a new tab failed."

            return browser_page, None
        except Exception as e:
            _logger.warning("Electron CDP connection failed: %s", str(e))
            # CDP 연결 실패 시각 기록 → 다음 _CDP_RETRY_COOLDOWN 초간 재시도 방지
            _last_cdp_attempt = time.time()
            # 실제 browser_* 도구 호출(auto_restart=True)이고 CDP가 꺼져 있으면
            # Electron에게 재시작 요청 플래그를 쓴다. Electron이 이를 감지해
            # --remote-debugging-port=9222 로 자동 재실행한다.
            if auto_restart and os.environ.get("BROWSER_CDP_URL"):
                _request_cdp_restart()
                return None, (
                    "브라우저 자동화(CDP)가 꺼져 있어 앱을 자동 재시작합니다. "
                    "잠시 후 같은 요청을 다시 시도하세요. (구글 로그인은 CDP OFF 상태에서도 정상 동작)"
                )
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

        try:
            if action == "status":
                page, err = _ensure_browser()
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    # In Electron mode, no browser tab yet — signal frontend to auto-open
                    if os.environ.get("BROWSER_CDP_URL") and ("no browser tab" in str(err).lower() or "tab" in str(err).lower()):
                        _pending_url = url
                        _logger.info("No browser tab — waiting for frontend auto-open (url=%s)", url)
                        page = None
                        for _ in range(20):  # 20 × 500ms = 10s
                            time.sleep(0.5)
                            page, err2 = _ensure_browser(auto_restart=True)
                            if not err2:
                                break
                        _pending_url = ""
                        if page is None:
                            _browser_result_queue.put({
                                "_result_id": result_id,
                                "error": "브라우저 뷰가 열리지 않았습니다. 우측 상단 브라우저 아이콘을 클릭하거나 '/b' 명령을 먼저 실행하세요.",
                            })
                        else:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            _last_url = page.url
                            _browser_active = True
                            _browser_result_queue.put({
                                "_result_id": result_id,
                                "status": "ok",
                                "url": page.url,
                                "title": page.title(),
                            })
                    else:
                        _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _last_url = page.url
                    _browser_active = True
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "title": page.title(),
                    })

            elif action == "snapshot":
                page, err = _ensure_browser(auto_restart=True)
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

                        # Get interactive elements via JS for refs
                        elements_js = """
                        (() => {
                            const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],details,summary';
                            const els = document.querySelectorAll(interactive);
                            const results = [];
                            els.forEach((el, i) => {
                                const rect = el.getBoundingClientRect();
                                if (rect.width === 0 && rect.height === 0) return;
                                const label = el.getAttribute('aria-label') || el.textContent?.trim()?.substring(0, 100) || '';
                                results.push({
                                    ref: 'e' + i,
                                    tag: el.tagName.toLowerCase(),
                                    text: label,
                                    href: el.href || null,
                                    type: el.type || null,
                                    placeholder: el.placeholder || null,
                                });
                            });
                            return results;
                        })()
                        """
                        elements = page.evaluate(elements_js)
                        # Build refs dict (agent-browser compatible format)
                        refs = {}
                        for el in elements:
                            refs[el.get("ref", "")] = el

                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "title": page.title(),
                            "snapshot": snapshot_text or page.inner_text('body')[:10000],
                            "refs": refs,
                            "elements": elements,
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # Try to find and click the element by ref
                    click_js = f"""
                    (() => {{
                        const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],details,summary';
                        const els = document.querySelectorAll(interactive);
                        const filtered = [];
                        els.forEach((el, i) => {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            filtered.push({{el, ref: 'e' + filtered.length}});
                        }});
                        const target = filtered.find(f => f.ref === '{ref}');
                        if (target) {{
                            target.el.scrollIntoView({{block: 'center'}});
                            target.el.click();
                            return {{clicked: true, ref: '{ref}', tag: target.el.tagName.toLowerCase()}};
                        }}
                        return {{clicked: false, ref: '{ref}', error: 'Element not found'}};
                    }})()
                    """
                    result = page.evaluate(click_js)
                    page.wait_for_timeout(500)  # Wait for any navigation/update
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    type_js = f"""
                    (() => {{
                        const interactive = 'input,textarea,[contenteditable="true"],[role="textbox"]';
                        const els = document.querySelectorAll(interactive);
                        const filtered = [];
                        els.forEach((el, i) => {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            filtered.push({{el, ref: 'e' + filtered.length}});
                        }});
                        const target = filtered.find(f => f.ref === '{ref}');
                        if (target) {{
                            target.el.scrollIntoView({{block: 'center'}});
                            target.el.focus();
                            target.el.value = {_json.dumps(text)};
                            target.el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            target.el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return {{typed: true, ref: '{ref}'}};
                        }}
                        // Fallback: type into focused element
                        const active = document.activeElement;
                        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {{
                            active.value = {_json.dumps(text)};
                            active.dispatchEvent(new Event('input', {{bubbles: true}}));
                            return {{typed: true, ref: 'active', tag: active.tagName.toLowerCase()}};
                        }}
                        return {{typed: false, ref: '{ref}', error: 'No editable element found'}};
                    }})()
                    """
                    result = page.evaluate(type_js)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "type_result": result,
                    })

            elif action == "screenshot":
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    screenshot_bytes = page.screenshot(type="png", full_page=False)
                    image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "image_base64": image_b64,
                    })

            elif action == "execute":
                expression = task.get("expression", "")
                page, err = _ensure_browser(auto_restart=True)
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
                page, err = _ensure_browser(auto_restart=True)
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # Get interactive elements for AI recommendations
                    rec_js = """
                    (() => {
                        const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],details,summary';
                        const els = document.querySelectorAll(interactive);
                        const results = [];
                        els.forEach((el, i) => {
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            const label = el.getAttribute('aria-label') || el.textContent?.trim()?.substring(0, 150) || '';
                            results.push({
                                ref: 'e' + i,
                                tag: el.tagName.toLowerCase(),
                                text: label,
                                href: el.href || null,
                                type: el.type || null,
                                placeholder: el.placeholder || null,
                                id: el.id || null,
                                name: el.getAttribute('name') || null,
                                className: el.className || null,
                            });
                        });
                        return {url: window.location.href, title: document.title, elements: results};
                    })()
                    """
                    data = page.evaluate(rec_js)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                        "title": page.title(),
                        "recommendations": data.get("elements", []),
                    })

            elif action == "scroll":
                direction = task.get("direction", "down")
                px_raw = task.get("pixels", "500")
                try:
                    px = int(px_raw)
                except (ValueError, TypeError):
                    px = 500
                page, err = _ensure_browser(auto_restart=True)
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.go_back(wait_until="domcontentloaded", timeout=15000)
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "url": page.url,
                    })

            elif action == "press":
                key = task.get("key", "")
                page, err = _ensure_browser(auto_restart=True)
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    fill_js = f"""
                    (() => {{
                        const interactive = 'input,textarea,[contenteditable="true"],[role="textbox"]';
                        const els = document.querySelectorAll(interactive);
                        const filtered = [];
                        els.forEach((el, i) => {{
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 && rect.height === 0) return;
                            filtered.push({{el, ref: 'e' + filtered.length}});
                        }});
                        const target = filtered.find(f => f.ref === '{ref}');
                        if (target) {{
                            target.el.scrollIntoView({{block: 'center'}});
                            target.el.focus();
                            target.el.value = {_json.dumps(text)};
                            target.el.dispatchEvent(new Event('input', {{bubbles: true}}));
                            target.el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return {{filled: true, ref: '{ref}'}};
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    if os.environ.get("BROWSER_CDP_URL") and ("no browser tab" in str(err).lower() or "tab" in str(err).lower()):
                        _pending_url = url
                        page = None
                        for _ in range(20):
                            time.sleep(0.5)
                            page, err2 = _ensure_browser(auto_restart=True)
                            if not err2:
                                break
                        _pending_url = ""
                        if page is None:
                            _browser_result_queue.put({
                                "_result_id": result_id,
                                "error": "브라우저 뷰가 열리지 않았습니다. 브라우저를 먼저 열어주세요.",
                            })
                        else:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            _last_url = page.url
                            _browser_active = True
                            _browser_result_queue.put({
                                "_result_id": result_id,
                                "status": "ok",
                                "data": {"url": page.url, "title": page.title()},
                            })
                    else:
                        _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    _last_url = page.url
                    _browser_active = True
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"url": page.url, "title": page.title()},
                    })

            elif action == "get_images":
                page, err = _ensure_browser(auto_restart=True)
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
                page, err = _ensure_browser(auto_restart=True)
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"messages": []},
                    })

            elif action == "errors":
                page, err = _ensure_browser(auto_restart=True)
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
    global _browser_worker
    with _browser_worker_lock:
        if _browser_worker is None or not _browser_worker.is_alive():
            _browser_worker = threading.Thread(
                target=_browser_worker_loop,
                name="browser-worker",
                daemon=True,
            )
            _browser_worker.start()
            _logger.info("Browser worker thread started (PID-like id: %s)", _browser_worker.ident)


def _submit_task(action: str, **kwargs) -> dict:
    """Submit a task to the browser worker and wait for the result."""
    _start_browser_worker()

    result_id = int(time.time() * 1000000)  # unique ID
    task = {"action": action, "_result_id": result_id, **kwargs}
    _browser_task_queue.put(task)

    try:
        result = _browser_result_queue.get(timeout=35)
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
        return {"error": "Browser operation timed out (35s)"}


# ── Route Handlers ──

def handle_get_browser_status(handler, parsed):
    """GET /api/browser/status — return current browser status."""
    result = _submit_task("status")
    if "error" in result:
        return j_ok(handler, {
            "status": "disconnected",
            "url": "",
            "error": result["error"],
            "pending_url": _pending_url,
        })
    return j_ok(handler, {
        "status": result.get("status", "unknown"),
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "pending_url": _pending_url,
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
    url = (body or {}).get("url", "")
    if not url:
        return j_err(handler, "Missing 'url' field")
    result = _submit_task("navigate", url=url)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
    })


def handle_post_browser_snapshot(handler, body: dict):
    """POST /api/browser/snapshot — get accessibility snapshot + interactive elements."""
    result = _submit_task("snapshot")
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
    ref = (body or {}).get("ref", "")
    if not ref:
        return j_err(handler, "Missing 'ref' field")
    result = _submit_task("click", ref=ref)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "click_result": result.get("click_result", {}),
    })


def handle_post_browser_type(handler, body: dict):
    """POST /api/browser/type — type text into an element."""
    ref = (body or {}).get("ref", "")
    text = (body or {}).get("text", "")
    if not ref:
        return j_err(handler, "Missing 'ref' field")
    result = _submit_task("type", ref=ref, text=text)
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "type_result": result.get("type_result", {}),
    })


def handle_post_browser_screenshot(handler, body: dict):
    """POST /api/browser/screenshot — capture a screenshot (base64 PNG)."""
    result = _submit_task("screenshot")
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
        "image_base64": result.get("image_base64", ""),
    })


def handle_post_browser_execute(handler, body: dict):
    """POST /api/browser/execute — execute arbitrary JavaScript in the page."""
    expression = (body or {}).get("expression", "")
    if not expression:
        return j_err(handler, "Missing 'expression' field")
    result = _submit_task("execute", expression=expression)
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
