# -*- coding: utf-8 -*-
"""
갭 E-4b: 자가 진화 활성화 (Self-Evolution Activation).

E-L1~E-L4 부품(capability_resolver / builder_agent / incorporation)은 모두
구현되어 있지만 프로덕션 채팅 에이전트에서는 세 가지 이유로 잠겨 있다:
  1. 인지 계층 부재 - 에이전트가 자가 진화 능력의 존재를 모른다.
  2. 리스크 5 기본 거부 - Builder 승인 게이트에 승인자(approver)가 없으면 거부.
  3. 진입 도구 없음 - 채팅 흐름에서 결핍 능력 제작을 요청할 방법이 없다.

이 모듈은 순수 부가(pure additive)로 세 가지를 모두 해결한다:
  * get_self_evolution_prompt_block() : 시스템 프롬프트 인지 블록 (항상 주입)
  * is_auto_mode_enabled()            : config.yaml self_evolution.auto_mode 옵트인
  * start_proposal()                  : 백그라운드 스레드에서
        승인(builder_spawn) -> Builder 서브팀 스폰 -> 편입 거버넌스 수행

불변 질서(생성 -> 격리 -> 검증 -> 승인 -> 편입 -> 사용)는 절대 우회하지 않는다:
auto_mode=True 여도 make_session_approver 의 자동 타임아웃 승인 경로만 사용하며,
게이트/편입 단계 자체를 건너뛰지 않는다. 어떤 함수도 절대 raise 하지 않는다.
"""

import threading

_PROMPT_BLOCK_HEADER = "[SELF-EVOLUTION CAPABILITY]"

# 동시 실행 중인 제안 스레드 (능력 이름별). 프로세스 내 중복 스폰 방지.
_ACTIVE_PROPOSALS = {}
_ACTIVE_LOCK = threading.Lock()


def _cfg_get(key_path, default=None):
    """config.yaml 에서 dot-path 로 값을 읽는다. 실패 시 default."""
    try:
        from api.config import get_config
        val = get_config()
        for part in key_path.split('.'):
            if isinstance(val, dict):
                val = val.get(part)
            else:
                val = None
                break
        return val if val is not None else default
    except Exception:
        return default


def is_auto_mode_enabled() -> bool:
    """self_evolution.auto_mode 옵트인 플래그.

    기본 False (리스크 5 안전 기본 유지). config.yaml 또는 환경변수
    DAON_SELF_EVOLUTION_AUTO=1 로 명시적으로 켠 경우에만 True.
    """
    try:
        import os
        env_val = os.environ.get('DAON_SELF_EVOLUTION_AUTO')
        if env_val is not None:
            return str(env_val).strip().lower() in ('1', 'true', 'yes', 'on')
    except Exception:
        pass
    val = _cfg_get('self_evolution.auto_mode', False)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('1', 'true', 'yes', 'on')


def get_self_evolution_prompt_block() -> str:
    """시스템 프롬프트에 주입할 자가 진화 인지 블록.

    항상 주입 대상(인지 계층은 무료) — auto_mode 와 무관하게 능력의 존재와
    진입 방법을 알린다. 실패 시 '' 반환 (절대 raise 하지 않음).
    """
    try:
        lines = [
            _PROMPT_BLOCK_HEADER,
            "You are a DAON system agent with a SELF-EVOLUTION capability.",
            "When you detect a MISSING capability during a task (no tool, no skill,",
            "no plugin can accomplish what the user needs), you may propose building it:",
            "",
            "1. Call the `propose_self_evolution` tool with:",
            "   - capability: short name of the missing ability (e.g. 'pdf-form-filler')",
            "   - description: what it must do and how success is verified",
            "2. The system runs the immutable order:",
            "   create -> isolate -> verify -> approve -> incorporate -> use.",
            "3. A Builder sub-team drafts the Skill; governance verifies probes and",
            "   asks the user for approval before anything is incorporated.",
            "4. After incorporation the new Skill becomes available automatically.",
            "",
            "Rules:",
            "- Propose ONLY when no existing tool/skill can do the job.",
            "- One proposal per capability per session; never spam duplicates.",
            "- Never claim the capability exists until incorporation succeeds.",
            "- The user always sees and controls the approval step.",
        ]
        return "\n".join(lines)
    except Exception:
        return ''


def _log_to_stream(session_id, agent, message, status="running"):
    """SSE 큐로 진행 상황 전달 (최선 노력, 절대 raise 하지 않음)."""
    if not session_id:
        return
    try:
        from api.config import STREAMS
        queue = STREAMS.get(session_id)
        if queue:
            queue.put(('agent_log', {
                'agent': agent,
                'content': message,
                'status': status,
            }))
    except Exception:
        pass


def _run_proposal(capability, description, session_id):
    """백그라운드 제안 스레드 본체: 승인 -> 스폰 -> 편입.

    E-L2 dispatch_builder_requests 와 E-L4 incorporate_builder_dispatches 를
    직접 재사용한다. 어떤 경로에서도 스레드가 죽지 않도록 최외곽 try/except.
    """
    dispatch_record = None
    incorporation_results = []
    error_msg = ""
    try:
        # 1) 승인자 구성 (auto_mode 여부와 무관하게 세션 승인 인프라 사용).
        approver = None
        try:
            from api.dynamic.builder_approval import (
                make_session_approver, APPROVAL_KIND_BUILDER_SPAWN,
                APPROVAL_KIND_INCORPORATION)
            approver = make_session_approver(
                session_id, kind=APPROVAL_KIND_BUILDER_SPAWN,
                log_callback=lambda a, c, s="running": _log_to_stream(session_id, a, c, s))
            incorporation_approver = make_session_approver(
                session_id, kind=APPROVAL_KIND_INCORPORATION,
                log_callback=lambda a, c, s="running": _log_to_stream(session_id, a, c, s))
        except Exception as e:
            incorporation_approver = None
            _log_to_stream(session_id, "Builder",
                           "승인 인프라 구성 실패: %s" % e, "warning")

        # 2) E-L2: 게이트 + Builder 서브팀 스폰.
        builder_queue = [{
            "capability": capability,
            "description": description or "",
        }]
        try:
            from api.dynamic.builder_agent import dispatch_builder_requests
            records = dispatch_builder_requests(
                builder_queue, approver=approver,
                log_callback=lambda a, c, s="running": _log_to_stream(session_id, a, c, s))
            dispatch_record = records[0] if records else None
        except Exception as e:
            error_msg = "builder dispatch failed: %s" % e
            _log_to_stream(session_id, "Builder", error_msg, "error")

        spawned = bool(dispatch_record and dispatch_record.get("status") == "spawned")
        if not spawned:
            reason = (dispatch_record or {}).get("reason") or error_msg or "dispatch denied"
            _log_to_stream(
                session_id, "Builder",
                "제작 요청이 처리되지 않았습니다: %s" % reason, "warning")
            return

        # 3) E-L4: 편입 거버넌스 (진입 -> 프로브 -> 승인 -> 편입).
        try:
            from api.dynamic.builder_pipeline import incorporate_builder_dispatches
            incorporation_results = incorporate_builder_dispatches(
                [dispatch_record], session_id=session_id,
                approver=incorporation_approver,
                log_callback=lambda a, c, s="running": _log_to_stream(session_id, a, c, s))
        except Exception as e:
            _log_to_stream(session_id, "Governance",
                           "편입 파이프라인 오류: %s" % e, "error")

        ok = any(r.get("ok") for r in incorporation_results if isinstance(r, dict))
        if ok:
            names = ", ".join(str(r.get("name") or "") for r in incorporation_results
                              if isinstance(r, dict) and r.get("ok"))
            _log_to_stream(
                session_id, "Governance",
                "자가 진화 완료: '%s' 스킬이 편입되었습니다. 다음 턴부터 사용 가능합니다." % names,
                "running")
        else:
            _log_to_stream(
                session_id, "Governance",
                "편입이 완료되지 않았습니다 (초안은 draft 상태로 보존됨).", "warning")
    except Exception as e:
        _log_to_stream(session_id, "System",
                       "자가 진화 파이프라인 내부 오류: %s" % e, "error")
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_PROPOSALS.pop(str(capability or "").strip(), None)


def start_proposal(capability, description="", session_id=None):
    """결핍 능력 제작 제출. 백그라운드 스레드를 띄우고 즉시 반환.

    반환 dict: {"ok": bool, "status": started|duplicate|invalid, "message": str}
    절대 raise 하지 않는다.
    """
    cap = str(capability or "").strip()
    if not cap:
        return {"ok": False, "status": "invalid",
                "message": "capability is required"}
    with _ACTIVE_LOCK:
        if cap in _ACTIVE_PROPOSALS:
            return {"ok": False, "status": "duplicate",
                    "message": ("A build proposal for '%s' is already running "
                                "in this session." % cap)}
        thread = threading.Thread(
            target=_run_proposal,
            args=(cap, str(description or ""), session_id),
            daemon=True,
            name="self-evolution-%s" % cap[:40],
        )
        _ACTIVE_PROPOSALS[cap] = thread
    thread.start()
    return {"ok": True, "status": "started",
            "message": ("Proposal accepted for '%s'. Approval gate -> Builder "
                        "spawn -> incorporation governance will run in the "
                        "background." % cap)}


__all__ = [
    "is_auto_mode_enabled",
    "get_self_evolution_prompt_block",
    "start_proposal",
]
