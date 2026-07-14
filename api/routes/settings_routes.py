"""
Settings & Profile route helpers for Hermes Web UI.
Extracted from api/routes.py (Phase 2 — Structuring).
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.config import (
    STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE, DEFAULT_MODEL,
    SESSIONS, SESSIONS_MAX, LOCK, STREAMS, STREAMS_LOCK, CANCEL_FLAGS,
    SERVER_START_TIME, CLI_TOOLSETS, _INDEX_HTML_PATH,
    IMAGE_EXTS, MD_EXTS, MIME_MAP, MAX_FILE_BYTES, MAX_UPLOAD_BYTES,
    CHAT_LOCK, load_settings, save_settings,
)
from api.helpers import require, bad, j, t, read_body, _security_headers
from api.models import (
    Session, get_session, new_session, all_sessions, title_from,
    _write_session_index, SESSION_INDEX_FILE,
    load_projects, save_projects, import_cli_session,
    get_cli_sessions, get_cli_session_messages,
)
from api.workspace import (
    load_workspaces, save_workspaces, get_last_workspace, set_last_workspace,
    list_dir, read_file_content, safe_resolve_ws,
)


# ── GET route helpers ─────────────────────────────────────────────────────────

def handle_get_settings(handler, parsed) -> bool:
    """GET /api/settings — return current settings (without password hash)."""
    settings = load_settings()
    settings.pop('password_hash', None)
    return j(handler, settings)


def handle_get_profiles(handler, parsed) -> bool:
    """GET /api/profiles — list all profiles and active profile."""
    from api.profiles import list_profiles_api, get_active_profile_name
    return j(handler, {'profiles': list_profiles_api(), 'active': get_active_profile_name()})


def handle_get_profile_active(handler, parsed) -> bool:
    """GET /api/profile/active — return active profile name and path."""
    from api.profiles import get_active_profile_name, get_active_hermes_home
    return j(handler, {'name': get_active_profile_name(), 'path': str(get_active_hermes_home())})


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_settings(handler, body) -> bool:
    """POST /api/settings — save settings."""
    saved = save_settings(body)
    saved.pop('password_hash', None)
    return j(handler, saved)


def handle_post_profile_switch(handler, body) -> bool:
    """POST /api/profile/switch — switch to a different profile."""
    name = body.get('name', '').strip()
    if not name:
        return bad(handler, 'name is required')
    try:
        from api.profiles import switch_profile
        result = switch_profile(name)
        return j(handler, result)
    except (ValueError, FileNotFoundError) as e:
        return bad(handler, str(e), 404)
    except RuntimeError as e:
        return bad(handler, str(e), 409)


def handle_post_profile_create(handler, body) -> bool:
    """POST /api/profile/create — create a new profile."""
    name = body.get('name', '').strip()
    if not name:
        return bad(handler, 'name is required')
    import re as _re
    if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,63}$', name):
        return bad(handler, 'Invalid profile name: lowercase letters, numbers, hyphens, underscores only')
    clone_from = body.get('clone_from')
    if clone_from is not None:
        clone_from = str(clone_from).strip()
        if not _re.match(r'^[a-z0-9][a-z0-9_-]{0,63}$', clone_from):
            return bad(handler, 'Invalid clone_from name')
    try:
        from api.profiles import create_profile_api
        result = create_profile_api(
            name,
            clone_from=clone_from,
            clone_config=bool(body.get('clone_config', False)),
        )
        return j(handler, {'ok': True, 'profile': result})
    except (ValueError, FileExistsError, RuntimeError) as e:
        return bad(handler, str(e))


def handle_post_profile_delete(handler, body) -> bool:
    """POST /api/profile/delete — delete a profile."""
    name = body.get('name', '').strip()
    if not name:
        return bad(handler, 'name is required')
    try:
        from api.profiles import delete_profile_api
        result = delete_profile_api(name)
        return j(handler, result)
    except (ValueError, FileNotFoundError) as e:
        return bad(handler, str(e))
    except RuntimeError as e:
        return bad(handler, str(e), 409)


# ── Custom Provider CRUD + Model Auto-Fetch ────────────────────────────────────

def handle_get_providers(handler, parsed) -> bool:
    """GET /api/providers — list custom providers + presets."""
    from api.managers.model_manager import model_manager
    return j(handler, {
        'presets': model_manager.get_presets(),
        'providers': model_manager.get_custom_providers(),
    })


def handle_post_provider_add(handler, body) -> bool:
    """POST /api/providers/add — add or update a custom provider."""
    name = body.get('name', '').strip()
    api_key = body.get('api_key', '').strip()
    base_url = body.get('base_url', '').strip()
    models = body.get('models', None)

    if not name:
        return bad(handler, 'name is required')
    if not base_url:
        return bad(handler, 'base_url is required')

    try:
        from api.managers.model_manager import model_manager
        result = model_manager.add_custom_provider(name, api_key, base_url, models)

        # Refresh model selector profiles
        try:
            from api.dynamic.model_selector import get_model_selector
            selector = get_model_selector()
            if selector is not None:
                selector.refresh_profiles()
        except Exception:
            pass

        return j(handler, result)
    except ValueError as e:
        return bad(handler, str(e))


def handle_post_provider_delete(handler, body) -> bool:
    """POST /api/providers/delete — delete a custom provider."""
    name = body.get('name', '').strip()
    if not name:
        return bad(handler, 'name is required')
    try:
        from api.managers.model_manager import model_manager
        result = model_manager.delete_custom_provider(name)

        # Refresh model selector profiles
        try:
            from api.dynamic.model_selector import get_model_selector
            selector = get_model_selector()
            if selector is not None:
                selector.refresh_profiles()
        except Exception:
            pass

        return j(handler, result)
    except KeyError as e:
        return bad(handler, str(e), 404)


def handle_post_provider_fetch_models(handler, body) -> bool:
    """POST /api/providers/fetch-models — auto-fetch models from provider's /models endpoint."""
    base_url = body.get('base_url', '').strip()
    api_key = body.get('api_key', '').strip()

    if not base_url:
        return bad(handler, 'base_url is required')
    if not api_key:
        return bad(handler, 'api_key is required')

    try:
        from api.managers.model_manager import model_manager
        models = model_manager.fetch_models_from_provider(base_url, api_key)
        return j(handler, {'success': True, 'models': models, 'count': len(models)})
    except RuntimeError as e:
        return bad(handler, str(e))
    except Exception as e:
        return bad(handler, f'Failed to fetch models: {e}')


def handle_post_provider_update_models(handler, body) -> bool:
    """POST /api/providers/update-models — update the models list for a custom provider."""
    name = body.get('name', '').strip()
    models = body.get('models', None)

    if not name:
        return bad(handler, 'name is required')
    if models is None or not isinstance(models, list):
        return bad(handler, 'models (array) is required')

    try:
        from api.managers.model_manager import model_manager
        result = model_manager.update_custom_provider_models(name, models)

        # Refresh model selector profiles
        try:
            from api.dynamic.model_selector import get_model_selector
            selector = get_model_selector()
            if selector is not None:
                selector.refresh_profiles()
        except Exception:
            pass

        return j(handler, result)
    except KeyError as e:
        return bad(handler, str(e), 404)
