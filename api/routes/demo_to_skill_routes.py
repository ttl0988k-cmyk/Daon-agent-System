"""
Demo-to-Skill API Routes for Daon Agent System.

REST endpoints for:
  - Start/stop recording sessions
  - Query session status and events
  - Direct text-to-skill conversion
  - List and manage generated skills
"""
import logging
import threading
from urllib.parse import parse_qs
from api.helpers import j, j_ok, j_err, require

_logger = logging.getLogger(__name__)


def _get_manager():
    """Lazy-import the RecordingManager."""
    from api.demo_to_skill import get_recording_manager
    return get_recording_manager()


# ── Session Management ──────────────────────────────────────────────

def handle_post_demo_start(handler, body: dict) -> bool:
    """POST /api/demo/start — start a new recording session.

    Body:
        name: (optional) session name
        source: "cdp" (default) or "text"
        cdp_port: Chrome DevTools Protocol port (default 9222)
        capture_screenshots: (optional) capture screenshot per event (default false)
        capture_dom: (optional) capture DOM snapshot per event (default false)
    """
    try:
        require(body, 'source')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = _get_manager()
    session_id = mgr.start_session(
        name=body.get('name', ''),
        source=body.get('source', 'cdp'),
        cdp_port=int(body.get('cdp_port', 9222)),
        capture_screenshots=body.get('capture_screenshots', False),
        capture_dom=body.get('capture_dom', False),
    )

    session = mgr.get_session(session_id)
    return j(handler, {
        'ok': True,
        'session_id': session_id,
        'session': session,
    })


def handle_post_demo_stop(handler, body: dict) -> bool:
    """POST /api/demo/stop — stop recording and trigger Skill generation.

    Body:
        session_id: (required) session to stop
        preferred_model: (optional) model override for LLM analysis

    This endpoint returns immediately, and Skill generation runs in background.
    Use GET /api/demo/status?session_id=... to poll for results.
    """
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    session_id = body['session_id']
    preferred_model = body.get('preferred_model', None)

    mgr = _get_manager()
    session = mgr.get_session(session_id)
    if not session:
        return j_err(handler, f'Session not found: {session_id}')

    if session['status'] not in ('recording', 'idle'):
        return j_err(handler, f'Session is not recording (status: {session["status"]})')

    # Run analysis in background thread
    result = {"skill_path": None, "error": None}

    def _analyze():
        try:
            path = mgr.stop_session(
                session_id=session_id,
                preferred_model=preferred_model,
            )
            result["skill_path"] = path
        except Exception as e:
            _logger.exception("[DemoToSkill] Analysis failed")
            result["error"] = str(e)
            s = mgr._sessions.get(session_id)
            if s:
                s.status = "error"
                s.error = str(e)

    threading.Thread(target=_analyze, daemon=True).start()

    return j(handler, {
        'ok': True,
        'status': 'analyzing',
        'message': 'Recording stopped. Skill analysis started in background.',
        'session_id': session_id,
    })


def handle_get_demo_status(handler, parsed) -> bool:
    """GET /api/demo/status?session_id=... — get session status.

    Also supports ?session_id=all to list all sessions.
    """
    mgr = _get_manager()
    query_params = parse_qs(parsed.query)
    session_id = query_params.get('session_id', [''])[0]

    if session_id == 'all' or not session_id:
        sessions = mgr.list_sessions()
        return j_ok(handler, {'sessions': sessions})

    session = mgr.get_session(session_id)
    if not session:
        return j_err(handler, f'Session not found: {session_id}')

    return j_ok(handler, {'session': session})


def handle_get_demo_events(handler, parsed) -> bool:
    """GET /api/demo/events?session_id=... — get captured events (live preview).

    Returns events captured so far, including in-progress CDP events.
    """
    mgr = _get_manager()
    query_params = parse_qs(parsed.query)
    session_id = query_params.get('session_id', [''])[0]

    if not session_id:
        return j_err(handler, 'Missing session_id parameter')

    events = mgr.get_session_events(session_id)
    return j_ok(handler, {
        'session_id': session_id,
        'event_count': len(events),
        'events': events[-50:],  # Return last 50 for UI display
    })


def handle_post_demo_cancel(handler, body: dict) -> bool:
    """POST /api/demo/cancel — cancel an active recording session."""
    try:
        require(body, 'session_id')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = _get_manager()
    success = mgr.cancel_session(body['session_id'])
    return j(handler, {
        'ok': success,
        'message': 'Recording cancelled' if success else 'Session not found or already stopped',
    })


def handle_post_demo_text_workflow(handler, body: dict) -> bool:
    """POST /api/demo/text-workflow — convert a text description into a Skill.

    Body:
        description: (required) natural language workflow description
        skill_name: (optional) name for the generated skill

    This is the browser-free path: describe what you do, don't demonstrate it.
    """
    try:
        require(body, 'description')
    except ValueError as e:
        return j_err(handler, str(e))

    description = body['description']
    skill_name = body.get('skill_name', '')
    preferred_model = body.get('preferred_model', None)

    mgr = _get_manager()

    # Run in background
    result = {"skill_path": None, "error": None}

    def _analyze():
        try:
            path = mgr.analyze_text_workflow(
                description=description,
                skill_name=skill_name,
                preferred_model=preferred_model,
            )
            result["skill_path"] = path
        except Exception as e:
            _logger.exception("[DemoToSkill] Text workflow analysis failed")
            result["error"] = str(e)

    threading.Thread(target=_analyze, daemon=True).start()

    return j(handler, {
        'ok': True,
        'message': 'Text workflow analysis started in background.',
    })


def handle_post_demo_add_event(handler, body: dict) -> bool:
    """POST /api/demo/add-event — manually add an event to a text-based session.

    Body:
        session_id: (required)
        description: (required) natural language description of the step
    """
    try:
        require(body, 'session_id', 'description')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = _get_manager()
    success = mgr.add_text_event(body['session_id'], body['description'])
    return j(handler, {
        'ok': success,
        'message': 'Event added' if success else 'Session not found',
    })

def handle_post_skill_approve(handler, body: dict) -> bool:
    """POST /api/demo/skill/approve — approve a generated skill."""
    try:
        require(body, 'skill_name')
    except ValueError as e:
        return j_err(handler, str(e))

    from api.skill_registry import get_skill_registry
    registry = get_skill_registry()
    success = registry.promote_skill(body['skill_name'])
    return j(handler, {
        'ok': success,
        'message': 'Skill approved' if success else 'Failed to approve skill',
    })

def handle_post_skill_reject(handler, body: dict) -> bool:
    """POST /api/demo/skill/reject — reject a generated skill."""
    try:
        require(body, 'skill_name')
    except ValueError as e:
        return j_err(handler, str(e))

    from api.skill_registry import get_skill_registry
    registry = get_skill_registry()
    success = registry.reject_skill(body['skill_name'])
    return j(handler, {
        'ok': success,
        'message': 'Skill rejected' if success else 'Failed to reject skill',
    })
