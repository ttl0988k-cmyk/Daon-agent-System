# -*- coding: utf-8 -*-
"""자가 진화(self-evolution) 배선 검증 스크립트.

배경
----
9af7c02 (08-26) 에서는 `propose_self_evolution` 도구가 streaming.py 에 인라인으로
구현되어 있었다. eb2d212 (09-08) 리팩터가 이를 streaming_tools.py 로 옮기면서
`register_self_evolution_tools` 구현체를 삭제했고, 임포트만 남아 ImportError 가
try/except 에 삼켜졌다. 결과적으로:

  * 프롬프트는 "propose_self_evolution 을 호출하라"고 지시하는데
  * 도구는 레지스트리/에이전트 어디에도 존재하지 않는다 (프롬프트-툴 불일치).

여기에 더해 4개 거버넌스 지점(승인 배너, 계획 승인, 위험 명령 승인, 진화 진행 로그)이
`STREAMS.get(session_id)` 로 큐를 조회했는데, STREAMS 는 **stream_id** 로 키잉되므로
항상 None 이 되어 SSE 알림이 조용히 유실되었다.

이 스크립트는 위 두 결함이 실제로 수정되었는지 검증한다. 서버 기동 없이 순수
인프로세스로 실행된다.

실행
----
    python scripts/verify_self_evolution_wiring.py

종료 코드 0 = 전부 통과, 1 = 하나 이상 실패.
"""

import inspect
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# server.py 와 동일한 import 경로 구성을 사용한다.
for _p in (
    ROOT,
    ROOT / "api",
    ROOT / "api" / "api",
    ROOT / "hermes-agent",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


_PASS = []
_FAIL = []


def check(name, condition, detail=""):
    if condition:
        _PASS.append(name)
        print("[PASS] %s" % name)
    else:
        _FAIL.append((name, detail))
        print("[FAIL] %s%s" % (name, (" -- " + detail) if detail else ""))


# ---------------------------------------------------------------------------
# [1] 레지스트리/에이전트 주입: 툴이 실제로 존재하고, 두 번 주입해도 한 번만 남는다.
# ---------------------------------------------------------------------------
print("\n[1] 툴 등록 및 주입 멱등성")

TOOL_NAME = "propose_self_evolution"

try:
    from tools.registry import registry
    from api.streaming_tools import (
        inject_self_evolution_tool,
        register_all_streaming_tools,
    )
    check("import: streaming_tools / registry", True)
except Exception as e:  # pragma: no cover
    check("import: streaming_tools / registry", False, repr(e))
    print("\n치명적 임포트 실패로 나머지 검사를 건너뜁니다.")
    sys.exit(1)


class _StubAgent:
    """run_agent.AIAgent 의 최소 대역 (tools/valid_tool_names 만 필요)."""

    def __init__(self):
        self.tools = []
        self.valid_tool_names = set()


agent = _StubAgent()
inject_self_evolution_tool(agent, session_id="sess-1", stream_id="stream-1")
names_after_first = [
    t.get("function", {}).get("name") for t in agent.tools if isinstance(t, dict)
]
check(
    "주입 1회: propose_self_evolution 스키마가 agent.tools 에 존재",
    names_after_first.count(TOOL_NAME) == 1,
    "tools=%r" % (names_after_first,),
)

# 같은 에이전트에 재주입해도 중복 append 되면 안 된다.
inject_self_evolution_tool(agent, session_id="sess-1", stream_id="stream-1")
names_after_second = [
    t.get("function", {}).get("name") for t in agent.tools if isinstance(t, dict)
]
check(
    "주입 2회(중복 방지): agent.tools 에 정확히 1개만 유지",
    names_after_second.count(TOOL_NAME) == 1,
    "tools=%r" % (names_after_second,),
)
check(
    "valid_tool_names 에 등록 (런타임 미등록 도구 거부 방지)",
    TOOL_NAME in agent.valid_tool_names,
)

# 다른 세션으로 재주입해도 registry/agent 양쪽 모두 중복이 생기면 안 된다.
agent2 = _StubAgent()
inject_self_evolution_tool(agent2, session_id="sess-2", stream_id="stream-2")
check(
    "다른 세션 재주입: 신규 에이전트에도 정확히 1개",
    [t.get("function", {}).get("name") for t in agent2.tools].count(TOOL_NAME) == 1,
)

# ---------------------------------------------------------------------------
# [2] 함수 계약: inject / register_all 이 session_id, stream_id 를 받는다.
# ---------------------------------------------------------------------------
print("\n[2] 함수 시그니처 계약")

try:
    sig_inject = inspect.signature(inject_self_evolution_tool)
    params = set(sig_inject.parameters)
    check(
        "inject_self_evolution_tool(agent, session_id, stream_id)",
        {"agent", "session_id", "stream_id"}.issubset(params),
        "params=%r" % (sorted(params),),
    )

    sig_all = inspect.signature(register_all_streaming_tools)
    params_all = set(sig_all.parameters)
    check(
        "register_all_streaming_tools(..., stream_id)",
        "stream_id" in params_all,
        "params=%r" % (sorted(params_all),),
    )
except Exception as e:
    check("함수 시그니처 조회", False, repr(e))

# ---------------------------------------------------------------------------
# [3] 레지스트리 등록 + toolset 별칭 ('evolution' -> 'self-evolution').
# ---------------------------------------------------------------------------
print("\n[3] 레지스트리 등록 및 toolset 별칭")

try:
    entry = registry.get_entry(TOOL_NAME)
    check("registry 에 propose_self_evolution 등록됨", entry is not None)
    if entry is not None:
        check(
            "registry toolset == 'self-evolution'",
            getattr(entry, "toolset", None) == "self-evolution",
            "toolset=%r" % (getattr(entry, "toolset", None),),
        )
        schema = getattr(entry, "schema", None) or {}
        check(
            "registry 스키마 이름 정합",
            schema.get("name") == TOOL_NAME,
            "schema.name=%r" % (schema.get("name"),),
        )
    try:
        target = registry.get_toolset_alias_target("evolution")
    except Exception:
        target = None
    check(
        "toolset 별칭 'evolution' -> 'self-evolution'",
        target == "self-evolution",
        "target=%r" % (target,),
    )
except Exception as e:
    check("레지스트리 조회", False, repr(e))

# ---------------------------------------------------------------------------
# [4] 핸들러 안전성: 잘못된 인자에도 raise 없이 JSON 을 반환해야 한다.
# ---------------------------------------------------------------------------
print("\n[4] 핸들러 안전성")

try:
    from api.dynamic.self_evolution import register_self_evolution_tools

    schema_probe = register_self_evolution_tools(registry, session_id="sess-x")
    check(
        "register_self_evolution_tools 가 OpenAI 함수 스키마를 반환",
        isinstance(schema_probe, dict)
        and schema_probe.get("type") == "function"
        and schema_probe.get("function", {}).get("name") == TOOL_NAME,
        "schema=%r" % (schema_probe,),
    )
    check(
        "required == ['capability']",
        (schema_probe.get("function", {}).get("parameters", {}) or {}).get("required")
        == ["capability"],
    )

    entry = registry.get_entry(TOOL_NAME)
    handler = getattr(entry, "handler", None) if entry is not None else None
    check("registry 핸들러 콜러블", callable(handler))

    if callable(handler):
        raw = handler({})
        parsed = json.loads(raw)
        check(
            "capability 누락 시 ok=False / status='invalid' (raise 없음)",
            isinstance(parsed, dict)
            and parsed.get("ok") is False
            and parsed.get("status") == "invalid",
            "result=%r" % (parsed,),
        )
except Exception as e:
    check("핸들러 안전성", False, repr(e))

# ---------------------------------------------------------------------------
# [5] 프롬프트-툴 정합성: 프롬프트가 지시하는 도구가 실제로 존재해야 한다.
# ---------------------------------------------------------------------------
print("\n[5] 프롬프트-툴 정합성")

try:
    from api.dynamic.self_evolution import get_self_evolution_prompt_block

    block = get_self_evolution_prompt_block()
    check("프롬프트 블록 생성 (빈 문자열 아님)", bool(block and block.strip()))

    mentioned = set(re.findall(r"`([a-z_]+)`", block or ""))
    check(
        "프롬프트가 지시하는 도구 이름 파싱",
        TOOL_NAME in mentioned,
        "mentioned=%r" % (sorted(mentioned),),
    )

    # 프롬프트가 언급한 자가진화 소관 도구(이 스크립트가 주입 대상으로 삼는
    # 도구)는 실제 레지스트리에 있어야 한다. request_server_update 는
    # inject_self_update_tool 이 별도로 등록하는 도구이므로 여기서는 제외한다.
    _SE_OWNED = {TOOL_NAME}
    missing = sorted(
        n for n in (mentioned & _SE_OWNED) if registry.get_entry(n) is None
    )
    check(
        "프롬프트가 지시하는 자가진화 도구가 registry 에 존재",
        not missing,
        "미등록=%r" % (missing,),
    )
    # 프롬프트가 request_server_update 를 언급한다면, 그 도구를 소유한
    # 주입기(inject_self_update_tool)를 실제로 실행해 등록됨을 증명한다.
    # 존재하지 않는 도구를 프롬프트가 지시하면 LLM 이 헛호출을 시도한다.
    if "request_server_update" in mentioned:
        from api.streaming_tools import inject_self_update_tool

        _su_agent = _StubAgent()
        inject_self_update_tool(_su_agent, session=None, session_id="sess-rsu-1")
        _rsu_in_registry = registry.get_entry("request_server_update") is not None
        _rsu_in_agent = "request_server_update" in _su_agent.valid_tool_names
        check(
            "프롬프트의 request_server_update 언급 시 주입기가 실제 등록",
            _rsu_in_registry and _rsu_in_agent,
            "registry=%s agent_valid=%s" % (_rsu_in_registry, _rsu_in_agent),
        )
    else:
        check(
            "프롬프트의 request_server_update 언급 여부 확인",
            True,
            "mentioned=%r (언급 시 주입기 검증 분기)" % (sorted(mentioned),),
        )
except Exception as e:
    check("프롬프트-툴 정합성", False, repr(e))

# ---------------------------------------------------------------------------
# [6] SSE 큐 해석: session_id 로도 살아있는 stream 큐를 찾아야 한다.
# ---------------------------------------------------------------------------
print("\n[6] session_id -> stream 큐 해석 (핵심 버그)")

try:
    import api.config as cfg

    SESSION = "sess-wire-1"
    STREAM = "stream-wire-1"

    sentinel_q = object()
    cfg.STREAMS[STREAM] = sentinel_q
    with cfg.ACTIVE_SESSION_STREAMS_LOCK:
        cfg.ACTIVE_SESSION_STREAMS[SESSION] = STREAM

    check(
        "resolve_stream_id(session) == stream",
        cfg.resolve_stream_id(SESSION) == STREAM,
        "got=%r" % (cfg.resolve_stream_id(SESSION),),
    )
    check(
        "get_stream_queue(stream_id) 직접 조회",
        cfg.get_stream_queue(STREAM) is sentinel_q,
    )
    check(
        "get_stream_queue(session_id) 역참조 해석 (과거엔 항상 None)",
        cfg.get_stream_queue(SESSION) is sentinel_q,
    )
    check(
        "get_stream_queue(미지의 키) == None (raise 하지 않음)",
        cfg.get_stream_queue("no-such-session") is None,
    )

    # 세션 스트림이 정리되면 역참조도 더 이상 큐를 반환하지 않아야 한다.
    with cfg.ACTIVE_SESSION_STREAMS_LOCK:
        cfg.ACTIVE_SESSION_STREAMS.pop(SESSION, None)
    cfg.STREAMS.pop(STREAM, None)
    check(
        "스트림 종료 후 세션 키 조회 == None (누수 없음)",
        cfg.get_stream_queue(SESSION) is None,
    )
except Exception as e:
    check("SSE 큐 해석", False, repr(e))

# ---------------------------------------------------------------------------
# [7] 진행 로그 라우팅: _log_to_stream 이 해석된 큐로 이벤트를 넣어야 한다.
# ---------------------------------------------------------------------------
print("\n[7] 진화 진행 로그 SSE 라우팅")

try:
    import queue as _q_mod

    SESSION = "sess-wire-2"
    STREAM = "stream-wire-2"
    live_q = _q_mod.Queue()
    cfg.STREAMS[STREAM] = live_q
    with cfg.ACTIVE_SESSION_STREAMS_LOCK:
        cfg.ACTIVE_SESSION_STREAMS[SESSION] = STREAM

    from api.dynamic.self_evolution import _log_to_stream

    _log_to_stream(SESSION, "self-evolution", "proposal started", stream_id=STREAM)

    routed = False
    evt = None
    try:
        routed = not live_q.empty()
        if routed:
            evt = live_q.get_nowait()
    except Exception:
        pass

    check("활성 스트림으로 진행 로그 전달됨", routed)
    check(
        "이벤트 형식 == ('agent_log', {...})",
        isinstance(evt, tuple)
        and len(evt) == 2
        and evt[0] == "agent_log"
        and evt[1].get("content") == "proposal started",
        "evt=%r" % (evt,),
    )

    # 스트림이 없을 때는 조용히 무시해야 한다 (raise 금지).
    try:
        _log_to_stream("sess-no-stream", "self-evolution", "should be dropped")
        check("라이브 스트림 없음 -> 조용히 무시 (raise 없음)", True)
    except Exception as e:
        check("라이브 스트림 없음 -> 조용히 무시 (raise 없음)", False, repr(e))

    with cfg.ACTIVE_SESSION_STREAMS_LOCK:
        cfg.ACTIVE_SESSION_STREAMS.pop(SESSION, None)
    cfg.STREAMS.pop(STREAM, None)
except Exception as e:
    check("진행 로그 라우팅", False, repr(e))

# ---------------------------------------------------------------------------
# [8] 정적 검사: session_id 로 STREAMS 를 직접 조회하는 버그 패턴이 남아 있으면 안 된다.
# ---------------------------------------------------------------------------
print("\n[8] 정적 검사: STREAMS.get(session_id) 잔존 여부")

_GOVERNANCE_FILES = [
    "api/api/dynamic/builder_approval.py",
    "api/api/dynamic/orchestrator.py",
    "api/api/dynamic_jobs.py",
    "api/api/dynamic/self_evolution.py",
]

_BAD_PATTERN = re.compile(r"STREAMS\s*\.\s*get\s*\(\s*session_id\s*\)")

for rel in _GOVERNANCE_FILES:
    path = ROOT / rel
    if not path.exists():
        check("파일 존재: %s" % rel, False, "not found")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = _BAD_PATTERN.findall(text)
    check(
        "session_id 직접 조회 없음: %s" % rel,
        not hits,
        "hits=%d" % (len(hits),),
    )

# 4개 지점이 모두 공용 해석 헬퍼를 쓰는지 확인한다.
for rel in _GOVERNANCE_FILES:
    path = ROOT / rel
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    check(
        "get_stream_queue 사용: %s" % rel,
        "get_stream_queue" in text,
    )

# ---------------------------------------------------------------------------
# 결과 요약
# ---------------------------------------------------------------------------
print("\n" + "=" * 62)
print("통과 %d / 실패 %d" % (len(_PASS), len(_FAIL)))
if _FAIL:
    print("-" * 62)
    for name, detail in _FAIL:
        print("  FAIL: %s%s" % (name, (" (%s)" % detail) if detail else ""))
    print("=" * 62)
    sys.exit(1)

print("모든 배선 검증을 통과했습니다.")
print("=" * 62)
sys.exit(0)
