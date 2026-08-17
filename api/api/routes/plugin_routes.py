"""
Plugin Routes — DAON 플러그인 REST API.

GET    /api/plugins                              — 설치된 플러그인 목록 (+전역/세션 상태)
GET    /api/plugins/state                        — 전체 상태 스냅샷 (전역 + 세션 스코프)
GET    /api/plugins/credentials/pending          — 미해결 자격증명 요청 목록 (값 없음)
GET    /api/plugins/{name}/credentials           — 자격증명 설정 상태 (값 반환 금지)
POST   /api/plugins/import                       — 외부 플러그인 import {identifier, source_type?, force?}
POST   /api/plugins/{name}/enable                — 전역 ON  (body: {} )
POST   /api/plugins/{name}/disable               — 전역 OFF (body: {} )
POST   /api/plugins/{name}/session               — 세션 스코프 토글 (body: {session_id, enabled})
POST   /api/plugins/{name}/remove                — 사용자 플러그인 삭제
POST   /api/plugins/{name}/credentials           — 자격증명 저장 (UI secure input 전용, body: {key, value})
POST   /api/plugins/{name}/credentials/remove    — 자격증명 삭제 (body: {key})
DELETE /api/plugins/{name}/credentials/{key}     — 자격증명 삭제
"""
from __future__ import annotations

import logging

from api.helpers import bad, j

from api.plugin_gateway import (
    delete_plugin_credential,
    get_plugin,
    get_plugin_credential_status,
    import_plugin,
    list_installed_plugins,
    list_pending_credentials,
    remove_plugin,
    sync_plugin_skill_env,
)
from api.plugin_state import (
    get_all_plugins_state,
    set_plugin_global_enabled,
    set_session_plugin,
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def handle_get_plugins(handler, parsed) -> bool:
    """GET /api/plugins — 설치된 플러그인 목록 + 상태."""
    try:
        plugins = list_installed_plugins()
        # 각 플러그인의 자격증명 설정 상태(값 자체는 반환하지 않음)를 합쳐 준다.
        # 플러그인 카드에서 '✓ 인증됨' 표시 + [⚙ 인증정보 관리] 모달에 사용.
        for p in plugins:
            try:
                p["credential_status"] = get_plugin_credential_status(p["name"])
            except Exception:
                p["credential_status"] = {"authenticated": False, "secrets": []}
        state = get_all_plugins_state()
        return j(handler, {"ok": True, "plugins": plugins, "state": state})
    except Exception as exc:
        _logger.exception("GET /api/plugins failed")
        return bad(handler, str(exc), 500)


def handle_get_plugins_state(handler, parsed) -> bool:
    """GET /api/plugins/state — 전체 상태 스냅샷."""
    try:
        return j(handler, {"ok": True, "state": get_all_plugins_state()})
    except Exception as exc:
        _logger.exception("GET /api/plugins/state failed")
        return bad(handler, str(exc), 500)


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------

def handle_post_plugins_import(handler, body: dict) -> bool:
    """POST /api/plugins/import — 외부 플러그인 import."""
    identifier = (body.get("identifier") or "").strip()
    if not identifier:
        return bad(handler, "identifier is required")
    source_type = (body.get("source_type") or "auto").strip() or "auto"
    force = bool(body.get("force", False))
    try:
        result = import_plugin(identifier, source_type=source_type, force=force)
        return j(handler, {"ok": True, "plugin": result})
    except ValueError as exc:
        return bad(handler, str(exc), 400)
    except Exception as exc:
        _logger.exception("POST /api/plugins/import failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_enable(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/enable — 전역 ON."""
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        set_plugin_global_enabled(plugin_name, True)
        sync_plugin_skill_env()
        return j(handler, {"ok": True, "name": plugin_name, "enabled": True})
    except Exception as exc:
        _logger.exception("enable plugin failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_disable(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/disable — 전역 OFF (모든 세션에서 제거)."""
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        set_plugin_global_enabled(plugin_name, False)
        sync_plugin_skill_env()
        return j(handler, {"ok": True, "name": plugin_name, "enabled": False})
    except Exception as exc:
        _logger.exception("disable plugin failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_session(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/session — 세션(탭) 단위 스코프 토글.

    body: {session_id: "...", enabled: true|false}
    """
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return bad(handler, "session_id is required")
    enabled = bool(body.get("enabled", True))
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        set_session_plugin(session_id, plugin_name, enabled)
        from api.plugin_state import get_session_plugins
        active = get_session_plugins(session_id)
        return j(handler, {
            "ok": True,
            "name": plugin_name,
            "session_id": session_id,
            "enabled": enabled,
            "session_active": active,
        })
    except Exception as exc:
        _logger.exception("session plugin toggle failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_remove(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/remove — 사용자 플러그인 삭제."""
    try:
        removed = remove_plugin(plugin_name)
        if not removed:
            return bad(handler, f"Plugin '{plugin_name}' not found or is bundled", 404)
        return j(handler, {"ok": True, "name": plugin_name, "removed": True})
    except Exception as exc:
        _logger.exception("remove plugin failed")
        return bad(handler, str(exc), 500)


# ---------------------------------------------------------------------------
# Credentials (자격증명)
#
# 보안 원칙: 값 자체는 어떤 응답에도 노출하지 않는다. GET 은 설정 여부(bool)
# 만 반환하고, 값 저장(POST set) 은 UI 의 secure input 에서만 호출된다.
# ---------------------------------------------------------------------------

def handle_get_plugin_credentials_pending(handler, parsed) -> bool:
    """GET /api/plugins/credentials/pending — 미해결 자격증명 요청 목록 (값 없음).

    반환: {"ok": True, "pending": [{"plugin", "key", "session_id", "created_at"}]}
    """
    try:
        pending = list_pending_credentials()
        return j(handler, {"ok": True, "pending": pending})
    except Exception as exc:
        _logger.exception("GET credentials/pending failed")
        return bad(handler, str(exc), 500)


def handle_get_plugin_credentials(handler, parsed, plugin_name: str) -> bool:
    """GET /api/plugins/{name}/credentials — 자격증명 설정 상태 (값 반환 금지).

    반환: {"ok": True, "name": "...", "authenticated": bool,
           "secrets": [{"name", "description", "set"}]}
    """
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        status = get_plugin_credential_status(plugin_name)
        return j(handler, {
            "ok": True,
            "name": plugin_name,
            "authenticated": bool(status.get("authenticated", False)),
            "secrets": status.get("secrets", []),
        })
    except Exception as exc:
        _logger.exception("GET plugin credentials failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_credentials_set(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/credentials — 자격증명 저장 (UI secure input 전용).

    body: {key: "...", value: "..."}  — 값은 서버에서 저장만 하고 응답에 되돌려주지 않는다.
    """
    key = (body.get("key") or "").strip()
    value = body.get("value")
    if not key:
        return bad(handler, "key is required")
    if value is None or value == "":
        return bad(handler, "value is required")
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        # 값은 절대 로그에 남기지 않는다.
        from api.plugin_credentials import set_credential
        saved = set_credential(plugin_name, key, str(value))
        if not saved:
            return bad(handler, f"'{key}' is not a declared secret of plugin '{plugin_name}'", 400)
        # 새 키가 활성 플러그인 환경변수에 반영되도록 재동기화 (pending 도 함께 해소됨).
        sync_plugin_skill_env()
        return j(handler, {
            "ok": True,
            "name": plugin_name,
            "key": key,
            "set": True,
            "message": f"'{key}' 저장 완료. 활성 플러그인의 환경변수에 즉시 반영됩니다.",
        })
    except Exception as exc:
        _logger.exception("set plugin credential failed")
        return bad(handler, str(exc), 500)


def handle_post_plugin_credentials_remove(handler, body: dict, plugin_name: str) -> bool:
    """POST /api/plugins/{name}/credentials/remove — 자격증명 삭제.

    body: {key: "..."}
    """
    key = (body.get("key") or "").strip()
    if not key:
        return bad(handler, "key is required")
    try:
        plugin = get_plugin(plugin_name)
        if not plugin:
            return bad(handler, f"Plugin '{plugin_name}' not found", 404)
        deleted = delete_plugin_credential(plugin_name, key)
        if not deleted:
            return bad(handler, f"Credential '{key}' for '{plugin_name}' not found", 404)
        sync_plugin_skill_env()
        return j(handler, {"ok": True, "name": plugin_name, "key": key, "deleted": True})
    except Exception as exc:
        _logger.exception("remove plugin credential failed")
        return bad(handler, str(exc), 500)
