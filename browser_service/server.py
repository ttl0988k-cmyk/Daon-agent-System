# -*- coding: utf-8 -*-
"""
DAON Browser Agent Service
Playwright + Chrome DevTools Protocol (CDP) 온디맨드 실시간 브라우저 서비스.
- 포트: 8088
- 기능: 실시간 화면 스트리밍 (Screencast), 사용자 개입 (Human Takeover), 에이전트 제어 도구 (REST)
"""
import base64
import json
import os
import sys
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set, Dict, Any, List

# Ensure Playwright finds browsers in %LOCALAPPDATA%\ms-playwright
local_app_data = os.environ.get("LOCALAPPDATA", "")
if local_app_data:
    playwright_path = os.path.join(local_app_data, "ms-playwright")
    if os.path.isdir(playwright_path):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = playwright_path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from playwright.async_api import async_playwright, Playwright, Browser as PwBrowser, BrowserContext, Page, CDPSession

VIEWPORT = {"width": 1280, "height": 800}

# 특수키 → Windows 가상키코드 및 CDP keydown 파라미터 매핑 (Enter 폼제출, Backspace 삭제, 방향키 등)
_SPECIAL_KEYCODES = {
    "Enter": {"vk": 13, "text": "\r"},
    "Backspace": {"vk": 8},
    "Tab": {"vk": 9},
    "Escape": {"vk": 27},
    " ": {"vk": 32, "text": " "},
    "ArrowLeft": {"vk": 37},
    "ArrowUp": {"vk": 38},
    "ArrowRight": {"vk": 39},
    "ArrowDown": {"vk": 40},
    "Delete": {"vk": 46},
    "Home": {"vk": 36},
    "End": {"vk": 35},
    "PageUp": {"vk": 33},
    "PageDown": {"vk": 34},
    "F1": {"vk": 112}, "F2": {"vk": 113}, "F3": {"vk": 114}, "F4": {"vk": 115},
    "F5": {"vk": 116}, "F6": {"vk": 117}, "F7": {"vk": 118}, "F8": {"vk": 119},
    "F9": {"vk": 120}, "F10": {"vk": 121}, "F11": {"vk": 122}, "F12": {"vk": 123},
    "HangulMode": {"vk": 21}, "Hanji": {"vk": 25},
}

_EXTRACT_INTERACTIVE_JS = """
(() => {
    const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],[role="checkbox"],[role="radio"],[role="menuitem"],[role="tab"],[contenteditable="true"],details,summary';
    const allEls = document.querySelectorAll(interactive);
    const results = [];
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;

    allEls.forEach((el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width <= 2 || rect.height <= 2) return;

        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') < 0.05) return;
        if (style.pointerEvents === 'none') return;
        if (el.disabled) return;

        const cx = Math.min(Math.max(rect.left + rect.width / 2, 0), vw - 1);
        const cy = Math.min(Math.max(rect.top + rect.height / 2, 0), vh - 1);
        const topEl = document.elementFromPoint(cx, cy);
        let isVisible = topEl && (el === topEl || el.contains(topEl) || topEl.contains(el));

        if (!isVisible && rect.width > 12 && rect.height > 12) {
            const p2 = document.elementFromPoint(rect.left + 6, rect.top + 6);
            if (p2 && (el === p2 || el.contains(p2) || p2.contains(el))) {
                isVisible = true;
            }
        }
        if (!isVisible) return;

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

class BrowserSessionManager:
    def __init__(self):
        self.pw: Optional[Playwright] = None
        self.browser: Optional[PwBrowser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.cdp: Optional[CDPSession] = None
        self.viewers: Set[WebSocket] = set()
        self.is_running: bool = False
        self._ref_store: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        # 마지막 정상 URL (페이지 크래시 시 자동 복구용)
        self._last_url: Optional[str] = None
        # 직접제어 입력 진단 카운터 (클릭 패킷 서버 도달 여부 판정용)
        self._input_stats: Dict[str, int] = {
            "mouse_down": 0, "mouse_up": 0, "mouse_move": 0,
            "key_down": 0, "key_up": 0, "wheel": 0, "text": 0, "errors": 0,
        }

    async def ensure_started(self):
        async with self._lock:
            if self.is_running and self.page and not self.page.is_closed():
                return

            print("[BrowserService] 🚀 브라우저 인스턴스 시작 중...", flush=True)
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1280,800",
                    "--lang=ko-KR,ko,en-US,en",
                ]
            )
            self.context = await self.browser.new_context(
                viewport=VIEWPORT,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            # 봇 탐지 우회 스크립트 (navigator.webdriver 등 마스킹)
            await self.context.add_init_script("""
                // Mask navigator.webdriver
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                // Mask window.chrome
                if (!window.chrome) {
                    window.chrome = {
                        runtime: {},
                        loadTimes: function() {},
                        csi: function() {},
                        app: {}
                    };
                }

                // Mask plugins / languages
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ko-KR', 'ko', 'en-US', 'en']
                });

                // target="_blank"를 현재 탭(_self)으로 강제 전환하여 분기 없이 단일 화면에서 바로 열리도록 보장
                document.addEventListener('click', (e) => {
                    try {
                        const a = e.target.closest('a');
                        if (a && a.target && a.target.toLowerCase() === '_blank') {
                            a.target = '_self';
                        }
                    } catch (_) {}
                }, true);

                // window.open 호출도 현재 탭 이동으로 처리
                try {
                    const origOpen = window.open;
                    window.open = function(url, target, features) {
                        if (url) {
                            window.location.href = url;
                            return window;
                        }
                        return origOpen ? origOpen.apply(this, arguments) : window;
                    };
                } catch (_) {}
            """)

            # 새 탭/창 생성 시 활성 탭으로 자동 전환하는 리스너 등록
            self.context.on("page", lambda p: asyncio.create_task(self._on_new_page(p)))

            self.page = await self.context.new_page()
            # 크래시로 새 페이지가 생겼으면 마지막 URL로 자동 복구
            if self._last_url:
                try:
                    await self.page.goto(self._last_url, wait_until="domcontentloaded", timeout=20000)
                    print(f"[BrowserService] 🔄 크래시 복구 — 마지막 페이지로 복귀: {self._last_url}", flush=True)
                except Exception as e:
                    print(f"[BrowserService] 복구 이동 실패: {e}", flush=True)

            await self._attach_page(self.page)
            self.is_running = True
            print("[BrowserService] ✅ 브라우저 및 CDP 스트리밍 준비 완료 (스텔스 모드 적용)", flush=True)

    async def _attach_page(self, page):
        """페이지에 CDP 및 프레임/네비게이션 리스너를 붙이고 활성 페이지로 설정"""
        if self.cdp:
            try:
                await self.cdp.send("Page.stopScreencast")
            except Exception:
                pass
            self.cdp = None

        self.page = page
        self.cdp = await self.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._on_frame)
        await self.cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": 80,
            "maxWidth": VIEWPORT["width"],
            "maxHeight": VIEWPORT["height"],
            "everyNthFrame": 1,
        })

        def _on_nav(frame):
            if frame == page.main_frame:
                self._last_url = frame.url
                asyncio.create_task(self._broadcast_nav(frame.url))
                asyncio.create_task(self._push_instant_frame())

        async def _inject_blank_killer():
            try:
                await page.evaluate("""
                    document.addEventListener('click', (e) => {
                        try {
                            const a = e.target.closest('a');
                            if (a && a.target && a.target.toLowerCase() === '_blank') {
                                a.target = '_self';
                            }
                        } catch (_) {}
                    }, true);
                """)
            except Exception:
                pass

        async def _on_page_load():
            await self._push_instant_frame()
            await _inject_blank_killer()

        page.on("framenavigated", _on_nav)
        page.on("load", lambda: asyncio.create_task(_on_page_load()))
        page.on("close", lambda: asyncio.create_task(self._on_page_closed(page)))
        asyncio.create_task(_inject_blank_killer())

        self._last_url = page.url
        asyncio.create_task(self._broadcast_nav(page.url))
        asyncio.create_task(self._push_instant_frame())

    async def _on_new_page(self, new_page):
        print(f"[BrowserService] 📄 새 탭/창 생성 감지: {new_page.url} -> 활성 탭으로 자동 전환", flush=True)
        try:
            await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await self._attach_page(new_page)

    async def _on_page_closed(self, closed_page):
        if self.page == closed_page and self.context:
            pages = self.context.pages
            remaining = [p for p in pages if p != closed_page and not p.is_closed()]
            if remaining:
                print(f"[BrowserService] 📄 현재 탭 닫힘 -> 이전 탭으로 자동 복귀", flush=True)
                await self._attach_page(remaining[-1])

    async def _broadcast_nav(self, url: str):
        if not self.viewers:
            return
        msg = json.dumps({"t": "nav", "url": url})
        dead = set()
        for ws in list(self.viewers):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        if dead:
            self.viewers -= dead

    async def _notify_viewers_closed(self, reason: str = "closed"):
        """브라우저가 닫혔음을 뷰어들에게 알리고 소켓을 정리한다.

        이 알림이 없으면 프론트 카드가 '연결됨' 상태로 남아 계속 재연결을
        시도하고, 브라우저를 닫아도 뷰어 카드가 사라지지 않는다.
        프론트(browser_viewer.js)는 {"t":"closed"} 수신 시 카드를 제거한다.
        """
        if not self.viewers:
            return
        msg = json.dumps({"t": "closed", "reason": reason})
        n = len(self.viewers)
        dead = set()
        for ws in list(self.viewers):
            try:
                await ws.send_text(msg)
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
            dead.add(ws)
        self.viewers -= dead
        print(f"[BrowserService] 📴 뷰어 {n}개에 종료 알림 전송 (reason={reason})", flush=True)

    async def stop(self):
        async with self._lock:
            if not self.is_running:
                # 이미 닫힌 상태여도 잔여 뷰어는 정리한다 (재연결 폭주 방지)
                await self._notify_viewers_closed("already_stopped")
                return
            print("[BrowserService] 🛑 브라우저 인스턴스 종료 및 리소스 반환 중...", flush=True)
            try:
                if self.cdp:
                    try:
                        await self.cdp.send("Page.stopScreencast")
                    except Exception:
                        pass
                if self.context:
                    await self.context.close()
                if self.browser:
                    await self.browser.close()
                if self.pw:
                    await self.pw.stop()
            except Exception as e:
                print(f"[BrowserService] 종료 중 예외 (무시): {e}", flush=True)
            finally:
                self.pw = None
                self.browser = None
                self.context = None
                self.page = None
                self.cdp = None
                self._ref_store.clear()
                self.is_running = False
                print("[BrowserService] ✅ 브라우저 세션 정리 완료", flush=True)
                await self._notify_viewers_closed("closed")

    async def _on_frame(self, frame):
        try:
            if self.cdp:
                await self.cdp.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})
        except Exception:
            pass

        msg = json.dumps({"t": "frame", "data": frame["data"]})
        dead = set()
        for ws in list(self.viewers):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        if dead:
            self.viewers -= dead

    async def _push_instant_frame(self):
        """사용자 조작 후 즉시 화면 프레임을 캡처하여 지연 없이 전송"""
        if not self.is_running or not self.page or not self.viewers:
            return
        try:
            shot = await self.page.screenshot(type="jpeg", quality=80)
            msg = json.dumps({"t": "frame", "data": base64.b64encode(shot).decode()})
            dead = set()
            for ws in list(self.viewers):
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.add(ws)
            if dead:
                self.viewers -= dead
        except Exception:
            pass

    async def handle_input(self, ev):
        kind = ev.get("t")
        # WS 경유 페이지 열기/이동 (폰 원격 등 8088 직접 fetch가 안 되는 환경용)
        if kind == "open":
            url = ev.get("url") or "https://www.google.com"
            try:
                if not self.is_running:
                    await self.ensure_started()
                if self.page:
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    self._last_url = url
            except Exception as e:
                print(f"[BrowserService] open 이동 오류: {e}", flush=True)
            return
        # WS 경유 페이지 탐색 (뒤로/앞으로/새로고침 — 카드 헤더 버튼용)
        if kind == "nav":
            action = ev.get("action", "")
            try:
                if not self.is_running:
                    await self.ensure_started()
                if self.page and not self.page.is_closed():
                    if action == "back":
                        await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
                    elif action == "forward":
                        await self.page.go_forward(wait_until="domcontentloaded", timeout=15000)
                    elif action == "reload":
                        await self.page.reload(wait_until="domcontentloaded", timeout=30000)
                    asyncio.create_task(self._push_instant_frame())
            except Exception as e:
                print(f"[BrowserService] nav({action}) 오류: {e}", flush=True)
            return
        if not self.is_running or not self.cdp:
            return

        x = float(ev.get("x", 0)) * VIEWPORT["width"]
        y = float(ev.get("y", 0)) * VIEWPORT["height"]
        kind = ev.get("t")
        btn = ev.get("button", "left")
        buttons_map = {"left": 1, "right": 2, "middle": 4}

        try:
            # 한글 완성 텍스트 통째 삽입 (헤드리스엔 OS IME가 없어 완성 음절/단어 단위로 삽입)
            if kind == "text":
                self._input_stats["text"] += 1
                txt = ev.get("text", "")
                if txt and self.page:
                    await self.page.keyboard.insert_text(txt)
                    asyncio.create_task(self._push_instant_frame())
                return
            if kind == "mouse":
                action = ev.get("kind")
                if action == "down":
                    self._mouse_pressed = True
                    buttons = buttons_map.get(btn, 1)
                    self._input_stats["mouse_down"] += 1
                    await self.cdp.send("Input.dispatchMouseEvent", {
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": btn,
                        "buttons": buttons,
                        "clickCount": int(ev.get("clickCount", 1)),
                    })
                elif action == "up":
                    self._mouse_pressed = False
                    self._input_stats["mouse_up"] += 1
                    await self.cdp.send("Input.dispatchMouseEvent", {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": btn,
                        "buttons": 0,
                        "clickCount": int(ev.get("clickCount", 1)),
                    })
                    # 마우스 클릭 완료 시 즉시 화면 갱신 푸시 (캡챠 타일 반응/체크 표시 즉시 렌더링)
                    asyncio.create_task(self._push_instant_frame())
                elif action == "click":
                    # 단일 클릭 (단발성 정확한 클릭 보장)
                    buttons = buttons_map.get(btn, 1)
                    self._input_stats["mouse_down"] += 1
                    await self.cdp.send("Input.dispatchMouseEvent", {
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": btn,
                        "buttons": buttons,
                        "clickCount": 1,
                    })
                    await asyncio.sleep(0.05)
                    self._input_stats["mouse_up"] += 1
                    await self.cdp.send("Input.dispatchMouseEvent", {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": btn,
                        "buttons": 0,
                        "clickCount": 1,
                    })
                    asyncio.create_task(self._push_instant_frame())
                elif action == "move":
                    self._input_stats["mouse_move"] += 1
                    is_down = ev.get("down", False) or getattr(self, "_mouse_pressed", False)
                    buttons = buttons_map.get(btn, 1) if is_down else 0
                    params = {
                        "type": "mouseMoved",
                        "x": x,
                        "y": y,
                        "buttons": buttons,
                    }
                    if is_down:
                        params["button"] = btn
                    await self.cdp.send("Input.dispatchMouseEvent", params)
            elif kind == "key":
                action = ev.get("kind")
                key = ev.get("key", "")
                code = ev.get("code", "")
                text = ev.get("text", "")

                if self.page:
                    if action == "down":
                        self._input_stats["key_down"] += 1
                        if text and len(text) > 0 and key not in _SPECIAL_KEYCODES:
                            await self.page.keyboard.insert_text(text)
                        elif key == "Enter":
                            await self.page.keyboard.press("Enter")
                        elif key == "Backspace":
                            await self.page.keyboard.press("Backspace")
                        else:
                            try:
                                await self.page.keyboard.down(key)
                            except Exception:
                                pass
                    elif action == "up":
                        self._input_stats["key_up"] += 1
                        if key not in ("Enter", "Backspace"):
                            try:
                                await self.page.keyboard.up(key)
                            except Exception:
                                pass
                        asyncio.create_task(self._push_instant_frame())
            elif kind == "wheel":
                self._input_stats["wheel"] += 1
                await self.cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": float(ev.get("deltaX", 0)),
                    "deltaY": float(ev.get("deltaY", 100)),
                })
                asyncio.create_task(self._push_instant_frame())
        except Exception as e:
            self._input_stats["errors"] += 1
            print(f"[BrowserService] 입력 주입 오류: {e}", flush=True)

    async def extract_snapshot(self) -> Dict[str, Any]:
        if not self.page:
            return {"ok": False, "error": "Page not open"}

        url = self.page.url
        title = await self.page.title()

        elements = []
        try:
            elements = await self.page.evaluate(_EXTRACT_INTERACTIVE_JS) or []
        except Exception as e:
            print(f"[BrowserService] Interactive elements eval error: {e}", flush=True)

        refs = {}
        self._ref_store.clear()
        lines = []
        for el in elements:
            ref = el.get("ref", "")
            if not ref:
                continue
            refs[ref] = el
            self._ref_store[ref] = el
            tag = el.get("tag", "")
            txt = (el.get("text") or el.get("placeholder") or "").strip()
            if txt:
                lines.append(f"[{ref}] <{tag}> {txt}")
            elif el.get("href"):
                lines.append(f"[{ref}] <{tag}> {el['href']}")

        snapshot_text = "\n".join(lines)
        if not snapshot_text:
            try:
                snapshot_text = (await self.page.inner_text("body"))[:10000]
            except Exception:
                snapshot_text = ""

        return {
            "ok": True,
            "url": url,
            "title": title,
            "snapshot": snapshot_text,
            "text": snapshot_text,
            "elements": elements,
            "refs": refs,
        }

    async def click_ref_or_selector(self, target: str) -> Dict[str, Any]:
        if not self.page:
            return {"ok": False, "error": "Page not open"}

        ref = target.lstrip("@")
        stored = self._ref_store.get(ref)
        ident_json = json.dumps(stored or {})

        click_js = f"""
        (() => {{
            const interactive = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="textbox"],details,summary';
            const stored = {ident_json};
            let targetEl = null;

            if (stored && (stored.id || stored.name || stored.href || stored.text)) {{
                const els = Array.from(document.querySelectorAll(interactive));
                targetEl = els.find(el => {{
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

            if (!targetEl) {{
                const els = document.querySelectorAll(interactive);
                const filtered = [];
                els.forEach((el) => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return;
                    filtered.push({{el, ref: 'e' + filtered.length}});
                }});
                targetEl = (filtered.find(f => f.ref === '{ref}') || {{}}).el || null;
            }}

            if (targetEl) {{
                targetEl.scrollIntoView({{block: 'center'}});
                targetEl.click();
                return {{clicked: true, ref: '{ref}', tag: targetEl.tagName.toLowerCase()}};
            }}
            return null;
        }})()
        """
        try:
            res = await self.page.evaluate(click_js)
            if res:
                await asyncio.sleep(0.2)
                return {"ok": True, **res}
        except Exception:
            pass

        # CSS 셀렉터 폴백
        try:
            await self.page.click(target, timeout=5000)
            return {"ok": True, "selector": target}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def type_ref_or_selector(self, target: str, text: str) -> Dict[str, Any]:
        if not self.page:
            return {"ok": False, "error": "Page not open"}

        if not target:
            await self.page.keyboard.type(text)
            return {"ok": True, "typed": text}

        ref = target.lstrip("@")
        stored = self._ref_store.get(ref)
        ident_json = json.dumps(stored or {})
        text_json = json.dumps(text)

        type_js = f"""
        (() => {{
            const interactive = 'input,textarea,[contenteditable="true"],[role="textbox"]';
            let targetEl = null;
            const stored = {ident_json};

            if (stored && (stored.id || stored.name || stored.placeholder)) {{
                const els = Array.from(document.querySelectorAll(interactive));
                targetEl = els.find(el => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return false;
                    if (stored.id && el.id === stored.id) return true;
                    if (stored.name && el.getAttribute('name') === stored.name) return true;
                    if (stored.placeholder && el.placeholder === stored.placeholder) return true;
                    return false;
                }}) || null;
            }}

            if (!targetEl) {{
                const els = document.querySelectorAll(interactive);
                const filtered = [];
                els.forEach((el) => {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) return;
                    filtered.push({{el, ref: 'e' + filtered.length}});
                }});
                targetEl = (filtered.find(f => f.ref === '{ref}') || {{}}).el || null;
            }}

            if (targetEl) {{
                targetEl.scrollIntoView({{block: 'center'}});
                targetEl.focus();
                targetEl.value = {text_json};
                targetEl.dispatchEvent(new Event('input', {{bubbles: true}}));
                targetEl.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{filled: true, ref: '{ref}'}};
            }}

            const active = document.activeElement;
            if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {{
                active.value = {text_json};
                active.dispatchEvent(new Event('input', {{bubbles: true}}));
                active.dispatchEvent(new Event('change', {{bubbles: true}}));
                return {{filled: true, active: true}};
            }}
            return null;
        }})()
        """
        try:
            res = await self.page.evaluate(type_js)
            if res:
                return {"ok": True, **res}
        except Exception:
            pass

        # CSS 셀렉터 폴백
        try:
            await self.page.fill(target, text, timeout=5000)
            return {"ok": True, "selector": target}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def scroll(self, direction: str = "down", pixels: int = 500) -> Dict[str, Any]:
        if not self.page:
            return {"ok": False, "error": "Page not open"}
        delta_y = pixels if direction == "down" else -pixels
        try:
            await self.page.evaluate(f"window.scrollBy(0, {delta_y})")
            return {"ok": True, "direction": direction, "pixels": pixels}
        except Exception as e:
            return {"ok": False, "error": str(e)}

manager = BrowserSessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[BrowserService] 서버 대기 시작 (포트: 8088)", flush=True)
    yield
    await manager.stop()

app = FastAPI(title="DAON Browser Agent Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "browser_running": manager.is_running,
        "current_url": manager.page.url if (manager.is_running and manager.page) else None,
        "viewers_count": len(manager.viewers),
        "input_stats": dict(manager._input_stats)
    }

@app.post("/api/session/start")
async def session_start(body: dict = None):
    await manager.ensure_started()
    url = (body or {}).get("url")
    if url and manager.page:
        try:
            await manager.page.goto(url, wait_until="domcontentloaded", timeout=15000)
            manager._last_url = url
        except Exception as e:
            print(f"[BrowserService] 시작 url 이동 경고: {e}", flush=True)
    return {
        "ok": True,
        "status": "started",
        "url": manager.page.url if manager.page else None,
        "title": (await manager.page.title()) if manager.page else None
    }

@app.post("/api/session/close")
async def session_close():
    await manager.stop()
    return {"ok": True, "status": "closed"}

@app.websocket("/stream")
async def websocket_stream(ws: WebSocket):
    await ws.accept()
    # 프로브 접속 (?probe=1): 상태만 반환하고 종료 — 브라우저 기동 없음
    # (새로고침 자동복구용. 원격 https에서도 WS 프록시를 타므로 혼합콘텐츠 문제 없음)
    if ws.query_params.get("probe") == "1":
        try:
            await ws.send_text(json.dumps({
                "t": "hello",
                "browser_running": manager.is_running,
                "current_url": manager.page.url if (manager.is_running and manager.page) else None,
            }))
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        return
    if not manager.is_running:
        await manager.ensure_started()

    manager.viewers.add(ws)

    # 뷰어 접속 즉시 스피너를 해제할 수 있도록 현재 화면 프레임 1장 즉시 송출
    if manager.is_running and manager.page:
        try:
            shot = await manager.page.screenshot(type="jpeg", quality=75)
            await ws.send_text(json.dumps({"t": "frame", "data": base64.b64encode(shot).decode()}))
        except Exception:
            pass

    try:
        while True:
            raw = await ws.receive_text()
            ev = json.loads(raw)
            await manager.handle_input(ev)
    except WebSocketDisconnect:
        manager.viewers.discard(ws)
    except Exception as e:
        manager.viewers.discard(ws)

@app.post("/api/navigate")
async def navigate(body: dict):
    await manager.ensure_started()
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="URL이 필요합니다.")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    try:
        await manager.page.goto(url, wait_until="domcontentloaded", timeout=20000)
        manager._last_url = url
    except Exception as e:
        return {"ok": False, "error": str(e), "url": manager.page.url if manager.page else url}

    return {
        "ok": True,
        "url": manager.page.url,
        "title": await manager.page.title()
    }

@app.post("/api/snapshot")
async def snapshot():
    await manager.ensure_started()
    return await manager.extract_snapshot()

@app.post("/api/click")
async def click(body: dict):
    await manager.ensure_started()
    selector = body.get("selector", "") or body.get("ref", "")
    if not selector:
        raise HTTPException(status_code=400, detail="selector 또는 ref가 필요합니다.")
    return await manager.click_ref_or_selector(selector)

@app.post("/api/type")
async def type_text(body: dict):
    await manager.ensure_started()
    text = body.get("text", "")
    selector = body.get("selector", "") or body.get("ref", "")
    return await manager.type_ref_or_selector(selector, text)

@app.post("/api/scroll")
async def scroll(body: dict):
    await manager.ensure_started()
    direction = body.get("direction", "down")
    pixels = int(body.get("pixels", 500))
    return await manager.scroll(direction, pixels)

@app.post("/api/back")
async def go_back():
    await manager.ensure_started()
    if not manager.page:
        return {"ok": False, "error": "Page not open"}
    try:
        await manager.page.go_back(wait_until="domcontentloaded", timeout=10000)
        return {"ok": True, "url": manager.page.url, "title": await manager.page.title()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/forward")
async def go_forward():
    await manager.ensure_started()
    if not manager.page:
        return {"ok": False, "error": "Page not open"}
    try:
        await manager.page.go_forward(wait_until="domcontentloaded", timeout=10000)
        return {"ok": True, "url": manager.page.url, "title": await manager.page.title()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/press")
async def press(body: dict):
    await manager.ensure_started()
    key = body.get("key", "Enter")
    try:
        await manager.page.keyboard.press(key)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/text")
async def get_text():
    await manager.ensure_started()
    try:
        title = await manager.page.title()
        url = manager.page.url
        text = await manager.page.inner_text("body")
        return {"ok": True, "title": title, "url": url, "text": text[:15000]}
    except Exception as e:
        return {"ok": False, "error": str(e), "text": ""}

@app.post("/api/screenshot")
async def screenshot():
    await manager.ensure_started()
    try:
        data = await manager.page.screenshot(type="png")
        return {"ok": True, "image": base64.b64encode(data).decode()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/eval")
async def js_eval(body: dict):
    await manager.ensure_started()
    js_code = body.get("js", "")
    try:
        result = await manager.page.evaluate(js_code)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("BROWSER_AGENT_PORT", 8088))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

