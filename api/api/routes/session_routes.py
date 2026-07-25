"""
Session & Project route helpers for Hermes Web UI.
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
    SESSIONS, SESSIONS_MAX, LOCK, SERVER_START_TIME,
    load_settings, save_settings,
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

def handle_get_session(handler, parsed) -> bool:
    """GET /api/session — return a single session by session_id."""
    sid = parse_qs(parsed.query).get('session_id', [''])[0]
    if not sid:
        return j(handler, {'error': 'session_id is required'}, status=400)
    try:
        s = get_session(sid)
        return j(handler, {'session': s.to_response()})
    except KeyError:
        # Not a WebUI session -- try CLI store
        msgs = get_cli_session_messages(sid)
        if msgs:
            cli_meta = None
            for cs in get_cli_sessions():
                if cs['session_id'] == sid:
                    cli_meta = cs
                    break
            sess = {
                'session_id': sid,
                'title': (cli_meta or {}).get('title', 'CLI Session'),
                'workspace': (cli_meta or {}).get('workspace', ''),
                'model': (cli_meta or {}).get('model', 'unknown'),
                'message_count': len(msgs),
                'created_at': (cli_meta or {}).get('created_at', 0),
                'updated_at': (cli_meta or {}).get('updated_at', 0),
                'pinned': False,
                'archived': False,
                'project_id': None,
                'profile': (cli_meta or {}).get('profile'),
                'is_cli_session': True,
                'messages': msgs,
                'tool_calls': [],
            }
            return j(handler, {'session': sess})
        return bad(handler, 'Session not found', 404)


def handle_get_sessions(handler, parsed) -> bool:
    """GET /api/sessions — return all sessions (WebUI + optional CLI)."""
    webui_sessions = all_sessions()
    settings = load_settings()
    if settings.get('show_cli_sessions'):
        cli = get_cli_sessions()
        webui_ids = {s['session_id'] for s in webui_sessions}
        deduped_cli = [s for s in cli if s['session_id'] not in webui_ids]
    else:
        deduped_cli = []
    merged = webui_sessions + deduped_cli
    merged.sort(key=lambda s: s.get('updated_at', 0) or 0, reverse=True)
    return j(handler, {'sessions': merged, 'cli_count': len(deduped_cli)})


def handle_get_session_export(handler, parsed) -> bool:
    """GET /api/session/export — export a session as JSON download."""
    sid = parse_qs(parsed.query).get('session_id', [''])[0]
    if not sid:
        return bad(handler, 'session_id is required')
    try:
        s = get_session(sid)
    except KeyError:
        return bad(handler, 'Session not found', 404)
    payload = json.dumps(s.__dict__, ensure_ascii=False, indent=2)
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Disposition', f'attachment; filename="hermes-{sid}.json"')
    handler.send_header('Content-Length', str(len(payload.encode('utf-8'))))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    try:
        handler.wfile.write(payload.encode('utf-8'))
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # Client disconnected before response could be sent
    return True


def handle_get_sessions_search(handler, parsed) -> bool:
    """GET /api/sessions/search — search sessions by title or content."""
    qs = parse_qs(parsed.query)
    q = qs.get('q', [''])[0].lower().strip()
    content_search = qs.get('content', ['1'])[0] == '1'
    depth = int(qs.get('depth', ['5'])[0])
    if not q:
        return j(handler, {'sessions': all_sessions()})
    results = []
    for s in all_sessions():
        title_match = q in (s.get('title') or '').lower()
        if title_match:
            results.append(dict(s, match_type='title'))
            continue
        if content_search:
            try:
                sess = get_session(s['session_id'])
                msgs = sess.messages[:depth] if depth else sess.messages
                for m in msgs:
                    c = m.get('content') or ''
                    if isinstance(c, list):
                        c = ' '.join(p.get('text', '') for p in c
                                     if isinstance(p, dict) and p.get('type') == 'text')
                    if q in str(c).lower():
                        results.append(dict(s, match_type='content'))
                        break
            except (KeyError, Exception):
                pass
    return j(handler, {'sessions': results, 'query': q, 'count': len(results)})


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_session_new(handler, body) -> bool:
    """POST /api/session/new — create a new session."""
    s = new_session(workspace=body.get('workspace'), model=body.get('model'))
    return j(handler, {'session': s.to_response()})


def handle_post_sessions_cleanup(handler, body, zero_only=False) -> bool:
    """POST /api/sessions/cleanup — clean up empty sessions."""
    cleaned = 0
    for p in SESSION_DIR.glob('*.json'):
        if p.name.startswith('_'): continue
        try:
            s = Session.load(p.stem)
            if zero_only:
                should_delete = s and len(s.messages) == 0
            else:
                should_delete = s and s.title == 'Untitled' and len(s.messages) == 0
            if should_delete:
                with LOCK: SESSIONS.pop(p.stem, None)
                p.unlink(missing_ok=True)
                cleaned += 1
        except Exception:
            pass
    if SESSION_INDEX_FILE.exists():
        SESSION_INDEX_FILE.unlink(missing_ok=True)
    return j(handler, {'ok': True, 'cleaned': cleaned})


def handle_post_session_rename(handler, body) -> bool:
    """POST /api/session/rename — rename a session."""
    try:
        require(body, 'session_id', 'title')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    s.title = str(body['title']).strip()[:80] or 'Untitled'
    s.save()
    return j(handler, {'session': s.to_response()})


def handle_post_session_update(handler, body) -> bool:
    """POST /api/session/update — update session workspace/model."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    new_ws = body.get('workspace', s.workspace)
    s.workspace = new_ws
    s.model = body.get('model', s.model)
    s.save()
    set_last_workspace(new_ws)
    return j(handler, {'session': s.to_response()})


def handle_post_session_delete(handler, body) -> bool:
    """POST /api/session/delete — delete a session."""
    sid = body.get('session_id', '')
    if not sid:
        return bad(handler, 'session_id is required')
    with LOCK:
        SESSIONS.pop(sid, None)
    p = SESSION_DIR / f'{sid}.json'
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        SESSION_INDEX_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        from api.models import delete_cli_session
        delete_cli_session(sid)
    except Exception:
        pass
    return j(handler, {'ok': True})


def handle_post_session_clear(handler, body) -> bool:
    """POST /api/session/clear — clear all messages in a session."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    s.messages = []
    s.tool_calls = []
    s.title = 'Untitled'
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_truncate(handler, body) -> bool:
    """POST /api/session/truncate — truncate messages to keep_count."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    if body.get('keep_count') is None:
        return bad(handler, 'Missing required field(s): keep_count')
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    keep = int(body['keep_count'])
    s.messages = s.messages[:keep]
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_pin(handler, body) -> bool:
    """POST /api/session/pin — toggle pin status."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    s.pinned = bool(body.get('pinned', True))
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_archive(handler, body) -> bool:
    """POST /api/session/archive — toggle archive status."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    s.archived = bool(body.get('archived', True))
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_move(handler, body) -> bool:
    """POST /api/session/move — move session to a project."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    s.project_id = body.get('project_id') or None
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_import(handler, body) -> bool:
    """POST /api/session/import — import a session from JSON export."""
    if not body or not isinstance(body, dict):
        return bad(handler, 'Request body must be a JSON object')
    messages = body.get('messages')
    if not isinstance(messages, list):
        return bad(handler, 'JSON must contain a "messages" array')
    title = body.get('title', 'Imported session')
    workspace = body.get('workspace', '')
    model = body.get('model', DEFAULT_MODEL)
    s = Session(
        title=title, workspace=workspace, model=model,
        messages=messages,
        tool_calls=body.get('tool_calls', []),
    )
    s.pinned = body.get('pinned', False)
    with LOCK:
        SESSIONS[s.session_id] = s
        SESSIONS.move_to_end(s.session_id)
        while len(SESSIONS) > SESSIONS_MAX:
            SESSIONS.popitem(last=False)
    s.save()
    return j(handler, {'ok': True, 'session': s.to_response()})


def handle_post_session_import_cli(handler, body) -> bool:
    """POST /api/session/import_cli — import a CLI session into WebUI store."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))

    sid = str(body['session_id'])

    # Check if already imported — idempotent
    existing = Session.load(sid)
    if existing:
        result = existing.to_response()
        result['is_cli_session'] = True
        return j(handler, {'session': result, 'imported': False})

    # Fetch messages from CLI store
    msgs = get_cli_session_messages(sid)
    if not msgs:
        return bad(handler, 'Session not found in CLI store', 404)

    # Derive title from first user message
    title = title_from(msgs, 'CLI Session')
    model = 'unknown'

    # Get profile and model from CLI session metadata
    profile = None
    for cs in get_cli_sessions():
        if cs['session_id'] == sid:
            profile = cs.get('profile')
            model = cs.get('model', 'unknown')
            break

    s = import_cli_session(sid, title, msgs, model, profile=profile)
    s.is_cli_session = True
    s._cli_origin = sid
    s.save()
    result = s.to_response()
    result['is_cli_session'] = True
    return j(handler, {
        'session': result,
        'imported': True,
    })


# ── Project CRUD ──────────────────────────────────────────────────────────────

def handle_get_projects(handler, parsed) -> bool:
    """GET /api/projects — list all projects."""
    return j(handler, {'projects': load_projects()})


def handle_post_project_create(handler, body) -> bool:
    """POST /api/projects/create — create a new project."""
    try:
        require(body, 'name')
    except ValueError as e:
        return bad(handler, str(e))
    import re as _re
    name = body['name'].strip()[:128]
    if not name:
        return bad(handler, 'name required')
    color = body.get('color')
    if color and not _re.match(r'^#[0-9a-fA-F]{3,8}$', color):
        return bad(handler, 'Invalid color format')
    projects = load_projects()
    proj = {'project_id': uuid.uuid4().hex[:12], 'name': name, 'color': color, 'created_at': time.time()}
    projects.append(proj)
    save_projects(projects)
    return j(handler, {'ok': True, 'project': proj})


def handle_post_project_rename(handler, body) -> bool:
    """POST /api/projects/rename — rename a project."""
    try:
        require(body, 'project_id', 'name')
    except ValueError as e:
        return bad(handler, str(e))
    import re as _re
    projects = load_projects()
    proj = next((p for p in projects if p['project_id'] == body['project_id']), None)
    if not proj:
        return bad(handler, 'Project not found', 404)
    proj['name'] = body['name'].strip()[:128]
    if 'color' in body:
        color = body['color']
        if color and not _re.match(r'^#[0-9a-fA-F]{3,8}$', color):
            return bad(handler, 'Invalid color format')
        proj['color'] = color
    save_projects(projects)
    return j(handler, {'ok': True, 'project': proj})


def handle_post_project_delete(handler, body) -> bool:
    """POST /api/projects/delete — delete a project and unassign its sessions."""
    try:
        require(body, 'project_id')
    except ValueError as e:
        return bad(handler, str(e))
    projects = load_projects()
    proj = next((p for p in projects if p['project_id'] == body['project_id']), None)
    if not proj:
        return bad(handler, 'Project not found', 404)
    projects = [p for p in projects if p['project_id'] != body['project_id']]
    save_projects(projects)
    # Unassign all sessions that belonged to this project
    if SESSION_INDEX_FILE.exists():
        try:
            index = json.loads(SESSION_INDEX_FILE.read_text(encoding='utf-8'))
            for entry in index:
                if entry.get('project_id') == body['project_id']:
                    try:
                        s = get_session(entry['session_id'])
                        s.project_id = None
                        s.save()
                    except Exception:
                        pass
        except Exception:
            pass
    return j(handler, {'ok': True})
