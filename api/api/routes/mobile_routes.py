"""
DAON Mobile Bridge REST API Routes.

다온 데스크탑 서버(9090)와 Supabase 사이의 안전 다리.
모바일/외부 디바이스에서 들어오는 요청을 받아 service_role 키로
Supabase에 안전하게 데이터를 쓰고, 라온에게 작업을 위임한다.

Endpoints:
    GET  /m                            — 모바일 라이트 채팅 HTML (SPA 진입점)
    GET  /static/mobile.js             — 모바일 클라이언트 스크립트
    POST /api/mobile/login             — anon key + email/pw → Supabase JWT
    POST /api/mobile/conversations     — 새 세션 생성 (device 태그)
    GET  /api/mobile/conversations     — 본인 세션 목록
    POST /api/mobile/messages          — 메시지 저장 + 라온 호출

설계 원칙:
    - 모바일은 anon key + JWT 만 가진다 → RLS로 본인 데이터만 SELECT 가능
    - INSERT는 service_role 만 가능 → 다온 서버가 중계
    - service_role 키는 .env 의 SUPABASE_SERVICE_ROLE_KEY 에서만 읽음
"""

import logging
import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import j_ok, j_err, require

_logger = logging.getLogger(__name__)

# ── 상수 ────────────────────────────────────────────────────────────────
_MOBILE_HTML = Path(__file__).resolve().parents[3] / "static" / "m.html"
_MOBILE_JS   = Path(__file__).resolve().parents[3] / "static" / "modules" / "mobile.js"

def _sb_url_val() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")

def _sb_anon_val() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "")

def _sb_service_val() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _sb_headers(service: bool = True) -> dict:
    """Supabase REST 헤더. service_role 이면 RLS 우회, anon 이면 RLS 적용."""
    key = _sb_service_val() if service else _sb_anon_val()
    if not key:
        raise RuntimeError("Supabase key missing in env (SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY)")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_url(table: str, query: str = "") -> str:
    """PostgREST URL 빌더."""
    base = f"{_sb_url_val()}/rest/v1/{table}"
    return f"{base}?{query}" if query else base


# ── Mobile SPA 진입점 ────────────────────────────────────────────────────


def handle_get_mobile(handler, parsed) -> bool:
    """GET /m — 모바일 라이트 채팅 HTML 반환."""
    if not _MOBILE_HTML.exists():
        return j_err(handler, "mobile HTML not found", 404)
    try:
        html = _MOBILE_HTML.read_text(encoding="utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        handler.wfile.write(html.encode("utf-8"))
        return True
    except Exception as e:
        _logger.exception("mobile html serve failed")
        return j_err(handler, f"mobile html failed: {e}", 500)


def handle_get_mobile_js(handler, parsed) -> bool:
    """GET /static/mobile.js — 모바일 클라이언트 스크립트."""
    if not _MOBILE_JS.exists():
        return j_err(handler, "mobile.js not found", 404)
    try:
        js = _MOBILE_JS.read_text(encoding="utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/javascript; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.end_headers()
        handler.wfile.write(js.encode("utf-8"))
        return True
    except Exception as e:
        _logger.exception("mobile.js serve failed")
        return j_err(handler, f"mobile.js failed: {e}", 500)


# ── Auth ─────────────────────────────────────────────────────────────────


def handle_post_mobile_login(handler, body: dict) -> bool:
    """POST /api/mobile/login — Supabase Auth 로그인.

    Body: { "email": "...", "password": "..." }
    Returns: { "access_token", "user_id", "email" }
    """
    import urllib.request

    email = require(body, "email", str)
    password = require(body, "password", str)

    if not _sb_url_val() or not _sb_anon_val():
        return j_err(handler, "Supabase not configured on server", 500)

    url = f"{_sb_url_val()}/auth/v1/token?grant_type=password"
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "apikey": _sb_anon_val(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        return j_err(handler, f"login failed: {body_text}", e.code)
    except Exception as e:
        _logger.exception("mobile login transport error")
        return j_err(handler, f"login transport failed: {e}", 500)

    access_token = data.get("access_token")
    user = data.get("user") or {}
    if not access_token:
        return j_err(handler, "no access_token in response", 502)

    return j_ok(handler, {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "user_id": user.get("id"),
        "email": user.get("email"),
        "expires_in": data.get("expires_in", 3600),
    })


# ── Conversations ────────────────────────────────────────────────────────


def handle_post_mobile_conversations(handler, body: dict) -> bool:
    """POST /api/mobile/conversations — 새 세션 생성.

    Body: { "access_token": "...", "device": "아이폰", "title": "..." (선택) }
    Returns: { "id", "created_at" }
    """
    access_token = require(body, "access_token", str)
    device = require(body, "device", str)
    title = body.get("title")

    if not _sb_service_val():
        return j_err(handler, "Supabase service key not configured", 500)

    # 1) JWT에서 user_id 추출 (service_role은 auth API로 verify)
    user_id = _verify_supabase_jwt(access_token)
    if not user_id:
        return j_err(handler, "invalid access_token", 401)

    # 2) INSERT via PostgREST
    import urllib.request
    row = {
        "user_id": user_id,
        "device": device,
        "title": title,
        "metadata": {"ua": body.get("ua", ""), "via": "mobile"},
    }
    payload = json.dumps([row]).encode("utf-8")
    req = urllib.request.Request(
        f"{_sb_url_val()}/rest/v1/conversations",
        data=payload, method="POST",
        headers=_sb_headers(service=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        return j_err(handler, f"insert failed: {body_text}", e.code)
    except Exception as e:
        _logger.exception("mobile conv insert failed")
        return j_err(handler, f"conv insert failed: {e}", 500)

    if not data:
        return j_err(handler, "no row returned", 500)
    conv = data[0]
    return j_ok(handler, {
        "id": conv["id"],
        "device": conv["device"],
        "title": conv.get("title"),
        "created_at": conv["created_at"],
    })


def handle_get_mobile_conversations(handler, body_or_parsed) -> bool:
    """GET /api/mobile/conversations — 본인 세션 목록.

    Auth: Authorization 헤더 또는 ?access_token= 쿼리.
    """
    import urllib.request

    # Authorization 헤더 또는 쿼리에서 토큰 추출
    token = ""
    try:
        auth = handler.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        else:
            qs = parse_qs(getattr(body_or_parsed, "query", "") or "")
            token = (qs.get("access_token") or [""])[0]
    except Exception:
        pass

    if not token:
        return j_err(handler, "missing access_token", 401)

    user_id = _verify_supabase_jwt(token)
    if not user_id:
        return j_err(handler, "invalid access_token", 401)

    if not _SUPABASE_SERVICE:
        return j_err(handler, "Supabase service key not configured", 500)

    # 본인 세션만 (RLS 우회이지만 명시적 필터)
    query = f"user_id=eq.{user_id}&order=updated_at.desc&limit=50"
    req = urllib.request.Request(
        _sb_url("conversations", query),
        method="GET",
        headers=_sb_headers(service=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        _logger.exception("list conversations failed")
        return j_err(handler, f"list failed: {e}", 500)

    return j_ok(handler, {"conversations": data, "count": len(data)})


# ── Messages ─────────────────────────────────────────────────────────────


def handle_post_mobile_messages(handler, body: dict) -> bool:
    """POST /api/mobile/messages — 메시지 저장 + 라온 호출.

    Body: {
      "access_token": "...",
      "conversation_id": "uuid",
      "role": "user" | "assistant",
      "content": "...",
      "metadata": {} (선택)
    }

    role=user 면 → 저장 후 라온 호출 → 어시스턴트 답변도 저장 → SSE 같은 응답
    """
    import urllib.request

    access_token = require(body, "access_token", str)
    conversation_id = require(body, "conversation_id", str)
    role = require(body, "role", str)
    content = require(body, "content", str)
    metadata = body.get("metadata", {})

    if role not in ("user", "assistant", "system", "tool"):
        return j_err(handler, "invalid role", 400)

    user_id = _verify_supabase_jwt(access_token)
    if not user_id:
        return j_err(handler, "invalid access_token", 401)

    if not _SUPABASE_SERVICE:
        return j_err(handler, "Supabase service key not configured", 500)

    # 1) 메시지 저장
    row = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "role": role,
        "content": content,
        "metadata": metadata,
    }
    payload = json.dumps([row]).encode("utf-8")
    req = urllib.request.Request(
        f"{_sb_url_val()}/rest/v1/messages",
        data=payload, method="POST",
        headers=_sb_headers(service=True),
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            saved = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")
        return j_err(handler, f"message insert failed: {body_text}", e.code)
    except Exception as e:
        _logger.exception("message insert failed")
        return j_err(handler, f"message insert failed: {e}", 500)

    # 2) user 메시지면 → 라온 호출 (간단 동기 응답)
    assistant_reply = None
    if role == "user":
        assistant_reply = _invoke_raon(content, conversation_id, user_id)

    return j_ok(handler, {
        "saved": saved[0] if saved else None,
        "assistant_reply": assistant_reply,
    })


# ── Helpers ──────────────────────────────────────────────────────────────


def _verify_supabase_jwt(access_token: str) -> str | None:
    """Supabase JWT 의 user_id (sub) 반환. 검증 실패 시 None."""
    import urllib.request

    if not _sb_url_val():
        return None
    url = f"{_sb_url_val()}/auth/v1/user"
    req = urllib.request.Request(
        url, method="GET",
        headers={
            "apikey": _sb_anon_val(),
            "Authorization": f"Bearer {access_token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("id")
    except Exception as e:
        _logger.warning("JWT verify failed: %s", e)
        return None


def _invoke_raon(user_content: str, conversation_id: str, user_id: str) -> dict:
    """라온에게 동기 호출 (현재는 stub → 추후 /api/chat/sync 경유로 교체).

    Phase 1: 단순 echo + 라온 페르소나 응답.
    Phase 2: 기존 chat_sync 라우트 호출 (진짜 라온 응답).
    """
    # TODO: handle_post_chat_sync 와 통합하여 진짜 스트리밍 응답
    # 지금은 stub
    return {
        "role": "assistant",
        "content": (
            "📱 모바일 라우트 작동 확인. "
            f"user={user_id[:8]}... conv={conversation_id[:8]}... "
            "실제 라온 호출은 다음 단계에서 연결됩니다."
        ),
        "stub": True,
    }