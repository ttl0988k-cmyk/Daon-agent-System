"""
DAON Multi-Agent Collaborator Engine (collaborator.py)
Orchestrates inter-agent delegation and autonomous completion relay between Raon and colleague agents.
"""
import json
import logging
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)

# Profile alias mappings (supporting English and Korean names)
AGENT_PROFILE_MAP = {
    "bill": "빌(개발)",
    "빌": "빌(개발)",
    "dev": "빌(개발)",
    "개발": "빌(개발)",
    "빌(개발)": "빌(개발)",
    "sherlock": "셜록(검수)",
    "셜록": "셜록(검수)",
    "qa": "셜록(검수)",
    "검수": "셜록(검수)",
    "셜록(검수)": "셜록(검수)",
    "tony": "토니(기획)",
    "토니": "토니(기획)",
    "planner": "토니(기획)",
    "기획": "토니(기획)",
    "토니(기획)": "토니(기획)",
    "prada": "프라다(디자인)",
    "프라다": "프라다(디자인)",
    "designer": "프라다(디자인)",
    "디자인": "프라다(디자인)",
    "프라다(디자인)": "프라다(디자인)",
    "daon": "다온(응대)",
    "다온": "다온(응대)",
    "응대": "다온(응대)",
    "다온(응대)": "다온(응대)",
    "raon": "raon",
    "라온": "raon",
}

# Maximum autonomous report relay depth to prevent infinite loops
MAX_RELAY_DEPTH = 8


def normalize_agent_profile(name: str) -> str:
    """Normalize agent name or alias to canonical profile name."""
    if not name:
        return "raon"
    key = str(name).strip().lower()
    return AGENT_PROFILE_MAP.get(key, name)


def find_or_create_agent_session(profile_name: str, workspace: Optional[str] = None, parent_session_id: Optional[str] = None) -> Any:
    """Find the most recent active session for the given profile or create a fresh one."""
    from api.models import all_sessions, get_session, new_session
    from api.config import get_last_workspace

    canonical = normalize_agent_profile(profile_name)
    eff_workspace = workspace or get_last_workspace()

    # 1. Search existing non-archived sessions for matching profile
    try:
        for s_info in all_sessions():
            p = (s_info.get("profile") or "").lower()
            if p == canonical.lower() and not s_info.get("archived"):
                try:
                    s = get_session(s_info["session_id"])
                    if eff_workspace:
                        s.workspace = str(eff_workspace)
                    if parent_session_id and not getattr(s, "parent_session_id", None):
                        s.parent_session_id = parent_session_id
                    return s
                except Exception:
                    pass
    except Exception as e:
        _logger.warning("Error searching existing sessions: %s", e)

    # 2. Create new session for this agent profile
    s = new_session(workspace=eff_workspace, profile=canonical)
    if parent_session_id:
        s.parent_session_id = parent_session_id
        try:
            s.save()
        except Exception:
            pass
    return s


def execute_agent_task(
    agent_name: str,
    task: str,
    timeout: int = 180,
    parent_session_id: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a task synchronously on a colleague agent and return final output.

    Used by Raon's `delegate_to_agent` tool to wait for colleague results.
    """
    from api.config import STREAMS, STREAMS_LOCK, ACTIVE_SESSION_STREAMS, ACTIVE_SESSION_STREAMS_LOCK
    from api.streaming import BroadcastQueue, _run_agent_streaming, cancel_stream

    canonical_profile = normalize_agent_profile(agent_name)
    target_session = find_or_create_agent_session(
        profile_name=canonical_profile,
        workspace=workspace,
        parent_session_id=parent_session_id,
    )

    sid = target_session.session_id
    stream_id = uuid.uuid4().hex

    # Instruction wrapper providing clear delegator attribution and reporting guidance
    instruction = (
        f"[라온 총괄기획자의 작업 지시]\n"
        f"{task.strip()}\n\n"
        f"[협업 및 보고 규칙]:\n"
        f"- 맡은 역할을 최선을 다해 자율 완주하세요.\n"
        f"- 작업 완료 시 답변 맨 끝에 반드시 [MSG to=raon task=작업명 priority=normal]결과 요약 및 산출물[/MSG] 블록을 포함하여 라온에게 완료 보고하세요."
    )

    q = BroadcastQueue()
    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    # Register in active session streams map so UI multi-pane can attach live
    with ACTIVE_SESSION_STREAMS_LOCK:
        ACTIVE_SESSION_STREAMS[sid] = stream_id

    # Subscribe to queue to collect output
    sub = q.subscribe()

    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(sid, instruction, target_session.model, target_session.workspace, stream_id),
        daemon=True,
        name=f"ColleagueRun-{canonical_profile}",
    )
    thr.start()

    start_time = time.time()
    accumulated_tokens: List[str] = []
    final_output = ""
    success = False
    error_msg = ""

    try:
        while True:
            remaining = max(1.0, timeout - (time.time() - start_time))
            if time.time() - start_time > timeout:
                error_msg = f"에이전트 '{canonical_profile}' 작업이 제한시간({timeout}초)을 초과했습니다."
                cancel_stream(stream_id, session_id=sid)
                break

            try:
                event, data = sub.get(timeout=min(2.0, remaining))
            except queue.Empty:
                continue

            if event == "token":
                tok = str(data or "")
                accumulated_tokens.append(tok)
            elif event == "done":
                success = True
                # Extract clean assistant response from updated session
                try:
                    from api.models import get_session
                    updated_s = get_session(sid)
                    for m in reversed(updated_s.messages):
                        if m.get("role") == "assistant":
                            final_output = str(m.get("content") or "").strip()
                            break
                except Exception:
                    pass
                if not final_output:
                    final_output = "".join(accumulated_tokens).strip()
                break
            elif event == "error":
                error_msg = str((data or {}).get("message") or "실행 오류 발생")
                break
            elif event == "cancel":
                error_msg = "작업이 취소되었습니다."
                break
    finally:
        q.unsubscribe(sub)

    if success:
        return {
            "ok": True,
            "agent": canonical_profile,
            "session_id": sid,
            "output": final_output or "".join(accumulated_tokens).strip(),
        }
    else:
        return {
            "ok": False,
            "agent": canonical_profile,
            "session_id": sid,
            "error": error_msg or "작업 미완료",
            "partial_output": "".join(accumulated_tokens).strip(),
        }


def relay_report_to_raon(sender: str, dispatched_reports: List[Dict[str, Any]], workspace: Optional[str] = None) -> bool:
    """Relay an asynchronous completion report to Raon, triggering an autonomous review turn."""
    from api.models import all_sessions, get_session
    from api.config import ACTIVE_SESSION_STREAMS, STREAMS, STREAMS_LOCK
    from api.streaming import BroadcastQueue, _run_agent_streaming

    try:
        # 1. Locate Raon's session
        raon_sid = None
        for s_info in all_sessions():
            p = (s_info.get("profile") or "").lower()
            if p in ("raon", "라온") and not s_info.get("archived"):
                raon_sid = s_info["session_id"]
                break

        if not raon_sid:
            _logger.info("[AutoReportRelay] No active Raon session found.")
            return False

        # 2. Check if Raon is already busy/running
        if raon_sid in ACTIVE_SESSION_STREAMS:
            _logger.info("[AutoReportRelay] Raon (session: %s) is currently active, skipping duplicate relay.", raon_sid)
            return False

        s_raon = get_session(raon_sid)

        # 3. Guard against infinite ping-pong relay loops
        relay_count = getattr(s_raon, "_relay_count", 0)
        if relay_count >= MAX_RELAY_DEPTH:
            _logger.warning("[AutoReportRelay] Max relay depth (%d) reached for session %s, terminating relay loop.", MAX_RELAY_DEPTH, raon_sid)
            return False
        setattr(s_raon, "_relay_count", relay_count + 1)

        # 4. Construct wake-up prompt with full reports
        report_blocks = []
        for r in dispatched_reports:
            t = r.get("task") or "작업 보고"
            b = r.get("body") or ""
            report_blocks.append(f"• [{sender} 완료 보고 - 작업: {t}]\n{b}")

        all_reports_text = "\n\n".join(report_blocks)

        wake_prompt = (
            f"[동료 에이전트 작업 완료 보고 도착]\n\n"
            f"{all_reports_text}\n\n"
            f"[오케스트레이터 라온 지침]:\n"
            f"1. 위 동료의 작업 완료 보고를 면밀히 검토하세요.\n"
            f"2. 사용자가 요청한 전체 목표를 완주하기 위해 추가 작업이 필요하다면:\n"
            f"   - 전문 동료 에이전트에게 `delegate_to_agent(agent_name='...', task='...')` 호출\n"
            f"   - 코딩 워커에게 `delegate_to_worker(worker_name='...', prompt='...')` 호출\n"
            f"3. 모든 작업이 완전히 완주되었다면, 최종 성과와 산출물을 종합하여 대표님(사용자)께 최종 보고를 전달하세요."
        )

        # 5. Launch streaming execution for Raon
        stream_id = uuid.uuid4().hex
        q = BroadcastQueue()
        with STREAMS_LOCK:
            STREAMS[stream_id] = q

        thr = threading.Thread(
            target=_run_agent_streaming,
            args=(s_raon.session_id, wake_prompt, s_raon.model, workspace or s_raon.workspace, stream_id),
            daemon=True,
            name="RaonRelayTurn",
        )
        thr.start()
        _logger.info("[AutoReportRelay] 🚀 Successfully woke up Raon (session: %s) with report from %s", raon_sid, sender)
        return True
    except Exception as e:
        _logger.warning("[AutoReportRelay] Failed to relay report to Raon: %s", e)
        return False
