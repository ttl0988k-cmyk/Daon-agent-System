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

    def _ensure_browser():
        """Connect to Electron's WebContentsView via CDP."""
        nonlocal browser, browser_page, pw
        if browser is not None:
            try:
                browser_page.title()
                return browser_page, None
            except Exception:
                browser = None
                browser_page = None

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
            # Connect to Electron remote debugging port
            _logger.info("Attempting CDP connection to Electron at localhost:9222")
            browser = pw.chromium.connect_over_cdp("http://localhost:9222")

            # Find the TabManager's WebContentsView page.
            # In Electron, the TabManager creates a WebContentsView for the browser tab.
            # We find a page that is NOT the main UI (http://127.0.0.1:xxxx) and
            # NOT about:blank (which is the default empty page).
            # IMPORTANT: NEVER call new_page() in CDP mode — it spawns a new
            # BrowserWindow in Electron, taking over the entire screen.
            pages = browser.contexts[0].pages
            target_page = None
            for p in pages:
                _logger.debug("CDP page: %s", p.url)
                if p.url.startswith("http://127.0.0.1"):
                    continue  # skip main UI
                if p.url == "about:blank":
                    continue  # skip blank pages
                target_page = p
                break

            if target_page:
                browser_page = target_page
                _logger.info("Connected to existing browser tab: %s", browser_page.url)
            elif pages:
                # Use the last non-UI page, even if about:blank
                for p in reversed(pages):
                    if not p.url.startswith("http://127.0.0.1"):
                        browser_page = p
                        break
                if browser_page:
                    _logger.info("Using fallback CDP page: %s", browser_page.url)
                else:
                    return None, "Electron CDP connected but no browser tab found. Open a browser tab first."
            else:
                return None, "Electron CDP connected but no pages available. Open a browser tab first."

            return browser_page, None
        except Exception as e:
            _logger.warning("Electron CDP connection failed: %s", str(e))
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
                page, err = _ensure_browser()
                if err:
                    # In Electron mode, no browser tab yet — signal frontend to auto-open
                    if os.environ.get("BROWSER_CDP_URL") and ("no browser tab" in str(err).lower() or "tab" in str(err).lower()):
                        _pending_url = url
                        _logger.info("No browser tab — waiting for frontend auto-open (url=%s)", url)
                        page = None
                        for _ in range(20):  # 20 × 500ms = 10s
                            time.sleep(0.5)
                            page, err2 = _ensure_browser()
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
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # Get accessibility snapshot from Playwright
                    try:
                        snapshot = page.accessibility.snapshot()
                        # Also get interactive elements via JS for richer data
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
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "title": page.title(),
                            "dom": _json.dumps(snapshot, default=str) if snapshot else "",
                            "elements": elements,
                            "text": page.inner_text('body')[:10000] if hasattr(page, 'inner_text') else "",
                        })
                    except Exception as snap_err:
                        # Fallback: just get page text
                        _browser_result_queue.put({
                            "_result_id": result_id,
                            "status": "ok",
                            "url": page.url,
                            "title": page.title(),
                            "dom": "",
                            "elements": [],
                            "text": page.inner_text('body')[:10000] if hasattr(page, 'inner_text') else "",
                            "snapshot_error": str(snap_err),
                        })

            elif action == "click":
                ref = task.get("ref", "")
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
