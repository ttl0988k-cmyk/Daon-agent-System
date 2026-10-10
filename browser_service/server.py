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
                ]
            )
            self.context = await self.browser.new_context(
                viewport=VIEWPORT,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            self.page = await self.context.new_page()
            self.cdp = await self.context.new_cdp_session(self.page)

            # CDP 화면 송출 리스너 등록
            self.cdp.on("Page.screencastFrame", self._on_frame)
            await self.cdp.send("Page.startScreencast", {
                "format": "jpeg",
                "quality": 75,
                "maxWidth": VIEWPORT["width"],
                "maxHeight": VIEWPORT["height"],
                "everyNthFrame": 2,
            })
            self.is_running = True
            print("[BrowserService] ✅ 브라우저 및 CDP 스트리밍 준비 완료", flush=True)

    async def stop(self):
        async with self._lock:
            if not self.is_running:
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

    async def handle_input(self, ev):
        if not self.is_running or not self.cdp:
            return

        x = ev.get("x", 0) * VIEWPORT["width"]
        y = ev.get("y", 0) * VIEWPORT["height"]
        kind = ev.get("t")

        try:
            if kind == "mouse":
                mtype = {"move": "mouseMoved", "down": "mousePressed", "up": "mouseReleased"}.get(ev.get("kind"), "mouseMoved")
                params = {"type": mtype, "x": x, "y": y}
                if ev.get("kind") != "move":
                    params["button"] = "left"
                    params["clickCount"] = 1
                await self.cdp.send("Input.dispatchMouseEvent", params)
            elif kind == "key":
                params = {
                    "type": "keyDown" if ev.get("kind") == "down" else "keyUp",
                    "key": ev.get("key", "")
                }
                if ev.get("code"):
                    params["code"] = ev["code"]
                if ev.get("text"):
                    params["text"] = ev["text"]
                await self.cdp.send("Input.dispatchKeyEvent", params)
            elif kind == "wheel":
                await self.cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": 0,
                    "deltaY": ev.get("deltaY", 100),
                })
        except Exception as e:
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
        "viewers_count": len(manager.viewers)
    }

@app.post("/api/session/start")
async def session_start(body: dict = None):
    await manager.ensure_started()
    url = (body or {}).get("url")
    if url and manager.page:
        try:
            await manager.page.goto(url, wait_until="domcontentloaded", timeout=15000)
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

