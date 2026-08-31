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

    def _ensure_browser():
        """Connect to Electron's WebContentsView via CDP.

        (8월 3일 정상 빌드 복원) 앱 시작 시 CDP 9222가 항상 ON이므로 연결만 하면
        된다. 사용자가 본 내부 WebContentsView(기본 세션) 페이지를 그대로 공유·
        제어한다 — new_page()는 절대 호출하지 않는다(Electron에서 새 BrowserWindow
        를 만들어 화면을 가로채므로). 브라우저 패널을 열지 않았으면 "탭 없음"으로
        응답하고, frontend가 pending_url 로 내부 패널을 자동 생성하도록 안내한다.
        """
        nonlocal browser, browser_page, pw
        global _last_cdp_attempt, _cdp_fail_streak
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
            # 127.0.0.1 명시: Windows가 localhost를 IPv6(::1)로 우선 해석해 CDP
            # 리스너(IPv4)와 불일치하면 ECONNREFUSED ::1:9222 로 연결이 실패한다.
            # 사전 점검: 엔드포인트가 무응답이면 connect 자체를 건너뛰고 실패 처리로
            # 우회한다(드라이버 비정상 시 hang 방지).
            if not _cdp_endpoint_ready():
                raise ConnectionError("CDP endpoint 127.0.0.1:9222 not reachable (pre-check)")
            _logger.info("Attempting CDP connection to DAON browser at 127.0.0.1:9222")
            # timeout 필수: 없으면 드라이버 비정상 시 _sync()가 GIL을 쥔 채 무한
            # spin한다(py-spy로 확인). 타임아웃이 있으면 드라이버가 10초 후 예외 반환.
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=_CDP_CONNECT_TIMEOUT_MS)
            _cdp_fail_streak = 0  # 연결 성공 → 연속 실패 카운트 리셋

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
                    if url.startswith("devtools://") or url.startswith("chrome://") or url.startswith("devtools:"):
                        # Electron/Chromium 내부 DevTools 페이지는 절대 공유 페이지로
                        # 선택하지 않는다(F12로 연 devtools:// 타깃이 target_page 로
                        # 잡히면 에이전트가 DevTools 화면을 제어하는 오동작 발생).
                        _logger.debug("Skipping internal DevTools page: %s", url)
                        continue
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
                # Electron 모드(내부 WebContentsView): new_page()는 새 BrowserWindow를
                # 만들어 화면을 가로채므로 절대 금지(8월 3일 방식).
                # frontend가 pending_url 로 내부 패널을 자동 생성하도록 안내한다.
                return None, "Electron 내부 브라우저 탭이 아직 없습니다. 브라우저 뷰를 열고 페이지를 로드한 후 다시 시도하세요."

            return browser_page, None
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

        # 만료된 status 태스크: 요청자가 이미 타임아웃으로 포기하고 떠났으므로
        # 실행을 건너뛴다. (워커가 일시 정체됐다가 복구된 후, 쌓인 status 폴링을
        # 뒤늦게 몰아서 실행하는 것을 방지)
        _task_expires_at = task.get("_expires_at") or 0
        if action == "status" and _task_expires_at and time.time() > _task_expires_at:
            continue

        _worker_task_start_ts = time.time()
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
                # TTL pending: 탭 유무와 무관하게 항상 설정 → 프론트 폴링이 에디터 뒤 브라우저 뷰를
                # 자동으로 앞으로 가져온다. (이전엔 탭 없음 분기에서만 설정되어, 이미 뷰가 존재하지만
                # 숨겨진 경우 폴링이 토글을 잡지 못하는 race가 있었다.)
                _set_pending(url)
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    # 스냅샷 시점 식별자를 우선 사용하고, 실패 시 위치 재계산으로 폴백.
                    stored = _get_stored_ref(ref)
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
                            target = (filtered.find(f => f.ref === '{ref}') || {{}}).el || null;
                        }}

                        if (target) {{
                            target.scrollIntoView({{block: 'center'}});
                            target.click();
                            return {{clicked: true, ref: '{ref}', tag: target.tagName.toLowerCase(), by: 'stored-or-positional'}};
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
                    # 스냅샷 시점 식별자 우선 → 위치 재계산 폴백 → 포커스 폴백
                    stored = _get_stored_ref(ref)
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
                            target = (filtered.find(f => f.ref === '{ref}') || {{}}).el || null;
                        }}

                        if (target) {{
                            target.scrollIntoView({{block: 'center'}});
                            target.focus();
                            target.value = {_json.dumps(text)};
                            target.dispatchEvent(new Event('input', {{bubbles: true}}));
                            target.dispatchEvent(new Event('change', {{bubbles: true}}));
                            return {{typed: true, ref: '{ref}', by: 'stored-or-positional'}};
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
                    # recommend 결과의 refs도 저장소에 반영 — 이후 click/type/fill이
                    # 식별자 매칭으로 정확한 요소를 찾도록 한다.
                    _store_refs(data.get("elements", []))
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
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
                page, err = _ensure_browser()
                if err:
                    _browser_result_queue.put({"_result_id": result_id, "error": err})
                else:
                    _browser_result_queue.put({
                        "_result_id": result_id,
                        "status": "ok",
                        "data": {"messages": []},
                    })

            elif action == "errors":
                page, err = _ensure_browser()
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
    """Submit a task to the browser worker and wait for the result.

    wait_timeout: 결과 대기 최대 초. status 폴링은 짧은 값을 전달해 워커가
    정체됐을 때 서버 스레드가 35초씩 블로킹되지 않도록 한다.
    """
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
    """GET /api/browser/status — return current browser status.

    적체 방지: 워커가 정체 중(현재 태스크가 _WORKER_STUCK_THRESHOLD 초과)이거나
    이미 status 태스크가 대기 중이면 큐에 중복 투입하지 않고 캐시 상태를 즉시
    반환한다. 프론트 5초 폴링이 큐에 쌓여 스레드당 35초씩 블로킹되는 것을 막는다.
    """
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
    resp = {
        "url": result.get("url", ""),
        "title": result.get("title", ""),
    }
    # 큰 iframe이 있으면 에이전트/사용자에게 힌트 — upsampler.co처럼 생성기가
    # iframe으로 임베드된 사이트에서 iframe URL을 직접 열면 전체 화면으로 쓸 수 있다.
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


def handle_post_browser_back(handler, body: dict):
    """POST /api/browser/back — go back in browser history."""
    result = _submit_task("back")
    if "error" in result:
        return j_err(handler, result["error"], status=500)
    return j_ok(handler, {
        "url": result.get("url", ""),
    })


def handle_post_browser_forward(handler, body: dict):
    """POST /api/browser/forward — go forward in browser history."""
    result = _submit_task("forward")
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
