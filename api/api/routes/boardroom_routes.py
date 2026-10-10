"""
Boardroom Routes — 8-slot agent meeting (6 static agents + 2 CLI workers).

POST /api/boardroom/broadcast — Broadcast a topic/instruction to all 8 slots.

Unlike /api/debate/start (which runs 2 generic LLMs as debaters), this route
wires the boardroom directly to the LIVE agent runtime:

  * 6 static agents  -> api.collaborator.execute_agent_task(name, task, model=...)
                        (the same engine behind the delegate_to_agent tool)
  * 2 CLI workers    -> api.managers.herdr_manager.execute_worker_task(..., model=...)
                        (the same engine behind the delegate_to_worker tool)

Per-slot model override:
  Request body may include `models` = {"01": "glm-5.3-flash", "07": "gpt-6-luna", ...}
  keyed by slot number (or agent/worker key). When present, that slot runs on the
  requested model instead of its profile default. Empty/missing => profile default.

Results are streamed over the shared SSE channel (/api/chat/stream?stream_id=...)
as three custom events:
  boardroom_slot  {slot, speaker, status}             — slot started running
  boardroom_reply {slot, speaker, kind, status, text, model, elapsed}
  boardroom_done  {ok, task}                          — every slot finished
"""

import logging
import threading
import time
import uuid

logger = logging.getLogger(__name__)


# (slot_no, label, kind, key)
#   kind='agent'  -> collaborator.execute_agent_task(key, task, model=...)
#   kind='worker' -> herdr_manager.execute_worker_task(name=key, prompt=task, model=...)
BOARDROOM_SLOTS = [
    ("01", "라온 (총괄기획)", "agent", "raon"),
    ("02", "빌 (개발)", "agent", "bill"),
    ("03", "셜록 (검수)", "agent", "sherlock"),
    ("04", "토니 (기획)", "agent", "tony"),
    ("05", "프라다 (디자인)", "agent", "prada"),
    ("06", "다온 (응대)", "agent", "daon"),
    ("07", "코덱스 워커", "worker", "worker-codex"),
    ("08", "클로드 워커", "worker", "worker-claude"),
]

SLOT_TIMEOUT = 180


def _resolve_slot_model(models: dict, slot, key) -> str:
    """Pick the model override for a slot. Accepts keys by slot number ('01')
    or by agent/worker key ('raon', 'worker-codex'). Returns '' when unset."""
    if not isinstance(models, dict):
        return ""
    val = models.get(slot) or models.get(key) or ""
    return str(val).strip()


def _run_agent_slot(emit, slot, label, agent_key, task, model=None):
    """Run one static-agent slot and emit its reply."""
    t0 = time.time()
    emit("boardroom_slot", {"slot": slot, "speaker": label, "kind": "agent", "status": "running", "model": model or ""})
    ok = False
    text = ""
    try:
        from api.collaborator import execute_agent_task
        # force_new_session=True keeps these meeting turns out of the agent's
        # regular conversation threads (esp. raon, whose most-recent session is
        # the live chat session the user is typing in).
        res = execute_agent_task(
            agent_key, task, timeout=SLOT_TIMEOUT, force_new_session=True, model=(model or None)
        )
        ok = bool(res.get("ok"))
        text = (res.get("output") if ok else (res.get("error") or "작업 미완료")) or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("boardroom agent slot failed: %s", agent_key)
        ok, text = False, f"{type(e).__name__}: {e}"
    emit("boardroom_reply", {
        "slot": slot, "speaker": label, "kind": "agent",
        "status": "done" if ok else "error",
        "text": text, "model": model or "", "elapsed": round(time.time() - t0, 1),
    })


def _run_worker_slot(emit, slot, label, worker_key, task, model=None):
    """Run one CLI-worker slot and emit its reply."""
    t0 = time.time()
    emit("boardroom_slot", {"slot": slot, "speaker": label, "kind": "worker", "status": "running", "model": model or ""})
    ok = False
    text = ""
    try:
        from api.managers.herdr_manager import herdr_manager
        res = herdr_manager.execute_worker_task(
            name=worker_key, prompt=task, timeout=SLOT_TIMEOUT, model=(model or None)
        )
        ok = bool(res.get("ok"))
        text = (res.get("result") if ok else (res.get("error") or res.get("status") or "워커 미완료")) or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("boardroom worker slot failed: %s", worker_key)
        ok, text = False, f"{type(e).__name__}: {e}"
    emit("boardroom_reply", {
        "slot": slot, "speaker": label, "kind": "worker",
        "status": "done" if ok else "error",
        "text": text, "model": model or "", "elapsed": round(time.time() - t0, 1),
    })


def handle_post_boardroom_broadcast(handler, body: dict) -> bool:
    """POST /api/boardroom/broadcast — fan a topic out to all 8 boardroom slots."""
    task = (body.get("task") or body.get("topic") or "").strip()
    if not task:
        handler.send_json({"ok": False, "error": "task is required"}, 400)
        return True

    # Optional per-slot model overrides: {"01": "glm-5.3-flash", "worker-codex": "gpt-6-luna"}
    models = body.get("models") or {}
    if not isinstance(models, dict):
        models = {}

    try:
        from api.config import STREAMS, STREAMS_LOCK
        from api.streaming import BroadcastQueue
    except Exception as e:  # noqa: BLE001
        handler.send_json({"ok": False, "error": f"stream infra unavailable: {e}"}, 500)
        return True

    stream_id = uuid.uuid4().hex
    q = BroadcastQueue()
    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    def emit(event: str, data: dict) -> None:
        try:
            q.put((event, data))
        except Exception:  # noqa: BLE001
            pass

    def orchestrate() -> None:
        workers = []
        for slot, label, kind, key in BOARDROOM_SLOTS:
            slot_model = _resolve_slot_model(models, slot, key)
            fn = _run_agent_slot if kind == "agent" else _run_worker_slot
            th = threading.Thread(
                target=fn, args=(emit, slot, label, key, task, slot_model),
                daemon=True, name=f"Boardroom-{slot}",
            )
            th.start()
            workers.append(th)
        for th in workers:
            th.join()
        emit("boardroom_done", {"ok": True, "task": task})

    threading.Thread(target=orchestrate, daemon=True, name="BoardroomBroadcast").start()

    handler.send_json({"ok": True, "stream_id": stream_id, "slots": len(BOARDROOM_SLOTS)})
    return True
