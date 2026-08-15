"""
Chat route helpers for Hermes Web UI.
Extracted from api/routes.py (Phase 2 — Structuring).
"""
import json
import logging
import os
import queue
import threading
import uuid
from pathlib import Path

_logger = logging.getLogger(__name__)
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
from api.streaming import (
    _sse, _run_agent_streaming, cancel_stream,
    _COMPLETED_STREAMS, _COMPLETED_STREAMS_LOCK,
    _CANCELLED_STREAMS, _CANCELLED_STREAMS_LOCK,
)


# ── GET route helpers ─────────────────────────────────────────────────────────

def handle_get_stream_status(handler, parsed) -> bool:
    """GET /api/chat/stream/status — check if a stream is active."""
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    return j(handler, {'active': stream_id in STREAMS, 'stream_id': stream_id})


def handle_get_chat_cancel(handler, parsed) -> bool:
    """GET /api/chat/cancel — cancel an active stream."""
    qs = parse_qs(parsed.query)
    stream_id = qs.get('stream_id', [''])[0]
    if not stream_id:
        return bad(handler, 'stream_id required')
    # session_id를 함께 전달해야 _force_release_session_lock이 역방향 조회(실패
    # 가능) 없이 락을 직접 해제한다. (streaming.cancel_stream 참고)
    session_id = qs.get('session_id', [''])[0] or None
    cancelled = cancel_stream(stream_id, session_id=session_id)
    return j(handler, {'ok': True, 'cancelled': cancelled, 'stream_id': stream_id})


def handle_post_chat_cancel(handler, body) -> bool:
    """POST /api/chat/cancel — cancel an active stream (body: {stream_id})."""
    stream_id = (body or {}).get('stream_id', '')
    if not stream_id:
        return bad(handler, 'stream_id required')
    session_id = (body or {}).get('session_id', '') or None
    cancelled = cancel_stream(stream_id, session_id=session_id)
    return j(handler, {'ok': True, 'cancelled': cancelled, 'stream_id': stream_id})


def handle_get_sse_stream(handler, parsed) -> bool:
    """GET /api/chat/stream — SSE stream endpoint."""
    stream_id = parse_qs(parsed.query).get('stream_id', [''])[0]
    q = STREAMS.get(stream_id)
    if q is None:
        # Check the completed streams cache — the EventSource may be
        # auto-reconnecting after the stream already finished.  Serve
        # the cached done event so the client renders the result
        # instead of showing a "Stream not found" error.
        with _COMPLETED_STREAMS_LOCK:
            cached = _COMPLETED_STREAMS.get(stream_id)
        if cached is not None:
            done_data, _ts = cached
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            handler.send_header('Cache-Control', 'no-cache')
            handler.send_header('X-Accel-Buffering', 'no')
            handler.send_header('Connection', 'keep-alive')
            handler.end_headers()
            try:
                _sse(handler, 'done', done_data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            return True
        # Check the cancelled streams cache — the EventSource may be
        # auto-reconnecting right after the user pressed cancel. Serve a
        # clean 'cancel' event instead of a 404, which the UI would surface
        # as a scary "connection lost" error.
        with _CANCELLED_STREAMS_LOCK:
            was_cancelled = stream_id in _CANCELLED_STREAMS
        if was_cancelled:
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            handler.send_header('Cache-Control', 'no-cache')
            handler.send_header('X-Accel-Buffering', 'no')
            handler.send_header('Connection', 'keep-alive')
            handler.end_headers()
            try:
                _sse(handler, 'cancel', {'message': 'Cancelled by user'})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            return True
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
                # 15초: 프론트엔드 idle timer(30초)보다 짧게 유지해,
                # 백엔드가 오래 걸리는 작업(이미지/영상 생성) 중에도
                # heartbeat가 주기적으로 전송되어 연결이 유지된다.
                # 주의: SSE 주석(': heartbeat')은 EventSource에서 어떤 이벤트도
                # dispatch하지 않으므로, 프론트엔드 idle 타이머가 리셋되려면
                # 반드시 실제 이벤트여야 한다 (plan.md Cause C).
                event, data = q.get(timeout=15)
            except queue.Empty:
                try:
                    if not _sse(handler, 'heartbeat', {}):
                        break  # client disconnected during heartbeat
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    break  # client disconnected during heartbeat
                continue
            # _sse()는 클라이언트 연결이 끊어지면 False를 반환한다.
            # False면 즉시 루프를 종료해 죽은 소켓에 계속 쓰지 않게 한다
            # (WinError 10053 폭주 방지 — 에이전트 응답이 UI에 전달되지
            # 않는 "서버응답 없음" 증상의 근본 원인).
            if not _sse(handler, event, data):
                break
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
    # #27 fix: save()를 데몬 스레드로 비동기화하여 HTTP 응답이 즉시 반환되도록 함.
    # save() 내부의 _write_session_index() (O(n) I/O) + _save_session_to_db()
    # (sqlite3.connect timeout=10) 때문에 최대 10초 이상 블로킹될 수 있음.
    def _save_async():
        try:
            s.save()
        except Exception:
            _logger.warning("Async save failed for session %s", s.session_id, exc_info=True)
    threading.Thread(target=_save_async, daemon=True).start()
    set_last_workspace(workspace)
    # Auto-cancel any existing stream for this session so the new message
    # doesn't have to wait for the previous run_conversation() to finish.
    from api.streaming import cancel_session_streams
    cancelled_previous = cancel_session_streams(s.session_id)
    stream_id = uuid.uuid4().hex
    q = queue.Queue()
    if cancelled_previous:
        # 이전 실행 중이던 작업이 자동 취소되었음을 새 스트림으로 안내해,
        # 사용자가 이전 작업이 왜 멈췄는지 모르게 되는 상황을 방지 (plan.md Cause D).
        q.put_nowait(('notice', {'message': '새 메시지 전송으로 이전 작업이 자동 취소되었습니다.'}))
    with STREAMS_LOCK:
        STREAMS[stream_id] = q
    planning_mode = body.get('planning_mode', False)
    open_tabs = body.get('open_tabs') or []
    media_options = body.get('media_options') or {}
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(s.session_id, msg, model, workspace, stream_id, attachments, planning_mode, open_tabs, media_options),
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
