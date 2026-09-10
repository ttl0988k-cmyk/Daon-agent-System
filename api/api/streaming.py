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
    init_hermes_auth_env, get_thread_env,
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

# Cache of recently cancelled streams (stream_id → timestamp). When a client's
# EventSource auto-reconnects after its stream was cancelled, we serve a clean
# 'cancel' event instead of returning 404 — a 404 would surface in the UI as
# a scary "connection lost" error right after the user pressed cancel.
# Entries auto-expire after 60 seconds.
_CANCELLED_STREAMS = {}
_CANCELLED_STREAMS_LOCK = threading.Lock()

# Map stream_id → worker thread running _run_agent_streaming. cancel_stream()
# checks this before force-cleaning STREAMS: while the agent thread is still
# winding down, force cleanup would make SSE reconnects hit 404 and look like
# the agent disconnected.
_STREAM_THREADS = {}
_STREAM_THREADS_LOCK = threading.Lock()

# Thread-local capture of the active stream's put() callable. Tool handlers
# running inside the agent thread (e.g. the Dynamic Harness tool) call
# get_current_thread_put() to emit extra SSE events such as 'agent_log'.
_thread_put = threading.local()


class StreamEmitter:
    """Manages SSE event emission for an agent stream with cancellation filtering,
    terminal event caching, and thread-local routing.
    Can be invoked directly as emitter(event, data) or emitter.emit(event, data).
    """

    def __init__(self, stream_id: str, queue_obj, cancel_event):
        self.stream_id = stream_id
        self.queue = queue_obj
        self.cancel_event = cancel_event
        self.terminal_done_data = None
        self._emitted_count = 0

    def __call__(self, event: str, data=None) -> None:
        self.emit(event, data)

    def emit(self, event: str, data=None) -> None:
        # If cancelled, drop all further events except terminal cancel and error events
        if self.cancel_event.is_set() and event not in ('cancel', 'error'):
            return
        if event == 'done':
            self.terminal_done_data = data
        try:
            self.queue.put_nowait((event, data))
            self._emitted_count += 1
        except Exception as e:
            _logger.warning("Failed to enqueue SSE event %s for stream %s: %s", event, self.stream_id, e)


def get_current_thread_put():
    """Return the put() callable bound to the current stream thread, or None."""
    return getattr(_thread_put, 'put', None)

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
    """Write one SSE event to the response stream.

    Returns False if the client disconnected (socket write failed) so callers
    can break their loop instead of hammering a dead socket — which previously
    produced thousands of WinError 10053 logs and made agent responses vanish
    from the UI. Returns True on success.
    """
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    try:
        handler.wfile.write(payload.encode('utf-8'))
        handler.wfile.flush()
        return True
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f'[SSE-DIAG] _sse write failed for event={event}: {e}', flush=True)  # Client disconnected before SSE event could be sent
        return False


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
        # session_id를 명시적으로 넘긴다. 그렇지 않으면 취소 직후 새 스트림이
        # _ACTIVE_SESSION_STREAMS[session_id]를 덮어써 _force_release_session_lock의
        # 역방향 조회가 실패해 세션 락이 해제되지 않고, 새 메시지가
        # "이전 작업이 아직 종료되지 않았습니다"로 거부되는 레이스 컨디션이 발생한다.
        cancelled_any = cancel_stream(old_stream_id, session_id=session_id)
    return cancelled_any


def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id, attachments=None, planning_mode=False, open_tabs=None, media_options=None):
    """Run agent in background thread, writing SSE events to STREAMS[stream_id]."""
    q = STREAMS.get(stream_id)
    if q is None:
        return

    # Register the worker thread so cancel_stream() can wait for the agent to
    # actually wind down before force-cleaning STREAMS. Force-removing the
    # stream while the agent thread is still finishing (tool abort, session
    # save) made SSE reconnects hit 404 — which surfaced in the UI as a
    # "connection lost" error right after the user pressed cancel.
    with _STREAM_THREADS_LOCK:
        _STREAM_THREADS[stream_id] = threading.current_thread()

    # Auto-cancel any previous stream for this session before starting
    cancel_session_streams(session_id)

    # Register this stream as the active one for this session
    with _ACTIVE_SESSION_STREAMS_LOCK:
        _ACTIVE_SESSION_STREAMS[session_id] = stream_id

    # Sprint 10: create a cancel event for this stream
    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    # Sprint 10: StreamEmitter encapsulates event queueing, cancellation, and terminal done caching
    emitter = StreamEmitter(stream_id, q, cancel_event)
    put = emitter
    _thread_put.put = emitter

    # Whether we registered the dangerous-command approval gateway callback for
    # this session (must be unregistered in the outer finally block).
    _gateway_notify_registered = False
    _cmd_approval_notify = None  # 등록된 콜백 참조 (해제 시 소유권 확인용)

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

          # ── Streaming  stripping ──
          # 일부 모델(Qwen3/GLM/MiniMax thinking 모드)은 content 델타에 리터럴
          #  태그를 출력한다. 응답 확정 전에는 _strip_think_blocks가
          # 적용되지 않으므로 스트리밍 중에 여기서 제거해야 챗창에 노출되지
          # 않는다. 버퍼 전체를 매번 재스캔하므로 토큰이 태그 중간에서
          # 분할돼도 안전하다.
          _THINK_OPEN_RE = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
          _THINK_CLOSE_RE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)
          _THINK_TAG_FULLS = ('<think>', '<thinking>', '</think>', '</thinking>')

          def _partial_think_tag_tail(seg):
              """seg 끝에 think 태그로 자라날 수 있는 '<...' 파편이 있으면
              그 시작 오프셋을 반환, 없으면 -1. 다음 토큰까지 보류용."""
              lt = seg.rfind('<')
              if lt < 0:
                  return -1
              tail = seg[lt:].lower()
              if '>' in tail:
                  return -1  # 이미 닫힌(완전한) 태그 — 정규식이 처리
              for full in _THINK_TAG_FULLS:
                  if full.startswith(tail):
                      return lt
              return -1

          def _strip_think_streaming(text):
              """think 블록 밖의 표시 가능 텍스트만 반환.
              닫히지 않은 think 블록의 나머지/끝의 불완전 태그는 보류."""
              out = []
              i = 0
              n = len(text)
              in_think = False
              while i < n:
                  if in_think:
                      m = _THINK_CLOSE_RE.search(text, i)
                      if not m:
                          return ''.join(out)
                      i = m.end()
                      in_think = False
                  else:
                      m = _THINK_OPEN_RE.search(text, i)
                      if not m:
                          hold = _partial_think_tag_tail(text[i:])
                          if hold >= 0:
                              out.append(text[i:i + hold])
                          else:
                              out.append(text[i:])
                          return ''.join(out)
                      out.append(text[i:m.start()])
                      i = m.end()
                      in_think = True
              return ''.join(out)

          # CONTEXT COMPACTION 패턴 감지 — 내부용 요약이 채팅에 표시되는 버그 방지
          _COMPACTION_PATTERNS = (
              '[CONTEXT COMPACTION',
              '## Context Compaction',
              '## CONTEXT COMPACTION',
          )

          def _is_compaction_text(text):
              """토큰 텍스트가 컨텍스트 압축 요약인지 확인 (대소문자 무시)."""
              low = text.lower()
              for pat in _COMPACTION_PATTERNS:
                  if pat.lower() in low:
                      return True
              return False

          def _compaction_hold_len(text):
              """text 끝이 압축 패턴의 접두사로 자라날 수 있으면 그 길이를 반환.
              패턴이 토큰 경계에서 분할돼도 감지할 수 있도록 해당 꼬리를
              다음 토큰이 올 때까지 송출 보류한다."""
              max_hold = 0
              low = text.lower()
              for pat in _COMPACTION_PATTERNS:
                  pl = pat.lower()
                  k = min(len(pl) - 1, len(low))
                  while k > 0:
                      if low.endswith(pl[:k]):
                          if k > max_hold:
                              max_hold = k
                          break
                      k -= 1
              return max_hold

          def _compaction_find_pos(text):
              """전체 버퍼에서 압축 패턴이 처음 나타나는 위치를 반환 (대소문자 무시).
              패턴이 없으면 -1."""
              low = text.lower()
              best = -1
              for pat in _COMPACTION_PATTERNS:
                  i = low.find(pat.lower())
                  if i >= 0 and (best < 0 or i < best):
                      best = i
              return best

          # 압축 요약 감지 래치: 한 번 감지되면 이후 모든 토큰을 reasoning
          # 채널로 라우팅한다 (요약 전문이 챗창에 새는 것을 차단).
          _compaction_mode = False

          # API 오류 중계 상태 (스팸 방지 스로틀용)
          _api_err_state = {'last_msg': None, 'last_ts': 0.0, 'count': 0}

          def on_token(text):
              nonlocal _token_buf, _token_sent, _compaction_mode
              if text is None:
                  # end-of-stream sentinel: 보류 중이던 꼬리가 있으면 플러시
                  if not _compaction_mode:
                      cleaned = _ANSI_RE.sub("", _token_buf)
                      visible = _strip_think_streaming(cleaned)
                      if len(visible) > _token_sent:
                          put('token', {'text': visible[_token_sent:]})
                          _token_sent = len(visible)
                  return
              _token_buf += text
              cleaned = _ANSI_RE.sub("", _token_buf)
              visible = _strip_think_streaming(cleaned)
              if len(visible) <= _token_sent:
                  return
              delta = visible[_token_sent:]
              # CONTEXT COMPACTION 요약이 채팅에 노출되는 버그 방지:
              # 델타가 아닌 전체 버퍼를 검사해 패턴이 토큰 경계에서 분할돼도
              # 감지하고, 한 번 감지되면 이후 토큰은 모두 'reasoning' 채널로
              # 라우팅해 프론트엔드의 접이식 "생각 중" 상자에만 표시한다.
              if _compaction_mode:
                  _token_sent = len(visible)
                  on_reasoning(delta)
                  return
              pos = _compaction_find_pos(visible)
              if pos >= 0:
                  # 패턴 앞의 정상 텍스트는 챗창에 남기고, 패턴부터 reasoning으로
                  if pos > _token_sent:
                      put('token', {'text': visible[_token_sent:pos]})
                  _compaction_mode = True
                  _token_sent = len(visible)
                  on_reasoning(visible[pos:])
                  return
              # 패턴 접두사로 자라날 수 있는 꼬리는 다음 토큰까지 송출 보류
              hold = _compaction_hold_len(visible)
              send_end = len(visible) - hold
              if send_end > _token_sent:
                  put('token', {'text': visible[_token_sent:send_end]})
                  _token_sent = send_end

          def on_api_error(error_msg):
              # 에이전트 내부 API 호출 실패(404/503 등)를 UI 로 중계한다.
              # 재시도 루프는 계속 진행되므로 스트림을 끊지 않는 "경고" 이벤트.
              # 동일 오류 스팸 방지: 같은 메시지는 15초 내 재발행 억제,
              # 스트림당 최대 8회까지만 발행.
              try:
                  now = time.time()
                  if error_msg == _api_err_state.get('last_msg') and \
                          (now - _api_err_state.get('last_ts', 0)) < 15.0:
                      return
                  if _api_err_state.get('count', 0) >= 8:
                      return
                  _api_err_state['last_msg'] = error_msg
                  _api_err_state['last_ts'] = now
                  _api_err_state['count'] = _api_err_state.get('count', 0) + 1
                  put('apierror', {
                      'message': error_msg[:500],
                      'type': 'api_error',
                      'count': _api_err_state['count'],
                  })
              except Exception:
                  pass

          def on_reasoning(text):
              # 추론(reasoning) 델타 — 별도 'reasoning' SSE 이벤트로 전송해
              # 챗 본문에 섞이지 않게 한다. 프론트엔드는 접이식 "생각 중"
              # 상자에 표시하고 idle timer도 갱신한다.
              if not text:
                  return
              put('reasoning', {'text': text})

          # Track job state for progress block UX
          _job_active = False
          _job_has_error = False
          _job_tools = []  # List of (tool_name, status) for progress display
          # Track pending file edits: tool.started stores path so tool.completed
          # (which receives args=None) can re-read the file from disk.
          _pending_file_edits = {}  # tool_name -> path
          # ── Phase 6 인과 그래프 캡처 ──
          # tool.started에서 경로 보관 → tool.completed 확정 시점 기록 (실패 쓰기는 미기록).
          _p6_pending_artifacts = {}    # tool_name -> path
          _p6_turn_injected_facts = []  # 이번 턴 주입 fact id (직접 영향 후보)

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
                  # ── Phase 6-A: 생성될 파일 경로 보관 (completed 확정 시 기록) ──
                  try:
                      if tool_name in ('write_file', 'patch', 'apply_diff') and isinstance(args, dict):
                          _p6_p = args.get('path') or args.get('file_path') or ''
                          if _p6_p:
                              _p6_pending_artifacts[tool_name] = _p6_p
                  except Exception as _p6_err:
                      _logger.debug("Phase 6 artifact tracking failed: %s", _p6_err)
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
                  # ── Phase 6-A: 쓰기 성공 확정 시 산출물 기록 (실패 쓰기는 미기록) ──
                  try:
                      if not is_error:
                          _p6_p = _p6_pending_artifacts.pop(tool_name, None)
                          if _p6_p:
                              from api import memory_store as _p6_ms
                              _p6_ms.record_session_artifact(
                                  session_id, 'file', _p6_p,
                                  tool_name=tool_name,
                                  workspace=(getattr(s, 'workspace', '') or ''),
                                  direct_fact_ids=_p6_turn_injected_facts,
                              )
                  except Exception as _p6_rec_err:
                      _logger.debug("Phase 6 artifact recording failed: %s", _p6_rec_err)
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
                      except Exception as _mode_err:
                          _logger.debug("Session mode check failed: %s", _mode_err)

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

          # ── auth.json에서 API 키를 환경변수로 1회 안전 캐싱 주입 ──
          # resolve_model_provider가 올바르게 라우팅할 수 있도록 환경변수를 초기화한다.
          init_hermes_auth_env()

          # ?�경변??주입 ??모델/?�로바이???�결??
          resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model)

          # ── UI 등록 프로바이더(custom_providers.json) 우선 해석 ──
          # 사용자가 WebUI의 프로바이더 관리에서 등록/저장한 키와 base_url을 최우선으로 사용한다.
          # resolve_runtime_provider(auth.json credential_pool)가 먼저 실행되면
          # 구버전 키가 우선되어 UI에서 새로 등록한 키가 영원히 무시되는 문제를 막는다.
          resolved_api_key = None
          rt_provider = None
          rt_base_url = None
          try:
              from api.managers.model_manager import model_manager as _mm
              _cp_key = _mm._get_api_key(resolved_provider) if resolved_provider else ''
              _cp_url = _mm._get_base_url(resolved_provider) if resolved_provider else None
              if _cp_key:
                  resolved_api_key = _cp_key
                  print(f"[webui] API key from custom_providers.json (UI) for '{resolved_provider}'", flush=True)
              if _cp_url:
                  resolved_base_url = _cp_url.rstrip('/')
                  print(f"[webui] base_url from custom_providers.json (UI) for '{resolved_provider}': {resolved_base_url}", flush=True)
          except Exception as _cp_ui_e:
              print(f"[webui] WARNING: custom_providers.json UI lookup failed: {_cp_ui_e}", flush=True)

          # Resolve API key via Hermes runtime provider (matches gateway behaviour)
          # UI 등록 키가 없을 때만 auth.json credential_pool을 사용한다.
          if not resolved_api_key:
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
              except Exception as _auth_e:
                  _logger.debug("auth.json lookup failed: %s", _auth_e)

          if resolved_provider in ('zai', 'ollama-cloud') and not resolved_api_key:
              resolved_api_key = os.getenv('OLLAMA_API_KEY')

          # custom_providers.json에서 API 키 fallback (UI에서 등록한 프로바이더)
          if not resolved_api_key and resolved_provider and resolved_provider != 'custom':
              try:
                  from api.managers.model_manager import model_manager as _mm2
                  _cp_key = _mm2._get_api_key(resolved_provider)
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
              # ── 폴백 API 키 해석 ──
              # 키가 없으면 hermes fallback.py가 내부 출처(auth.json/env)의
              # 낡은 키로 해석해 401 "invalid api key"가 발생한다.
              # 메인 키와 동일한 우선순위(UI 등록 키 → 메인 키 공유 →
              # runtime provider)로 여기서 해석해 명시적으로 주입한다.
              fb_api_key = None
              try:
                  from api.managers.model_manager import model_manager as _mm_fb
                  if fb_provider:
                      fb_api_key = _mm_fb._get_api_key(fb_provider) or None
                  if not fb_base_url and fb_provider:
                      _fb_url = _mm_fb._get_base_url(fb_provider)
                      if _fb_url:
                          fb_base_url = str(_fb_url).rstrip('/')
              except Exception as _fb_e:
                  print(f"[webui] WARNING: fallback key/base_url lookup failed: {_fb_e}", flush=True)
              if not fb_api_key and fb_provider and fb_provider == resolved_provider:
                  # 메인 프로바이더와 동일하면 이미 해석된 키를 재사용한다.
                  fb_api_key = resolved_api_key
              if not fb_api_key and fb_provider:
                  try:
                      from hermes_cli.runtime_provider import resolve_runtime_provider as _rrp_fb
                      _rt_fb = _rrp_fb(requested=fb_provider)
                      fb_api_key = _rt_fb.get("api_key") or None
                      if not fb_base_url:
                          fb_base_url = (_rt_fb.get("base_url") or '').rstrip('/') or None
                  except Exception as _rt_fb_e:
                      print(f"[webui] WARNING: fallback runtime_provider lookup failed: {_rt_fb_e}", flush=True)
              _fallback_resolved = {
                  'model': fb_model,
                  'provider': fb_provider,
                  'base_url': fb_base_url,
                  'api_key': fb_api_key,
              }
              print(f"[webui-debug] fallback_resolved model={fb_model} provider={fb_provider} base_url={fb_base_url} api_key={'set' if fb_api_key else 'MISSING'}", flush=True)
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
                      except Exception as _bu_err:
                          _logger.debug("Media base_url lookup failed: %s", _bu_err)
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
                          _mo = media_options or {}
                          _media_box['result'] = run_media_generation(
                              prompt=msg_text,
                              model=resolved_model,
                              base_url=_media_base_url,
                              api_key=resolved_api_key,
                              model_type=_media_type,
                              size=_mo.get('size') or None,
                              n=_mo.get('n') or None,
                          )
                      except Exception as _w_err:
                          _media_box['error'] = _w_err
                      finally:
                          _media_box['done'] = True

                  _media_worker = _media_threading.Thread(target=_run_media_worker, daemon=True)
                  _media_worker.start()

                  # 완료될 때까지 10초마다 전용 heartbeat 이벤트 발송.
                  # token 재사용 대신 별도 이벤트를 써서 토큰 처리 로직과 분리한다.
                  # 프론트엔드는 heartbeat 리스너에서 resetIdleTimer()를 호출해
                  # idle timer(30초)가 스트림을 조기 종료하지 않도록 한다.
                  while not _media_box['done']:
                      _media_worker.join(timeout=10.0)
                      if not _media_box['done']:
                          put('heartbeat', {})  # keep-alive → 프론트엔드 idle timer 리셋

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
          except Exception as _br_err:
              _logger.debug("Browser context active check failed: %s", _br_err)

          # ── Inject active plugin skills for this session ──
          # 세션에서 ON 된 플러그인의 SKILL.md 컨텐츠를 ephemeral 시스템
          # 프롬프트에 주입한다. ephemeral이므로 세션 DB 시스템 프롬프트
          # 캐시를 오염시키지 않아 플러그인 ON/OFF 가 매 턴 즉시 반영된다.
          try:
              from api.plugin_gateway import active_plugin_skills
              _p_qualified, _p_blocks = active_plugin_skills(session_id)
              if _p_blocks:
                  _plugin_block = (
                      "[Active Plugins: You have the following plugin skills enabled for this session. "
                      "Use them directly to complete the user's task when relevant. You may also invoke "
                      "them via the skill_view tool using their qualified names.]\n\n"
                      + "\n\n".join(_p_blocks)
                  )
                  _ephemeral_prompt = (
                      (_ephemeral_prompt + "\n\n" + _plugin_block)
                      if _ephemeral_prompt
                      else _plugin_block
                  )
          except Exception as _p_err:
              print(f"[webui] WARNING: plugin skill injection failed: {_p_err}", flush=True)

          # 세션 스코프 플러그인 credentials 를 이 실행 스레드의 ContextVar 값
          # 레지스트리에 바인딩한다 (os.environ 전역 주입 없음 → 세션 간 누출 방지).
          # 샌드박스 env 빌더(_make_run_env / child_env / docker init)가 이
          # 레지스트리를 읽어 해당 세션의 실행 env 에만 병합한다.
          try:
              from api.plugin_gateway import session_plugin_credentials
              from tools.env_passthrough import set_plugin_credential_env
              set_plugin_credential_env(session_plugin_credentials(session_id))
          except Exception as _p_cred_err:
              print(f"[webui] WARNING: plugin credential env binding failed: {_p_cred_err}", flush=True)

          # Install the Electron/CDP browser bridge before creating the agent.
          # This prevents initialization paths from starting standalone
          # agent-browser Chromium before the bridge is installed.
          try:
              from api.browser_bridge import patch_browser_tool
              _browser_bridge_ready = patch_browser_tool()
              if not _browser_bridge_ready and os.environ.get('BROWSER_CDP_URL'):
                  print('[webui] WARNING: Electron browser bridge could not be installed; standalone browser fallback is disabled.', flush=True)
          except Exception as _bt_init_err:
              _browser_bridge_ready = False
              print(f"[webui] WARNING: Electron browser bridge initialization failed: {_bt_init_err}", flush=True)

          # ── 위험 명령 승인 (gateway notify) ────────────────────────────────
          # 일반 채팅은 HERMES_EXEC_ASK=1이라 위험 명령이 승인 분기
          # (tools/approval.py is_ask)로 진입한다. 이때 세션에 notify 콜백이
          # 등록되어 있지 않으면 즉각 approval_required를 반환하는 폴백 경로로
          # 빠져 에이전트가 블로킹되지 않는다 → 사용자에게 보이는 승인 카드를
          # 승인해도 명령이 실행되지 않는 데드 패스가 된다 (plan.md Cause A).
          # 여기서는 승인 요청을 SSE로 WebUI에 전달하는 콜백을 등록해,
          # 에이전트 스레드가 사용자 응답까지 블로킹(최대 5분)되게 한다.
          try:
              from tools.approval import register_gateway_notify as _reg_gw_notify

              def _cmd_approval_notify(approval_data):
                  try:
                      # status를 그대로 전달한다: 'pending'(대기) 또는
                      # 'auto_approved'(파일 도구 승인 45초 무응답 자동 실행) —
                      # 프론트가 auto_approved를 받으면 카드를 자동 승인 완료로 교체한다.
                      put('approval', {
                          'type': 'dangerous_command',
                          'status': approval_data.get('status', 'pending'),
                          'session_id': session_id,
                          'command': approval_data.get('command', ''),
                          'description': approval_data.get('description', ''),
                          'pattern_key': approval_data.get('pattern_key', ''),
                          'pattern_keys': approval_data.get('pattern_keys', []),
                          'message': approval_data.get('message', ''),
                      })
                  except Exception:
                      _logger.warning("Failed to emit command approval SSE event", exc_info=True)

              _reg_gw_notify(session_id, _cmd_approval_notify)
              _gateway_notify_registered = True
          except Exception as _gw_reg_err:
              _logger.warning("register_gateway_notify failed for session %s: %s", session_id, _gw_reg_err)

          # ── OpenCode Zen/Go 모델별 API 라우팅 ──────────────────────────────
          # OpenCode Go는 모델마다 API 표면이 다르다(MiniMax→/v1/messages,
          # GLM/Kimi/Mimo→/v1/chat/completions). AIAgent는 provider 이름만으로
          # 이를 추론하지 못해 기본 chat_completions로 오라우팅되므로, Daon 쪽에서
          # 명시적으로 (api_mode, base_url)를 계산해 주입한다. 그 외 프로바이더는
          # (None, 동일 URL)이 반환되어 기존 동작이 그대로 유지된다.
          _resolved_api_mode = None
          try:
              from api.managers.model_manager import (
                  normalize_opencode_provider as _oc_norm_p,
                  normalize_opencode_model_id as _oc_norm_m,
                  resolve_opencode_route as _oc_route,
              )
              resolved_provider = _oc_norm_p(resolved_provider)
              _resolved_api_mode, _oc_url = _oc_route(resolved_provider, resolved_model, resolved_base_url)
              if _resolved_api_mode:
                  resolved_model = _oc_norm_m(resolved_provider, resolved_model)
                  resolved_base_url = _oc_url
                  print(f"[webui] OpenCode route: provider={resolved_provider} model={resolved_model} api_mode={_resolved_api_mode} base_url={resolved_base_url}", flush=True)
          except Exception as _oc_route_e:
              print(f"[webui] WARNING: opencode route resolution failed: {_oc_route_e}", flush=True)

          print(f"[webui-debug] Creating AIAgent: model={resolved_model} provider={resolved_provider} base_url={resolved_base_url} api_mode={_resolved_api_mode} api_key={'set' if resolved_api_key else 'NONE'}", flush=True)
          agent = AIAgent(
              model=resolved_model,
              provider=resolved_provider,
              base_url=resolved_base_url,
              api_key=resolved_api_key,
              api_mode=_resolved_api_mode,
              platform='webui',
              quiet_mode=True,
              enabled_toolsets=_toolsets,
              fallback_model=_fallback_resolved,
              session_id=session_id,
              session_db=_session_db_instance,
              stream_delta_callback=on_token,
              tool_progress_callback=on_tool,
              reasoning_callback=on_reasoning,
              api_error_callback=on_api_error,
              ephemeral_system_prompt=_ephemeral_prompt,
          )
          print(f"[webui-debug] AIAgent created, api_mode={getattr(agent, 'api_mode', '?')}", flush=True)

          # Register agent so cancel_stream() can call agent.interrupt()
          # to force-abort in-flight HTTP requests instead of waiting for the 120s timeout.
          with _ACTIVE_AGENTS_LOCK:
              _ACTIVE_AGENTS[stream_id] = agent

          # ── Dynamic Streaming Tools Injection ──
          # Injects MCP tools, Patch Registry, Memory Forget, Media generation,
          # Self-Update, and Self-Evolution tools into Hermes registry and agent.tools.
          from api.streaming_tools import register_all_streaming_tools
          injected_count = register_all_streaming_tools(agent, s, session_id, cancel_event)

          is_browser_session = bool(
              "[실시간 브라우저 환경 컨텍스트" in (msg_text or "") or
              "[브라우저 제어 명령 규칙]" in (msg_text or "") or
              "[구글 크롬" in (msg_text or "") or
              "[사용자 요청]" in (msg_text or "") or
              (isinstance(session_id, str) and session_id.startswith("browser_"))
          )
          if is_browser_session and hasattr(agent, 'tools') and isinstance(agent.tools, list):
              # 크롬 확장프로그램 사이드패널 모드일 때는 실패하는 내부 Electron 브라우저 도구를 비활성화
              agent.tools = [t for t in agent.tools if not t.get('function', {}).get('name', '').startswith('browser_')]

          # ── System Prompt & Multimodal Message Composition ──
          from api.streaming_prompts import compose_system_message, build_user_payload

          workspace_system_msg, _p6_turn_injected_facts = compose_system_message(
              session_id=session_id,
              workspace=s.workspace,
              resolved_model=resolved_model,
              msg_text=msg_text or '',
              planning_mode=planning_mode,
              open_tabs=open_tabs,
              injected_mcp_count=injected_count,
              browser_context="chrome_sidepanel" if is_browser_session else None,
          )
          if _ephemeral_prompt:
              workspace_system_msg += "\n\n" + _ephemeral_prompt

          # Clean up any trailing interrupted assistant messages from a previous cancelled run
          while s.messages and s.messages[-1].get('role') == 'assistant':
              _last_c = str(s.messages[-1].get('content') or '')
              if any(p in _last_c for p in ('Operation interrupted', 'Cancelled by user', 'Cancelled before', '작업이 중지', '이전 작업이 자동 취소')):
                  s.messages.pop()
              else:
                  break

          # TD1: Persist user message to history immediately so it's saved even if agent crashes
          display_user_msg = msg_text
          if "[연속 자율 실행 모드" in msg_text:
              display_user_msg = "🔄 [연속 자율 진행 피드백]"
          elif "[사용자 요청]" in msg_text:
              parts = msg_text.split("[사용자 요청]", 1)
              display_user_msg = parts[1].strip()
              if "[브라우저 제어" in display_user_msg:
                  display_user_msg = display_user_msg.split("[브라우저 제어", 1)[0].strip()
              if "[구글 크롬" in display_user_msg:
                  display_user_msg = display_user_msg.split("[구글 크롬", 1)[0].strip()
              if "[직전 브라우저" in display_user_msg:
                  display_user_msg = display_user_msg.split("[직전 브라우저", 1)[0].strip()
              if "(참고: 브라우저 조작" in display_user_msg:
                  display_user_msg = display_user_msg.split("(참고: 브라우저 조작", 1)[0].strip()

          if not any(m.get('role') == 'user' and m.get('content') in (msg_text, display_user_msg) for m in s.messages[-2:]):
              user_msg = {'role': 'user', 'content': display_user_msg or msg_text, 'timestamp': int(time.time())}
              # P6: Validate message shape against shared schema before persisting
              if _SCHEMA_AVAILABLE:
                  ok, err = _validate_msg(user_msg)
                  if not ok:
                      _logger.warning("Schema validation for user message failed: %s", err)
              s.messages.append(user_msg)
              s.save()

          # Process attachments to base64 images if present for multimodal models
          user_message_payload = build_user_payload(
              workspace=s.workspace,
              msg_text=msg_text,
              workspace_ctx="",
              attachments=attachments,
          )

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
              # 이전 실행이 아직 정리 중(취소 후 도구 중단/세션 저장 등)일 수
              # 있다. 즉시 실패하면 사용자에게 "연결 끊김"처럼 보이므로 취소
              # 정리 시간을 커버할 수 있도록 한 번 더 여유를 두고 대기한다.
              print(f"[webui] WARN: _agent_lock for session {session_id} not acquired after 25s — retrying (cancel wind-down)", flush=True)
              _lock_acquired = _agent_lock.acquire(timeout=30)
          if not _lock_acquired:
              print(f"[webui] WARN: _agent_lock for session {session_id} not acquired after retry — aborting", flush=True)
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
              # cancel_stream()이 취소 시 세션 락을 강제 해제한 경우,
              # 여기서 또 release()하면 RuntimeError가 발생하므로 무시한다.
              try:
                  _agent_lock.release()
              except RuntimeError:
                  pass
          print(f"[webui-debug] run_conversation completed for session={session_id}", flush=True)
          _result_msgs = result.get('messages')
          if _result_msgs:
              s.messages = [m for m in _result_msgs if m.get('role') != 'system']
          if cancel_event.is_set():
              while s.messages and s.messages[-1].get('role') == 'assistant':
                  _lc = str(s.messages[-1].get('content') or '')
                  if any(p in _lc for p in ('Operation interrupted', 'Cancelled by user', 'Cancelled before', '작업이 중지', '이전 작업이 자동 취소')):
                      s.messages.pop()
                  else:
                      break

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
          _old_sid = session_id  # always defined so we can use it in the compressed event
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
          # Notify the frontend that compression happened — include new_session_id
          # so the frontend can update State.activeSessionId to avoid "Session not found"
          # on the next message (the old session_id file was renamed).
          if _compressed:
              _new_sid = s.session_id
              put('compressed', {
                  'message': 'Context auto-compressed to continue the conversation',
                  'old_session_id': _old_sid,
                  'new_session_id': _new_sid,
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
                  # [2026-08-31] OpenAI 호환 형식(assistant.tool_calls 필드) 파싱 —
                  # Qwen 등 OpenAI 호환 모델은 tool 호출이 content가 아니라
                  # m['tool_calls'] 필드([{id, function:{name, arguments}}])로 온다.
                  # 기존 로직은 Anthropic 형식(content 내 tool_use 블록)만 파싱해
                  # 이 모델들에서 tool_calls가 항상 0개였다(도구 카드 소실 원인).
                  for _tc in (m.get('tool_calls') or []):
                      if not isinstance(_tc, dict):
                          continue
                      _fn = _tc.get('function') or {}
                      _tid = _tc.get('id') or _tc.get('call_id') or ''
                      _tname = _fn.get('name') or _tc.get('name') or ''
                      if _tid and _tname:
                          pending_names[_tid] = _tname
                          try:
                              pending_args[_tid] = json.loads(_fn.get('arguments') or '{}')
                          except Exception:
                              pending_args[_tid] = {}
                          pending_asst_idx[_tid] = msg_idx
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
        # 위험 명령 승인 콜백 등록 해제: 아직 블로킹 중인 승인 스레드가 있으면
        # 즉시 해제되어 영원히 hang하지 않는다 (unregister가 이벤트 set).
        if _gateway_notify_registered:
            try:
                import tools.approval as _tools_approval_mod
                # 같은 세션의 스트림 경합 안전장치: 우리가 등록한 콜백이 아직
                # 그대로일 때만 해제한다. (이전 스트림의 cleanup이 새 스트림의
                # 콜백을 잘못 해제해 승인 경로가 끊기는 것을 방지)
                if _tools_approval_mod._gateway_notify_cbs.get(session_id) is _cmd_approval_notify:
                    _tools_approval_mod.unregister_gateway_notify(session_id)
            except Exception:
                _logger.warning("unregister_gateway_notify failed for session %s", session_id, exc_info=True)
        _clear_thread_env()  # TD1: always clear thread-local context
        _thread_put.put = None  # release the stream-local put() capture
        with _ACTIVE_AGENTS_LOCK:
            _ACTIVE_AGENTS.pop(stream_id, None)
        with _STREAM_THREADS_LOCK:
            _STREAM_THREADS.pop(stream_id, None)
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
        # Do not drain the queue here.  The SSE handler may already be blocked
        # in q.get() and must receive the terminal event that was enqueued by
        # put().  Draining it here creates a race where the browser remains in
        # the "cancel"/busy state forever.  The captured payload is sufficient
        # for late EventSource reconnects.
        _cached_done = emitter.terminal_done_data
        if _cached_done is not None:
            with _COMPLETED_STREAMS_LOCK:
                _COMPLETED_STREAMS[stream_id] = (_cached_done, time.time())
                # Clean up entries older than 60 seconds
                _now = time.time()
                _stale = [sid for sid, (_, ts) in _COMPLETED_STREAMS.items() if _now - ts > 60]
                for sid in _stale:
                    del _COMPLETED_STREAMS[sid]
        elif cancel_event.is_set():
            # Cancelled stream: remember it so an EventSource that
            # auto-reconnects gets a clean 'cancel' event instead of a 404
            # "stream not found" (which the UI shows as a connection error).
            with _CANCELLED_STREAMS_LOCK:
                _CANCELLED_STREAMS[stream_id] = time.time()
                _now = time.time()
                _stale = [sid for sid, ts in _CANCELLED_STREAMS.items() if _now - ts > 60]
                for sid in _stale:
                    del _CANCELLED_STREAMS[sid]


def cancel_stream(stream_id: str, session_id: str | None = None) -> bool:
    """Signal an in-flight stream to cancel. Returns True if the stream existed.

    session_id가 주어지면 강제 정리 시 세션 락을 직접 해제할 수 있어,
    새 스트림이 _ACTIVE_SESSION_STREAMS[session_id]를 덮어쓴 뒤에도
    (역방향 조회 실패로 인한) 락 누수가 발생하지 않는다.
    """
    # 0) 즉시 _CANCELLED_STREAMS에 등록:
    # 브라우저 EventSource가 끊어지고 즉시 재연결할 때 404가 발생하지 않고
    # 클린한 'cancel' 이벤트를 받아 UI가 에러 없이 정상 종료되도록 한다.
    with _CANCELLED_STREAMS_LOCK:
        _CANCELLED_STREAMS[stream_id] = time.time()
        _now = time.time()
        _stale = [sid for sid, ts in _CANCELLED_STREAMS.items() if _now - ts > 60]
        for sid in _stale:
            del _CANCELLED_STREAMS[sid]

    # NEW: Tell the AIAgent to stop its in-flight HTTP request immediately.
    # Without this, cancel_event.set() has no way to reach the agent thread —
    # the HTTP request keeps running until its own 120s timeout.
    with _ACTIVE_AGENTS_LOCK:
        agent = _ACTIVE_AGENTS.get(stream_id)
    if agent:
        try:
            agent.interrupt("User cancelled")
        except Exception as _intr_err:
            _logger.debug("agent.interrupt failed during cancel: %s", _intr_err)

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
        # Cleanup: 에이전트 워커 스레드가 실제로 종료될 때까지 기다렸다가
        # STREAMS를 제거한다. 워커가 아직 정리 중(도구 중단, 세션 저장)인데
        # 3초 만에 강제 제거하던 기존 동작은 SSE 재연결이 404를 받게 만들어
        # 사용자에게 "에이전트 연결 끊김"으로 보였다. 정상 경로에서는 워커의
        # finally 블록이 STREAMS를 제거하므로 이곳은 안전장치다.
        def _force_cleanup():
            with _STREAM_THREADS_LOCK:
                worker = _STREAM_THREADS.get(stream_id)
            # 1) 도구가 인터럽트를 반영해 스스로 종료되기를 잠시 기다린다. (1.5초)
            _grace_deadline = time.time() + 1.5
            while worker is not None and worker.is_alive() and time.time() < _grace_deadline:
                time.sleep(0.3)
            # 2) 인터럽트 전파 후에도 워커가 계속 실행 중이면(도구가 인터럽트를
            #    반영하지 못해 run_conversation()이 아직 반환하지 않은 경우)
            #    세션 락을 강제 해제해 다음 메시지가 즉시 진행될 수 있게 한다.
            if worker is not None and worker.is_alive():
                _force_release_session_lock(stream_id, session_id=session_id)
            # 3) STREAMS 정리 안전장치 (최대 20초 대기)
            deadline = time.time() + 20
            while worker is not None and worker.is_alive() and time.time() < deadline:
                time.sleep(0.5)
            with STREAMS_LOCK:
                if stream_id in STREAMS:
                    _logger.warning(
                        "Force-cleaning stale stream %s (agent did not finish within 20s of cancel)",
                        stream_id
                    )
                    STREAMS.pop(stream_id, None)
                    CANCEL_FLAGS.pop(stream_id, None)
            with _ACTIVE_AGENTS_LOCK:
                _ACTIVE_AGENTS.pop(stream_id, None)
        threading.Thread(target=_force_cleanup, daemon=True).start()
        return True


def _force_release_session_lock(stream_id: str, session_id: str | None = None) -> None:
    """취소 후에도 세션 락이 남아 있으면 강제로 해제한다.

    도구(terminal 등)가 인터럽트를 반영하지 못하고 오래 실행 중이어도,
    새 메시지가 '이전 작업이 아직 종료되지 않았습니다'로 거부되지 않도록
    한다. 워커의 finally 블록에서 release()를 한 번 더 호출해도
    RuntimeError가 발생하지 않도록 이미 try/except로 보호되어 있다.

    session_id를 명시적으로 받은 경우 그대로 사용하고, 받지 못한 경우에만
    (하위 호환) 역방향 조회로 찾는다. 역방향 조회는 취소 직후 새 스트림이
    _ACTIVE_SESSION_STREAMS[session_id]를 덮어쓰면 실패할 수 있으므로,
    취소 경로에서는 반드시 session_id를 전달해야 락이 해제된다.
    """
    if not session_id:
        with _ACTIVE_SESSION_STREAMS_LOCK:
            for _sid, _sid_stream in _ACTIVE_SESSION_STREAMS.items():
                if _sid_stream == stream_id:
                    session_id = _sid
                    break
    if not session_id:
        return
    try:
        lock = _get_session_agent_lock(session_id)
        if lock.locked():
            lock.release()
            _logger.info(
                "Force-released session lock for session %s (stream %s cancelled)",
                session_id, stream_id,
            )
    except Exception as e:
        _logger.warning(
            "Failed to force-release session lock for session %s: %s",
            session_id, e,
        )
