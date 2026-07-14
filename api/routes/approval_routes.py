"""
Approval API Routes for Daon Agent System.
Handles Architect mode approval flow: pending, approve, reject, history,
and Harness skill-save confirmation.
"""
import threading
from api.helpers import j, j_ok, j_err, require
from api.approval import (
    has_pending,
    get_pending,
    clear_pending,
    approve,
    reject,
    get_history,
    get_skill_save_data,
)


def handle_get_approval_pending(handler, parsed) -> bool:
    """GET /api/approval/pending?session_id=... — check for pending approval.
    Combines both Architect diff/plan approvals and CLI dangerous command approvals.
    """
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]

    if not sid:
        return j_err(handler, 'session_id is required')

    # 1. Check Architect diff/plan approval first (from api.approval)
    from api.approval import has_pending as has_arch_pending, get_pending as get_arch_pending
    if has_arch_pending(sid):
        pending = get_arch_pending(sid)
        return j_ok(handler, {'session_id': sid, 'has_pending': True, 'pending': pending})

    # 2. Check CLI command approval (from tools.approval)
    try:
        from tools.approval import has_pending as has_cmd_pending, _pending as cmd_pending_dict, _lock as cmd_lock
        if has_cmd_pending(sid):
            with cmd_lock:
                pending = dict(cmd_pending_dict.get(sid, {}))
            return j_ok(handler, {'session_id': sid, 'has_pending': True, 'pending': pending})
    except ImportError:
        pass

    return j_ok(handler, {'session_id': sid, 'has_pending': False, 'pending': {}})



def handle_get_approval_history(handler, parsed) -> bool:
    """GET /api/approval/history?session_id=... — get approval history."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query)
    sid = qs.get('session_id', [''])[0]

    if not sid:
        return j_err(handler, 'session_id is required')

    limit = int(qs.get('limit', [30])[0])
    hist = get_history(sid, limit)
    return j_ok(handler, {'session_id': sid, 'history': hist})


def handle_post_approval_approve(handler, body: dict) -> bool:
    """POST /api/approval/approve — approve pending Architect changes.

    Body: { session_id, preview_id? }
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    sid = body['session_id']
    if not has_pending(sid):
        return j_err(handler, 'No pending approval for this session')

    result = approve(sid, reviewer=body.get('reviewer', 'user'))
    if result.get('ok'):
        preview_id = body.get('preview_id', '') or result['data'].get('preview_id', '')
        return j_ok(handler, {
            'ok': True,
            'approved': result['data'],
            'preview_id': preview_id,
            'message': 'Changes approved. You can now apply the diff preview.',
        })
    else:
        return j_err(handler, result.get('error', 'Approval failed'))


def handle_post_approval_reject(handler, body: dict) -> bool:
    """POST /api/approval/reject — reject pending Architect changes.

    Body: { session_id, reason? }
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    sid = body['session_id']
    if not has_pending(sid):
        return j_err(handler, 'No pending approval for this session')

    result = reject(sid, reason=body.get('reason', ''), reviewer=body.get('reviewer', 'user'))
    if result.get('ok'):
        return j_ok(handler, {
            'ok': True,
            'rejected': result['data'],
            'message': 'Changes rejected. The diff preview has been discarded.',
        })
    else:
        return j_err(handler, result.get('error', 'Rejection failed'))


def handle_post_skill_save_approve(handler, body: dict) -> bool:
    """POST /api/approval/skill-save/approve — user clicked 'save as skill'.

    Body: { session_id }
    Triggers background skill extraction from the pending approval data.
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    sid = body['session_id']
    data = get_skill_save_data(sid)
    if not data:
        return j_err(handler, 'No pending skill-save approval for this session')

    # Extract skill in background thread
    task = data.get('task', '')
    plan = data.get('plan', {})
    final_output = data.get('final_output', '')
    run_id = data.get('run_id', '')

    clear_pending(sid)

    def _extract_bg():
        try:
            from api.dynamic.skill_extractor import _extract_and_save_skill
            _extract_and_save_skill(task, plan, final_output, run_id)
        except Exception as e:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning("Background skill extraction failed: %s", e)

    threading.Thread(target=_extract_bg, daemon=True,
                     name=f"SkillSaveApprove_{run_id}").start()

    return j_ok(handler, {
        'ok': True,
        'message': '스킬 저장이 시작되었습니다. ~/.hermes/skills/ 에서 확인하세요.',
    })


def handle_post_skill_save_reject(handler, body: dict) -> bool:
    """POST /api/approval/skill-save/reject — user clicked 'no thanks'.

    Body: { session_id }
    Simply clears the pending approval without saving.
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    sid = body['session_id']
    data = get_skill_save_data(sid)
    if not data:
        return j_err(handler, 'No pending skill-save approval for this session')

    clear_pending(sid)
    return j_ok(handler, {
        'ok': True,
        'message': '스킬 저장이 취소되었습니다.',
    })
