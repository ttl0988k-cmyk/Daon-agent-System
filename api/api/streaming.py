"""
Hermes Web UI -- SSE streaming engine and agent thread runner.
Includes Sprint 10 cancel support via CANCEL_FLAGS.
"""
import json
import logging
import os
import queue
import re
import threading
import time
import traceback
from pathlib import Path

_logger = logging.getLogger(__name__)

from api.config import (
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, CLI_TOOLSETS,
    LOCK, SESSIONS, SESSION_DIR,
    _get_session_agent_lock, _set_thread_env, _clear_thread_env,
    resolve_model_provider,
)

# Global lock for os.environ writes. Per-session locks (_agent_lock) prevent
# concurrent runs of the SAME session, but two DIFFERENT sessions can still
# interleave their os.environ writes. This global lock serializes the env
# save/restore around the entire agent run.
_ENV_LOCK = threading.Lock()

# Map stream_id → AIAgent instance so cancel_stream() can call agent.interrupt()
# to force-kill in-flight HTTP requests instead of waiting for the 120s timeout.
_ACTIVE_AGENTS = {}
_ACTIVE_AGENTS_LOCK = threading.Lock()

# Reverse mapping: session_id → stream_id so we can auto-cancel a session's
# previous stream when a new message arrives for the same session.
_ACTIVE_SESSION_STREAMS = {}
_ACTIVE_SESSION_STREAMS_LOCK = threading.Lock()

# Cache of recently completed streams (stream_id → done_event_data).
# When a client's EventSource auto-reconnects after stream completion,
# we serve the cached 'done' event instead of returning 404.
# Entries auto-expire after 60 seconds.
_COMPLETED_STREAMS = {}
_COMPLETED_STREAMS_LOCK = threading.Lock()

# Lazy import to avoid circular deps -- hermes-agent is on sys.path via api/config.py
try:
    from run_agent import AIAgent
    from hermes_state import SessionDB
except ImportError:
    AIAgent = None
    SessionDB = None
from api.models import get_session, title_from
from api.workspace import set_last_workspace

# P6: Shared schema validation — validates message/session shapes against the SSOT contract
try:
    from shared.schema import validate_message as _validate_msg, validate_session_compact as _validate_sess
    _SCHEMA_AVAILABLE = True
except ImportError:
    _validate_msg = lambda d: (True, "")
    _validate_sess = lambda d: (True, "")
    _SCHEMA_AVAILABLE = False

# Fields that are safe to send to LLM provider APIs.
# Everything else (attachments, timestamp, _ts, etc.) is display-only
# metadata added by the webui and must be stripped before the API call.
_API_SAFE_MSG_KEYS = {'role', 'content', 'tool_calls', 'tool_call_id', 'name', 'refusal'}


def _sanitize_messages_for_api(messages):
    """Return a deep copy of messages with only API-safe fields.

    The webui stores extra metadata on messages (attachments, timestamp, _ts)
    for display purposes. Some providers (e.g. Z.AI/GLM) reject unknown fields
    instead of ignoring them, causing HTTP 400 errors on subsequent messages.

    System-role messages are intentionally stripped here: the caller always
    passes a fresh system_message directly to run_conversation(), so any
    system messages left in the history are stale (from a previous model) and
    would conflict with the new model's identity prompt.
    """
    clean = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        # ?�전 모델???�스???�롬?�트 중복 주입 방�?: system 메시지???�스?�리?�서 무조�??�거
        if msg.get('role') == 'system':
            continue
        sanitized = {k: v for k, v in msg.items() if k in _API_SAFE_MSG_KEYS}
        if sanitized.get('role'):
            clean.append(sanitized)
    return clean


# ── Agent Voice Output: Tool → Korean Speech Mapping ─────────────────────
# 툴 이벤트 발생 시 짧은 한국어 음성 피드백을 위한 매핑 테이블.
# key = tool_name (hermes-agent 툴 이름), value = 읽을 한국어 문장.
# tool_name에 '.started' / '.completed' 접미사를 붙여 더 세밀하게 매핑 가능.
_TOOL_SPEAK_MAP = {
    # 범용
    'todo': '작업 계획을 세우고 있습니다.',
    'web_search': '웹 검색을 시작합니다.',
    'web_extract': '웹 페이지 내용을 추출합니다.',
    'read_file': '파일을 읽고 있습니다.',
    'write_file': '파일을 생성합니다.',
    'patch': '코드를 수정합니다.',
    'search_files': '파일을 검색하고 있습니다.',
    'terminal': '터미널 명령을 실행합니다.',
    'execute_command': '명령을 실행합니다.',
    'browser_navigate': '브라우저를 실행합니다.',
    'browser_snapshot': '브라우저 화면을 분석합니다.',
    'browser_click': '브라우저를 조작합니다.',
    'vision_analyze': '이미지를 분석하고 있습니다.',
    'image_generate': '이미지를 생성합니다.',
    'execute_code': '코드를 실행합니다.',
    'delegate_task': '하위 작업을 위임합니다.',
    'memory': '메모리를 검색하고 있습니다.',
    'clarify': '사용자에게 확인이 필요합니다.',
    'apply_diff': '코드 변경사항을 적용합니다.',
    'skill_view': '스킬 정보를 확인하고 있습니다.',
    'text_to_speech': '음성을 생성합니다.',
    # tool.started / tool.completed 구분
    'web_search.started': '웹 검색을 시작합니다.',
    'web_search.completed': '검색이 완료되었습니다.',
    'write_file.started': '파일을 생성합니다.',
    'write_file.completed': '파일 생성이 완료되었습니다.',
    'terminal.started': '터미널 명령을 실행합니다.',
    'terminal.completed': '명령 실행이 완료되었습니다.',
    'patch.started': '코드를 수정합니다.',
    'patch.completed': '코드 수정이 완료되었습니다.',
    'browser_navigate.started': '브라우저를 실행합니다.',
    'execute_command.started': '명령을 실행합니다.',
    'execute_command.completed': '명령 실행이 완료되었습니다.',
    'todo.started': '작업 계획을 세우고 있습니다.',
    'delegate_task.started': '하위 에이전트에게 작업을 위임합니다.',
    'delegate_task.completed': '하위 작업이 완료되었습니다.',
    'apply_diff.started': '코드 변경사항을 적용합니다.',
    'read_file.started': '파일을 읽고 있습니다.',
    'search_files.started': '파일을 검색하고 있습니다.',
}

# ── Speak dedup: 같은 툴이 쿨다운 내 반복되지 않도록 억제 ──
_SPEAK_COOLDOWN_SEC = 4.0  # 초
_SPEAK_COOLDOWN: dict[str, float] = {}  # key: tool_name → 마지막 emit 시각


def _get_speak_text(tool_name: str, event_type: str) -> str | None:
    """툴 이름과 이벤트 타입으로부터 음성 출력할 한국어 텍스트를 반환.
    같은 툴은 쿨다운(4초) 내 중복 출력하지 않는다.
    (tool.started → tool.completed 연속 호출 시 started만 음성 출력)"""
    et = event_type.replace('tool.', '')
    now = time.time()

    # ── 오래된 키 정리 (60초 이상 지난 항목 제거) ──
    stale = [k for k, v in _SPEAK_COOLDOWN.items() if now - v > 60]
    for k in stale:
        del _SPEAK_COOLDOWN[k]

    # ── 쿨다운 체크: 같은 툴이면 started/completed 구분 없이 억제 ──
    last = _SPEAK_COOLDOWN.get(tool_name)
    if last is not None and (now - last) < _SPEAK_COOLDOWN_SEC:
        return None

    # 1) tool_name.event_type 형태 먼저 확인 (예: "web_search.started")
    key = f"{tool_name}.{et}"
    if key in _TOOL_SPEAK_MAP:
        _SPEAK_COOLDOWN[tool_name] = now
        return _TOOL_SPEAK_MAP[key]
    # 2) tool_name 만으로 확인
    if tool_name in _TOOL_SPEAK_MAP:
        _SPEAK_COOLDOWN[tool_name] = now
        return _TOOL_SPEAK_MAP[tool_name]
    # 3) 매핑 없음 → 음성 출력 안 함
    return None


def _generate_voice_summary(resolved_model: str, resolved_provider: str,
                            resolved_base_url: str, resolved_api_key: str,
                            resolved_api_mode: str,
                            user_msg: str, job_tools: list,
                            last_assistant_content: str) -> str | None:
    """LLM을 호출하여 작업 결과를 2문장 이내의 한국어 음성 요약으로 생성한다.
    api_mode에 따라 Anthropic SDK 또는 OpenAI SDK를 선택적으로 사용."""
    if not resolved_api_key:
        print("[Speak] No API credentials for summary generation", flush=True)
        return None

    # 툴 목록 요약
    tool_names = list(dict.fromkeys(name for name, _status in job_tools if name))[:20]
    tool_list_str = ', '.join(tool_names)

    # 마지막 응답에서 핵심만 추출 (최대 500자)
    assistant_snippet = (last_assistant_content or '')[:500].strip()

    prompt = (
        "다음은 AI 에이전트의 작업 결과입니다. "
        "이 결과를 한국어 2문장 이내로 간결하게 음성 안내용으로 요약하세요. "
        "불필요한 설명이나 서론 없이 핵심 결과만 말하듯이 작성하세요.\n\n"
        f"사용자 요청: {user_msg[:200]}\n"
        f"실행된 도구: {tool_list_str}\n"
        f"에이전트 최종 응답: {assistant_snippet}\n\n"
        "음성 안내 요약 (한국어 2문장 이내):"
    )

    system_msg = "당신은 작업 결과를 간결한 한국어 음성 안내로 요약하는 어시스턴트입니다."

    # ── Anthropic Messages API 모드 ──
    if resolved_api_mode == 'anthropic_messages':
        try:
            import anthropic
        except ImportError:
            print("[Speak] anthropic package not available", flush=True)
            return f"작업이 완료되었습니다. {len(job_tools)}개의 작업을 처리했습니다."
        try:
            client = anthropic.Anthropic(
                api_key=resolved_api_key,
                base_url=resolved_base_url,
                timeout=15.0,
            )
            response = client.messages.create(
                model=resolved_model,
                max_tokens=120,
                temperature=0.3,
                system=system_msg,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response.content[0].text.strip()
            summary = summary.strip('"\'""\'\'')
            if summary and not summary.endswith(('.', '!', '?', '다', '요', '니다')):
                summary += '.'
            print(f"[Speak] LLM summary (Anthropic): {summary}", flush=True)
            return summary
        except Exception as e:
            print(f"[Speak] LLM summary API call failed (Anthropic): {e}", flush=True)
            return f"작업이 완료되었습니다. {len(job_tools)}개의 작업을 처리했습니다."

    # ── OpenAI-compatible API 모드 (기본) ──
    try:
        from openai import OpenAI
    except ImportError:
        print("[Speak] openai package not available for summary generation", flush=True)
        return None

    if not resolved_base_url:
        print("[Speak] No base_url for OpenAI-compatible summary", flush=True)
        return f"작업이 완료되었습니다. {len(job_tools)}개의 작업을 처리했습니다."

    try:
        client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=15.0,
        )
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_tokens=120,
            temperature=0.3,
        )
        summary = response.choices[0].message.content.strip()
        summary = summary.strip('"\'""\'\'')
        if summary and not summary.endswith(('.', '!', '?', '다', '요', '니다')):
            summary += '.'
        print(f"[Speak] LLM summary (OpenAI): {summary}", flush=True)
        return summary
    except Exception as e:
        print(f"[Speak] LLM summary API call failed (OpenAI): {e}", flush=True)
        return f"작업이 완료되었습니다. {len(job_tools)}개의 작업을 처리했습니다."


def _sse(handler, event, data):
    """Write one SSE event to the response stream."""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    try:
        handler.wfile.write(payload.encode('utf-8'))
        handler.wfile.flush()
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f'[SSE-DIAG] _sse write failed for event={event}: {e}', flush=True)  # Client disconnected before SSE event could be sent


def cancel_session_streams(session_id: str) -> bool:
    """Cancel all active streams for the given session.

    Called automatically when a new message is sent for a session that
    already has a running agent, so the user doesn't have to wait for
    the previous run_conversation() to finish before the new one starts.

    Returns True if at least one stream was cancelled.
    """
    cancelled_any = False
    with _ACTIVE_SESSION_STREAMS_LOCK:
        old_stream_id = _ACTIVE_SESSION_STREAMS.get(session_id)
    if old_stream_id:
        _logger.info("Auto-cancelling previous stream %s for session %s", old_stream_id, session_id)
        cancelled_any = cancel_stream(old_stream_id)
    return cancelled_any


def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id, attachments=None, planning_mode=False, open_tabs=None):
    """Run agent in background thread, writing SSE events to STREAMS[stream_id]."""
    q = STREAMS.get(stream_id)
    if q is None:
        return

    # Auto-cancel any previous stream for this session before starting
    cancel_session_streams(session_id)

    # Register this stream as the active one for this session
    with _ACTIVE_SESSION_STREAMS_LOCK:
        _ACTIVE_SESSION_STREAMS[session_id] = stream_id

    # Sprint 10: create a cancel event for this stream
    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    def put(event, data):
        # If cancelled, drop all further events except the cancel event itself
        if cancel_event.is_set() and event not in ('cancel', 'error'):
            return
        try:
            q.put_nowait((event, data))
        except Exception:
            _logger.warning("Failed to enqueue SSE event %s for stream %s", event, stream_id, exc_info=True)

    try:
        s = get_session(session_id)
        # (Workspace override removed to allow user-selected paths)
        s.model = model

        _agent_lock = _get_session_agent_lock(session_id)
        # TD1: set thread-local env context so concurrent sessions don't clobber globals
        # Check for pre-flight cancel (user cancelled before agent even started)
        if cancel_event.is_set():
            put('cancel', {'message': 'Cancelled before start'})
            return

        # Resolve profile home for this agent run (snapshot at start)
        try:
            from api.profiles import get_active_hermes_home
            _profile_home = str(get_active_hermes_home())
        except ImportError:
            _logger.debug("api.profiles not available, falling back to HERMES_HOME env var")
            _profile_home = os.environ.get('HERMES_HOME', '')

        _set_thread_env(
            TERMINAL_CWD=str(s.workspace),
            HERMES_EXEC_ASK='1',
            HERMES_SESSION_KEY=session_id,
            HERMES_HOME=_profile_home,
        )
        # Still set process-level env as fallback for tools that bypass thread-local
        with _ENV_LOCK:
          old_cwd = os.environ.get('TERMINAL_CWD')
          old_exec_ask = os.environ.get('HERMES_EXEC_ASK')
          old_session_key = os.environ.get('HERMES_SESSION_KEY')
          old_hermes_home = os.environ.get('HERMES_HOME')
          os.environ['TERMINAL_CWD'] = str(s.workspace)
          os.environ['HERMES_EXEC_ASK'] = '1'
          os.environ['HERMES_SESSION_KEY'] = session_id
          if _profile_home:
              os.environ['HERMES_HOME'] = _profile_home

        try:
          # Stateful ANSI stripping with inline regex (no external dependency).
          # Buffer accumulates across token boundaries so split escape sequences
          # (e.g. "\x1b" in one token, "[e~" in the next) are still stripped.
          _token_buf = ""
          _token_sent = 0
          _ANSI_RE = re.compile(
              r"\x1b"
              r"(?:"
                  r"\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
                  r"|\][\s\S]*?(?:\x07|\x1b\\)"
                  r"|[PX^_][\s\S]*?(?:\x1b\\)"
                  r"|[\x20-\x2f]+[\x30-\x7e]"
                  r"|[\x30-\x7e]"
              r")"
              r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
              r"|\x9d[\s\S]*?(?:\x07|\x9c)"
              r"|[\x80-\x9f]",
              re.DOTALL,
          )

          def on_token(text):
              nonlocal _token_buf, _token_sent
              if text is None:
                  return  # end-of-stream sentinel
              _token_buf += text
              cleaned = _ANSI_RE.sub("", _token_buf)
              if len(cleaned) > _token_sent:
                  delta = cleaned[_token_sent:]
                  _token_sent = len(cleaned)
                  put('token', {'text': delta})

          # Track job state for progress block UX
          _job_active = False
          _job_has_error = False
          _job_tools = []  # List of (tool_name, status) for progress display
          # Track pending file edits: tool.started stores path so tool.completed
          # (which receives args=None) can re-read the file from disk.
          _pending_file_edits = {}  # tool_name -> path

          def on_tool(event_type, tool_name, preview, args, **kwargs):
              # tool_executor.py calls with 4 positional args:
              #   agent.tool_progress_callback("tool.started", function_name, preview, function_args)
              #   agent.tool_progress_callback("tool.completed", function_name, None, None, duration=..., is_error=...)
              #   agent.tool_progress_callback("tool.output", function_name, text, None)  -- streaming terminal output
              print(f"[MonacoEditorUX-debug] on_tool called: event_type={event_type} tool_name={tool_name} preview={str(preview)[:80]}", flush=True)

              nonlocal _job_active, _job_has_error, _job_tools

              # Real-time terminal output streaming
              if event_type == 'tool.output':
                  put('terminal_output', {'tool': tool_name, 'text': preview})
                  return

              # ── Agent Voice Output: 툴 이벤트 → 한국어 음성 피드백 ──
              speak_text = _get_speak_text(tool_name, event_type)
              if speak_text:
                  put('speak', {'text': speak_text, 'tool': tool_name, 'event': event_type})

              # Track job state
              if event_type == 'tool.started':
                  _job_active = True
                  _job_tools.append((tool_name, 'running'))
                  # Emit job_start event (first tool started)
                  if _job_tools.count((tool_name, 'running')) == 1 or len(_job_tools) == 1:
                      put('job', {'type': 'start', 'tool': tool_name, 'preview': preview, 'tools': _job_tools.copy()})
              elif event_type == 'tool.completed':
                  is_error = kwargs.get('is_error', False)
                  duration = kwargs.get('duration', 0)
                  if is_error:
                      _job_has_error = True
                  # Update tool status
                  for i, (name, status) in enumerate(_job_tools):
                      if name == tool_name and status == 'running':
                          _job_tools[i] = (tool_name, 'error' if is_error else 'completed')
                          break
                  # Emit progress update
                  put('job', {'type': 'progress', 'tool': tool_name, 'status': 'error' if is_error else 'completed', 'duration': duration, 'tools': _job_tools.copy()})
                  # ── File edit finalization ──
                  # tool.completed receives args=None, so re-read the written file
                  # from disk and push the authoritative content to the editor.
                  if not is_error and tool_name in ('write_file', 'patch'):
                      _fe_path = _pending_file_edits.pop(tool_name, None)
                      if _fe_path:
                          try:
                              _fe_target = Path(s.workspace) / _fe_path
                              if _fe_target.exists() and _fe_target.is_file():
                                  _fe_content = _fe_target.read_text(encoding='utf-8', errors='replace')
                                  put('file_edit_done', {'name': tool_name, 'path': _fe_path, 'content': _fe_content})
                          except Exception as _fe_err:
                              _logger.warning("file_edit_done read failed for %s: %s", _fe_path, _fe_err)
              
              args_snap = {}
              if isinstance(args, dict):
                  for k, v in list(args.items())[:4]:
                      # Preserve list/dict types intact for structured tool args
                      # (e.g. ask_followup_question.follow_up must stay as array)
                      if isinstance(v, (list, dict)):
                          args_snap[k] = v
                      else:
                          s2 = str(v)
                          args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')
              put('tool', {'name': tool_name, 'event': event_type, 'preview': preview, 'args': args_snap})
              # Monaco Editor UX를 위한 파일 편집 이벤트 전송
              if event_type == 'tool.started' and tool_name in ('write_file', 'patch') and isinstance(args, dict):
                  print(f"[MonacoEditorUX-debug] ✅ file_edit event SENT for {tool_name} args_keys={list(args.keys())}", flush=True)
                  # Remember the path so tool.completed can re-read from disk.
                  _fe_p = args.get('path') or args.get('file_path') or ''
                  if _fe_p:
                      _pending_file_edits[tool_name] = _fe_p
                  put('file_edit', {'name': tool_name, 'args': args})
              # ── Diff Preview auto-generation ──
              # When write_file/patch is called, auto-generate a diff preview
              # by comparing existing file content with the new content.
              if event_type == 'tool.started' and tool_name in ('write_file', 'patch', 'apply_diff') and isinstance(args, dict):
                  _file_path = args.get('path') or args.get('file_path') or ''
                  _new_content = args.get('content') or args.get('new_content') or ''
                  if _file_path and _new_content:
                      # Check if Architect mode requires approval
                      _architect_approval = False
                      try:
                          from api.routes.mode_routes import get_session_mode
                          _current_mode = get_session_mode(session_id)
                          _architect_approval = (_current_mode == 'architect')
                      except Exception:
                          pass

                      try:
                          from api.routes.diff_routes import _compute_line_changes as _calc_lc
                          _target = Path(s.workspace) / _file_path
                          _original = ''
                          if _target.exists() and _target.is_file():
                              _original = _target.read_text(encoding='utf-8')
                          _lc = _calc_lc(_original, _new_content)
                          _preview_data = {
                              'session_id': session_id,
                              'path': _file_path,
                              'original_snippet': '\n'.join(_original.split('\n')[:80]),
                              'new_snippet': '\n'.join(_new_content.split('\n')[:80]),
                              'line_changes': _lc,
                              'source_agent': 'architect' if _architect_approval else 'coder',
                              'preview_id': '',  # filled below
                              'approval_required': _architect_approval,
                          }
                          try:
                              import uuid as _uuid
                              _pid = _uuid.uuid4().hex[:16]
                              from api.routes.diff_routes import _diff_previews, _previews_lock
                              with _previews_lock:
                                  _diff_previews[_pid] = {
                                      'session_id': session_id,
                                      'path': _file_path,
                                      'blocks': [],
                                      'original': _original,
                                      'new_content': _new_content,
                                      'line_changes': _lc,
                                      'source_agent': 'architect' if _architect_approval else 'coder',
                                      'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                      'approval_required': _architect_approval,
                                  }
                              _preview_data['preview_id'] = _pid
                              _preview_data['original_full'] = _original
                              _preview_data['new_full'] = _new_content
                              put('diff_preview', _preview_data)
                              print(f"[DiffPreview] ✅ diff_preview SSE emitted: preview_id={_pid} path={_file_path} architect_approval={_architect_approval}", flush=True)

                              # If Architect mode, set pending approval
                              if _architect_approval:
                                  try:
                                      from api.approval import set_pending as _set_approval
                                      _set_approval(session_id, {
                                          'preview_id': _pid,
                                          'path': _file_path,
                                          'line_changes': _lc,
                                          'source_agent': 'architect',
                                          'message': f'Architect mode change to {_file_path}',
                                          'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                                      })
                                      put('approval', {
                                          'preview_id': _pid,
                                          'path': _file_path,
                                          'line_changes': _lc,
                                          'message': f'Architect mode: {_file_path} needs your approval',
                                          'status': 'pending',
                                      })
                                      print(f"[Approval] ⚠️ Architect approval pending: preview_id={_pid}", flush=True)
                                  except Exception as _appr_e:
                                      print(f"[Approval] WARNING: set_pending failed: {_appr_e}", flush=True)
                          except Exception as _dp_e:
                              print(f"[DiffPreview] WARNING: internal preview failed: {_dp_e}", flush=True)
                      except Exception as _lc_e:
                          print(f"[DiffPreview] WARNING: line_changes compute failed: {_lc_e}", flush=True)
              # also check for pending approval and surface it immediately
              try:
                  from api.approval import has_pending as _has_pending, get_pending as _get_pending
                  if _has_pending(session_id):
                      p = _get_pending(session_id)
                      if p:
                          put('approval', p)
              except Exception:
                  pass  # api.approval not available

              # ── Patch Registry: 파일 편집 전 등록된 패치 경고 주입 ──
              if event_type == 'tool.started' and tool_name in ('write_file', 'write_to_file', 'patch', 'apply_diff') and isinstance(args, dict):
                  try:
                      from api.patch_registry import get_warning_block
                      _patch_target = args.get('path') or args.get('file_path') or ''
                      if _patch_target:
                          _patch_warning = get_warning_block(_patch_target)
                          if _patch_warning:
                              put('patch_warning', {'path': _patch_target, 'warning': _patch_warning, 'tool': tool_name})
                              print(f"[PatchRegistry] ⚠️ warning injected for {_patch_target} (tool={tool_name})", flush=True)
                  except Exception as _pr_e:
                      print(f"[PatchRegistry] WARNING: hook failed: {_pr_e}", flush=True)

          if AIAgent is None:
              raise ImportError("AIAgent not available -- check that hermes-agent is on sys.path")

          # ── auth.json에서 API 키를 환경변수로 주입 (안전하게 _ENV_LOCK 아래에서) ──
          # ~/.hermes/auth.json의 credential_pool에서 키를 읽어
          # resolve_model_provider가 올바르게 라우팅할 수 있도록 환경변수에 주입한다.
          with _ENV_LOCK:
              try:
                  import json as _json
                  _auth_path = Path.home() / '.hermes' / 'auth.json'
                  if _auth_path.exists():
                      _cp = _json.loads(_auth_path.read_text()).get('credential_pool', {})
                      # gemini → GOOGLE_API_KEY
                      if not os.getenv('GOOGLE_API_KEY') and 'gemini' in _cp:
                          _token = (_cp['gemini'][0].get('access_token') if isinstance(_cp['gemini'], list) and _cp['gemini'] else None)
                          if _token:
                              os.environ['GOOGLE_API_KEY'] = _token
                      # openrouter → OPENROUTER_API_KEY
                      if not os.getenv('OPENROUTER_API_KEY') and 'openrouter' in _cp:
                          _token = (_cp['openrouter'][0].get('access_token') if isinstance(_cp['openrouter'], list) and _cp['openrouter'] else None)
                          if _token:
                              os.environ['OPENROUTER_API_KEY'] = _token
                      # ollama-cloud → OLLAMA_API_KEY
                      if not os.getenv('OLLAMA_API_KEY') and 'ollama-cloud' in _cp:
                          _token = (_cp['ollama-cloud'][0].get('access_token') if isinstance(_cp['ollama-cloud'], list) and _cp['ollama-cloud'] else None)
                          if _token:
                              os.environ['OLLAMA_API_KEY'] = _token
                      # nvidia → NVIDIA_API_KEY
                      if not os.getenv('NVIDIA_API_KEY') and 'nvidia' in _cp:
                          _token = (_cp['nvidia'][0].get('access_token') if isinstance(_cp['nvidia'], list) and _cp['nvidia'] else None)
                          if _token:
                              os.environ['NVIDIA_API_KEY'] = _token
                      # minimax → MINIMAX_API_KEY
                      if not os.getenv('MINIMAX_API_KEY') and 'minimax' in _cp:
                          _token = (_cp['minimax'][0].get('access_token') if isinstance(_cp['minimax'], list) and _cp['minimax'] else None)
                          if _token:
                              os.environ['MINIMAX_API_KEY'] = _token
                      # dashscope → DASHSCOPE_API_KEY
                      if not os.getenv('DASHSCOPE_API_KEY') and 'dashscope' in _cp:
                          _token = (_cp['dashscope'][0].get('access_token') if isinstance(_cp['dashscope'], list) and _cp['dashscope'] else None)
                          if _token:
                              os.environ['DASHSCOPE_API_KEY'] = _token
              except Exception as _inject_e:
                  print(f"[webui] WARNING: auth.json key injection failed: {_inject_e}", flush=True)

          # ?�경변??주입 ??모델/?�로바이???�결??
          resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model)

          # Resolve API key via Hermes runtime provider (matches gateway behaviour)
          resolved_api_key = None
          try:
              from hermes_cli.runtime_provider import resolve_runtime_provider
              _rt = resolve_runtime_provider(requested=resolved_provider)
              resolved_api_key = _rt.get("api_key")
              rt_provider = _rt.get("provider")
              rt_base_url = _rt.get("base_url")
              if rt_provider:
                  resolved_provider = rt_provider
              if rt_base_url and (not resolved_base_url or str(resolved_provider).startswith('custom')):
                  resolved_base_url = rt_base_url
          except Exception as _e:
              print(f"[webui] WARNING: resolve_runtime_provider failed: {_e}", flush=True)

          if not resolved_api_key:
              # auth.json credential_pool?�서 직접 ??추출
              try:
                  import json as _json2
                  _auth_path2 = Path.home() / '.hermes' / 'auth.json'
                  if _auth_path2.exists():
                      _cp2 = _json2.loads(_auth_path2.read_text()).get('credential_pool', {})
                      _search_keys = [resolved_provider]
                      if 'rt_provider' in locals() and rt_provider:
                          _search_keys.append(rt_provider)
                      # google ??gemini ?�방??검??
                      if 'google' in _search_keys:
                          _search_keys.append('gemini')
                      if 'gemini' in _search_keys:
                          _search_keys.append('google')
                      for _k in _search_keys:
                          if _k and _k in _cp2 and isinstance(_cp2[_k], list) and _cp2[_k]:
                              resolved_api_key = _cp2[_k][0].get('access_token')
                              if resolved_api_key:
                                  break
              except Exception:
                  pass

          if resolved_provider in ('zai', 'ollama-cloud') and not resolved_api_key:
              resolved_api_key = os.getenv('OLLAMA_API_KEY')

          # custom_providers.json에서 API 키 fallback (UI에서 등록한 프로바이더)
          if not resolved_api_key and resolved_provider and resolved_provider != 'custom':
              try:
                  from api.managers.model_manager import model_manager as _mm
                  _cp_key = _mm._get_api_key(resolved_provider)
                  if _cp_key:
                      resolved_api_key = _cp_key
                      print(f"[webui] API key resolved from custom_providers.json for '{resolved_provider}'", flush=True)
              except Exception as _cp_e:
                  print(f"[webui] WARNING: custom_providers key lookup failed: {_cp_e}", flush=True)

          # Read per-profile config at call time (not module-level snapshot)
          from api.config import get_config as _get_config
          _cfg = _get_config()

          # Per-profile toolsets (fall back to module-level CLI_TOOLSETS)
          _pt = _cfg.get('platform_toolsets', {})
          _toolsets = _pt.get('webui', CLI_TOOLSETS) if isinstance(_pt, dict) else CLI_TOOLSETS

          # Fallback model from profile config (e.g. for rate-limit recovery)
          _fallback = _cfg.get('fallback_model') or None
          if _fallback:
              # Resolve the fallback through our provider logic too
              fb_model = _fallback.get('model', '')
              fb_provider = _fallback.get('provider', '')
              fb_base_url = _fallback.get('base_url')
              _fallback_resolved = {
                  'model': fb_model,
                  'provider': fb_provider,
                  'base_url': fb_base_url,
              }
          else:
              _fallback_resolved = None

          print(f"[webui-debug] resolved_model={resolved_model} resolved_provider={resolved_provider} resolved_base_url={resolved_base_url}", flush=True)

          # ── Image/Video generation model detection (3-tier: registry > metadata > name) ──
          from api.media_generation import run_media_generation
          from api.managers.model_manager import model_manager as _mm_type
          _media_type = _mm_type.get_model_type(resolved_model)
          if _media_type in ('image', 'video'):
              print(f"[webui] Media generation model detected: {resolved_model} -> {_media_type}", flush=True)
              put('token', {'text': f"🎨 {'이미지' if _media_type == 'image' else '영상'} 생성 중... (모델: {resolved_model})\n\n"})
              try:
                  _media_base_url = resolved_base_url or ''
                  if not _media_base_url:
                      try:
                          from api.managers.model_manager import model_manager as _mm2
                          _media_base_url = _mm2._get_base_url(resolved_provider) or ''
                      except Exception:
                          pass
                  print(f"[webui] Media: base_url={_media_base_url or 'EMPTY'}", flush=True)
                  if not _media_base_url:
                      raise RuntimeError(f"프로바이더 '{resolved_provider}'의 base_url을 찾을 수 없습니다.")
                  print(f"[webui] Media: api_key={'set' if resolved_api_key else 'MISSING'}", flush=True)
                  if not resolved_api_key:
                      raise RuntimeError("API 키가 없습니다. 설정에서 프로바이더 API 키를 등록하세요.")

                  print(f"[webui] Media: calling run_media_generation (model={resolved_model}, type={_media_type})...", flush=True)
                  # 미디어 생성은 60초+ 소요 가능. 그 동안 SSE에 이벤트가 없으면
                  # 프론트엔드 idle timer(30초)가 스트림을 조기 종료한다
                  # (sse.close() → 백엔드 WinError 10053 → media_result 미전달).
                  # 별도 스레드로 실행하고 매 10초 빈 keep-alive token을 보내
                  # 프론트엔드 resetIdleTimer()가 계속 갱신되도록 한다.
                  import threading as _media_threading
                  _media_box = {'result': None, 'error': None, 'done': False}

                  def _run_media_worker():
                      try:
                          _media_box['result'] = run_media_generation(
                              prompt=msg_text,
                              model=resolved_model,
                              base_url=_media_base_url,
                              api_key=resolved_api_key,
                              model_type=_media_type,
                          )
                      except Exception as _w_err:
                          _media_box['error'] = _w_err
                      finally:
                          _media_box['done'] = True

                  _media_worker = _media_threading.Thread(target=_run_media_worker, daemon=True)
                  _media_worker.start()

                  # 완료될 때까지 10초마다 keep-alive token 발송 (빈 텍스트 → 버블 무해)
                  while not _media_box['done']:
                      _media_worker.join(timeout=10.0)
                      if not _media_box['done']:
                          put('token', {'text': ''})  # keep-alive → 프론트엔드 idle timer 리셋

                  if _media_box['error'] is not None:
                      raise _media_box['error']
                  _media_result = _media_box['result']
                  print(f"[webui] Media: run_media_generation returned OK", flush=True)
                  try:
                      _mi = _media_result.get('images', []) if isinstance(_media_result, dict) else []
                      print(f"[webui] Media: result type={_media_result.get('type') if isinstance(_media_result, dict) else '?'} "
                            f"images={len(_mi)} "
                            f"urls={[ (im.get('url') or '')[:100] for im in _mi ]} "
                            f"b64={[ bool(im.get('b64_json')) for im in _mi ]}", flush=True)
                  except Exception as _mr_log_err:
                      print(f"[webui] Media: result log failed: {_mr_log_err}", flush=True)
                  print(f"[webui] Media: SENDING media_result SSE event", flush=True)
                  put('media_result', _media_result)
                  put('done', {'text': ''})
                  print(f"[webui] Media: media_result + done events SENT", flush=True)
              except Exception as _media_err:
                  print(f"[webui] Media generation failed: {_media_err}", flush=True)
                  put('token', {'text': f"\n\n❌ 생성 실패: {_media_err}"})
                  put('done', {'text': ''})
              return

          # ?�?� SessionDB ?�스?�스 1???�성 (모델 ?�환 감�? + AIAgent 공유) ?�?�?�?�?�?�
          # SessionDB()??SQLite 커넥?�을 ?�기 ?�문??�??�청마다 ??�??�성?�면
          # 지?�이 발생?�다. ?�나�?만들??모델 ?�환 감�??� AIAgent??모두 ?�다.
          _session_db_instance = SessionDB() if SessionDB else None

          # 같�? ?�션 ??모델 ?�환 ???�전 system_prompt 무효??
          # (?�???�용?� 건드리�? ?�음, system_prompt�?None ??run_agent가 ?�빌??
          if _session_db_instance:
              try:
                  _stored = _session_db_instance.get_session(session_id)
                  if (
                      _stored
                      and _stored.get('system_prompt')
                      and _stored.get('model')
                      and _stored['model'] != resolved_model
                  ):
                      _session_db_instance.update_system_prompt(session_id, None)
                      print(
                          f"[webui] Model changed ({_stored['model']} ??{resolved_model}): "
                          f"cleared cached system_prompt for session {session_id}",
                          flush=True,
                      )
              except Exception as _sp_e:
                  print(f"[webui] WARNING: system_prompt invalidation failed: {_sp_e}", flush=True)

          # ── Inject Browser Context if active ──
          _ephemeral_prompt = None
          try:
              from api.routes.browser_routes import _browser_active, _last_url
              if _browser_active and _last_url:
                  _ephemeral_prompt = f"[System Note: The user currently has a browser tab open viewing URL: {_last_url}. If they ask to analyze or interact with the page, use your browser-agent skill to assist them.]"
          except Exception:
              pass

          print(f"[webui-debug] Creating AIAgent: model={resolved_model} provider={resolved_provider} base_url={resolved_base_url} api_key={'set' if resolved_api_key else 'NONE'}", flush=True)
          agent = AIAgent(
              model=resolved_model,
              provider=resolved_provider,
              base_url=resolved_base_url,
              api_key=resolved_api_key,
              platform='webui',
              quiet_mode=True,
              enabled_toolsets=_toolsets,
              fallback_model=_fallback_resolved,
              session_id=session_id,
              session_db=_session_db_instance,
              stream_delta_callback=on_token,
              tool_progress_callback=on_tool,
              ephemeral_system_prompt=_ephemeral_prompt,
          )
          print(f"[webui-debug] AIAgent created, api_mode={getattr(agent, 'api_mode', '?')}", flush=True)

          # ── Path A: Redirect browser_tool._run_browser_command to Playwright via CDP ──
          # This replaces the agent-browser CLI (Path B) with direct Playwright calls,
          # eliminating zombie CDP sessions and subprocess.Popen daemon processes.
          try:
              from api.browser_bridge import patch_browser_tool
              patch_browser_tool()
          except Exception as _bt_err:
              print(f"[webui] WARNING: browser_tool patch failed (will use CLI fallback): {_bt_err}", flush=True)

          # Register agent so cancel_stream() can call agent.interrupt()
          # to force-abort in-flight HTTP requests instead of waiting for the 120s timeout.
          with _ACTIVE_AGENTS_LOCK:
              _ACTIVE_AGENTS[stream_id] = agent

          # === [NEW] Inject MCP tools into the Hermes Registry ===
          # A 시스템(MCPManager)에서 연결된 MCP 서버들의 도구를
          # hermes-agent 의 tools.registry.registry 에 정식 등록한다.
          # 등록된 도구는 handle_function_call → registry.dispatch 경로로
          # 정상 실행된다.
          try:
              from api.mcp_client import get_mcp_manager
              from tools.registry import registry
              import re as _mcp_re
              
              mcp_manager = get_mcp_manager()
              
              # FORCE SYNC: Wait up to 5 seconds for MCP servers (was 30s — too slow after cancel)
              mcp_tools = []
              for _ in range(25):
                  # Bail out immediately if this stream was cancelled while waiting
                  if cancel_event.is_set():
                      print(f"[webui] MCP sync aborted — stream cancelled for session {session_id}", flush=True)
                      break
                  mcp_tools = mcp_manager.get_all_tools()
                  pending = sum(1 for c in mcp_manager._connections.values() if not c.connected and not c.error)
                  if pending == 0:
                      break
                  time.sleep(0.2)
              
              # Inline equivalents of tools.mcp_tool helpers (avoid import side-effects)
              def _safe_name(raw: str) -> str:
                  # 하이픈(-) 보존: PlayMCP 등 외부 게이트웨이에서 하이픈을
                  # 사용하는 도구 이름이 언더스코어로 변환되지 않도록 방지.
                  return _mcp_re.sub(r'[^A-Za-z0-9_-]', '_', str(raw or ''))
              
              def _normalize_input_schema(schema):
                  if not schema:
                      return {"type": "object", "properties": {}}
                  if schema.get("type") == "object" and "properties" not in schema:
                      return {**schema, "properties": {}}
                  return schema
              
              injected_count = 0
              registered_toolsets = set()
              
              for t in mcp_tools:
                  server_id = t.get('_mcp_server', 'unknown')
                  orig_name = t.get('name', '')
                  
                  safe_srv = _safe_name(server_id)
                  safe_tool = _safe_name(orig_name)
                  mcp_func_name = f"mcp_{safe_srv}_{safe_tool}"
                  toolset_name = f"mcp-{safe_srv}"
                  
                  # 1) OpenAI-format schema for agent.tools (model visibility)
                  api_schema = {
                      "type": "function",
                      "function": {
                          "name": mcp_func_name,
                          "description": t.get('description', f"MCP tool {orig_name} from {server_id}"),
                          "parameters": t.get('inputSchema', {"type": "object", "properties": {}})
                      }
                  }
                  agent.tools.append(api_schema)
                  agent.valid_tool_names.add(mcp_func_name)
                  
                  # 2) Flat registry schema for dispatch
                  registry_schema = {
                      "name": mcp_func_name,
                      "description": api_schema["function"]["description"],
                      "parameters": _normalize_input_schema(t.get('inputSchema')),
                  }
                  
                  # 3) Handler: A 시스템의 mcp_manager.call_tool() 사용
                  #    registry.dispatch 는 handler(args_dict, **kwargs) → str 시그니처를 요구
                  def _make_handler(sid, tname):
                      def _handler(args: dict, **kwargs) -> str:
                          import json as _json
                          print(f"[mcp-debug] tools/call → server={sid} tool_name={tname} args_keys={list(args.keys()) if args else 'none'}", flush=True)
                          result = mcp_manager.call_tool(sid, tname, args)
                          if result.get('ok'):
                              payload = result.get('result', 'Success')
                              if isinstance(payload, str):
                                  return _json.dumps({"result": payload}, ensure_ascii=False)
                              return _json.dumps({"result": _json.dumps(payload, ensure_ascii=False, default=str)}, ensure_ascii=False)
                          else:
                              return _json.dumps({"error": result.get('error', 'Unknown error')}, ensure_ascii=False)
                      return _handler
                  
                  # 4) check_fn: 서버 연결 상태 확인 (A 시스템 기준)
                  def _make_check_fn(sid):
                      def _check() -> bool:
                          conn = mcp_manager._connections.get(sid)
                          return conn is not None and conn.connected
                      return _check
                  
                  registry.register(
                      name=mcp_func_name,
                      toolset=toolset_name,
                      schema=registry_schema,
                      handler=_make_handler(server_id, orig_name),
                      check_fn=_make_check_fn(server_id),
                      is_async=False,
                      description=registry_schema["description"],
                  )
                  registered_toolsets.add(toolset_name)
                  injected_count += 1
              
              # toolset alias 등록: "filesystem" → "mcp-filesystem" 매핑
              for ts in registered_toolsets:
                  alias = ts.replace("mcp-", "", 1)
                  registry.register_toolset_alias(alias, ts)
              
              if injected_count > 0:
                  print(f"[webui-debug] Registered {injected_count} MCP tools into Hermes registry + agent.tools.", flush=True)
                  # Debug log
                  try:
                      import json
                      with open("mcp_injection_debug.json", "w", encoding="utf-8") as f:
                          json.dump([t['function']['name'] for t in agent.tools], f, indent=2)
                  except Exception:
                      pass
          except Exception as e:
              import traceback as _tb
              print(f"[webui-debug] Failed to inject MCP tools: {e}", flush=True)
              _tb.print_exc()
          # ========================================================

          # === [Patch Registry] 에이전트 도구 주입: query_patches / register_patch ===
          try:
              from api.patch_registry import query_patches as _pr_query, register_patch as _pr_register, query_all_patches as _pr_query_all
              from tools.registry import registry as _pr_registry

              # 1) query_patches 도구
              _pr_query_schema = {
                  "type": "function",
                  "function": {
                      "name": "query_patches",
                      "description": "Query registered load-bearing patches for a file. Returns warnings about code that must NOT be reverted. Use before editing any file to check for protected patches.",
                      "parameters": {
                          "type": "object",
                          "properties": {
                              "file_path": {"type": "string", "description": "Relative file path to query patches for (e.g. 'api/api/streaming.py')"},
                          },
                          "required": ["file_path"]
                      }
                  }
              }
              agent.tools.append(_pr_query_schema)
              agent.valid_tool_names.add("query_patches")

              def _pr_query_handler(args: dict, **kwargs) -> str:
                  import json as _json
                  fp = args.get('file_path', '')
                  results = _pr_query(fp)
                  if not results:
                      return _json.dumps({"patches": [], "message": f"No registered patches for {fp}"}, ensure_ascii=False)
                  return _json.dumps({"patches": results, "count": len(results)}, ensure_ascii=False)

              _pr_registry.register(
                  name="query_patches",
                  toolset="patch-registry",
                  schema={"name": "query_patches", "description": "Query registered patches for a file", "parameters": _pr_query_schema["function"]["parameters"]},
                  handler=_pr_query_handler,
                  check_fn=lambda: True,
                  is_async=False,
                  description="Query registered load-bearing patches for a file",
              )

              # 2) register_patch 도구
              _pr_register_schema = {
                  "type": "function",
                  "function": {
                      "name": "register_patch",
                      "description": "Register a load-bearing patch that must NOT be reverted. Call this after making an important fix so future edits are warned.",
                      "parameters": {
                          "type": "object",
                          "properties": {
                              "file_path": {"type": "string", "description": "Relative file path the patch applies to"},
                              "description": {"type": "string", "description": "What the patch does (e.g. 'str type guard for models list')"},
                              "reason": {"type": "string", "description": "Why this patch exists and why reverting it would break things"},
                              "commit_hash": {"type": "string", "description": "Git commit hash if available"},
                          },
                          "required": ["file_path", "description"]
                      }
                  }
              }
              agent.tools.append(_pr_register_schema)
              agent.valid_tool_names.add("register_patch")

              def _pr_register_handler(args: dict, **kwargs) -> str:
                  import json as _json
                  fp = args.get('file_path', '')
                  desc = args.get('description', '')
                  reason = args.get('reason', '')
                  commit = args.get('commit_hash', '')
                  if not fp or not desc:
                      return _json.dumps({"error": "file_path and description are required"}, ensure_ascii=False)
                  pid = _pr_register(fp, desc, commit_hash=commit, reason=reason)
                  if pid:
                      return _json.dumps({"ok": True, "patch_id": pid, "message": f"Patch registered for {fp}"}, ensure_ascii=False)
                  return _json.dumps({"error": "Failed to register patch"}, ensure_ascii=False)

              _pr_registry.register(
                  name="register_patch",
                  toolset="patch-registry",
                  schema={"name": "register_patch", "description": "Register a load-bearing patch", "parameters": _pr_register_schema["function"]["parameters"]},
                  handler=_pr_register_handler,
                  check_fn=lambda: True,
                  is_async=False,
                  description="Register a load-bearing patch that must not be reverted",
              )

              _pr_registry.register_toolset_alias("patches", "patch-registry")
              print(f"[PatchRegistry] ✅ Injected query_patches + register_patch tools into agent.", flush=True)
          except Exception as _pr_inj_e:
              print(f"[PatchRegistry] WARNING: tool injection failed: {_pr_inj_e}", flush=True)
          # ========================================================

          # Prepend workspace context so the agent always knows which directory
          # to use for file operations, regardless of session age or AGENTS.md defaults.
          import platform as _platform
          _is_windows = _platform.system() == 'Windows'
          _os_ctx = (
              "\n\nOperating System: Windows (bash shell available via Git Bash / WSL). "
              "PREFER built-in file tools (read_file, search_files, write_to_file, apply_diff) "
              "over terminal commands whenever possible — they are safer and more reliable. "
              "When terminal commands are necessary: bash-style commands (ls, cat, grep, cp, mv, rm) "
              "work because the terminal runs bash, not cmd.exe. "
              "File paths accept both forward slashes and backslashes."
          ) if _is_windows else ""

          # ── Build open-tabs context for the agent ──
          _open_tabs = open_tabs or []
          _tabs_ctx = ""
          _tabs_sys = ""
          if _open_tabs:
              _active_tab = None
              for _t in _open_tabs:
                  if _t.get('active'):
                      _active_tab = _t
                      break
              _tab_paths = [_t['path'] for _t in _open_tabs]
              _tabs_sys = (
                  f"[OPEN EDITOR TABS]\n"
                  f"  Files: {', '.join(_tab_paths)}\n"
              )
              if _active_tab:
                  _tabs_sys += f"  Active (focused) tab: {_active_tab['path']}\n"
              _tabs_sys += (
                  "  These files are OPEN in the user's editor RIGHT NOW. "
                  "When the user asks you to modify / fix / improve \"this\" file without specifying a path, "
                  "infer they mean the active tab or one of the open tabs.\n"
              )

          workspace_ctx = ""
          workspace_system_msg = (
              f"Active workspace: {s.workspace}\n"
              "Use this directory for ALL file operations unless the user specifies otherwise.\n\n"
              + _tabs_sys +
              "[WEBUI ENVIRONMENT]\n"
              "You are running in the Daon WebUI — a rich web-based chat interface.\n"
              "You CAN and MUST use Markdown formatting directly in your text responses.\n"
              "DO NOT use browser tools (browser_navigate, execute_command with curl, etc.) to \"verify\" "
              "whether markdown works — it ALREADY works. Just output the markdown and it will render.\n\n"
              "Supported Markdown features:\n"
              "- **bold**, *italic*, `inline code`, ```code blocks```, lists, headers\n"
              "- Images: ![alt text](image_url) — renders inline. Use https:// URLs for web images.\n"
              "  For local workspace files, use: /api/file/raw?session_id={s.session_id}&path=RELATIVE_PATH\n"
              "  Example: ![chart](/api/file/raw?session_id={s.session_id}&path=output/chart.png)\n"
              "- Links: [link text](url) — rendered as clickable hyperlinks\n\n"
              "IMPORTANT: When you want to show an image, simply write ![description](url) in your response.\n"
              "The frontend already has a working markdown-to-HTML renderer that converts this to an <img> tag.\n"
              "You do NOT need to test, verify, or debug image rendering — just use the markdown syntax.\n\n"
              "[MEMORY POLICY]\n"
              "You have access to Memory MCP tools (mcp_memory_*) for long-term knowledge storage.\n"
              "CRITICAL: Do NOT automatically save information to memory. Only use memory tools when:\n"
              "1. The user explicitly asks you to remember/save/store something, OR\n"
              "2. The user asks you to recall/search previously stored memories.\n"
              "Do not proactively create entities or relations. Memory is on-demand only.\n\n"
              f"You are running as model: {resolved_model}."
          ) + _os_ctx

          # ── Always-on Wake-up Hook: 장기 기억(facts/profile) 주입 ──
          # 매 채팅마다 마지막 활동 시각을 갱신하고, 8시간 이상 경과 후 재개 시
          # 'Wake-up' 강조 헤더와 함께 이전 맥락을 시스템 프롬프트에 주입한다.
          try:
              from api.memory_store import build_memory_prompt
              _memory_prompt = build_memory_prompt(
                  query_text=msg_text or '',
                  session_id=session_id or '',
              )
              if _memory_prompt:
                  workspace_system_msg += "\n\n" + _memory_prompt
          except Exception as _mem_e:
              print(f"[webui] WARNING: memory prompt injection failed: {_mem_e}", flush=True)

          # ── Patch Registry: 시스템 프롬프트에 패치 인식 블록 주입 ──
          try:
              from api.patch_registry import get_system_prompt_block
              _patch_prompt = get_system_prompt_block()
              if _patch_prompt:
                  workspace_system_msg += "\n\n" + _patch_prompt
          except Exception as _pr_prompt_e:
              print(f"[webui] WARNING: patch registry prompt injection failed: {_pr_prompt_e}", flush=True)

          # ── 에이전트 간 메시징: 활성 프로필(페르소나)의 수신함을 주입 ──
          # 다른 에이전트(Dynamic Harness 노드 또는 채팅)가 이 프로필 앞으로 보낸
          # 읽지 않은 메시지를 시스템 프롬프트에 주입한다. 수신자 = 활성 프로필 이름.
          try:
              from api.profiles import get_active_profile_name
              from api.memory_store import format_inbox_prompt
              _chat_agent_name = get_active_profile_name() or 'default'
              _inbox_prompt = format_inbox_prompt(_chat_agent_name)
              if _inbox_prompt:
                  workspace_system_msg += "\n\n" + _inbox_prompt
          except Exception as _inbox_e:
              print(f"[webui] WARNING: agent inbox injection failed: {_inbox_e}", flush=True)

          if planning_mode:
              workspace_system_msg += (
                  "\n\n[PLANNING MODE ENABLED]\n"
                  "The user has enabled Planning Mode. You must act carefully before making code changes.\n"
                  "If the request requires significant logic, major changes, or is complex:\n"
                  "1. Research and understand the codebase first.\n"
                  "2. Create a detailed `plan.md` in the workspace outlining your proposed changes.\n"
                  "3. Stop execution and explicitly ask the user for approval.\n"
                  "4. Only proceed with actual modifications after the user approves the plan.\n"
                  "If the request is trivial, you may execute it directly without a plan."
              )

          if injected_count > 0:
              workspace_system_msg += (
                  f"\n\n[MCP INJECTION ACTIVE]\n"
                  f"You have been dynamically injected with {injected_count} MCP tools from the WebUI.\n"
                  f"These tools are prefixed with `mcp_` (e.g. `mcp_playwright_...`).\n"
                  f"CRITICAL: You MUST call these tools natively as standard function calls.\n"
                  f"DO NOT try to execute them via HTTP API (e.g. /api/mcp/invoke) or Python scripts.\n"
                  f"They are fully registered in your environment; just call them directly!\n"
              )

          # TD1: Persist user message to history immediately so it's saved even if agent crashes
          if not any(m.get('role') == 'user' and m.get('content') == msg_text for m in s.messages[-2:]):
               user_msg = {'role': 'user', 'content': msg_text, 'timestamp': int(time.time())}
               # P6: Validate message shape against shared schema before persisting
               if _SCHEMA_AVAILABLE:
                   ok, err = _validate_msg(user_msg)
                   if not ok:
                       _logger.warning("Schema validation for user message failed: %s", err)
               s.messages.append(user_msg)
               s.save()

          # Process attachments to base64 images if present for multimodal models
          user_message_payload = workspace_ctx + msg_text
          if attachments:
              import base64
              import mimetypes
              multimodal_content = [{"type": "text", "text": workspace_ctx + msg_text}]
              has_images = False

              # Helper: resize image via Pillow (max 2048px on longest edge, JPEG quality 75)
              def _resize_image_bytes(raw_bytes: bytes, mime: str) -> bytes:
                  try:
                      from PIL import Image
                      import io as _io
                      img = Image.open(_io.BytesIO(raw_bytes))
                      fmt = img.format or ('PNG' if 'png' in mime else 'JPEG')
                      w, h = img.size
                      max_dim = 2048
                      if max(w, h) > max_dim:
                          ratio = max_dim / max(w, h)
                          new_size = (int(w * ratio), int(h * ratio))
                          img = img.resize(new_size, Image.LANCZOS)
                      # Convert to RGB for JPEG output (avoids RGBA issues)
                      if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
                          img = img.convert('RGB')
                      buf = _io.BytesIO()
                      save_kwargs = {}
                      if fmt == 'JPEG':
                          save_kwargs = {'quality': 75, 'optimize': True}
                      elif fmt == 'PNG':
                          save_kwargs = {'optimize': True}
                      elif fmt == 'WEBP':
                          save_kwargs = {'quality': 75}
                      img.save(buf, format=fmt, **save_kwargs)
                      return buf.getvalue()
                  except Exception:
                      return raw_bytes  # fallback to original

              for filename in attachments:
                  file_path = Path(s.workspace) / filename
                  if file_path.exists() and file_path.is_file():
                      mime_type, _ = mimetypes.guess_type(str(file_path))
                      if not mime_type:
                          ext = file_path.suffix.lower().lstrip('.')
                          if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'):
                              mime_type = f"image/{ext}"
                              if ext == 'svg':
                                  mime_type = "image/svg+xml"

                      if mime_type and (mime_type.startswith('image/') or mime_type == 'image/svg+xml'):
                          try:
                              img_bytes = file_path.read_bytes()
                              # Resize/compress for non-SVG images (SVG is vector, skip Pillow)
                              if mime_type != 'image/svg+xml' and not mime_type.startswith('image/gif'):
                                  img_bytes = _resize_image_bytes(img_bytes, mime_type)
                              b64_data = base64.b64encode(img_bytes).decode('utf-8')
                              multimodal_content.append({
                                  "type": "image_url",
                                  "image_url": {
                                      "url": f"data:{mime_type};base64,{b64_data}"
                                  }
                              })
                              has_images = True
                              print(f"[webui] Image '{filename}' encoded ({len(b64_data)} chars base64)", flush=True)
                          except Exception as img_err:
                              print(f"[webui] Failed to read image {filename}: {img_err}", flush=True)
              if has_images:
                  user_message_payload = multimodal_content

          # Cancel gate: if user cancelled during setup (MCP injection, prompt build, etc.),
          # skip the expensive run_conversation() call entirely.
          if cancel_event.is_set():
              print(f"[webui] Skipping run_conversation — stream cancelled for session {session_id}", flush=True)
              put('cancel', {'message': 'Cancelled before agent run'})
              return

          print(f"[webui-debug] Starting run_conversation for session={session_id} msg_len={len(msg_text)}", flush=True)
          # Use timeout-based lock to avoid infinite blocking after cancel.
          # #27 fix: 타임아웃을 15s → 25s로 증가. cancel_stream의 cleanup 지연(3s) +
          # agent.interrupt()가 HTTP recv() 블로킹을 해제하는 시간 + run_conversation()
          # finally 블록 실행 시간을 모두 커버할 수 있도록 충분한 여유를 줌.
          _lock_acquired = _agent_lock.acquire(timeout=25)
          if not _lock_acquired:
              print(f"[webui] WARN: _agent_lock for session {session_id} not acquired after 15s — aborting", flush=True)
              put('apperror', {
                  'message': '이전 작업이 아직 종료되지 않았습니다. 잠시 후 다시 시도하세요.',
                  'type': 'lock_timeout',
              })
              return
          try:
              result = agent.run_conversation(
                  user_message=user_message_payload,
                  system_message=workspace_system_msg,
                  conversation_history=_sanitize_messages_for_api(s.messages[:-1] if s.messages and s.messages[-1].get('role') == 'user' else s.messages),
                  task_id=session_id,
                  persist_user_message=msg_text,
              )
          finally:
              _agent_lock.release()
          print(f"[webui-debug] run_conversation completed for session={session_id}", flush=True)
          _result_msgs = result.get('messages')
          if _result_msgs:
              # 모델 ?환 ???전 ?스???롬?트가 ?적?는 것을 방?:
              # ?이?트가 반환??메시지 목록?서??system 메시지??거?고 ??한??
              s.messages = [m for m in _result_msgs if m.get('role') != 'system']
          # (결과가 ?으?기존 s.messages ??)

          # ==== [NEW] Attach model attribution metadata for UI ====
          actual_model = getattr(agent, 'model', resolved_model)
          if s.messages and s.messages[-1].get('role') == 'assistant':
              s.messages[-1]['actual_model'] = actual_model
              s.messages[-1]['requested_model'] = resolved_model
          # ========================================================

          # ==== [NEW] Always send model info for UI attribution ====
          put('model_info', {
              'requested': resolved_model,
              'actual': actual_model,
          })
          # ========================================================

          # ==== [NEW] Auto-notify frontend on model fallback ====
          if actual_model != resolved_model:
              put('model_fallback', {
                  'requested': resolved_model,
                  'actual': actual_model,
                  'message': f'⚠️ 요청한 모델({resolved_model})이 응답하지 않아 {actual_model}(으)로 자동 전환되었습니다.'
              })
          # ========================================================

          # ?? Handle context compression side effects ??
          # If compression fired inside run_conversation, the agent may have
          # rotated its session_id. Detect and fix the mismatch so the WebUI
          # continues writing to the correct session file.
          _agent_sid = getattr(agent, 'session_id', None)
          _compressed = False
          if _agent_sid and _agent_sid != session_id:
              old_sid = session_id
              new_sid = _agent_sid
              # Rename the session file
              old_path = SESSION_DIR / f'{old_sid}.json'
              new_path = SESSION_DIR / f'{new_sid}.json'
              s.session_id = new_sid
              with LOCK:
                  if old_sid in SESSIONS:
                      SESSIONS[new_sid] = SESSIONS.pop(old_sid)
              if old_path.exists() and not new_path.exists():
                  try:
                      old_path.rename(new_path)
                  except OSError:
                      pass
              _compressed = True
          # Also detect compression via the result dict or compressor state
          if not _compressed:
              _compressor = getattr(agent, 'context_compressor', None)
              if _compressor and getattr(_compressor, 'compression_count', 0) > 0:
                  _compressed = True
          # Notify the frontend that compression happened
          if _compressed:
              put('compressed', {
                  'message': 'Context auto-compressed to continue the conversation',
              })

          # Stamp 'timestamp' on any messages that don't have one yet
          _now = time.time()
          for _m in s.messages:
              if isinstance(_m, dict) and not _m.get('timestamp') and not _m.get('_ts'):
                  _m['timestamp'] = int(_now)
          s.title = title_from(s.messages, s.title)
          # Read token/cost usage from the agent object (if available)
          input_tokens = getattr(agent, 'session_prompt_tokens', 0) or 0
          output_tokens = getattr(agent, 'session_completion_tokens', 0) or 0
          estimated_cost = getattr(agent, 'session_estimated_cost_usd', None)
          s.input_tokens = (s.input_tokens or 0) + input_tokens
          s.output_tokens = (s.output_tokens or 0) + output_tokens
          if estimated_cost:
              s.estimated_cost = (s.estimated_cost or 0) + estimated_cost
          # Extract tool call metadata grouped by assistant message index
          # Each tool call gets assistant_msg_idx so the client can render
          # cards inline with the assistant bubble that triggered them.
          tool_calls = []
          pending_names = {}   # tool_call_id -> name
          pending_args = {}    # tool_call_id -> args dict
          pending_asst_idx = {} # tool_call_id -> index in s.messages
          for msg_idx, m in enumerate(s.messages):
              if m.get('role') == 'assistant':
                  c = m.get('content', '')
                  if isinstance(c, list):
                      for p in c:
                          if isinstance(p, dict) and p.get('type') == 'tool_use':
                              tid = p.get('id', '')
                              pending_names[tid] = p.get('name', '')
                              pending_args[tid] = p.get('input', {})
                              pending_asst_idx[tid] = msg_idx
              elif m.get('role') == 'tool':
                  tid = m.get('tool_call_id') or m.get('tool_use_id', '')
                  name = pending_names.get(tid, '')
                  if not name or name == 'tool':
                      continue  # skip unresolvable tool entries
                  asst_idx = pending_asst_idx.get(tid, -1)
                  args = pending_args.get(tid, {})
                  raw = str(m.get('content', ''))
                  try:
                      rd = json.loads(raw)
                      snippet = str(rd.get('output') or rd.get('result') or rd.get('error') or raw)[:200]
                  except Exception:
                      snippet = raw[:200]
                  # Truncate args values for storage
                  args_snap = {}
                  if isinstance(args, dict):
                      for k, v in list(args.items())[:6]:
                          if isinstance(v, (list, dict)):
                              args_snap[k] = v
                          else:
                              s2 = str(v)
                              args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')
                  tool_calls.append({
                      'name': name, 'snippet': snippet, 'tid': tid,
                      'assistant_msg_idx': asst_idx, 'args': args_snap,
                  })
          s.tool_calls = tool_calls
          # Tag the matching user message with attachment filenames for display on reload
          # Only tag a user message whose content relates to this turn's text
          # (msg_text is the full message including the [Attached files: ...] suffix)
          if attachments:
              for m in reversed(s.messages):
                  if m.get('role') == 'user':
                      content = str(m.get('content', ''))
                      # Match if content is part of the sent message or vice-versa
                      base_text = msg_text.split('\n\n[Attached files:')[0].strip()
                      if base_text[:60] in content or content[:60] in msg_text:
                          m['attachments'] = attachments
                          break
          s.save()
          # Sync to state.db for /insights (opt-in setting)
          try:
              from api.config import load_settings as _load_settings
              if _load_settings().get('sync_to_insights'):
                  from api.state_sync import sync_session_usage
                  sync_session_usage(
                      session_id=s.session_id,
                      input_tokens=s.input_tokens or 0,
                      output_tokens=s.output_tokens or 0,
                      estimated_cost=s.estimated_cost,
                      model=model,
                      title=s.title,
                  )
          except Exception:
              pass  # never crash the stream for sync failures
          usage = {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'estimated_cost': estimated_cost}
          # Include context window data from the agent's compressor for the UI indicator
          _cc = getattr(agent, 'context_compressor', None)
          if _cc:
              usage['context_length'] = getattr(_cc, 'context_length', 0) or 0
              usage['threshold_tokens'] = getattr(_cc, 'threshold_tokens', 0) or 0
              usage['last_prompt_tokens'] = getattr(_cc, 'last_prompt_tokens', 0) or 0

          # ── done 이벤트를 먼저 전송하여 UI가 즉시 잠금 해제되도록 함 ──
          put('done', {'session': s.to_response(), 'usage': usage, 'job_error': _job_has_error})

          # ── Agent Voice Output: LLM 요약 생성 (done 이후 백그라운드로 실행) ──
          if _job_tools:
              def _async_voice_summary():
                  try:
                      _api_mode = getattr(agent, 'api_mode', 'chat_completions')
                      _summary_text = _generate_voice_summary(
                          resolved_model, resolved_provider, resolved_base_url,
                          resolved_api_key, _api_mode, msg_text, _job_tools,
                          s.messages[-1].get('content', '') if s.messages else '',
                      )
                      if _summary_text:
                          put('speak', {'text': _summary_text, 'summary': True})
                  except Exception as _sum_err:
                      print(f"[Speak] LLM summary generation failed: {_sum_err}", flush=True)
              _voice_thread = threading.Thread(target=_async_voice_summary, daemon=True)
              _voice_thread.start()

          # ── DAON 기억 시스템: facts/profile/summary 자동 추출 (백그라운드) ──
          try:
              from api.memory_store import process_session_async
              process_session_async(s)
          except Exception:
              pass  # 기억 시스템 실패가 채팅을 깨뜨리지 않도록
        finally:
          with _ENV_LOCK:
            if os.environ.get('TERMINAL_CWD') == str(s.workspace):
                if old_cwd is None: os.environ.pop('TERMINAL_CWD', None)
                else: os.environ['TERMINAL_CWD'] = old_cwd
            if os.environ.get('HERMES_EXEC_ASK') == '1':
                if old_exec_ask is None: os.environ.pop('HERMES_EXEC_ASK', None)
                else: os.environ['HERMES_EXEC_ASK'] = old_exec_ask
            if os.environ.get('HERMES_SESSION_KEY') == session_id:
                if old_session_key is None: os.environ.pop('HERMES_SESSION_KEY', None)
                else: os.environ['HERMES_SESSION_KEY'] = old_session_key
            if _profile_home and os.environ.get('HERMES_HOME') == _profile_home:
                if old_hermes_home is None: os.environ.pop('HERMES_HOME', None)
                else: os.environ['HERMES_HOME'] = old_hermes_home

    except Exception as e:
        print('[webui] stream error:\n' + traceback.format_exc(), flush=True)
        # NEW: Try to save the session even on error so user message is not lost
        try: s.save()
        except: pass
        err_str = str(e)
        # Detect rate limit errors specifically so the client can show a helpful card
        # rather than the generic "Connection lost" message
        is_rate_limit = 'rate limit' in err_str.lower() or '429' in err_str or 'RateLimitError' in type(e).__name__
        if is_rate_limit:
            put('apperror', {
                'message': err_str,
                'type': 'rate_limit',
                'hint': 'Rate limit reached. The fallback model (if configured) was also exhausted. Try again in a moment.',
            })
        else:
            put('apperror', {'message': err_str, 'type': 'error'})
    finally:
        _clear_thread_env()  # TD1: always clear thread-local context
        with _ACTIVE_AGENTS_LOCK:
            _ACTIVE_AGENTS.pop(stream_id, None)
        with _ACTIVE_SESSION_STREAMS_LOCK:
            # Only remove if this stream is still the active one for the session
            if _ACTIVE_SESSION_STREAMS.get(session_id) == stream_id:
                _ACTIVE_SESSION_STREAMS.pop(session_id, None)

        # Cache the 'done' event data so that clients whose EventSource
        # auto-reconnects after the stream has completed still get a proper
        # response instead of a 404 "stream not found" error.
        _cached_done = None
        with STREAMS_LOCK:
            _q = STREAMS.pop(stream_id, None)
            CANCEL_FLAGS.pop(stream_id, None)
        # Drain the queue to find the last 'done' event
        if _q is not None:
            while True:
                try:
                    evt, data = _q.get_nowait()
                    if evt == 'done':
                        _cached_done = data
                except queue.Empty:
                    break
        if _cached_done is not None:
            with _COMPLETED_STREAMS_LOCK:
                _COMPLETED_STREAMS[stream_id] = (_cached_done, time.time())
                # Clean up entries older than 60 seconds
                _now = time.time()
                _stale = [sid for sid, (_, ts) in _COMPLETED_STREAMS.items() if _now - ts > 60]
                for sid in _stale:
                    del _COMPLETED_STREAMS[sid]


def cancel_stream(stream_id: str) -> bool:
    """Signal an in-flight stream to cancel. Returns True if the stream existed."""
    # NEW: Tell the AIAgent to stop its in-flight HTTP request immediately.
    # Without this, cancel_event.set() has no way to reach the agent thread —
    # the HTTP request keeps running until its own 120s timeout.
    with _ACTIVE_AGENTS_LOCK:
        agent = _ACTIVE_AGENTS.get(stream_id)
    if agent:
        try:
            agent.interrupt("User cancelled")
        except Exception:
            pass

    with STREAMS_LOCK:
        if stream_id not in STREAMS:
            return False
        flag = CANCEL_FLAGS.get(stream_id)
        if flag:
            flag.set()
        # Put a cancel sentinel into the queue so the SSE handler wakes up
        q = STREAMS.get(stream_id)
        if q:
            q.put_nowait(('cancel', {'message': 'Cancelled by user'}))
        # #27 fix: cleanup delay 10s → 3s. 10초는 너무 길어서 _agent_lock.acquire(timeout=25)와
        # 결합될 때 전체 대기 시간이 과도하게 길어짐. 대부분의 API 호출은 interrupt() 후
        # 3초 이내에 타임아웃되거나 종료되므로 3초면 충분.
        _cleanup_delay = 3
        def _force_cleanup():
            time.sleep(_cleanup_delay)
            with STREAMS_LOCK:
                if stream_id in STREAMS:
                    _logger.warning(
                        "Force-cleaning stale stream %s (agent did not finish within %ds of cancel)",
                        stream_id, _cleanup_delay
                    )
                    STREAMS.pop(stream_id, None)
                    CANCEL_FLAGS.pop(stream_id, None)
            with _ACTIVE_AGENTS_LOCK:
                _ACTIVE_AGENTS.pop(stream_id, None)
        threading.Thread(target=_force_cleanup, daemon=True).start()
        return True
