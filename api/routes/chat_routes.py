"""
Chat route helpers for Hermes Web UI.
Extracted from api/routes.py (Phase 2 — Structuring).
"""
import json
import os
import queue
import threading
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from api.config import (
    STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE, DEFAULT_MODEL,
    SESSIONS, SESSIONS_MAX, LOCK, STREAMS, STREAMS_LOCK, CANCEL_FLAGS,
    SERVER_START_TIME, CLI_TOOLSETS, CHAT_LOCK,
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
from api.streaming import _sse, _run_agent_streaming, cancel_stream


# ── GET route helpers ─────────────────────────────────────────────────────────

def handle_get_stream_status(handler, parsed) -> bool:
    """GET /api/chat/stream/status — check if a stream is active."""
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    return j(handler, {'active': stream_id in STREAMS, 'stream_id': stream_id})


def handle_get_chat_cancel(handler, parsed) -> bool:
    """GET /api/chat/cancel — cancel an active stream."""
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    if not stream_id:
        return bad(handler, 'stream_id required')
    cancelled = cancel_stream(stream_id)
    return j(handler, {'ok': True, 'cancelled': cancelled, 'stream_id': stream_id})


def handle_get_sse_stream(handler, parsed) -> bool:
    """GET /api/chat/stream — SSE stream endpoint."""
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    q = STREAMS.get(stream_id)
    if q is None:
        return j(handler, {'error': 'stream not found'}, status=404)
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'keep-alive')
    handler.end_headers()
    try:
        while True:
            try:
                event, data = q.get(timeout=30)
            except queue.Empty:
                try:
                    handler.wfile.write(b': heartbeat\n\n')
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    break  # client disconnected during heartbeat
                continue
            _sse(handler, event, data)
            if event in ('done', 'error', 'cancel'):
                break
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    return True


# ── POST route helpers ────────────────────────────────────────────────────────

def handle_post_chat_start(handler, body) -> bool:
    """POST /api/chat/start — start a streaming chat."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return bad(handler, str(e))
    try:
        s = get_session(body['session_id'])
    except KeyError:
        return bad(handler, 'Session not found', 404)
    msg = str(body.get('message', '')).strip()
    if not msg:
        return bad(handler, 'message is required')
    attachments = [str(a) for a in (body.get('attachments') or [])][:20]
    workspace = str(Path(body.get('workspace') or s.workspace).expanduser().resolve())
    model = body.get('model') or s.model
    s.workspace = workspace
    s.model = model
    s.save()
    set_last_workspace(workspace)
    stream_id = uuid.uuid4().hex
    q = queue.Queue()
    with STREAMS_LOCK:
        STREAMS[stream_id] = q
    planning_mode = body.get('planning_mode', False)
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(s.session_id, msg, model, workspace, stream_id, attachments, planning_mode),
        daemon=True,
    )
    thr.start()
    return j(handler, {'stream_id': stream_id, 'session_id': s.session_id})


def handle_post_chat_sync(handler, body) -> bool:
    """POST /api/chat — fallback synchronous chat endpoint. Not used by frontend."""
    from api.config import _get_session_agent_lock
    s = get_session(body['session_id'])
    msg = str(body.get('message', '')).strip()
    if not msg:
        return j(handler, {'error': 'empty message'}, status=400)
    workspace = Path(body.get('workspace') or s.workspace).expanduser().resolve()
    s.workspace = str(workspace)
    s.model = body.get('model') or s.model
    old_cwd = os.environ.get('TERMINAL_CWD')
    os.environ['TERMINAL_CWD'] = str(workspace)
    old_exec_ask = os.environ.get('HERMES_EXEC_ASK')
    old_session_key = os.environ.get('HERMES_SESSION_KEY')
    os.environ['HERMES_EXEC_ASK'] = '1'
    os.environ['HERMES_SESSION_KEY'] = s.session_id
    try:
        from run_agent import AIAgent
        with CHAT_LOCK:
            from api.config import resolve_model_provider
            _model, _provider, _base_url = resolve_model_provider(s.model)
            _api_key = None
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider
                _rt = resolve_runtime_provider(requested=_provider)
                _api_key = _rt.get("api_key")
                rt_provider = _rt.get("provider")
                rt_base_url = _rt.get("base_url")
                if not _provider or str(_provider).startswith('custom:'):
                    _provider = rt_provider
                if not _base_url or str(_provider).startswith('custom'):
                    _base_url = rt_base_url
            except Exception as _e:
                print(f"[webui] WARNING: resolve_runtime_provider failed: {_e}", flush=True)
            agent = AIAgent(
                model=_model, provider=_provider, base_url=_base_url,
                api_key=_api_key, platform='webui', quiet_mode=True,
                enabled_toolsets=CLI_TOOLSETS, session_id=s.session_id,
            )
            workspace_ctx = f"[Workspace: {s.workspace}]\n"
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace: /absolute/path] indicating the "
                "workspace the user has selected in the web UI at the time they sent that message. "
                "This tag is the single authoritative source of the active workspace and updates "
                "with every message. It overrides any prior workspace mentioned in this system "
                "prompt, memory, or conversation history. Always use the value from the most recent "
                "[Workspace: ...] tag as your default working directory for ALL file operations: "
                "write_file, read_file, search_files, terminal workdir, and patch. "
                "Never fall back to a hardcoded path when this tag is present."
            )
            from api.streaming import _sanitize_messages_for_api
            result = agent.run_conversation(
                user_message=workspace_ctx + msg,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_api(s.messages),
                task_id=s.session_id,
                persist_user_message=msg,
            )
    finally:
        if old_cwd is None:
            os.environ.pop('TERMINAL_CWD', None)
        else:
            os.environ['TERMINAL_CWD'] = old_cwd
        if old_exec_ask is None:
            os.environ.pop('HERMES_EXEC_ASK', None)
        else:
            os.environ['HERMES_EXEC_ASK'] = old_exec_ask
        if old_session_key is None:
            os.environ.pop('HERMES_SESSION_KEY', None)
        else:
            os.environ['HERMES_SESSION_KEY'] = old_session_key
    s.messages = result.get('messages') or s.messages
    s.title = title_from(s.messages, s.title)
    s.save()
    # Sync to state.db for /insights (opt-in setting)
    try:
        if load_settings().get('sync_to_insights'):
            from api.state_sync import sync_session_usage
            sync_session_usage(
                session_id=s.session_id,
                input_tokens=s.input_tokens or 0,
                output_tokens=s.output_tokens or 0,
                estimated_cost=s.estimated_cost,
                model=s.model,
                title=s.title,
            )
    except Exception:
        pass
    return j(handler, {
        'answer': result.get('final_response') or '',
        'status': 'done' if result.get('completed', True) else 'partial',
        'session': s.compact() | {'messages': s.messages},
        'result': {k: v for k, v in result.items() if k != 'messages'},
    })
