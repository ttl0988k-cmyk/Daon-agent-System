"""
Plugin Routes — DAON 플러그인 REST API.

GET  /api/plugins                     — 설치된 플러그인 목록 (+전역/세션 상태)
GET  /api/plugins/state               — 전체 상태 스냅샷 (전역 + 세션 스코프)
POST /api/plugins/import              — 외부 플러그인 import {identifier, source_type?, force?}
POST /api/plugins/{name}/enable       — 전역 ON  (body: {} )
POST /api/plugins/{name}/disable      — 전역 OFF (body: {} )
POST /api/plugins/{name}/session      — 세션 스코프 토글 (body: {session_id, enabled})
POST /api/plugins/{name}/remove       — 사용자 플러그인 삭제
"""
from __future__ import annotations

import logging

from api.helpers import bad, j

from api.plugin_gateway import (
    get_plugin,
    import_plugin,
    list_installed_plugins,
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
