"""
Dynamic Hermes job registry and background execution helpers.

Manages the in-memory _DYNAMIC_JOBS dictionary with thread-safe access
and provides background execution orchestration for Harness JIT runs.
"""

import json
import threading
import time
import traceback
import uuid
from pathlib import Path

import sys

# Resolve static resource paths for PyInstaller environment
if hasattr(sys, '_MEIPASS'):
    RUN_DIR = Path(sys.executable).parent.resolve()
else:
    RUN_DIR = Path(__file__).parent.parent.parent.resolve()

_DYNAMIC_JOBS = {}
_DYNAMIC_JOBS_LOCK = threading.Lock()

_CANCELLED_JOBS = set()

# 실행 중인 AIAgent 인스턴스 레지스트리 (run_id → set[AIAgent]).
# 취소 시 in-flight LLM HTTP 요청을 즉시 중단(interrupt)하기 위해 사용한다.
_RUNNING_AGENTS: dict = {}
_RUNNING_AGENTS_LOCK = threading.Lock()

# 취소 가능한(아직 종료되지 않은) 잡 상태. 'running'뿐 아니라 의도 확인 대기
# ('clarifying'), 승인 대기 ('awaiting_approval')에서도 취소가 먹혀야 한다.
_CANCELLABLE_STATUSES = ('running', 'clarifying', 'awaiting_approval')

# ── 갭 D: 위임 혈통(lineage) 레지스트리 ──
# run_id → {"parent_run_id", "root_run_id", "depth", "spawn_reason", "created_at"}
# delegate_team 도구가 자식 실행을 만들 때 등록한다.
# 부모 취소 시 get_descendants()로 서브트리를 찾아 연쇄 취소한다.
_LINEAGE: dict = {}
_LINEAGE_LOCK = threading.Lock()


def register_lineage(run_id: str, parent_run_id: str, root_run_id: str,
                     depth: int, spawn_reason: str = "") -> None:
    """위임으로 생성된 자식 실행의 혈통을 등록한다."""
    if not run_id:
        return
    with _LINEAGE_LOCK:
        _LINEAGE[run_id] = {
            "parent_run_id": parent_run_id,
            "root_run_id": root_run_id,
            "depth": int(depth),
            "spawn_reason": str(spawn_reason or ""),
            "created_at": time.time(),
        }


def get_lineage(run_id: str) -> dict | None:
    """run_id의 혈통 정보를 반환한다. 없으면 None."""
    with _LINEAGE_LOCK:
        entry = _LINEAGE.get(run_id)
        return dict(entry) if entry else None


def get_descendants(run_id: str) -> list:
    """run_id의 모든 후손 run_id를 BFS로 수집한다 (자기 자신 제외)."""
    with _LINEAGE_LOCK:
        children_map: dict = {}
        for rid, info in _LINEAGE.items():
            pid = info.get("parent_run_id")
            if pid:
                children_map.setdefault(pid, []).append(rid)
        result = []
        queue = [run_id]
        while queue:
            current = queue.pop(0)
            for child in children_map.get(current, []):
                if child not in result:
                    result.append(child)
                    queue.append(child)
        return result


def unregister_lineage_subtree(run_id: str) -> None:
    """run_id와 그 서브트리의 혈통 기록을 제거한다 (실행 종료 후 정리)."""
    with _LINEAGE_LOCK:
        to_remove = [run_id]
        children_map: dict = {}
        for rid, info in _LINEAGE.items():
            pid = info.get("parent_run_id")
            if pid:
                children_map.setdefault(pid, []).append(rid)
        queue = [run_id]
        while queue:
            current = queue.pop(0)
            for child in children_map.get(current, []):
                if child not in to_remove:
                    to_remove.append(child)
                    queue.append(child)
        for rid in to_remove:
            _LINEAGE.pop(rid, None)


def register_running_agent(run_id: str, agent) -> None:
    """실행 중인 노드의 AIAgent를 등록해 취소 시 interrupt()를 걸 수 있게 한다."""
    if not run_id or agent is None:
        return
    with _RUNNING_AGENTS_LOCK:
        _RUNNING_AGENTS.setdefault(run_id, set()).add(agent)


def unregister_running_agent(run_id: str, agent) -> None:
    """노드 종료 시 AIAgent 등록을 해제한다."""
    if not run_id or agent is None:
        return
    with _RUNNING_AGENTS_LOCK:
        agents = _RUNNING_AGENTS.get(run_id)
        if agents:
            agents.discard(agent)
            if not agents:
                _RUNNING_AGENTS.pop(run_id, None)


def _interrupt_running_agents(run_id: str) -> int:
    """등록된 모든 AIAgent에 interrupt()를 걸어 in-flight 요청을 즉시 중단한다."""
    with _RUNNING_AGENTS_LOCK:
        agents = list(_RUNNING_AGENTS.get(run_id, ()))
    count = 0
    for agent in agents:
        try:
            agent.interrupt("Cancelled by user via harness cancel")
            count += 1
        except Exception:
            pass
    return count


def cancel_job(run_id: str) -> bool:
    """Cancel a running job.

    'running'뿐 아니라 'clarifying'(의도 확인 대기), 'awaiting_approval'(승인 대기)
    상태에서도 취소를 허용한다. 취소 즉시 상태를 'cancelled'로 전환하고, 실행 중인
    AIAgent를 interrupt()하며, clarification 답변 대기를 해제한다.

    갭 D: 위임으로 생성된 후손 실행(서브트리)에도 취소를 연쇄 전파한다.
    """
    with _DYNAMIC_JOBS_LOCK:
        job = _DYNAMIC_JOBS.get(run_id)
        if job is None:
            return False
        if job['status'] not in _CANCELLABLE_STATUSES:
            return False
        _CANCELLED_JOBS.add(run_id)
        job['status'] = 'cancelled'

    # 록 밖에서 수행 — agent.interrupt / clarifier event set은 다른 록을 잡을 수 있다.
    # 1) 실행 중인 AIAgent 즉시 중단 (in-flight LLM HTTP 요청 정지)
    _interrupt_running_agents(run_id)

    # 2) clarification 답변 대기 중이면 즉시 해제
    try:
        from api.dynamic.clarifier import abort_clarification
        abort_clarification(run_id)
    except Exception:
        pass

    # 3) 갭 D: 위임 서브트리 연쇄 취소. 자식 실행은 delegate_team 도구 안에서
    #    동기 실행되므로 _DYNAMIC_JOBS에 없다 — _CANCELLED_JOBS에 직접 표시하고
    #    (러너의 is_job_cancelled 폴링이 감지) 해당 AIAgent를 interrupt한다.
    try:
        _descendants = get_descendants(run_id)
    except Exception:
        _descendants = []
    for _child_id in _descendants:
        try:
            with _DYNAMIC_JOBS_LOCK:
                _CANCELLED_JOBS.add(_child_id)
            _interrupt_running_agents(_child_id)
            try:
                from api.dynamic.clarifier import abort_clarification
                abort_clarification(_child_id)
            except Exception:
                pass
        except Exception:
            pass

    return True

def is_job_cancelled(run_id: str) -> bool:
    """Check if a job has been cancelled."""
    with _DYNAMIC_JOBS_LOCK:
        return run_id in _CANCELLED_JOBS



def get_job(run_id: str) -> dict | None:
    """Thread-safe read of a dynamic job by run_id."""
    with _DYNAMIC_JOBS_LOCK:
        return _DYNAMIC_JOBS.get(run_id)


def get_job_logs_since(run_id: str, cursor: int) -> tuple[list[dict], int]:
    """Return (new_logs, next_cursor) for incremental polling."""
    with _DYNAMIC_JOBS_LOCK:
        job = _DYNAMIC_JOBS.get(run_id)
        if job is None:
            return None, 0
        logs = list(job.get('logs', []))
        new_logs = logs[cursor:]
        return new_logs, cursor + len(new_logs)


def init_job(run_id: str, session_id: str = None) -> dict:
    """Create a new job entry and return it."""
    job = {
        'status': 'running',
        'result': None,
        'error': '',
        'started_at': time.time(),
        'logs': [],
        'clarification': None,  # clarification questions when status='clarifying'
        'session_id': session_id,  # session_id for approval resolution
    }
    with _DYNAMIC_JOBS_LOCK:
        _DYNAMIC_JOBS[run_id] = job
    return job


def set_job_clarifying(run_id: str, questions: list[str], turn: int):
    """Mark a job as waiting for user clarification."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['status'] = 'clarifying'
            _DYNAMIC_JOBS[run_id]['clarification'] = {
                'questions': questions,
                'turn': turn,
            }


def set_job_running(run_id: str):
    """Transition job back to running after clarification."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['status'] = 'running'
            _DYNAMIC_JOBS[run_id]['clarification'] = None


def set_job_awaiting_approval(run_id: str, message: str = ''):
    """Mark a job as waiting for user approval (e.g. plan.md review)."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['status'] = 'awaiting_approval'
            _DYNAMIC_JOBS[run_id]['approval_message'] = message or '작업 승인이 필요합니다.'
            _DYNAMIC_JOBS[run_id]['available_actions'] = ['approve', 'reject']


def append_job_log(run_id: str, agent_id: str, content: str, status: str = "running"):
    """Append a log entry to a running job."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['logs'].append({
                'agent_id': agent_id,
                'content': content,
                'status': status
            })


def set_job_done(run_id: str, result: str):
    """Mark a job as completed with a result."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['status'] = 'done'
            _DYNAMIC_JOBS[run_id]['result'] = result


def set_job_error(run_id: str, error: str):
    """Mark a job as failed with an error message."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]['status'] = 'error'
            _DYNAMIC_JOBS[run_id]['error'] = error


def get_job_status_response(run_id: str) -> dict | None:
    """Build the standard poll response for /api/dynamic/status."""
    with _DYNAMIC_JOBS_LOCK:
        job = _DYNAMIC_JOBS.get(run_id)
        if job is None:
            return None
    
    resp = {
        'run_id': run_id,
        'status': job['status'],
        'started_at': job['started_at'],
        'elapsed': round(time.time() - job['started_at'], 1),
    }
    if job['status'] == 'done':
        resp['result'] = job['result']
    elif job['status'] == 'error':
        resp['error'] = job['error']
    return resp


def start_harness_job(body: dict) -> str:
    """Parse request body, create a job, and spawn a background thread for HermesDynamicRunner.
    
    Returns the run_id.
    """
    task = body.get('task')
    preferred_model = body.get('model')
    workspace = body.get('workspace', '')

    if not task:
        raise ValueError("task is required")

    # Default workspace if none provided
    if not workspace:
        workspace = str(RUN_DIR).replace('\\', '/')

    run_id = uuid.uuid4().hex[:16]

    planning_mode = body.get('planning_mode', False)
    session_id = body.get('session_id')
    allowed_providers = body.get('allowedProviders')
    forced_skills = list(body.get('skills') or [])  # 사용자가 명시적으로 지정한 스킬 목록

    # ── 세션에서 ON 된 플러그인의 스킬을 forced_skills 에 자동 병합 ──
    # active_plugin_skills 가 반환하는 qualified 스킬 이름("plugin:skill")을
    # CEO planner 의 MANDATORY DIRECTIVE 로 주입한다. 실제 컨텐츠는
    # skill_registry 가 플러그인 스킬을 "plugin" 소스로 인덱싱하므로
    # compiler 의 load_skills() 경로를 통해 각 에이전트 시스템 프롬프트에
    # 자동 주입된다. (플러그인 OFF 시 세션 상태에서 빠지므로 즉시 제거.)
    if session_id:
        try:
            from api.plugin_gateway import active_plugin_skills
            _p_qualified, _p_blocks = active_plugin_skills(session_id)
            for _q in _p_qualified:
                if _q not in forced_skills:
                    forced_skills.append(_q)
            if _p_qualified:
                print(
                    f"[dynamic] Merged {len(_p_qualified)} plugin skill(s) "
                    f"into forced_skills for session {session_id}: {_p_qualified}",
                    flush=True,
                )
        except Exception as _p_err:
            print(f"[dynamic] WARNING: plugin skill merge failed: {_p_err}", flush=True)

    init_job(run_id, session_id=session_id)

    # Check if clarification (interview) is enabled
    enable_clarification = body.get('clarification', True)

    def _run_in_background(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers, enable_clarification, forced_skills):
        from api.dynamic_hermes import HermesDynamicRunner

        def log_callback(agent_name, content, status="running"):
            display_name = agent_name
            if preferred_model and f"({preferred_model})" not in str(agent_name):
                display_name = f"{agent_name} ({preferred_model})"
            append_job_log(run_id, display_name, content, status)

        # Register gateway notify for the session!
        if session_id:
            try:
                from tools.approval import register_gateway_notify
                def _approval_notify(approval_data):
                    cmd = approval_data.get("command", "")
                    desc = approval_data.get("description", "dangerous command")
                    status = approval_data.get("status", "pending")
                    if status == "auto_approved":
                        log_callback("System", f"⏱️ 응답 없음 — 파일 변경이 자동 승인되었습니다. ({cmd})", "running")
                    else:
                        log_callback("System", f"⚠️ Command approval required ({desc}): {cmd}\nPlease review and approve in the chat interface.", "running")
                    from api.config import STREAMS
                    q = STREAMS.get(session_id)
                    if q:
                        # type을 'dangerous_command'로 통일 — 프론트(approval.js)가
                        # 이 타입을 /api/approval/respond 로 라우팅한다. 'command' 타입은
                        # architect diff 경로로 잘못 응답되므로 사용하지 않는다.
                        q.put(('approval', {
                            'type': 'dangerous_command',
                            'command': cmd,
                            'description': desc,
                            'status': status,
                            'session_id': session_id,
                            'pattern_key': approval_data.get('pattern_key', ''),
                            'pattern_keys': approval_data.get('pattern_keys', []),
                            'message': approval_data.get('message', ''),
                        }))
                register_gateway_notify(session_id, _approval_notify)
            except ImportError:
                pass

        # ── Set TERMINAL_CWD so hermes tools (write_file/terminal/MCP) use workspace ──
        import os as _os
        _old_terminal_cwd = _os.environ.get('TERMINAL_CWD')
        _os.environ['TERMINAL_CWD'] = str(workspace)

        try:
            # ── CEO Clarification Phase (interview) ──
            enriched_task = task
            if enable_clarification:
                from api.dynamic.clarifier import (
                    init_clarification, wait_for_answers, evaluate_answers,
                    build_enriched_task, get_clarification_status,
                    ensure_acceptance_criteria,
                    MAX_CLARIFICATION_TURNS, _clear_state
                )
                state = init_clarification(run_id, task, preferred_model)

                if state["status"] == "waiting":
                    # Enter clarifying loop
                    while state["status"] == "waiting" and state["turn"] <= MAX_CLARIFICATION_TURNS:
                        # Update job status so frontend shows questions
                        set_job_clarifying(run_id, state["current_questions"], state["turn"])
                        log_callback("CEO", f"💬 의도 확인 (턴 {state['turn']})", "clarifying")

                        # Block until user answers (via POST /api/dynamic/answer)
                        answers = wait_for_answers(run_id, timeout=300.0)
                        if answers is None:
                            log_callback("CEO", "⏱️ 응답 대기 시간 초과 — 기존 정보로 진행합니다", "warning")
                            break

                        # Record answers
                        state["qa_history"][-1]["answers"] = answers

                        # Immediately clear 'clarifying' status so frontend doesn't
                        # re-render the same questions while LLM evaluation is running
                        set_job_running(run_id)
                        log_callback("CEO", "🤔 답변 평가 중...", "running")

                        # Evaluate sufficiency
                        evaluation = evaluate_answers(task, state["qa_history"], state["turn"], preferred_model)

                        if not evaluation.get("needs_clarification") or state["turn"] >= MAX_CLARIFICATION_TURNS:
                            # 갭 C: 인터뷰에서 추출한 수용 기준을 캡처한다
                            _criteria = [str(c).strip() for c in (evaluation.get("acceptance_criteria") or []) if str(c).strip()]
                            enriched = evaluation.get("enriched_task", "")
                            if not enriched:
                                enriched = build_enriched_task(task, state["qa_history"], acceptance_criteria=_criteria)
                            else:
                                # LLM이 만든 enriched에 수용 기준 섹션이 없으면 인터뷰 기준을 부착한다
                                enriched = ensure_acceptance_criteria(enriched, preferred_model, precomputed=_criteria)
                            enriched_task = enriched
                            state["status"] = "done"
                            log_callback("CEO", "✅ 의도 파악 완료 — 작업을 시작합니다", "success")
                        else:
                            state["turn"] += 1
                            state["current_questions"] = evaluation.get("questions", [])[:3]
                            state["qa_history"].append({"questions": state["current_questions"], "answers": []})
                            state["status"] = "waiting"

                    # Fallback if loop ended without 'done'
                    if state["status"] != "done":
                        enriched_task = build_enriched_task(task, state["qa_history"])

                    _clear_state(run_id)

                # Transition back to running
                set_job_running(run_id)

            # ── 갭 C: 수용 기준 섹션 보장 (검증 에이전트의 판정 근거) ──
            # 인터뷰 미사용/타임아웃/폴백 경로에서도 마커 섹션이 존재하도록 한다.
            try:
                from api.dynamic.clarifier import ensure_acceptance_criteria as _ensure_criteria
                enriched_task = _ensure_criteria(enriched_task, preferred_model)
            except Exception:
                traceback.print_exc()

            # ── Main Harness Execution ──
            run_dir = Path(workspace)
            runner = HermesDynamicRunner()
            # 갭 E-L2/E-L4 프로덕션 배선: 세션 기반 승인자 주입.
            # E-L2 스폰 게이트와 E-L4 편입 승인 모두 기존 승인 인프라(set_pending)를
            # 재사용하므로, 자율 실행 토글이 켜져 있으면 자동 승인된다.
            # 실패 시 안전 기본값(None 유지 = 게이트/편입 거부).
            try:
                from api.dynamic.builder_approval import (
                    make_session_approver,
                    APPROVAL_KIND_BUILDER_SPAWN, APPROVAL_KIND_INCORPORATION,
                )
                runner.builder_approver = make_session_approver(
                    session_id, kind=APPROVAL_KIND_BUILDER_SPAWN,
                    run_id=run_id, log_callback=log_callback)
                runner.builder_incorporation_approver = make_session_approver(
                    session_id, kind=APPROVAL_KIND_INCORPORATION,
                    run_id=run_id, log_callback=log_callback)
            except Exception:
                traceback.print_exc()
            res = runner.run(enriched_task, preferred_model=preferred_model, log_callback=log_callback, run_dir=run_dir, planning_mode=planning_mode, session_id=session_id, run_id=run_id, allowed_providers=allowed_providers, forced_skills=forced_skills)
            final_output = res.get('final_output', '') if isinstance(res, dict) else str(res)
            # ── 결과 보고 보장: final_output이 비어 있으면 디스크의 final_output.md로 폴백 ──
            if not final_output:
                try:
                    fallback_path = run_dir / "final_output.md"
                    if fallback_path.exists():
                        final_output = fallback_path.read_text(encoding="utf-8", errors="replace").strip()
                except Exception:
                    traceback.print_exc()
            set_job_done(run_id, final_output)
        except Exception as e:
            traceback.print_exc()
            set_job_error(run_id, str(e))
        finally:
            # Restore TERMINAL_CWD to its previous value
            if _old_terminal_cwd is None:
                _os.environ.pop('TERMINAL_CWD', None)
            else:
                _os.environ['TERMINAL_CWD'] = _old_terminal_cwd

            if session_id:
                try:
                    from tools.approval import unregister_gateway_notify
                    unregister_gateway_notify(session_id)
                except ImportError:
                    pass

    threading.Thread(
        target=_run_in_background,
        args=(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers, enable_clarification, forced_skills),
        daemon=True
    ).start()

    return run_id
