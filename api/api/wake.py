"""Background Wake — 백그라운드 작업 완료 시 에이전트 기상 레지스트리.

문제: 터미널 백그라운드·워커·하네스 작업이 끝나도, 브라우저가 닫혀 있거나
채팅 스트림이 없으면 완료 소식을 라온이 받지 못해 작업이 끊겼다.
(SSE notice는 스트림이 살아 있을 때만 전달, DB pending은 다음 턴까지 대기)

구조 (2026-10-17):
  1. 라온이 백그라운드 작업을 걸 때 watches.json에 watch를 1줄 등록한다.
     {kind: worker|terminal|harness, ref: job_id|proc_id|run_id,
      session_id: WebUI 채팅 세션, label, next: 완료 후 이어할 일}
     (등록은 파일 직접 쓰기 — 별도 도구/인증 불필요)
  2. 프론트 기상 폴러(app.js, 8초 간격)가 GET /api/wake/poll?session_id= 로
     미배달 완료를 조회 → 있으면 ack 후 chat-input에 자동 기상 메시지를
     넣어 handleSendMessage()로 턴을 연다 = 서버가 라온을 깨운다.
  3. 브라우저가 닫혀 폴러가 못 뛴 경우: streaming.py 턴 시작 시
     peek_completions()으로 미배달 완료를 주입한다 (폴백, 중복 없음).

스레드 세이프 + 전면 fail-open: 어떤 조회가 실패해도 빈 목록을 반환하고
턴/폴링을 절대 깨지 않는다.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WAKE_DIR = Path(r"C:\daon\_wake")
WATCHES_FILE = WAKE_DIR / "watches.json"
WORKER_JOBS_FILE = Path(r"C:\daon\_worker_runs\worker_jobs.json")

_TAIL_CHARS = 1500
_lock = threading.Lock()


# ── watch 저장소 ──

def _load_watches() -> list:
    try:
        if not WATCHES_FILE.exists():
            return []
        data = json.loads(WATCHES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("wake: watches load failed: %s", e)
        return []


def _save_watches(watches: list) -> None:
    try:
        WAKE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = WATCHES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(watches, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(WATCHES_FILE)
    except Exception as e:
        logger.warning("wake: watches save failed: %s", e)


def register_watch(kind: str, ref: str, session_id: str, label: str = "", next: str = "") -> dict:
    """watch 1건 등록. 라온이 파일 직접 쓰기와 동일한 효과 (도구 경유용)."""
    watch = {
        "id": f"w_{int(time.time())}_{os.urandom(3).hex()}",
        "kind": kind,
        "ref": ref,
        "session_id": session_id,
        "label": label or ref,
        "next": next or "",
        "created_at": time.time(),
        "delivered": False,
        "delivered_at": 0,
    }
    with _lock:
        watches = _load_watches()
        watches.append(watch)
        _save_watches(watches)
    return watch


# ── 완료 판정 (소스별) ──

def _tail(text: str) -> str:
    try:
        s = str(text or "")
        return s[-_TAIL_CHARS:] if len(s) > _TAIL_CHARS else s
    except Exception:
        return ""


def _resolve_worker(ref: str) -> dict:
    """worker_jobs.json 기준. 반환: {state: active|done, ...}"""
    try:
        jobs = json.loads(WORKER_JOBS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"state": "unknown", "note": f"worker_jobs.json 읽기 실패: {e}"}
    job = jobs.get(ref) if isinstance(jobs, dict) else None
    if not job:
        return {"state": "done", "ok": False, "stale": True,
                "summary": "워커 잡 기록이 없음 (삭제됐거나 만료). 결과는 세션 기록에서 확인 필요.",
                "finished_at": 0}
    status = str(job.get("status", ""))
    if status in ("completed", "failed"):
        out = job.get("output") or job.get("raw_tail") or job.get("error") or ""
        return {"state": "done", "ok": status == "completed",
                "summary": _tail(out) or f"워커 종료 (status={status})",
                "finished_at": job.get("finished_at") or 0}
    return {"state": "active", "status": status}


def _resolve_terminal(ref: str) -> dict:
    try:
        from tools.process_registry import process_registry
    except Exception as e:
        return {"state": "unknown", "note": f"process_registry import 실패: {e}"}
    try:
        s = process_registry.get(ref)
    except Exception as e:
        return {"state": "unknown", "note": f"레지스트리 조회 실패: {e}"}
    if s is None:
        return {"state": "done", "ok": False, "stale": True,
                "summary": "터미널 세션 기록이 메모리 레지스트리에 없음 (서버 재시작으로 유실 가능). 명령 결과는 직접 확인 필요.",
                "finished_at": 0}
    try:
        if getattr(s, "exited", False):
            return {"state": "done", "ok": (getattr(s, "exit_code", 1) == 0),
                    "summary": f"exit_code={getattr(s, 'exit_code', '?')}\n{_tail(getattr(s, 'output_buffer', ''))}",
                    "finished_at": 0}
    except Exception as e:
        return {"state": "unknown", "note": f"세션 상태 읽기 실패: {e}"}
    return {"state": "active", "status": "running"}


def _resolve_harness(ref: str) -> dict:
    try:
        import api.dynamic_jobs as dj
    except Exception as e:
        return {"state": "unknown", "note": f"dynamic_jobs import 실패: {e}"}
    try:
        resp = dj.get_job_status_response(ref)
    except Exception as e:
        return {"state": "unknown", "note": f"하네스 상태 조회 실패: {e}"}
    if resp is None:
        return {"state": "done", "ok": False, "stale": True,
                "summary": "하네스 run 기록이 없음 (만료). 결과는 Dynamic Harness 탭에서 확인 필요.",
                "finished_at": 0}
    status = str(resp.get("status", ""))
    if status == "done":
        return {"state": "done", "ok": True,
                "summary": _tail(resp.get("result", "")) or "하네스 완료 (결과 비어 있음)",
                "finished_at": 0}
    if status == "error":
        return {"state": "done", "ok": False,
                "summary": _tail(resp.get("error", "")) or "하네스 오류",
                "finished_at": 0}
    if status in ("clarifying", "awaiting_approval"):
        # 질문/승인 대기는 멈춤 상태 — 즉시 깨워야 한다.
        extra = ""
        if status == "clarifying":
            qs = ((resp.get("clarification") or {}).get("questions")) or []
            extra = "질문: " + " / ".join(str(q)[:200] for q in qs[:3])
        else:
            extra = str(resp.get("approval_message", ""))[:500]
        return {"state": "done", "ok": True, "needs_input": True,
                "summary": f"하네스가 '{status}' 상태에서 멈춤 — 라온의 입력 필요. {extra}",
                "finished_at": 0}
    return {"state": "active", "status": status, "phase": resp.get("phase", "")}


_RESOLVERS = {
    "worker": _resolve_worker,
    "terminal": _resolve_terminal,
    "harness": _resolve_harness,
}


# ── 조회/수령 API (프론트 폴러 + 턴 시작 폴백이 공유) ──

def peek_completions(session_id: str):
    """미배달 완료 (completions) + 진행 중 (active) 목록. 표시만 하고 수령 처리 안 함."""
    active, completions = [], []
    try:
        watches = _load_watches()
    except Exception:
        return active, completions
    now = time.time()
    for w in watches:
        try:
            if w.get("delivered"):
                continue
            if session_id and w.get("session_id") and w.get("session_id") != session_id:
                continue
            resolver = _RESOLVERS.get(str(w.get("kind", "")))
            if resolver is None:
                continue
            r = resolver(str(w.get("ref", ""))) or {}
            if r.get("state") == "done":
                completions.append({
                    "id": w.get("id", ""),
                    "kind": w.get("kind", ""),
                    "ref": w.get("ref", ""),
                    "label": w.get("label", "") or w.get("ref", ""),
                    "ok": bool(r.get("ok", True)),
                    "stale": bool(r.get("stale", False)),
                    "needs_input": bool(r.get("needs_input", False)),
                    "summary": r.get("summary", ""),
                    "finished_at": r.get("finished_at") or 0,
                    "next": w.get("next", ""),
                })
            elif r.get("state") == "active":
                active.append({
                    "id": w.get("id", ""),
                    "kind": w.get("kind", ""),
                    "ref": w.get("ref", ""),
                    "label": w.get("label", "") or w.get("ref", ""),
                    "status": r.get("status", "running"),
                    "elapsed": round(now - float(w.get("created_at", now)), 1),
                })
            # state == unknown → 조용히 다음 폴링으로 연기 (깨우지 않음)
        except Exception:
            continue
    return active, completions


def mark_delivered(ids) -> int:
    """수령 처리. 반환: 처리 건수."""
    ids = set(ids or [])
    if not ids:
        return 0
    n = 0
    with _lock:
        watches = _load_watches()
        for w in watches:
            if w.get("id") in ids and not w.get("delivered"):
                w["delivered"] = True
                w["delivered_at"] = time.time()
                n += 1
        if n:
            _save_watches(watches)
    return n


# ── HTTP 핸들러 ──

def handle_get_wake_poll(handler, parsed) -> bool:
    """GET /api/wake/poll?session_id=... — 기상 폴러용 조회 (수령 처리 없음)."""
    from urllib.parse import parse_qs
    qs = parse_qs(parsed.query) if getattr(parsed, "query", None) else {}
    session_id = qs.get("session_id", [""])[0].strip()
    if not session_id:
        handler.send_json({"ok": False, "error": "session_id is required"}, 400)
        return True
    try:
        active, completions = peek_completions(session_id)
        handler.send_json({"ok": True, "active": active, "completions": completions})
    except Exception as e:
        logger.exception("wake poll failed")
        handler.send_json({"ok": False, "error": str(e)}, 500)
    return True


def handle_post_wake_ack(handler, body: dict) -> bool:
    """POST /api/wake/ack {ids:[...]} — 자동 기상 직전 수령 처리 (중복 기상 방지)."""
    try:
        ids = body.get("ids") or []
        n = mark_delivered(ids)
        handler.send_json({"ok": True, "acked": n})
    except Exception as e:
        logger.exception("wake ack failed")
        handler.send_json({"ok": False, "error": str(e)}, 500)
    return True
