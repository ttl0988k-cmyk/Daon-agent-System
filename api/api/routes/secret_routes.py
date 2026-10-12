"""
Secret Routes — DAON 시크릿카드 REST API.

GET    /api/secrets                              — 시크릿 목록 (값 미포함)
GET    /api/secrets/pending                      — 미해결 시크릿 요청 목록 (값 없음)
POST   /api/secrets/set                          — 시크릿 저장 (UI secure input 전용, body: {name, value, purpose?, scope?})
POST   /api/secrets/allow                        — 시크릿에 세션/봇 scope 추가 (body: {name, session_id?})
POST   /api/secrets/cancel                       — 시크릿 요청 취소 (body: {id})
POST   /api/secrets/update                       — 시크릿 scope 변경 (body: {name, scope})
DELETE /api/secrets/{name}                       — 시크릿 삭제
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs

from api.helpers import bad, j

try:
    from api.daon_secrets import (
        allow_secret,
        cancel_request,
        delete_secret,
        list_requests,
        list_secrets,
        set_secret,
        update_scope,
    )
except ImportError:  # 패키지 상대 import (api.routes.secret_routes 로 로드된 경우)
    try:
        from ..daon_secrets import (
            allow_secret,
            cancel_request,
            delete_secret,
            list_requests,
            list_secrets,
            set_secret,
            update_scope,
        )
    except ImportError:  # 단독 스크립트 실행 등 최후 폴백 (api/api 가 sys.path 에 직접 올라간 경우)
        from daon_secrets import (  # type: ignore
            allow_secret,
            cancel_request,
            delete_secret,
            list_requests,
            list_secrets,
            set_secret,
            update_scope,
        )

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def handle_get_secrets(handler, parsed) -> bool:
    """GET /api/secrets — 시크릿 목록 (값 반환 금지)."""
    try:
        return j(handler, {"ok": True, "secrets": list_secrets()})
    except Exception as exc:
        _logger.exception("GET /api/secrets failed")
        return bad(handler, str(exc), 500)


def handle_get_secrets_pending(handler, parsed) -> bool:
    """GET /api/secrets/pending — 미해결 시크릿 요청 목록 (값 없음).

    선택적 쿼리: ?owner=<id> 또는 ?session_id=<id> 로 특정 세션/봇의
    요청만 조회한다. 값 필드는 절대 포함하지 않는다.
    """
    try:
        qs = parse_qs(getattr(parsed, "query", "") or "")
        owner = (qs.get("owner") or qs.get("session_id") or [""])[0].strip()
        pending = list_requests(owner=owner or None, pending_only=True)
        return j(handler, {"ok": True, "pending": pending})
    except Exception as exc:
        _logger.exception("GET /api/secrets/pending failed")
        return bad(handler, str(exc), 500)


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------

def handle_post_secrets_set(handler, body: dict) -> bool:
    """POST /api/secrets/set — 시크릿 저장 (UI secure input 전용).

    body: {name: "...", value: "...", purpose?: "...", scope?: "all"|[...]}
    값은 서버에서 저장만 하고 응답에 되돌려주지 않는다.
    """
    name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
    value = body.get("value")
    if not name:
        return bad(handler, "name is required")
    if value is None or (isinstance(value, str) and value == ""):
        return bad(handler, "value is required")
    purpose = body.get("purpose") if isinstance(body.get("purpose"), str) else ""
    scope = body.get("scope", "all")
    requested_by = body.get("requested_by") if isinstance(body.get("requested_by"), str) else None
    try:
        # 값은 절대 로그에 남기지 않는다.
        view = set_secret(name, value, purpose=purpose, scope=scope,
                          requested_by=requested_by)
        return j(handler, {
            "ok": True,
            "secret": view,
            "message": f"'{view.get('name')}' 저장 완료.",
        })
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except Exception as exc:
        _logger.exception("POST /api/secrets/set failed")
        return bad(handler, str(exc), 500)


def handle_post_secrets_allow(handler, body: dict) -> bool:
    """POST /api/secrets/allow — 저장된 시크릿에 세션/봇 scope 추가.

    body: {name: "...", session_id?: "..."} (bot_id/owner 별칭 허용)
    """
    name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
    if not name:
        return bad(handler, "name is required")
    owner = ""
    for key in ("session_id", "bot_id", "owner"):
        raw = body.get(key)
        if isinstance(raw, str) and raw.strip():
            owner = raw.strip()
            break
    if not owner:
        return bad(handler, "session_id is required")
    try:
        view = allow_secret(name, owner)
        if view is None:
            return bad(handler, f"Secret '{name}' not found", 404)
        return j(handler, {"ok": True, "secret": view})
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except Exception as exc:
        _logger.exception("POST /api/secrets/allow failed")
        return bad(handler, str(exc), 500)


def handle_post_secrets_cancel(handler, body: dict) -> bool:
    """POST /api/secrets/cancel — 시크릿 요청 취소.

    body: {id: "..."} (request_id 별칭 허용)
    """
    request_id = ""
    for key in ("id", "request_id"):
        raw = body.get(key)
        if isinstance(raw, str) and raw.strip():
            request_id = raw.strip()
            break
    if not request_id:
        return bad(handler, "id is required")
    try:
        settled = cancel_request(request_id)
        if settled is None:
            return bad(handler, f"Request '{request_id}' not found", 404)
        return j(handler, {"ok": True, "request": settled})
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except Exception as exc:
        _logger.exception("POST /api/secrets/cancel failed")
        return bad(handler, str(exc), 500)


def handle_post_secrets_update(handler, body: dict) -> bool:
    """POST /api/secrets/update — 시크릿 scope 변경.

    body: {name: "...", scope: "all" | ["<session-or-bot-id>", ...]}
    """
    name = (body.get("name") or "").strip() if isinstance(body.get("name"), str) else ""
    if not name:
        return bad(handler, "name is required")
    if "scope" not in body:
        return bad(handler, "scope is required")
    try:
        view = update_scope(name, body.get("scope"))
        if view is None:
            return bad(handler, f"Secret '{name}' not found", 404)
        return j(handler, {"ok": True, "secret": view})
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except Exception as exc:
        _logger.exception("POST /api/secrets/update failed")
        return bad(handler, str(exc), 500)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def handle_delete_secret(handler, parsed, name: str) -> bool:
    """DELETE /api/secrets/{name} — 시크릿 삭제 (값 반환 금지)."""
    secret_name = (name or "").strip()
    if not secret_name:
        return bad(handler, "name is required", 400)
    try:
        deleted = delete_secret(secret_name)
        if not deleted:
            return bad(handler, f"Secret '{secret_name}' not found", 404)
        return j(handler, {"ok": True, "name": secret_name, "deleted": True})
    except Exception as exc:
        _logger.exception("DELETE /api/secrets failed")
        return bad(handler, str(exc), 500)
