"""
Demo-to-Skill (Record & Replay) — Programming by Demonstration Pipeline.

Architecture:
  1. User starts recording → system opens/connects to browser via CDP
  2. User demonstrates workflow (clicks, inputs, navigations)
  3. System captures raw CDP events in real-time
  4. User stops recording → events are batched and sent to LLM
  5. LLM analyzes events, extracts semantic intent, identifies variables
  6. LLM generates a reusable Skill (YAML frontmatter + markdown body)
  7. Skill is saved to ~/.hermes/skills/ and registered as DRAFT
  8. User can review, edit, promote the Skill

Event Sources:
  - CDP (Chrome DevTools Protocol) — browser automation observation
  - Text — user manually describes steps for non-browser workflows
  - MCP — capture MCP tool calls made during a session (future)

Usage:
    from api.demo_to_skill import DemoToSkillSession, get_recording_manager
    mgr = get_recording_manager()
    session_id = mgr.start_session(name="My Workflow", source="cdp", cdp_port=9222)
    # ... user demonstrates ...
    skill_path = mgr.stop_session(session_id)  # triggers LLM analysis
"""
import json
import logging
import os
import re
import socket
import ssl
import sys
import subprocess
import tempfile
import threading
import time
import hashlib
from pathlib import Path
from typing import Optional, Callable

_logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================
SKILL_ANALYSIS_SYSTEM_PROMPT = """You are a Skill Distiller — an expert at observing user demonstrations and converting them into reusable, executable Playwright automation skills.

Your task: Analyze a sequence of captured USER INTERACTION events (clicks, inputs, navigations) from a user demonstration and produce a complete, executable Skill definition.

## What makes a good Skill:
1. **Intent, not coordinates**: Describe WHAT the user is doing, not just WHERE they clicked. "Navigate to project settings" not "Click at (342, 567)".
2. **Generalize variables**: Identify which values are fixed vs which are parameters. A "user email" should be a variable, not hardcoded.
3. **Executable Playwright code**: The body MUST include a ```python code block with an async Playwright function that can replay the workflow.
4. **Specify inputs/outputs**: What does the user need to provide? What will they get back?
5. **Include success criteria**: How do we know the workflow completed successfully?

## Output Format:
You MUST respond with a valid JSON object containing two fields:
- "frontmatter": A dictionary of YAML frontmatter fields
- "body": The markdown body content with Playwright automation code

The frontmatter MUST include:
- name: kebab-case skill name
- version: "1.0"
- category: one of [browser-automation, data-processing, development, deployment, testing, communication, general]
- priority: one of [high, medium, low]
- tags: list of relevant tags
- purpose: WHY this skill exists (one sentence)
- when_to_use: WHEN to apply this skill
- when_not_to_use: when NOT to use
- inputs: what inputs are needed (parameterized, with descriptions)
- outputs: what the skill produces
- success_criteria: how to verify success

## Body Format:
The body MUST include:
1. A brief description of the workflow
2. A "## Variables" section listing all parameterized inputs
3. A "## Playwright Script" section with an executable async Python function:

```python
async def run(page, variables: dict):
    await page.goto(variables.get("url", "https://example.com"))
    await page.click('selector')
    await page.fill('selector', variables.get("input_value", ""))
    await page.keyboard.press("Enter")
    await page.wait_for_load_state("networkidle")
```

4. A "## Success Criteria" section

IMPORTANT: Use the most stable selector available.
Prioritize Playwright locators in this order:
1. `get_by_role("button", name="...")` or `get_by_text("...")`
2. `get_by_label("...")`
3. `get_by_placeholder("...")`
4. Generic CSS/XPath as a last resort.

Use {variable_name} syntax for parameterized values.
Focus on the USER'S INTENT and generate EXECUTABLE code."""

# Injected JavaScript: captures real user interactions with rich element context.
# Uses console.log as transport to CDP. Installs on every page load via
# Page.addScriptToEvaluateOnLoad so it survives navigations.
DAON_CAPTURE_SCRIPT = """
(function(){
if(window.__daonInstalled)return;
window.__daonInstalled=true;
function _sel(el){
  if(!el||el===document.body)return'body';
  if(el.id)return'#'+el.id;
  var p=[],c=el;
  while(c&&c!==document.body&&c.nodeType===1){
    var t=c.tagName.toLowerCase();
    if(c.id){p.unshift('#'+c.id);break;}
    var pa=c.parentElement;
    if(pa){
      var s=Array.from(pa.children).filter(function(x){return x.tagName===c.tagName});
      if(s.length>1)t+=':nth-of-type('+(s.indexOf(c)+1)+')';
    }
    p.unshift(t);
    c=pa;
  }
  return p.join(' > ');
}
function _xp(el){
  if(!el)return'';
  if(el.id)return'//*[@id="'+el.id.replace(/"/g,'\\"')+'"]';
  var parts=[],c=el;
  while(c&&c.nodeType===1){
    var t=c.tagName.toLowerCase(),idx=1,sib=c.previousElementSibling;
    while(sib){if(sib.tagName===c.tagName)idx++;sib=sib.previousElementSibling;}
    parts.unshift(t+'['+idx+']');
    c=c.parentElement;
  }
  return'/'+parts.join('/');
}
function _cap(e){
  var el=e.target,d=(el&&el.nodeType===1)?el:document.body;
  try{
    var data={
      type:e.type,
      url:location.href,
      title:document.title,
      ts:Date.now(),
      tag:d.tagName?d.tagName.toLowerCase():'',
      id:d.id||'',
      cls:(typeof d.className==='string')?d.className:'',
      text:(d.textContent||'').trim().substring(0,200),
      role:d.getAttribute('role')||'',
      aria:d.getAttribute('aria-label')||'',
      sel:_sel(d),
      xpath:_xp(d),
      val:(d.value!==undefined&&d.value!==null)?String(d.value).substring(0,200):'',
      ph:d.getAttribute('placeholder')||'',
      typeAttr:d.getAttribute('type')||''
    };
    if(e.type==='click'||e.type==='dblclick'){
      data.x=e.clientX;data.y=e.clientY;data.btn=e.button;
    }
    console.log('__DAON_EVENT__'+JSON.stringify(data));
  }catch(ex){}
}
document.addEventListener('click',_cap,true);
document.addEventListener('dblclick',_cap,true);
document.addEventListener('change',_cap,true);
document.addEventListener('input',function(e){
  var el=e.target;
  if(el._dti)clearTimeout(el._dti);
  el._dti=setTimeout(function(){_cap(e)},500);
},true);
document.addEventListener('submit',_cap,true);
var _st;
document.addEventListener('scroll', function(e){
  if(_st) clearTimeout(_st);
  _st = setTimeout(function(){
    try {
      console.log('__DAON_EVENT__'+JSON.stringify({
        type: 'scroll',
        url: location.href,
        ts: Date.now(),
        scrollY: window.scrollY
      }));
    } catch(ex){}
  }, 1000);
}, true);
console.log('__DAON_READY__');
})();
"""


# =============================================================================
# Lightweight WebSocket Client (stdlib only, no external deps)
# =============================================================================
class _SimpleWebSocket:
    """Minimal WebSocket client using only stdlib (socket + ssl).

    Used to connect to Chrome DevTools Protocol without external dependencies.
    """

    def __init__(self, url: str):
        self.url = url
        self.sock: Optional[socket.socket] = None
        self._ssl_sock = None
        self._recv_buffer = b""
        self._lock = threading.Lock()
        self._running = False

    def connect(self, timeout: float = 5.0) -> bool:
        """Perform WebSocket handshake and connect."""
        try:
            parsed = self.url.replace("ws://", "http://").replace("wss://", "https://")
            from urllib.parse import urlparse
            u = urlparse(parsed)
            host = u.hostname
            port = u.port or (443 if self.url.startswith("wss") else 80)
            path = u.path or "/"
            if u.query:
                path += "?" + u.query

            sock = socket.create_connection((host, port), timeout=timeout)

            if self.url.startswith("wss"):
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=host)

            # WebSocket handshake
            key = os.urandom(16)
            import base64
            key_b64 = base64.b64encode(key).decode()

            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key_b64}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            sock.sendall(request.encode())

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    sock.close()
                    return False
                response += chunk

            # Check upgrade response
            if b"101" not in response.split(b"\r\n")[0]:
                sock.close()
                return False

            self.sock = sock
            self._running = True
            return True
        except Exception as e:
            _logger.warning("[DemoToSkill] WebSocket connect failed: %s", e)
            if self.sock:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
            return False

    def send(self, message: str) -> None:
        """Send a text frame.

        RFC 6455 §5.1: client-to-server frames MUST be masked.
        """
        if not self.sock:
            return
        with self._lock:
            try:
                data = message.encode("utf-8")
                frame = bytearray()
                frame.append(0x81)  # FIN + text opcode

                length = len(data)
                if length < 126:
                    frame.append(0x80 | length)   # MASK bit set
                elif length < 65536:
                    frame.append(0x80 | 126)      # MASK bit set
                    frame.extend(length.to_bytes(2, "big"))
                else:
                    frame.append(0x80 | 127)      # MASK bit set
                    frame.extend(length.to_bytes(8, "big"))

                # Generate 4-byte masking key and XOR payload
                mask_key = os.urandom(4)
                frame.extend(mask_key)
                masked = bytearray(data)
                for i in range(len(masked)):
                    masked[i] ^= mask_key[i % 4]
                frame.extend(masked)

                self.sock.sendall(bytes(frame))
            except Exception as e:
                _logger.warning("[DemoToSkill] WebSocket send failed: %s", e)
                self._running = False

    def recv(self, timeout: float = 1.0) -> Optional[str]:
        """Receive a text frame. Returns None on timeout or error."""
        if not self.sock or not self._running:
            return None
        try:
            self.sock.settimeout(timeout)
            # Read first 2 bytes
            header = self._recv_exact(2)
            if header is None:
                return None

            opcode = header[0] & 0x0F
            masked = (header[1] & 0x80) != 0
            length = header[1] & 0x7F

            if length == 126:
                ext = self._recv_exact(2)
                if ext is None:
                    return None
                length = int.from_bytes(ext, "big")
            elif length == 127:
                ext = self._recv_exact(8)
                if ext is None:
                    return None
                length = int.from_bytes(ext, "big")

            mask_key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length)
            if payload is None:
                return None

            if mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

            if opcode == 0x01:  # Text
                return payload.decode("utf-8", errors="replace")
            elif opcode == 0x08:  # Close
                self._running = False
                return None
            elif opcode == 0x09:  # Ping
                self._send_pong(payload)
                return self.recv(timeout)
            else:
                return self.recv(timeout)

        except socket.timeout:
            return None
        except Exception as e:
            _logger.debug("[DemoToSkill] WebSocket recv error: %s", e)
            return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Receive exactly n bytes."""
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                return None
        return data

    def _send_pong(self, payload: bytes) -> None:
        """Send a pong frame (client-to-server, MUST be masked per RFC 6455)."""
        if not self.sock:
            return
        try:
            frame = bytearray()
            frame.append(0x8A)  # FIN + pong opcode
            length = len(payload)
            if length < 126:
                frame.append(0x80 | length)
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(length.to_bytes(2, "big"))
            else:
                frame.append(0x80 | 127)
                frame.extend(length.to_bytes(8, "big"))
            mask_key = os.urandom(4)
            frame.extend(mask_key)
            masked = bytearray(payload)
            for i in range(len(masked)):
                masked[i] ^= mask_key[i % 4]
            frame.extend(masked)
            self.sock.sendall(bytes(frame))
        except Exception:
            pass

    def close(self) -> None:
        """Close the connection (close frame MUST be masked per RFC 6455)."""
        self._running = False
        if self.sock:
            try:
                # Send close frame with mask
                frame = bytearray([0x88, 0x80, 0x00, 0x00, 0x00, 0x00])
                self.sock.sendall(bytes(frame))
            except Exception:
                pass
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# =============================================================================
# CDP Event Collector
# =============================================================================
class CDPEventCollector:
    """Capture browser events via Chrome DevTools Protocol.

    Connects to a Chrome instance running with --remote-debugging-port=PORT.
    Captures: clicks, inputs, navigations, console messages, DOM mutations.

    Event enrichment: Injects a JavaScript interceptor (DAON_CAPTURE_SCRIPT)
    on every page load via Page.addScriptToEvaluateOnNewDocument. This script
    listens for click / dblclick / change / input / submit events and logs
    rich element context (url, title, selector, xpath, aria, text, role, etc.)
    via console.log with a __DAON_EVENT__ prefix. Those prefixed console
    messages are parsed by _normalize_event into the enriched event format.

    Optional per-step data:
      - capture_screenshots: calls Page.captureScreenshot after each event
      - capture_dom: calls DOM.getDocument after each event
    These are base64-encoded and appended to the event dict for Replay debugging.
    """

    # CDP domains to subscribe to
    WATCH_DOMAINS = ["Runtime", "Page", "DOM", "Input", "Network"]

    def __init__(self, cdp_host: str = "localhost", cdp_port: int = 9222,
                 on_event: Callable = None,
                 capture_screenshots: bool = False,
                 capture_dom: bool = False,
                 auto_launch: bool = True):
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port
        self.on_event = on_event
        self.capture_screenshots = capture_screenshots
        self.capture_dom = capture_dom
        self.auto_launch = auto_launch
        self.ws: Optional[_SimpleWebSocket] = None
        self._msg_id = 0
        self._msg_lock = threading.Lock()
        self._collecting = False
        self._events: list[dict] = []
        self._thread: Optional[threading.Thread] = None
        self._page_target_id: Optional[str] = None
        # Browser subprocess reference (for auto-launch cleanup)
        self._browser_process = None  # subprocess.Popen for launched Chrome
        self._temp_user_data_dir = None

    def _next_id(self) -> int:
        with self._msg_lock:
            self._msg_id += 1
            return self._msg_id

    def _list_targets(self) -> list[dict]:
        """List available CDP targets via HTTP."""
        try:
            import urllib.request
            url = f"http://{self.cdp_host}:{self.cdp_port}/json"
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            _logger.warning("[DemoToSkill] Cannot list CDP targets: %s", e)
            return []

    @staticmethod
    def _find_system_chrome():
        """Locate Chrome/Edge executable on the system. Returns (path, None) or (None, None)."""
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
                ['reg', 'query',
                 r'HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
                 '/ve'],
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

    def _launch_browser(self) -> bool:
        """Automatically launch Chrome in debug mode via subprocess.

        Uses a temporary user-data-dir so it doesn't conflict with the
        user's normal Chrome profile. Opens about:blank by default.

        Returns True if browser was launched (or we connected to an existing one).
        """
        if self._find_page_target():
            _logger.info("[DemoToSkill] Already have a CDP target on port %d; skip launch.", self.cdp_port)
            return True

        chrome_path, _ = self._find_system_chrome()
        if not chrome_path:
            _logger.warning("[DemoToSkill] Could not find Chrome/Edge on this system.")
            return False

        _logger.info("[DemoToSkill] Auto-launching browser on port %d: %s", self.cdp_port, chrome_path)

        try:
            # Create a temp user-data-dir to avoid profile conflicts
            import tempfile as _tmpfile
            temp_dir = _tmpfile.mkdtemp(prefix="daon_chrome_")
            self._temp_user_data_dir = temp_dir

            cmd = [
                chrome_path,
                f"--remote-debugging-port={self.cdp_port}",
                f"--user-data-dir={temp_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "about:blank",
            ]

            # Launch Chrome as a detached subprocess
            self._browser_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            _logger.info("[DemoToSkill] Browser launched with PID %d", self._browser_process.pid)

            # Wait for CDP endpoint to become available
            deadline = time.time() + 15.0
            while time.time() < deadline:
                time.sleep(0.5)
                if self._find_page_target():
                    _logger.info("[DemoToSkill] CDP target available after browser launch")
                    return True

            _logger.error("[DemoToSkill] Browser launched but CDP target never appeared on port %d (waited 15s)", self.cdp_port)
            self._cleanup_browser()
            return False

        except Exception as e:
            _logger.error("[DemoToSkill] Failed to auto-launch browser: %s", e, exc_info=True)
            self._cleanup_browser()
            return False

    def _cleanup_browser(self):
        """Clean up auto-launched browser subprocess (non-blocking best-effort)."""
        proc = self._browser_process
        self._browser_process = None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        # Clean up temp user-data-dir
        temp_dir = getattr(self, '_temp_user_data_dir', None)
        if temp_dir:
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
                self._temp_user_data_dir = None
            except Exception:
                pass

    def _find_page_target(self) -> Optional[str]:
        """Find a Page target WebSocket URL.

        Prefer the target matching our about:blank (or the one with
        lowest frame-count / simplest page), so we never attach to
        Chrome's internal welcome / new-tab pages.
        """
        targets = self._list_targets()
        candidates = [t for t in targets
                      if t.get("type") == "page" and "webSocketDebuggerUrl" in t]
        if not candidates:
            _logger.warning("[DemoToSkill] No page-type candidates with WebSocket URL!")
            return None

        # Explicitly pick our own about:blank target first
        for t in candidates:
            if "about:blank" in (t.get("url", "") or ""):
                return t["webSocketDebuggerUrl"]

        # Fallback: pick the simplest page (fewest frames)
        candidates.sort(key=lambda t: 999 if "chrome://" in (t.get("url", "") or "") else 0)
        t = candidates[0]
        return t["webSocketDebuggerUrl"]

    def start(self) -> bool:
        """Connect to CDP, inject capture script, begin event collection.

        If auto_launch is True and no existing CDP target is found, this will
        automatically launch Chrome in debug mode via subprocess.
        """
        ws_url = self._find_page_target()
        if not ws_url:
            if self.auto_launch:
                _logger.info("[DemoToSkill] No CDP target found. Attempting auto-launch...")
                if not self._launch_browser():
                    _logger.warning("[DemoToSkill] Auto-launch failed. "
                                   "Start Chrome manually: chrome --remote-debugging-port=%d",
                                   self.cdp_port)
                    return False
                ws_url = self._find_page_target()
                if not ws_url:
                    _logger.error("[DemoToSkill] Auto-launch succeeded but still no CDP target.")
                    return False
            else:
                _logger.warning("[DemoToSkill] No CDP page target found on port %d. "
                               "Start Chrome with: chrome --remote-debugging-port=%d",
                               self.cdp_port, self.cdp_port)
                return False

        self.ws = _SimpleWebSocket(ws_url)
        if not self.ws.connect(timeout=5.0):
            _logger.warning("[DemoToSkill] Failed to connect to CDP WebSocket")
            self.ws = None
            if self.auto_launch:
                self._cleanup_browser()
            return False

        # Enable required domains
        for domain in self.WATCH_DOMAINS:
            self._send_cdp(f"{domain}.enable", {})

        # Enable runtime event collection
        self._send_cdp("Runtime.runIfWaitingForDebugger", {})

        # Inject the capture JavaScript on every (re)load so navigations
        # automatically wire up listeners on each new page.
        self._send_cdp("Page.addScriptToEvaluateOnNewDocument", {
            "source": DAON_CAPTURE_SCRIPT,
        })

        # Also inject right now on the already-loaded page (if any)
        self._send_cdp("Runtime.evaluate", {
            "expression": DAON_CAPTURE_SCRIPT,
        })

        self._collecting = True
        self._events = []
        self._thread = threading.Thread(target=self._event_loop, daemon=True)
        self._thread.start()
        _logger.info("[DemoToSkill] CDP event collection started (screenshots=%s, dom=%s)",
                     self.capture_screenshots, self.capture_dom)
        return True

    def _send_cdp(self, method: str, params: dict = None) -> Optional[int]:
        """Send a CDP command, return message id."""
        if not self.ws:
            return None
        msg_id = self._next_id()
        msg = json.dumps({"id": msg_id, "method": method, "params": params or {}})
        self.ws.send(msg)
        return msg_id

    def _capture_screenshot(self) -> Optional[str]:
        """Capture a base64-encoded PNG screenshot via CDP. Returns base64 string or None."""
        if not self.ws or not self._collecting:
            return None
        try:
            msg_id = self._send_cdp("Page.captureScreenshot", {"format": "png"})
            # We need to wait for the response; this is a sync-style helper
            # that polls recv until the matching id is found.
            deadline = time.time() + 5.0
            while time.time() < deadline:
                raw = self.ws.recv(timeout=0.3)
                if raw is None:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == msg_id:
                    result = msg.get("result", {})
                    return result.get("data")  # base64 PNG
            return None
        except Exception as e:
            _logger.debug("[DemoToSkill] Screenshot failed: %s", e)
            return None

    def _capture_dom_snapshot(self) -> Optional[dict]:
        """Capture the DOM tree via CDP. Returns the DOM.getDocument result dict or None."""
        if not self.ws or not self._collecting:
            return None
        try:
            msg_id = self._send_cdp("DOM.getDocument", {"depth": -1, "pierce": True})
            deadline = time.time() + 10.0
            while time.time() < deadline:
                raw = self.ws.recv(timeout=0.3)
                if raw is None:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == msg_id:
                    return msg.get("result", {})
            return None
        except Exception as e:
            _logger.debug("[DemoToSkill] DOM snapshot failed: %s", e)
            return None

    def _event_loop(self) -> None:
        """Background thread: continuously read CDP events."""
        while self._collecting and self.ws:
            raw = self.ws.recv(timeout=0.5)
            if raw is None:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Skip command responses (have 'id' field) — these are handled
            # by synchronous helpers (_capture_screenshot, _capture_dom_snapshot)
            # which poll with their own recv calls. However, command responses
            # may still arrive here; skip them silently.
            if "id" in msg and "method" not in msg:
                continue

            method = msg.get("method", "")
            params = msg.get("params", {})

            # Filter to interesting events
            event = self._normalize_event(method, params)
            if event:
                # Optional per-step enrichment
                if self.capture_screenshots:
                    screenshot = self._capture_screenshot()
                    if screenshot:
                        event["_screenshot"] = screenshot
                if self.capture_dom:
                    dom = self._capture_dom_snapshot()
                    if dom:
                        event["_dom_snapshot"] = dom
                self._events.append(event)
                if self.on_event:
                    try:
                        self.on_event(event)
                    except Exception:
                        pass

    def _normalize_event(self, method: str, params: dict) -> Optional[dict]:
        """Convert a raw CDP event into a semantically rich observation.

        Two sources of user-interaction events:
          1. __DAON_EVENT__ prefixed console.log messages injected by the
             JavaScript interceptor (DAON_CAPTURE_SCRIPT) — these carry full
             element context (url, title, selector, xpath, aria, text, role).
             Parsed as type == 'click' / 'dblclick' / 'change' / 'input' / 'submit'.

          2. Native CDP events (Page.frameNavigated, Page.loadEventFired,
             Network.requestWillBeSent, DOM.childNodeInserted, and generic
             console messages) — used for page lifecycle and network tracking.
        """
        ts = params.get("timestamp", time.time() * 1000)

        # ── Enriched user-interaction events (from injected JS via console.log) ──
        if method == "Runtime.consoleAPICalled":
            args = params.get("args", [])
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)

            # DAON readiness heartbeat — ignore, not an actionable event
            if text.strip() == "__DAON_READY__":
                return None

            # DAON enriched event
            if text.startswith("__DAON_EVENT__"):
                try:
                    data = json.loads(text[len("__DAON_EVENT__"):])
                except json.JSONDecodeError:
                    _logger.debug("[DemoToSkill] Malformed __DAON_EVENT__: %.120s", text)
                    return None

                event_type = data.get("type", "")

                # Base event with full context
                event = {
                    "type": event_type,
                    "url": data.get("url", ""),
                    "title": data.get("title", ""),
                    "selector": data.get("sel", ""),
                    "xpath": data.get("xpath", ""),
                    "tag": data.get("tag", ""),
                    "id": data.get("id", ""),
                    "class": data.get("cls", ""),
                    "text": data.get("text", ""),
                    "role": data.get("role", ""),
                    "aria": data.get("aria", ""),
                    "value": data.get("val", ""),
                    "placeholder": data.get("ph", ""),
                    "type_attr": data.get("typeAttr", ""),
                    "timestamp": data.get("ts", ts),
                    "source": "daon_injected",
                }

                # Click-specific
                if event_type in ("click", "dblclick"):
                    event["x"] = data.get("x", 0)
                    event["y"] = data.get("y", 0)
                    event["button"] = data.get("btn", 0)

                return event

            # Generic console messages (non-DAON)
            msg_type = params.get("type", "")
            return {
                "type": "console",
                "level": msg_type,
                "text": text[:500],
                "timestamp": ts,
                "source": "cdp",
            }

        # ── Native CDP lifecycle events ──
        if method == "Page.frameNavigated":
            url = params.get("frame", {}).get("url", "")
            return {
                "type": "navigation",
                "url": url,
                "timestamp": ts,
                "source": "cdp",
            }

        if method == "Page.loadEventFired":
            return {
                "type": "page_load",
                "timestamp": ts,
                "source": "cdp",
            }

        # DOM mutation events
        if method == "DOM.childNodeInserted":
            node = params.get("node", {})
            node_name = node.get("nodeName", "").lower()
            return {
                "type": "dom_insert",
                "node_name": node_name,
                "timestamp": ts,
                "source": "cdp",
            }

        # Network requests (detect form submissions, API calls)
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            return {
                "type": "network_request",
                "url": request.get("url", ""),
                "method": request.get("method", "GET"),
                "timestamp": ts,
                "source": "cdp",
            }

        return None

    def stop(self) -> list[dict]:
        """Stop collecting and return captured events."""
        self._collecting = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self.ws:
            self.ws.close()
            self.ws = None
        events = list(self._events)
        # Clean up auto-launched browser if we own it
        if self.auto_launch and self._browser_process is not None:
            _logger.info("[DemoToSkill] Closing auto-launched browser...")
            self._cleanup_browser()
        _logger.info("[DemoToSkill] CDP collection stopped. Captured %d events.", len(events))
        return events

    def is_collecting(self) -> bool:
        return self._collecting


# =============================================================================
# Skill Analyzer (LLM-based)
# =============================================================================
class SkillAnalyzer:
    """Convert captured events into a reusable Skill using LLM analysis."""

    @staticmethod
    def analyze(events: list[dict], skill_name: str = "", 
                preferred_model: str = None, log_callback: Callable = None) -> dict:
        """Analyze events and generate a Skill definition.

        Returns: {"frontmatter": {...}, "body": "..."}
        """
        # Format events for the LLM
        event_summary = SkillAnalyzer._format_event_summary(events)

        prompt = f"""## User Demonstration Analysis

The following events were captured during a user's workflow demonstration.
Analyze the sequence to understand the INTENT and produce a reusable Skill.

### Skill Name Hint
{skill_name or '(infer from the workflow)'}

### Captured Event Sequence
{event_summary}

### Analysis Task
1. Identify the OVERALL GOAL of this workflow (not just step-by-step)
2. Group events into logical PHASES (e.g., "Login", "Navigate to Dashboard", "Submit Form")
3. Identify VARIABLES — which values change between runs vs which are fixed
4. Identify the TRIGGER — when would someone want to run this workflow?
5. Determine SUCCESS CRITERIA — how do we know it completed correctly?

Based on your analysis, generate a complete, editable, reusable Skill.
Output ONLY valid JSON with "frontmatter" and "body" fields."""

        if log_callback:
            log_callback("SkillAnalyzer", "🔍 분석 중: 사용자 시연에서 워크플로우 패턴 추출...", "running")

        try:
            result_json = _call_llm_direct(
                prompt=prompt,
                system_instruction=SKILL_ANALYSIS_SYSTEM_PROMPT,
                preferred_model=preferred_model,
            )
            parsed = _extract_json_from_response(result_json)

            if not parsed or "frontmatter" not in parsed:
                if log_callback:
                    log_callback("SkillAnalyzer", "⚠️ LLM 응답 파싱 실패, 기본 Skill 생성", "error")
                return SkillAnalyzer._fallback_skill(events, skill_name)

            if log_callback:
                log_callback("SkillAnalyzer", f"✅ Skill 분석 완료: {parsed.get('frontmatter', {}).get('name', '?')}", "done")

            return parsed
        except Exception as e:
            _logger.warning("[DemoToSkill] LLM analysis failed: %s", e)
            if log_callback:
                log_callback("SkillAnalyzer", f"⚠️ 분석 실패: {e}", "error")
            return SkillAnalyzer._fallback_skill(events, skill_name)

    @staticmethod
    def _format_event_summary(events: list[dict]) -> str:
        """Convert enriched events into a human-readable summary for the LLM.

        The enriched format gives the LLM enough context to infer
        INTENT — what the user is actually trying to accomplish —
        rather than just raw (x, y) coordinates.
        """
        if not events:
            return "(No events captured)"

        def _describe(ev: dict) -> str:
            """Build a detailed description string for a single event."""
            etype = ev.get("type", "?")

            # Enriched interaction events (from injected JS — daon_injected source)
            if ev.get("source") == "daon_injected":
                tag = ev.get("tag", "")
                selector = ev.get("selector", "")
                text = ev.get("text", "")[:100]
                rid = ev.get("id", "")
                role = ev.get("role", "")
                aria = ev.get("aria", "")
                val = ev.get("value", "")
                ph = ev.get("placeholder", "")
                url = ev.get("url", "")
                title = ev.get("title", "")
                type_attr = ev.get("type_attr", "")

                # Build element descriptor
                el_desc = f"<{tag}"
                if rid:
                    el_desc += f' id="{rid}"'
                if type_attr:
                    el_desc += f' type="{type_attr}"'
                if role:
                    el_desc += f' role="{role}"'
                if aria:
                    el_desc += f' aria-label="{aria}"'
                if ph:
                    el_desc += f' placeholder="{ph}"'
                el_desc += ">"

                # Build content hints
                hints = []
                if text:
                    hints.append(f'text="{text}"')
                if val:
                    hints.append(f'value="{val}"')
                hint_str = " | ".join(hints) if hints else ""

                # Page context
                page = url
                if title:
                    page += f' (title: "{title}")'

                if etype == "click" or etype == "dblclick":
                    x, y, btn = ev.get("x", "?"), ev.get("y", "?"), ev.get("button", "?")
                    return (
                        f"  🖱️ {etype.upper()}: {el_desc} at ({x},{y}) btn={btn} "
                        f"{'| ' + hint_str if hint_str else ''}"
                        f"\n     selector: {selector}"
                        f"\n     page: {page}"
                    )
                elif etype == "change":
                    return (
                        f"  ✏️ CHANGE: {el_desc} {hint_str}\n"
                        f"     selector: {selector}\n"
                        f"     page: {page}"
                    )
                elif etype == "input":
                    return (
                        f"  ⌨️ INPUT: {el_desc} {hint_str}\n"
                        f"     selector: {selector}\n"
                        f"     page: {page}"
                    )
                elif etype == "submit":
                    return (
                        f"  📤 SUBMIT: {el_desc} {hint_str}\n"
                        f"     selector: {selector}\n"
                        f"     page: {page}"
                    )
                elif etype == "scroll":
                    return f"  📜 SCROLL: Y={ev.get('scrollY', '?')} \n     page: {page}"
                else:
                    return (
                        f"  📌 {etype}: {el_desc} {hint_str}\n"
                        f"     selector: {selector}\n"
                        f"     page: {page}"
                    )

            # Native CDP events
            if etype == "navigation":
                return f"  🌐 Navigate to: {ev.get('url', '?')}"
            if etype == "page_load":
                return f"  📄 Page loaded"
            if etype == "network_request":
                url_short = ev.get("url", "")[:80]
                return f"  🌍 {ev.get('method', '?')} {url_short}"
            if etype == "dom_insert":
                return f"  ➕ DOM element added: <{ev.get('node_name', '?')}>"
            if etype == "console":
                return f"  💬 Console [{ev.get('level', '?')}]: {ev.get('text', '')[:150]}"

            return f"  ❓ {etype}: {json.dumps(ev, ensure_ascii=False)[:120]}"

        # Filter: only user interactions (daon_injected) + navigation/page_load/scroll
        # Exclude network_request, console, dom_insert noise
        user_events = [
            e for e in events
            if e.get("source") == "daon_injected"
            or e.get("type") in ("navigation", "page_load", "scroll")
        ]

        if not user_events:
            return "(No user interaction events captured)"

        lines = []
        for i, ev in enumerate(user_events[:100]):  # Cap at 100 user events
            lines.append(f"  [{i}] {_describe(ev)}")

        if len(user_events) > 100:
            lines.append(f"  ... ({len(user_events) - 100} more user events omitted)")

        # Add summary stats
        total_network = sum(1 for e in events if e.get("type") == "network_request")
        lines.append(f"\n  [Stats] {len(user_events)} user actions, {total_network} network requests (filtered out)")

        return "\n".join(lines)

    @staticmethod
    def _fallback_skill(events: list[dict], skill_name: str = "") -> dict:
        """Generate a Playwright-based fallback skill when LLM analysis fails."""
        name = skill_name or f"captured-workflow-{int(time.time())}"
        name_slug = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')[:50]

        # Filter to user interactions only
        user_events = [
            e for e in events
            if e.get("source") == "daon_injected"
            or e.get("type") in ("navigation", "page_load", "scroll")
        ]

        # Infer basic structure
        event_types = set(e.get("type", "") for e in user_events)
        is_browser = any(t in event_types for t in ("navigation", "click", "page_load"))
        category = "browser-automation" if is_browser else "general"
        purpose = f"Automate the captured browser workflow: {name}"

        # Auto-generate Playwright code from user events
        pw_lines = []
        pw_lines.append("async def run(page, variables: dict):")
        pw_lines.append('    """Auto-generated Playwright script from user demonstration."""')

        variables_found = set()
        for ev in user_events:
            etype = ev.get("type", "")
            selector = ev.get("selector", "")
            value = ev.get("value", "")
            url = ev.get("url", "")

            if etype == "navigation" and url:
                pw_lines.append(f'    await page.goto("{url}")')
                pw_lines.append('    await page.wait_for_load_state("networkidle")')
            elif etype == "click" and selector:
                pw_lines.append(f'    await page.click("{selector}")')
            elif etype == "input" and selector:
                var_name = ev.get("id") or ev.get("placeholder", "").lower().replace(" ", "_") or "input_value"
                var_name = re.sub(r'[^a-z0-9_]', '', var_name)[:30] or "input_value"
                variables_found.add(var_name)
                pw_lines.append(f'    await page.fill("{selector}", variables.get("{var_name}", "{value}"))')
            elif etype == "change" and selector:
                pw_lines.append(f'    # Change event on: {selector}')
            elif etype == "submit":
                pw_lines.append('    await page.keyboard.press("Enter")')
                pw_lines.append('    await page.wait_for_load_state("networkidle")')

        if not pw_lines[2:]:
            pw_lines.append('    pass  # No user actions captured')

        playwright_code = "\n".join(pw_lines)

        # Build variables section
        var_lines = []
        for v in sorted(variables_found):
            var_lines.append(f"- `{v}`: (describe this input)")
        if not var_lines:
            var_lines.append("- (No variables detected — review and add as needed)")

        return {
            "frontmatter": {
                "name": name_slug,
                "version": "1.0",
                "category": category,
                "priority": "medium",
                "tags": ["auto-generated", "browser-automation", "playwright"],
                "purpose": purpose,
                "when_to_use": f"When you need to perform the '{name}' workflow",
                "when_not_to_use": "When the target system has changed or is unavailable",
                "inputs": ", ".join(sorted(variables_found)) if variables_found else "Review and identify inputs",
                "outputs": "Completed browser workflow",
                "success_criteria": "All Playwright steps execute without errors",
                "conflicts_with": [],
                "graph_requires": [],
                "graph_compatible": [],
                "graph_conflicts": [],
            },
            "body": f"""# {name} (Auto-Generated)

⚠️ **This skill was auto-generated from a demonstration. Review the Playwright code before using.**

## User Actions Summary
{SkillAnalyzer._format_event_summary(events)}

## Variables
{chr(10).join(var_lines)}

## Playwright Script
```python
{playwright_code}
```

## Success Criteria
- All page navigations complete successfully
- All click/fill actions find their target elements
- No timeout errors during execution
""",
        }


# =============================================================================
# Skill Writer
# =============================================================================
class SkillWriter:
    """Write a generated Skill to disk in the standard format."""

    @staticmethod
    def write(skill_data: dict, skill_name: str = "") -> Path:
        """Write a Skill .md file and return the path.

        Args:
            skill_data: {"frontmatter": {...}, "body": "..."}
            skill_name: Optional override name

        Returns:
            Path to the created .md file
        """
        from api.skill_registry import _resolve_auto_skills_dir, _resolve_profile_auto_skills_dir

        frontmatter = skill_data.get("frontmatter", {})
        body = skill_data.get("body", "")

        name = skill_name or frontmatter.get("name", "auto-skill")
        name_slug = re.sub(r'[^a-z0-9-]', '-', name.lower()).strip('-')[:60]

        # Prefer profile-specific auto dir if a non-default profile is active
        profile_auto_dir = _resolve_profile_auto_skills_dir()
        auto_dir = profile_auto_dir if profile_auto_dir else _resolve_auto_skills_dir()

        # Create skill subdirectory: auto/{name_slug}/SKILL.md
        skill_dir = auto_dir / name_slug
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Avoid name collision
        skill_path = skill_dir / "SKILL.md"
        counter = 1
        while skill_path.exists():
            skill_dir = auto_dir / f"{name_slug}-v{counter}"
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path = skill_dir / "SKILL.md"
            counter += 1

        # Build frontmatter YAML
        fm = frontmatter.copy()
        fm.setdefault("name", name_slug)
        fm.setdefault("version", "1.0")
        fm.setdefault("category", "general")
        fm.setdefault("priority", "medium")
        fm.setdefault("tags", ["auto-generated"])

        yaml_lines = ["---"]
        for key, value in fm.items():
            if isinstance(value, list):
                if value:
                    yaml_lines.append(f"{key}:")
                    for item in value:
                        yaml_lines.append(f"  - {item}")
                else:
                    yaml_lines.append(f"{key}: []")
            elif isinstance(value, str):
                if "\n" in value:
                    yaml_lines.append(f"{key}: |")
                    for line in value.split("\n"):
                        yaml_lines.append(f"  {line}")
                else:
                    yaml_lines.append(f'{key}: "{value}"')
            else:
                yaml_lines.append(f"{key}: {value}")
        yaml_lines.append("---")

        content = "\n".join(yaml_lines) + "\n\n" + body

        skill_path.write_text(content, encoding="utf-8")
        _logger.info("[DemoToSkill] Skill written to: %s", skill_path)

        # Register in manifest as APPROVED (user explicitly triggered save)
        try:
            from api.skill_registry import SkillRegistry, SKILL_REVIEW
            SkillRegistry.register_new_auto_skill(skill_path, lifecycle=SKILL_REVIEW)
        except Exception as e:
            _logger.warning("[DemoToSkill] Failed to register skill in manifest: %s", e)

        return skill_path


# =============================================================================
# Recording Session Manager
# =============================================================================
class RecordingSession:
    """Tracks a single recording session."""

    def __init__(self, session_id: str, name: str, source: str = "cdp",
                 cdp_port: int = 9222,
                 capture_screenshots: bool = False,
                 capture_dom: bool = False):
        self.session_id = session_id
        self.name = name
        self.source = source
        self.cdp_port = cdp_port
        self.capture_screenshots = capture_screenshots
        self.capture_dom = capture_dom
        self.status = "idle"  # idle, recording, analyzing, done, error
        self.events: list[dict] = []
        self.collector: Optional[CDPEventCollector] = None
        self.result_skill_path: Optional[Path] = None
        self.result_skill_data: Optional[dict] = None
        self.created_at = time.time()
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "name": self.name,
            "source": self.source,
            "status": self.status,
            "event_count": len(self.events),
            "cdp_port": self.cdp_port,
            "capture_screenshots": self.capture_screenshots,
            "capture_dom": self.capture_dom,
            "has_result": self.result_skill_path is not None,
            "result_skill": str(self.result_skill_path) if self.result_skill_path else None,
            "result_skill_name": self.result_skill_path.parent.name if self.result_skill_path else None,
            "created_at": self.created_at,
            "error": self.error,
        }


class RecordingManager:
    """Singleton manager for Demo-to-Skill recording sessions."""

    def __init__(self):
        self._sessions: dict[str, RecordingSession] = {}
        self._lock = threading.Lock()

    def start_session(self, name: str = "", source: str = "cdp",
                      cdp_port: int = 9222,
                      capture_screenshots: bool = False,
                      capture_dom: bool = False) -> str:
        """Start a new recording session. Returns session_id.

        Args:
            name: Human-readable session name.
            source: "cdp" (browser) or "text" (manual description).
            cdp_port: Chrome DevTools Protocol port.
            capture_screenshots: If True, capture a screenshot after each event.
            capture_dom: If True, capture a DOM snapshot after each event.
        """
        session_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:12]
        session = RecordingSession(
            session_id=session_id,
            name=name or f"Recording-{session_id[:6]}",
            source=source,
            cdp_port=cdp_port,
            capture_screenshots=capture_screenshots,
            capture_dom=capture_dom,
        )

        with self._lock:
            self._sessions[session_id] = session

        if source == "cdp":
            collector = CDPEventCollector(
                cdp_host="localhost",
                cdp_port=cdp_port,
                capture_screenshots=capture_screenshots,
                capture_dom=capture_dom,
                auto_launch=True,
            )
            session.collector = collector
            success = collector.start()
            if not success:
                session.status = "error"
                session.error = f"Failed to connect to Chrome CDP on port {cdp_port}. Start Chrome with --remote-debugging-port={cdp_port}"
                _logger.warning("[DemoToSkill] %s", session.error)
                return session_id

        session.status = "recording"
        _logger.info("[DemoToSkill] Session '%s' started (source=%s, screenshots=%s, dom=%s)",
                     session_id, source, capture_screenshots, capture_dom)
        return session_id

    def add_text_event(self, session_id: str, description: str) -> bool:
        """Add a manually-described event to a session (for text-based demonstration)."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return False
        event = {
            "type": "text_description",
            "text": description,
            "timestamp": time.time() * 1000,
            "raw": "manual",
        }
        session.events.append(event)
        return True

    def stop_session(self, session_id: str, preferred_model: str = None,
                     log_callback: Callable = None) -> Optional[str]:
        """Stop recording and trigger Skill analysis. Returns skill path or None."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            _logger.warning("[DemoToSkill] Session not found: %s", session_id)
            return None

        # Collect events
        if session.collector:
            cdp_events = session.collector.stop()
            session.events.extend(cdp_events)

        session.status = "analyzing"

        # If no events collected, check if we have text events
        if not session.events:
            _logger.warning("[DemoToSkill] No events captured in session %s", session_id)
            # Still try to create a minimal skill
            pass

        if log_callback:
            log_callback("DemoToSkill",
                        f"📊 {len(session.events)}개 이벤트 수집 완료. LLM 분석 시작...",
                        "running")

        # Analyze and generate skill
        analyzer = SkillAnalyzer()
        skill_data = analyzer.analyze(
            events=session.events,
            skill_name=session.name,
            preferred_model=preferred_model,
            log_callback=log_callback,
        )

        if not skill_data:
            session.status = "error"
            session.error = "LLM analysis returned no result"
            return None

        session.result_skill_data = skill_data

        if log_callback:
            fm = skill_data.get("frontmatter", {})
            log_callback("DemoToSkill",
                        f"📝 Skill 생성 중: {fm.get('name', '?')} [{fm.get('category', '?')}]",
                        "running")

        # Write to disk
        writer = SkillWriter()
        skill_path = writer.write(skill_data, skill_name=session.name)
        session.result_skill_path = skill_path
        session.status = "done"

        if log_callback:
            log_callback("DemoToSkill",
                        f"✅ Skill 저장 완료: {skill_path.name}\n"
                        f"   → ~/.hermes/skills/ 에서 편집 가능 (DRAFT 상태)",
                        "done")

        # Reload skill registry so the new skill is available
        try:
            from api.skill_registry import get_skill_registry
            get_skill_registry().reload()
        except Exception:
            pass

        return str(skill_path)

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get session status."""
        with self._lock:
            session = self._sessions.get(session_id)
        return session.to_dict() if session else None

    def get_session_events(self, session_id: str) -> list[dict]:
        """Get captured events for a session (for live preview)."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return []
        events = list(session.events)
        # If CDP still running, include live events
        if session.collector and session.collector.is_collecting():
            events.extend(session.collector._events)
        return events

    def cancel_session(self, session_id: str) -> bool:
        """Cancel an active recording session."""
        with self._lock:
            session = self._sessions.get(session_id)
        if not session:
            return False
        if session.collector:
            session.collector.stop()
        session.status = "cancelled"
        return True

    def list_sessions(self) -> list[dict]:
        """List all recording sessions."""
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]

    def analyze_text_workflow(self, description: str, skill_name: str = "",
                               preferred_model: str = None,
                               log_callback: Callable = None) -> Optional[str]:
        """Direct text-to-skill: analyze a workflow description and generate a Skill.

        This is the zero-dependency path — no CDP/browser required.
        The user describes their workflow in natural language.
        """
        events = [{
            "type": "text_description",
            "text": description,
            "timestamp": time.time() * 1000,
            "raw": "manual",
        }]

        if log_callback:
            log_callback("DemoToSkill", "📝 텍스트 기반 워크플로우 분석 시작...", "running")

        analyzer = SkillAnalyzer()
        skill_data = analyzer.analyze(
            events=events,
            skill_name=skill_name,
            preferred_model=preferred_model,
            log_callback=log_callback,
        )

        if not skill_data:
            return None

        writer = SkillWriter()
        skill_path = writer.write(skill_data, skill_name=skill_name)

        try:
            from api.skill_registry import get_skill_registry
            get_skill_registry().reload()
        except Exception:
            pass

        if log_callback:
            log_callback("DemoToSkill",
                        f"✅ Skill 생성 완료: {skill_path.name}\n"
                        f"   → ~/.hermes/skills/ 에서 확인 및 편집 가능",
                        "done")

        return str(skill_path)


# =============================================================================
# LLM Direct Call (using existing AIAgent)
# =============================================================================
def _call_llm_direct(prompt: str, system_instruction: str = "",
                     preferred_model: str = None) -> str:
    """Call LLM directly using the existing AIAgent from hermes-agent.

    Resolves the model and provider from the system configuration
    (config.yaml → data/settings.json → env vars), NOT hardcoded to OpenAI.
    """
    agent_path = str(Path(__file__).resolve().parent.parent / "hermes-agent")
    if agent_path not in sys.path:
        sys.path.insert(0, agent_path)

    try:
        from run_agent import AIAgent
    except ImportError:
        _logger.error("[DemoToSkill] Cannot import AIAgent from hermes-agent")
        return "{}"

    # Resolve model: explicit preferred_model → system settings → env → fallback
    if preferred_model:
        model_id = preferred_model
    else:
        try:
            from api.config import DEFAULT_MODEL
            model_id = DEFAULT_MODEL or os.environ.get("DEFAULT_MODEL", "minimax-m3")
        except ImportError:
            model_id = os.environ.get("DEFAULT_MODEL", "minimax-m3")

    # Resolve provider from the model_id using ModelManager
    provider_id = None
    try:
        from api.managers.model_manager import model_manager
        _, provider_id, _ = model_manager.resolve_model_provider(model_id)
    except ImportError:
        pass
    if not provider_id or provider_id == "custom":
        provider_id = os.environ.get("DEFAULT_PROVIDER", "minimax")

    _logger.info("[DemoToSkill] Using model=%s provider=%s", model_id, provider_id)

    try:
        agent = AIAgent(
            model=model_id,
            provider=provider_id,
            enabled_toolsets=[],
            quiet_mode=True,
        )
        res = agent.run_conversation(
            user_message=prompt,
            system_message=system_instruction or "You are a helpful AI.",
        )
        if res.get("failed"):
            _logger.warning("[DemoToSkill] LLM call failed: %s", res.get("error"))
            return "{}"

        # Extract assistant content
        messages = res.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                return msg.get("content", "{}")
        return "{}"
    except Exception as e:
        _logger.warning("[DemoToSkill] LLM call exception: %s", e)
        return "{}"


def _extract_json_from_response(response: str) -> Optional[dict]:
    """Extract a JSON object from LLM response (handles markdown code blocks)."""
    # Try direct parse first
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass

    # Try extracting from code blocks
    json_patterns = [
        r'```(?:json)?\s*\n?([\s\S]*?)\n?```',
        r'\{[\s\S]*\}',
    ]

    for pattern in json_patterns:
        matches = re.findall(pattern, response)
        for match in matches:
            try:
                candidate = match.strip()
                if candidate.startswith("{"):
                    return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    _logger.warning("[DemoToSkill] Cannot extract JSON from: %.200s...", response)
    return None


# =============================================================================
# Singleton
# =============================================================================
_recording_manager: Optional[RecordingManager] = None
_recording_lock = threading.Lock()


def get_recording_manager() -> RecordingManager:
    """Get or create the global RecordingManager singleton."""
    global _recording_manager
    if _recording_manager is None:
        with _recording_lock:
            if _recording_manager is None:
                _recording_manager = RecordingManager()
    return _recording_manager
