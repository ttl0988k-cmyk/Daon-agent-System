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
    """POST /api/dynamic/run — Start a dynamic harness run.

    Body: {
        "task": "카페 랜딩페이지를 만들어줘",
        "session_id": "abc123",
        "workspace": "/path/to/workspace",
        "model": "gpt-4o",
        "planning_mode": false,
        "allowedProviders": [...]
    }

    Response: { "run_id": "a1b2c3d4e5f6..." }
    """
    from api.dynamic_jobs import start_harness_job

    task = body.get("task", "").strip()
    if not task:
        handler.send_json({"ok": False, "error": "task is required"}, 400)
        return True

    try:
        run_id = start_harness_job(body)
        handler.send_json({"ok": True, "run_id": run_id})
    except ValueError as e:
        handler.send_json({"ok": False, "error": str(e)}, 400)
    except Exception as e:
        logger.exception("Dynamic harness start failed")
        handler.send_json({"ok": False, "error": str(e)}, 500)

    return True


def handle_get_dynamic_status(handler, parsed) -> bool:
    """GET /api/dynamic/status/{run_id} — Poll run status.

    Response: {
        "run_id": "...",
        "status": "running" | "completed" | "failed",
        "elapsed": 12.3,
        "logs": [ {"message": "[CEO] 작업 시작", "type": "info"}, ... ],
        "agent_cards": { "ceo": {"status": "..."}, ... },
        "result": "...",       // when completed
        "error": "..."         // when failed
    }
    """
    from api.dynamic_jobs import get_job

    # Extract run_id from path: /api/dynamic/status/{run_id}
    path = parsed.path
    prefix = "/api/dynamic/status/"
    if not path.startswith(prefix):
        handler.send_json({"error": "invalid path"}, 400)
        return True

    run_id = path[len(prefix):].strip().rstrip("/")
    if not run_id:
        handler.send_json({"error": "run_id is required"}, 400)
        return True

    job = get_job(run_id)
    if job is None:
        handler.send_json({"error": "Not found"}, 404)
        return True

    # Map internal status → frontend status
    internal_status = job.get("status", "running")
    status_map = {
        "running": "running",
        "done": "completed",
        "error": "failed",
        "awaiting_approval": "awaiting_approval",
        "clarifying": "clarifying",
    }
    frontend_status = status_map.get(internal_status, internal_status)

    # Build logs array: transform {agent_id, content, status} → {message, type}
    raw_logs = job.get("logs", [])
    logs = []
    for entry in raw_logs:
        agent_id = entry.get("agent_id", "")
        content = entry.get("content", "")
        entry_status = entry.get("status", "running")
        # Map entry status to log type
        log_type = "info"
        if entry_status == "error":
            log_type = "error"
        elif entry_status == "done" or entry_status == "completed":
            log_type = "success"
        elif entry_status == "warning":
            log_type = "warning"

        message = f"[{agent_id}] {content}" if agent_id else content
        logs.append({"message": message, "type": log_type})

    resp = {
        "run_id": run_id,
        "status": frontend_status,
        "elapsed": round(__import__("time").time() - job.get("started_at", 0), 1),
        "logs": logs,
    }

    if frontend_status == "completed":
        resp["result"] = job.get("result", "")
    elif frontend_status == "failed":
        resp["error"] = job.get("error", "알 수 없는 오류")
    elif frontend_status == "awaiting_approval":
        resp["approval_message"] = job.get("approval_message", "작업 승인이 필요합니다.")
        resp["available_actions"] = job.get("available_actions", ["approve", "reject"])
    elif frontend_status == "clarifying":
        resp["clarification"] = job.get("clarification", {})

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

    # Resolve the approval by updating job status back to running
    from api.dynamic_jobs import _DYNAMIC_JOBS, _DYNAMIC_JOBS_LOCK
    with _DYNAMIC_JOBS_LOCK:
        if run_id in _DYNAMIC_JOBS:
            _DYNAMIC_JOBS[run_id]["status"] = "running"
            _DYNAMIC_JOBS[run_id]["approval_action"] = action
            _DYNAMIC_JOBS[run_id].pop("approval_message", None)
            _DYNAMIC_JOBS[run_id].pop("available_actions", None)

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
    handler.send_json({"ok": cancelled})
    return True
