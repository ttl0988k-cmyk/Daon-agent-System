"""
Dynamic Harness Routes — JIT agent orchestration API.

POST /api/dynamic/run              — Start a dynamic harness run
GET  /api/dynamic/status/{run_id}  — Poll run status + logs
POST /api/dynamic/approve/{run_id} — Approve a pending action
POST /api/dynamic/cancel/{run_id}  — Cancel a running job
"""

import logging

logger = logging.getLogger(__name__)


def handle_post_dynamic_run(handler, body: dict) -> bool:
    """POST /api/dynamic/run — Start a dynamic harness run."""
    from api.dynamic_jobs import start_harness_job

    task = body.get("task", "").strip()
    if not task:
        handler.send_json({"ok": False, "error": "task is required"}, 400)
        return True

    try:
        run_id = start_harness_job(body)
        handler.send_json({"ok": True, "run_id": run_id, "status": "running"})
    except ValueError as e:
        handler.send_json({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        logger.exception("Dynamic harness start failed")
        handler.send_json({"ok": False, "error": str(e)}, 500)

    return True


def handle_get_dynamic_status(handler, parsed) -> bool:
    """GET /api/dynamic/status?run_id=...&log_cursor=... OR GET /api/dynamic/status/{run_id} — Poll run status."""
    from urllib.parse import parse_qs
    import time
    import api.dynamic_jobs as dj

    qs = parse_qs(parsed.query) if parsed.query else {}
    run_id = qs.get('run_id', [''])[0].strip()
    try:
        log_cursor = int(qs.get('log_cursor', ['0'])[0])
    except (ValueError, TypeError):
        log_cursor = 0

    path = parsed.path
    if not run_id:
        prefix = "/api/dynamic/status/"
        if path.startswith(prefix):
            run_id = path[len(prefix):].strip().rstrip("/")

    if not run_id:
        handler.send_json({"ok": False, "error": "run_id is required"}, 400)
        return True

    job = dj.get_job(run_id)
    if job is None:
        handler.send_json({"ok": False, "error": f"run_id not found: {run_id}"}, 404)
        return True

    resp = dj.get_job_status_response(run_id)
    if resp is None:
        handler.send_json({"ok": False, "error": f"run_id not found: {run_id}"}, 404)
        return True

    new_logs, next_cursor = dj.get_job_logs_since(run_id, log_cursor)
    resp['logs'] = new_logs
    resp['next_cursor'] = next_cursor

    # Build agent_cards from all logs
    raw_logs = job.get("logs", [])
    agent_cards = {}
    for entry in raw_logs:
        aid = entry.get("agent_id", "")
        if aid:
            agent_cards[aid] = {"status": entry.get("content", ""), "log_status": entry.get("status", "running")}
    if agent_cards:
        resp['agent_cards'] = agent_cards

    # Build delegation_tree if any
    try:
        delegation_tree = []
        for desc_id in dj.get_descendants(run_id):
            lin = dj.get_lineage(desc_id) or {}
            delegation_tree.append({
                "run_id": desc_id,
                "parent_run_id": lin.get("parent_run_id", ""),
                "depth": lin.get("depth", 0),
                "spawn_reason": lin.get("spawn_reason", ""),
                "status": "running",
            })
        if delegation_tree:
            resp["delegation_tree"] = delegation_tree
    except Exception:
        pass

    handler.send_json(resp)
    return True



def handle_post_dynamic_approve(handler, body: dict, parsed=None) -> bool:
    """POST /api/dynamic/approve/{run_id} — Approve a pending action.

    Body: { "action": "approve" | "reject" | ... }
    run_id is extracted from the URL path (e.g. /api/dynamic/approve/abc123).
    """
    from api.dynamic_jobs import get_job

    # Extract run_id from URL path: /api/dynamic/approve/{run_id}
    run_id = ""
    if parsed is not None:
        prefix = "/api/dynamic/approve/"
        if parsed.path.startswith(prefix):
            run_id = parsed.path[len(prefix):].strip().rstrip("/")
    if not run_id:
        run_id = body.get("run_id", "")
    action = body.get("action", "approve")

    if not run_id:
        handler.send_json({"ok": False, "error": "run_id is required"}, 400)
        return True

    job = get_job(run_id)
    if job is None:
        handler.send_json({"ok": False, "error": "Not found"}, 404)
        return True

    # Resolve the approval by updating job status back to running (memory + SQLite)
    from api.dynamic_jobs import set_job_approval_response
    session_id = job.get("session_id")
    set_job_approval_response(run_id, action)

    # CRITICAL: resolve the api.approval pending entry so the orchestrator's
    # `while has_pending(session_id)` loop unblocks. Without this the harness
    # stays frozen after plan.md even when the user clicks approve.
    if session_id:
        try:
            from api.approval import approve as _apr_approve, reject as _apr_reject, has_pending as _apr_has
            if _apr_has(session_id):
                if action == "reject":
                    _apr_reject(session_id, reason="User rejected via harness approve endpoint")
                else:
                    _apr_approve(session_id, reviewer="user")
        except Exception as _apr_err:
            handler.send_json({"ok": False, "error": f"approval resolve failed: {_apr_err}"}, 500)
            return True

    handler.send_json({"ok": True, "action": action})
    return True


def handle_post_dynamic_answer(handler, body: dict, parsed=None) -> bool:
    """POST /api/dynamic/answer/{run_id} — Submit user answers to clarification questions.

    Body: { "answers": ["답변1", "답변2", ...] }
    run_id is extracted from the URL path (e.g. /api/dynamic/answer/abc123).
    """
    from api.dynamic.clarifier import submit_answers

    # Extract run_id from URL path: /api/dynamic/answer/{run_id}
    run_id = ""
    if parsed is not None:
        prefix = "/api/dynamic/answer/"
        if parsed.path.startswith(prefix):
            run_id = parsed.path[len(prefix):].strip().rstrip("/")
    if not run_id:
        run_id = body.get("run_id", "")
    if not run_id:
        handler.send_json({"ok": False, "error": "run_id is required"}, 400)
        return True

    answers = body.get("answers", [])
    if not answers:
        handler.send_json({"ok": False, "error": "answers array is required"}, 400)
        return True

    result = submit_answers(run_id, answers)
    handler.send_json(result)
    return True


def handle_post_dynamic_cancel(handler, body: dict, parsed=None) -> bool:
    """POST /api/dynamic/cancel/{run_id} — Cancel a running job.

    run_id is extracted from the URL path (e.g. /api/dynamic/cancel/abc123).
    """
    from api.dynamic_jobs import cancel_job

    # Extract run_id from URL path: /api/dynamic/cancel/{run_id}
    run_id = ""
    if parsed is not None:
        prefix = "/api/dynamic/cancel/"
        if parsed.path.startswith(prefix):
            run_id = parsed.path[len(prefix):].strip().rstrip("/")
    if not run_id:
        run_id = body.get("run_id", "")
    if not run_id:
        handler.send_json({"ok": False, "error": "run_id is required"}, 400)
        return True

    cancelled = cancel_job(run_id)
    if cancelled:
        handler.send_json({"ok": True, "message": "Job cancelled"})
    else:
        handler.send_json({"ok": False, "error": "Job not found or already completed"}, 404)
    return True

