"""
Hermes Web UI -- HTTP helper functions.
"""
import json as _json
from pathlib import Path
from api.config import IMAGE_EXTS, MD_EXTS


def require(body: dict, *fields) -> None:
    """Phase D: Validate required fields. Raises ValueError with clean message."""
    missing = [f for f in fields if not body.get(f) and body.get(f) != 0]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")


def bad(handler, msg, status: int=400):
    """Return a clean JSON error response."""
    return j(handler, {'error': msg}, status=status)


def safe_resolve(root: Path, requested: str) -> Path:
    """Resolve a relative path inside root, raising ValueError on traversal."""
    resolved = (root / requested).resolve()
    resolved.relative_to(root.resolve())  # raises ValueError if outside root
    return resolved


def _security_headers(handler):
    """Add security headers to every response."""
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('X-Frame-Options', 'DENY')
    handler.send_header('Referrer-Policy', 'same-origin')


def j(handler, payload, status: int=200) -> bool:
    """Send a JSON response. Returns True so callers can use 'return j(...)'."""
    body = _json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    _security_headers(handler)
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


def j_ok(handler, data=None, status=200, **kwargs):
    """통일된 성공 응답 (Integrator Contract).
    항상 {'ok': True, ...} 래퍼를 포함한다."""
    payload = {'ok': True}
    if data is not None:
        payload.update(data)
    payload.update(kwargs)
    return j(handler, payload, status=status)


def j_err(handler, message, status=400, **kwargs):
    """통일된 오류 응답 (Integrator Contract).
    항상 {'ok': False, 'error': ...} 래퍼를 포함한다."""
    payload = {'ok': False, 'error': message}
    payload.update(kwargs)
    return j(handler, payload, status=status)


def t(handler, payload, status: int=200, content_type: str='text/plain; charset=utf-8') -> bool:
    """Send a plain text or HTML response. Returns True so callers can use 'return t(...)'."""
    body = payload if isinstance(payload, bytes) else str(payload).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    _security_headers(handler)
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


MAX_BODY_BYTES = 20 * 1024 * 1024  # 20MB limit for non-upload POST bodies


def read_body(handler) -> dict:
    """Read and JSON-parse a POST request body (capped at 20MB)."""
    length = int(handler.headers.get('Content-Length', 0))
    if length > MAX_BODY_BYTES:
        raise ValueError(f'Request body too large ({length} bytes, max {MAX_BODY_BYTES})')
    raw = handler.rfile.read(length) if length else b'{}'
    try:
        return _json.loads(raw)
    except Exception:
        return {}
