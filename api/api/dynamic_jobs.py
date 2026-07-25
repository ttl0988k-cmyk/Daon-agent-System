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


def init_job(run_id: str) -> dict:
    """Create a new job entry and return it."""
    job = {
        'status': 'running',
        'result': None,
        'error': '',
        'started_at': time.time(),
        'logs': []
    }
    with _DYNAMIC_JOBS_LOCK:
        _DYNAMIC_JOBS[run_id] = job
    return job


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
    init_job(run_id)

    planning_mode = body.get('planning_mode', False)
    session_id = body.get('session_id')
    allowed_providers = body.get('allowedProviders')

    def _run_in_background(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers):
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

        try:
            run_dir = Path(workspace)
            runner = HermesDynamicRunner()
            res = runner.run(task, preferred_model=preferred_model, log_callback=log_callback, run_dir=run_dir, planning_mode=planning_mode, session_id=session_id, run_id=run_id, allowed_providers=allowed_providers)
            set_job_done(run_id, res.get('final_output', '') if isinstance(res, dict) else str(res))
        except Exception as e:
            traceback.print_exc()
            set_job_error(run_id, str(e))
        finally:
            if session_id:
                try:
                    from tools.approval import unregister_gateway_notify
                    unregister_gateway_notify(session_id)
                except ImportError:
                    pass

    threading.Thread(
        target=_run_in_background,
        args=(run_id, task, preferred_model, workspace, planning_mode, session_id, allowed_providers),
        daemon=True
    ).start()

    return run_id
