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
    RUN_DIR = Path(__file__).parent.parent.resolve()

_DYNAMIC_JOBS = {}
_DYNAMIC_JOBS_LOCK = threading.Lock()

_CANCELLED_JOBS = set()

def cancel_job(run_id: str) -> bool:
    """Cancel a running job."""
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS and _DYNAMIC_JOBS[run_id]['status'] == 'running':
            _CANCELLED_JOBS.add(run_id)
            return True
        return False

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

    init_job(run_id, session_id=session_id)

    # Check if clarification (interview) is enabled
    enable_clarification = body.get('clarification', True)

    def _run_in_background(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers, enable_clarification):
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
                    log_callback("System", f"⚠️ Command approval required ({desc}): {cmd}\nPlease review and approve in the chat interface.", "running")
                    
                    from api.config import STREAMS
                    q = STREAMS.get(session_id)
                    if q:
                        q.put(('approval', {
                            'type': 'command',
                            'command': cmd,
                            'description': desc,
                            'status': 'pending'
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
                            enriched = evaluation.get("enriched_task", "")
                            if not enriched:
                                enriched = build_enriched_task(task, state["qa_history"])
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

            # ── Main Harness Execution ──
            run_dir = Path(workspace)
            runner = HermesDynamicRunner()
            res = runner.run(enriched_task, preferred_model=preferred_model, log_callback=log_callback, run_dir=run_dir, planning_mode=planning_mode, session_id=session_id, run_id=run_id, allowed_providers=allowed_providers)
            set_job_done(run_id, res.get('final_output', '') if isinstance(res, dict) else str(res))
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
        args=(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers, enable_clarification),
        daemon=True
    ).start()

    return run_id
