"""
File & Workspace route helpers for Hermes Web UI.
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
from api.helpers import require, bad, j, t, safe_resolve, read_body, _security_headers
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
from api.upload import handle_upload


# ── GET route helpers ─────────────────────────────────────────────────────────

def handle_get_list_dir(handler, parsed) -> bool:
    """GET /api/list — list directory contents for a session workspace."""
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        return j(handler, {
            'entries': list_dir(Path(s.workspace), qs.get('path', ['.'])[0]),
            'path': qs.get('path', ['.'])[0],
        })
    except (FileNotFoundError, ValueError) as e:
        return bad(handler, str(e), 404)


def handle_get_file_raw(handler, parsed) -> bool:
    """GET /api/file/raw — serve raw file content (binary safe)."""
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found', 404)
    rel = qs.get('path', [''])[0]
    force_download = qs.get('download', [''])[0] == '1'
    target = safe_resolve(Path(s.workspace), rel)
    if not target.exists() or not target.is_file():
        # Fallback: try old regex sanitized filename (non-ASCII → _)
        # This handles files uploaded before the Korean-filename fix (v39)
        import re as _re
        old_safe_name = _re.sub(r'[^\w.\-]', '_', target.name)
        if old_safe_name != target.name:
            fallback = target.parent / old_safe_name
            if fallback.exists() and fallback.is_file():
                target = fallback
        if not target.exists() or not target.is_file():
            return j(handler, {'error': 'not found'}, status=404)
    ext = target.suffix.lower()
    mime = MIME_MAP.get(ext, 'application/octet-stream')
    raw_bytes = target.read_bytes()
    import urllib.parse as _up
    safe_name = _up.quote(target.name, safe='')
    handler.send_response(200)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(len(raw_bytes)))
    handler.send_header('Cache-Control', 'no-store')
    if force_download:
        handler.send_header('Content-Disposition',
            f'attachment; filename="{target.name}"; filename*=UTF-8\'\'{safe_name}')
    handler.end_headers()
    try:
        handler.wfile.write(raw_bytes)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


def handle_get_file_read(handler, parsed) -> bool:
    """GET /api/file — read file content as text."""
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found', 404)
    rel = qs.get('path', [''])[0]
    if not rel:
        return bad(handler, 'path is required')
    try:
        return j(handler, read_file_content(Path(s.workspace), rel))
    except (FileNotFoundError, ValueError):
        # Fallback: try old regex sanitized filename (non-ASCII → _)
        # This handles files uploaded before the Korean-filename fix (v39)
        import re as _re
        target = safe_resolve(Path(s.workspace), rel)
        old_safe_name = _re.sub(r'[^\w.\-]', '_', target.name)
        if old_safe_name != target.name:
            fallback = target.parent / old_safe_name
            if fallback.exists() and fallback.is_file():
                return j(handler, read_file_content(Path(s.workspace),
                       str(fallback.relative_to(Path(s.workspace)))))
        return bad(handler, 'File not found', 404)


def handle_get_git_info(handler, parsed) -> bool:
    """GET /api/git-info — return git info for a session workspace."""
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id required')
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found', 404)
    from api.workspace import git_info_for_workspace
    info = git_info_for_workspace(Path(s.workspace))
    return j(handler, {'git': info})


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_upload(handler, parsed) -> bool:
    """POST /api/upload — handle file upload."""
    return handle_upload(handler)


def handle_post_file_delete(handler, body) -> bool:
    """POST /api/file/delete — delete a file."""
    try:
        require(body, 'session_id', 'path')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists():
            return bad(handler, 'File not found', 404)
        if target.is_dir():
            return bad(handler, 'Cannot delete directories via this endpoint')
        target.unlink()
        return j(handler, {'ok': True, 'path': body['path']})
    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


def handle_post_file_save(handler, body) -> bool:
    """POST /api/file/save — save content to an existing file."""
    try:
        require(body, 'session_id', 'path')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if not target.exists():
            return bad(handler, 'File not found', 404)
        if target.is_dir():
            return bad(handler, 'Cannot save: path is a directory')
        target.write_text(body.get('content', ''), encoding='utf-8')
        return j(handler, {'ok': True, 'path': body['path'], 'size': target.stat().st_size})
    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


def handle_post_file_create(handler, body) -> bool:
    """POST /api/file/create — create a new file."""
    try:
        require(body, 'session_id', 'path')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if target.exists():
            return bad(handler, 'File already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get('content', ''), encoding='utf-8')
        return j(handler, {'ok': True, 'path': str(target.relative_to(Path(s.workspace)))})
    except (ValueError, PermissionError) as e:
        return bad(handler, str(e))


def handle_post_file_rename(handler, body) -> bool:
    """POST /api/file/rename — rename a file."""
    try:
        require(body, 'session_id', 'path', 'new_name')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        source = safe_resolve(Path(s.workspace), body['path'])
        if not source.exists():
            return bad(handler, 'File not found', 404)
        new_name = body['new_name'].strip()
        if not new_name or '/' in new_name or '..' in new_name:
            return bad(handler, 'Invalid file name')
        dest = source.parent / new_name
        if dest.exists():
            return bad(handler, f'A file named "{new_name}" already exists')
        source.rename(dest)
        new_rel = str(dest.relative_to(Path(s.workspace)))
        return j(handler, {'ok': True, 'old_path': body['path'], 'new_path': new_rel})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, str(e))


def handle_post_create_dir(handler, body) -> bool:
    """POST /api/file/create-dir — create a new directory."""
    try:
        require(body, 'session_id', 'path')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    try:
        target = safe_resolve(Path(s.workspace), body['path'])
        if target.exists():
            return bad(handler, 'Path already exists')
        target.mkdir(parents=True)
        return j(handler, {'ok': True, 'path': str(target.relative_to(Path(s.workspace)))})
    except (ValueError, PermissionError, OSError) as e:
        return bad(handler, str(e))


# ── Workspace management ──────────────────────────────────────────────────────

def handle_get_workspaces(handler, parsed) -> bool:
    """GET /api/workspaces — list all workspaces."""
    return j(handler, {'workspaces': load_workspaces(), 'last': get_last_workspace()})


def handle_post_workspace_add(handler, body) -> bool:
    """POST /api/workspaces/add — add a workspace."""
    path_str = body.get('path', '').strip()
    name = body.get('name', '').strip()
    if not path_str:
        return bad(handler, 'path is required')
    p = Path(path_str).expanduser().resolve()
    if not p.exists():
        return bad(handler, f'Path does not exist: {p}')
    if not p.is_dir():
        return bad(handler, f'Path is not a directory: {p}')
    wss = load_workspaces()
    if any(w['path'] == str(p) for w in wss):
        return bad(handler, 'Workspace already in list')
    wss.append({'path': str(p), 'name': name or p.name})
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})


def handle_post_workspace_remove(handler, body) -> bool:
    """POST /api/workspaces/remove — remove a workspace."""
    path_str = body.get('path', '').strip()
    if not path_str:
        return bad(handler, 'path is required')
    wss = load_workspaces()
    wss = [w for w in wss if w['path'] != path_str]
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})


def handle_post_workspace_rename(handler, body) -> bool:
    """POST /api/workspaces/rename — rename a workspace."""
    path_str = body.get('path', '').strip()
    name = body.get('name', '').strip()
    if not path_str or not name:
        return bad(handler, 'path and name are required')
    wss = load_workspaces()
    for w in wss:
        if w['path'] == path_str:
            w['name'] = name
            break
    else:
        return bad(handler, 'Workspace not found', 404)
    save_workspaces(wss)
    return j(handler, {'ok': True, 'workspaces': wss})
