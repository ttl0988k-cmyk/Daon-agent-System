# -*- coding: utf-8 -*-
"""
DAON External Worker Routes — API endpoints for Herdr-managed coding agents (Codex, Claude).

Endpoints:
  GET  /api/workers         - List all registered workers and their current status
  POST /api/workers/start   - Spawn or connect a worker in Herdr
  POST /api/workers/prompt  - Send task/prompt to worker
  GET  /api/workers/status  - Inspect specific worker status, clean output, and approval info
  POST /api/workers/approve - Approve or reject interactive prompts (folder trust, tool use)
  GET  /api/workers/stream  - Real-time SSE stream of worker activity
"""

from __future__ import annotations

import json
import logging
import queue
import time
from typing import Any, Dict
from urllib.parse import parse_qs

from api.helpers import bad, j, j_ok
from api.managers.herdr_manager import herdr_manager

_logger = logging.getLogger(__name__)


def handle_get_workers(handler, parsed) -> bool:
    """GET /api/workers — List all workers."""
    try:
        workers = herdr_manager.list_workers()
        return j_ok(handler, workers=workers)
    except Exception as e:
        _logger.error(f"[worker_routes] Failed to list workers: {e}")
        return bad(handler, str(e), status=500)


def handle_get_worker_status(handler, parsed) -> bool:
    """GET /api/workers/status?name=worker-codex — Get single worker status."""
    qs = parse_qs(parsed.query)
    name = qs.get("name", ["worker-codex"])[0]
    worker = herdr_manager.get_worker(name)
    if not worker:
        return bad(handler, f"Worker '{name}' not found", status=404)
    return j_ok(handler, worker=worker)


def handle_post_worker_start(handler, body: Dict[str, Any]) -> bool:
    """POST /api/workers/start — Start or attach a worker."""
    name = body.get("name", "worker-codex")
    kind = body.get("kind", "codex")
    cwd = body.get("cwd", r"C:\daon")
    try:
        worker = herdr_manager.start_worker(name=name, kind=kind, cwd=cwd)
        return j_ok(handler, worker=worker)
    except Exception as e:
        _logger.error(f"[worker_routes] Failed to start worker '{name}': {e}")
        return bad(handler, str(e), status=500)


def handle_post_worker_prompt(handler, body: Dict[str, Any]) -> bool:
    """POST /api/workers/prompt — Send a prompt/task to a worker."""
    name = body.get("name", "worker-codex")
    prompt = body.get("prompt", "")
    if not prompt:
        return bad(handler, "Field 'prompt' is required", status=400)

    try:
        worker = herdr_manager.prompt_worker(name=name, prompt=prompt)
        return j_ok(handler, worker=worker)
    except Exception as e:
        _logger.error(f"[worker_routes] Failed to prompt worker '{name}': {e}")
        return bad(handler, str(e), status=500)


def handle_post_worker_approve(handler, body: Dict[str, Any]) -> bool:
    """POST /api/workers/approve — Respond to an interactive approval request."""
    name = body.get("name", "worker-codex")
    decision = body.get("decision", "approve")
    custom_key = body.get("key")

    try:
        result = herdr_manager.approve(name=name, decision=decision, custom_key=custom_key)
        return j_ok(handler, **result)
    except Exception as e:
        _logger.error(f"[worker_routes] Failed to approve worker '{name}': {e}")
        return bad(handler, str(e), status=500)


def handle_post_worker_model(handler, body: Dict[str, Any]) -> bool:
    """POST /api/workers/model — Dynamically change worker model/provider."""
    name = body.get("name", "worker-codex")
    model = body.get("model", "")
    if not model:
        return bad(handler, "Field 'model' is required", status=400)

    try:
        worker = herdr_manager.set_worker_model(name=name, model_id=model)
        return j_ok(handler, worker=worker)
    except Exception as e:
        _logger.error(f"[worker_routes] Failed to set model for worker '{name}': {e}")
        return bad(handler, str(e), status=500)



def handle_get_worker_stream(handler, parsed) -> bool:
    """GET /api/workers/stream — Real-time SSE stream of worker status and terminal updates."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()

    # Send initial worker states
    try:
        init_workers = herdr_manager.list_workers()
        init_msg = f"event: init\ndata: {json.dumps(init_workers, ensure_ascii=False)}\n\n".encode("utf-8")
        handler.wfile.write(init_msg)
        handler.wfile.flush()
    except Exception:
        return False

    q = herdr_manager.subscribe()
    try:
        while True:
            try:
                event_data = q.get(timeout=15.0)
                ev_type = event_data.get("event", "update")
                payload = json.dumps(event_data, ensure_ascii=False)
                msg = f"event: {ev_type}\ndata: {payload}\n\n".encode("utf-8")
                handler.wfile.write(msg)
                handler.wfile.flush()
            except queue.Empty:
                # Keep-alive heartbeat
                try:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                except (ConnectionError, BrokenPipeError, OSError):
                    break
    except (ConnectionError, BrokenPipeError, OSError):
        pass
    finally:
        herdr_manager.unsubscribe(q)
    return True
