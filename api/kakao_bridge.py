"""
KakaoTalk Bridge — send messages to "나와의 채팅" via PlayMCP Gateway MemoChat.

Uses the MCPManager singleton to call KakaotalkChat-MemoChat on the
playmcp-gateway server. Provides a simple send_message() API and a
background worker for the poll loop pattern.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
import urllib.error
from typing import Optional, Callable

logger = logging.getLogger("api.kakao_bridge")

# ── Constants ──────────────────────────────────────────────────────────────────

GATEWAY_SERVER_ID = "playmcp-gateway"
MEMO_CHAT_TOOL = "KakaotalkChat-MemoChat"
MAX_MEMO_LENGTH = 900  # KakaoTalk memo length limit (conservative)


def _get_gateway_token_and_url() -> tuple[str, str]:
    """Read PlayMCP Gateway token and URL from mcp_servers.json.

    Raises RuntimeError if the JWT token is expired.
    """
    from pathlib import Path
    import base64
    try:
        from api.config import BASE_DIR
        config_path = BASE_DIR / 'data' / 'mcp_servers.json'
    except ImportError:
        config_path = Path("data/mcp_servers.json")
    if not config_path.exists():
        raise FileNotFoundError("mcp_servers.json not found")
    with open(config_path, "r", encoding="utf-8") as f:
        servers = json.load(f)
    for srv in servers:
        if srv.get("server_id") == GATEWAY_SERVER_ID:
            token = srv.get("auth_token", "")
            # Check JWT expiration to fail fast instead of getting 401
            if token and '.' in token:
                try:
                    parts = token.split('.')
                    payload_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
                    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                    exp = payload.get('exp', 0)
                    if time.time() > exp:
                        raise RuntimeError(
                            "PlayMCP JWT token has expired. Please re-authenticate via PlayMCP settings."
                        )
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass  # Not a decodable JWT, proceed as-is
            return token, srv.get("url", "https://playmcp.kakao.com/mcp")
    raise RuntimeError(f"Server '{GATEWAY_SERVER_ID}' not found in mcp_servers.json")


def send_message(message: str) -> dict:
    """
    Send a message to KakaoTalk "나와의 채팅" via MemoChat.

    Returns:
        {'ok': True, 'message': '...'} on success
        {'ok': False, 'error': '...'} on failure
    """
    if not message or not message.strip():
        return {"ok": False, "error": "Message is empty"}

    # Truncate long messages
    text = message.strip()
    if len(text) > MAX_MEMO_LENGTH:
        text = text[: MAX_MEMO_LENGTH - 3] + "..."

    # Try MCP Manager first (in-process, already connected)
    try:
        from api.mcp_client import get_mcp_manager
        mgr = get_mcp_manager()
        result = mgr.call_tool(
            GATEWAY_SERVER_ID,
            MEMO_CHAT_TOOL,
            {"message": text},
            timeout=10.0,
        )
        if result.get("ok"):
            logger.info("MemoChat sent (MCP): %d chars", len(text))
            return {"ok": True, "message": text}
        else:
            error = result.get("error", "Unknown MCP error")
            logger.warning("MemoChat MCP call failed: %s", error)
            # Fall through to direct HTTP
    except Exception as e:
        logger.warning("MCP Manager call failed, trying direct HTTP: %s", e)

    # Fallback: direct HTTP call to PlayMCP Gateway
    try:
        token, url = _get_gateway_token_and_url()
        if not token:
            return {"ok": False, "error": "No auth token configured"}

        # Initialize
        init_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "DaonKakaoBridge", "version": "1.0"},
                },
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=init_payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            sid = resp.headers.get("Mcp-Session-Id", "")

        if not sid:
            return {"ok": False, "error": "No Mcp-Session-Id in initialize response"}

        # Send initialized notification
        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8")
        req2 = urllib.request.Request(
            url,
            data=notif,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
                "Mcp-Session-Id": sid,
            },
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=10):
            pass

        # Call MemoChat
        call_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": MEMO_CHAT_TOOL,
                    "arguments": {"message": text},
                },
            }
        ).encode("utf-8")
        req3 = urllib.request.Request(
            url,
            data=call_payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
                "Mcp-Session-Id": sid,
            },
            method="POST",
        )
        with urllib.request.urlopen(req3, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        if "error" in result:
            err = result["error"]
            logger.error("Direct HTTP MemoChat failed: %s", err)
            return {"ok": False, "error": str(err)}

        logger.info("MemoChat sent (direct HTTP): %d chars", len(text))
        return {"ok": True, "message": text}

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        logger.error("HTTP error calling MemoChat: %s %s — %s", e.code, e.reason, body)
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        logger.error("Unexpected error in send_message: %s", e)
        return {"ok": False, "error": str(e)}


# ── Poll Loop Worker (for future bidirectional support) ────────────────────────

class KakaoPollWorker:
    """
    Background worker that polls for new KakaoTalk messages and responds.

    Currently a placeholder — reading messages requires KakaoTalk PC local DB
    access which is not reliably available. When a read mechanism is established,
    this worker will call `on_message` for each new message and send the response
    back via MemoChat.
    """

    def __init__(self, on_message: Optional[Callable[[str], str]] = None, interval: float = 5.0):
        self._on_message = on_message
        self._interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_id: int = 0

    def set_handler(self, handler: Callable[[str], str]):
        """Set the message handler: takes message text, returns response text."""
        self._on_message = handler

    def start(self):
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("KakaoPollWorker started (interval=%ss)", self._interval)

    def stop(self):
        """Stop the background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10.0)
            self._thread = None
        logger.info("KakaoPollWorker stopped")

    def _poll_loop(self):
        """Main polling loop — placeholder until read mechanism is available."""
        while self._running:
            try:
                # TODO: Implement actual message reading when feasible
                # e.g., KakaoTalk PC local DB query or window title monitoring
                pass
            except Exception as e:
                logger.error("Poll error: %s", e)
            time.sleep(self._interval)

    def send_response(self, message: str) -> dict:
        """Send a response back via MemoChat."""
        return send_message(message)


# ── Singleton ──────────────────────────────────────────────────────────────────

_poll_worker: Optional[KakaoPollWorker] = None
_worker_lock = threading.Lock()


def get_poll_worker() -> KakaoPollWorker:
    """Get or create the global Kakao poll worker singleton."""
    global _poll_worker
    with _worker_lock:
        if _poll_worker is None:
            _poll_worker = KakaoPollWorker()
        return _poll_worker
