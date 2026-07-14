"""
Approval module for Daon Agent System.
Provides session-based approval state management.
Used by Architect mode to require user approval before applying code changes,
and by Harness mode for skill-save confirmation.
"""
import threading
import time
import logging

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_pending = {}  # { session_id: dict }
_history = {}  # { session_id: list[dict] }  — approved/rejected history


def has_pending(session_id: str) -> bool:
    """Check if a session has pending approval requests."""
    with _lock:
        return session_id in _pending and bool(_pending[session_id])


def get_pending(session_id: str) -> dict:
    """Get pending approval data for a session."""
    with _lock:
        return dict(_pending.get(session_id, {}))


def set_pending(session_id: str, approval_data: dict) -> None:
    """Set pending approval data for a session."""
    with _lock:
        _pending[session_id] = dict(approval_data)


def clear_pending(session_id: str) -> None:
    """Clear pending approval for a session."""
    with _lock:
        _pending.pop(session_id, None)


def set_skill_save_pending(session_id: str, task: str, plan: dict,
                           final_output: str, run_id: str) -> None:
    """Set a pending skill-save approval for the given session.

    The frontend polls /api/approval/pending and renders a confirmation banner
    asking the user whether to save the completed harness run as a Skill.
    """
    if not session_id:
        _logger.warning("set_skill_save_pending: no session_id provided, skipping")
        return
    approval_data = {
        "type": "skill_save",
        "status": "pending",
        "task": task,
        "run_id": run_id,
        "title": "작업을 스킬로 저장할까요?",
        "message": f"'{task[:60]}' 실행 결과를 재사용 가능한 스킬로 저장합니다.",
        "plan": plan,
        "final_output": final_output,
    }
    set_pending(session_id, approval_data)
    _logger.info("Skill-save approval set for session=%s run=%s", session_id, run_id)


def get_skill_save_data(session_id: str) -> dict | None:
    """Get skill-save approval data if pending and of type 'skill_save'."""
    data = get_pending(session_id)
    if data and data.get("type") == "skill_save":
        return data
    return None


def approve(session_id: str, reviewer: str = 'user') -> dict:
    """Approve pending changes. Returns the approved data and records in history."""
    with _lock:
        data = _pending.pop(session_id, {})
        if not data:
            return {'ok': False, 'error': 'No pending approval'}
        entry = dict(data)
        entry['approved_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        entry['reviewer'] = reviewer
        entry['status'] = 'approved'
        if session_id not in _history:
            _history[session_id] = []
        _history[session_id].append(entry)
        # Keep last 50 entries
        if len(_history[session_id]) > 50:
            _history[session_id] = _history[session_id][-50:]
        return {'ok': True, 'data': entry}


def reject(session_id: str, reason: str = '', reviewer: str = 'user') -> dict:
    """Reject pending changes. Returns the rejected data and records in history."""
    with _lock:
        data = _pending.pop(session_id, {})
        if not data:
            return {'ok': False, 'error': 'No pending approval'}
        entry = dict(data)
        entry['rejected_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        entry['reviewer'] = reviewer
        entry['reason'] = reason
        entry['status'] = 'rejected'
        if session_id not in _history:
            _history[session_id] = []
        _history[session_id].append(entry)
        if len(_history[session_id]) > 50:
            _history[session_id] = _history[session_id][-50:]
        return {'ok': True, 'data': entry}


def get_history(session_id: str, limit: int = 30) -> list[dict]:
    """Get approval/rejection history for a session."""
    with _lock:
        hist = _history.get(session_id, [])
        return list(hist[-limit:])
