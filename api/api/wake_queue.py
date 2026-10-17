# -*- coding: utf-8 -*-
"""
api.wake_queue — 백그라운드 작업 완료 시 에이전트 자동 깨우기 (Wake Queue)
=========================================================================

Phase 1 (하네스 경로): Dynamic Harness 백그라운드 잡이 완료되면, 그 잡을 건
세션에 자율 깨우기 턴을 시작한다. 사용자 개입 없이, 턴 종료 후에도 전달된다.

설계 원칙 (Wake Queue 설계서 §3):
1. 영속 큐가 유일한 진실: 완료 이벤트는 먼저 DB(wake_events)에 기록,
   전달 완료 표시까지 지워지지 않는다. 스트림 push는 빠른 경로일 뿐이다.
2. 토큰 효율: 깨우기 프롬프트에 결과 전문을 넣지 않는다 (요약 ≤500자 + 조회 지시).
3. 바쁘면 기다린다: busy는 실패가 아니다. attempts를 소모하지 않고 pending 유지.
4. 합치기: 한 세션의 pending은 한 번의 깨우기 턴으로 묶어 전달한다.
5. 멱등성: dedup_key UNIQUE로 같은 완료의 중복 기록/전달을 방지한다.
6. 순수 부가: 기존 함수 시그니처·SSE 포맷 유지. 모든 신규 호출은 guarded.
7. 새 의존성 금지: SQLite(harness_jobs.db) + 표준 라이브러리만 사용한다.

기존 dynamic_notices/save_notice 경로와의 관계:
- DAON_WAKE_QUEUE=1(기본): push_harness_done은 wake_events에만 기록하고
  save_notice를 건너뛴다. 배달은 디스패처가 전담 → 중복 보고 없음.
- DAON_WAKE_QUEUE=0: enqueue를 건너뛰고 기존 동작(스트림 push → save_notice)
  그대로 → 킬스위치로 완전 복귀 (수용 T7).
- 턴 시작 시 take_pending_notices 주입은 그대로 둔다 (안전망 + 플래그 OFF 경로).

워커 경로(relay_report_to_raon 이관)는 Phase 2 범위 — 이 모듈에 kind만 준비.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
_WAKE_BACKOFF = (5.0, 30.0, 120.0)  # memory_store.job_queue와 동일 (초, 최대 3회)
_WAKE_MAX_ATTEMPTS = 3
_DISPATCH_POLL_INTERVAL = 1.0  # 초 (memory_store._QUEUE_POLL_INTERVAL과 동일)
_SUMMARY_LIMIT = 500  # 깨우기 프롬프트에 싣는 요약 상한 (자)


def wake_enabled() -> bool:
    """킬스위치. DAON_WAKE_QUEUE=0이면 디스패처·enqueue 모두 정지."""
    try:
        return os.getenv("DAON_WAKE_QUEUE", "1") != "0"
    except Exception:
        return True


# ─────────────────────────────────────────────
# DB 접근 (HarnessJobStore와 같은 파일, 독자 락)
# ─────────────────────────────────────────────
_DB_PATH: Optional[str] = None
_DB_LOCK = threading.Lock()


def _db_path() -> str:
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    try:
        from api.dynamic.job_store import _DEFAULT_DB_PATH as _p
        _DB_PATH = str(_p)
        return _DB_PATH
    except Exception:
        pass
    try:
        from api.config import STATE_DIR as _sd
        _DB_PATH = str(Path(_sd) / "harness_jobs.db")
        return _DB_PATH
    except Exception:
        pass
    _lad = os.getenv("LOCALAPPDATA")
    if _lad:
        _DB_PATH = str(Path(_lad) / "DAON Agent System" / "data" / "harness_jobs.db")
    else:
        _DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "harness_jobs.db")
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    p = _db_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


# ─────────────────────────────────────────────
# 큐 기록 / 조회 / 상태 전이 (예외를 던지지 않음)
# ─────────────────────────────────────────────
def enqueue_wake(
    kind: str,
    *,
    dedup_key: str,
    target_session_id: Optional[str] = None,
    target_profile: Optional[str] = None,
    run_id: Optional[str] = None,
    inbox_id: Optional[int] = None,
    ok: bool = True,
    summary: str = "",
    wake_depth: int = 0,
) -> bool:
    """완료 이벤트를 영속 큐에 기록한다. INSERT OR IGNORE (dedup).

    성공 즉시 디스패처에 nudge. 중복 키면 False (신규 기록 없음).
    어떤 실패에도 예외를 던지지 않고 False를 반환한다.
    """
    if not dedup_key or not kind:
        return False
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO wake_events
                       (dedup_key, kind, target_session_id, target_profile,
                        run_id, inbox_id, ok, summary, wake_depth,
                        status, attempts, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)""",
                    (
                        dedup_key, kind, target_session_id, target_profile,
                        run_id, inbox_id, 1 if ok else 0,
                        (summary or "")[:_SUMMARY_LIMIT],
                        int(wake_depth or 0), time.time(),
                    ),
                )
                conn.commit()
                inserted = cur.rowcount > 0
            finally:
                conn.close()
        if inserted:
            kick(target_session_id)
        return inserted
    except Exception as e:
        _logger.warning("[WakeQueue] enqueue failed (%s): %s", dedup_key, e)
        return False


def pending_sessions() -> List[str]:
    """배달 가능한 pending이 있는 세션 목록 (중복 제거)."""
    try:
        now = time.time()
        with _DB_LOCK:
            conn = _connect()
            try:
                cur = conn.execute(
                    """SELECT DISTINCT target_session_id FROM wake_events
                       WHERE status = 'pending'
                         AND target_session_id IS NOT NULL
                         AND target_session_id != ''
                         AND (next_retry_at IS NULL OR next_retry_at <= ?)""",
                    (now,),
                )
                return [r[0] for r in cur.fetchall() if r[0]]
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] pending_sessions failed: %s", e)
        return []


def claim_pending_for_session(session_id: str) -> List[Dict[str, Any]]:
    """그 세션의 배달 가능 pending 전부를 원자적으로 processing 전환 후 반환.

    합치기(coalescing) 단위: 한 번의 깨우기 턴이 이 목록 전체를 전달한다.
    next_retry_at이 미래인 행(백오프 대기 중)은 제외한다.
    """
    if not session_id:
        return []
    try:
        now = time.time()
        with _DB_LOCK:
            conn = _connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """SELECT id, dedup_key, kind, target_session_id, target_profile,
                              run_id, inbox_id, ok, summary, wake_depth,
                              attempts, created_at
                       FROM wake_events
                       WHERE status = 'pending'
                         AND target_session_id = ?
                         AND (next_retry_at IS NULL OR next_retry_at <= ?)
                       ORDER BY created_at ASC""",
                    (session_id, now),
                )
                rows = cur.fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    ph = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE wake_events SET status = 'processing' WHERE id IN ({ph})",
                        ids,
                    )
                conn.commit()
                return [dict(r) for r in rows]
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] claim failed (%s): %s", session_id, e)
        return []


def mark_delivered(ids: List[int]) -> None:
    """배달 성공 표시. 턴 '시작' 성공 기준 (§4-2)."""
    if not ids:
        return
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                ph = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE wake_events SET status = 'done', delivered_at = ? "
                    f"WHERE id IN ({ph})",
                    [time.time()] + list(ids),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] mark_delivered failed: %s", e)


def mark_failed(ids: List[int], error: Any) -> None:
    """배달 실패. job_queue와 같은 백오프로 재시도 (최대 3회 후 failed).

    최종 실패 시에는 save_notice로 최후 수단을 남긴다 — 깨우기가 죽어도
    다음 사용자 턴의 기존 주입 경로로 완료 사실이 보고되도록 (무손실 보장).
    busy 대기는 이 경로를 타지 않는다 (revert_to_pending 사용).
    """
    if not ids:
        return
    err = str(error or "")[:500]
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                ph = ",".join("?" for _ in ids)
                cur = conn.execute(
                    f"SELECT id, target_session_id, run_id, ok, summary, attempts "
                    f"FROM wake_events WHERE id IN ({ph})",
                    list(ids),
                )
                rows = cur.fetchall()
                now = time.time()
                for r in rows:
                    attempts = int(r["attempts"] or 0) + 1
                    if attempts >= _WAKE_MAX_ATTEMPTS:
                        conn.execute(
                            "UPDATE wake_events SET status = 'failed', attempts = ?, "
                            "last_error = ? WHERE id = ?",
                            (attempts, err, r["id"]),
                        )
                    else:
                        wait = _WAKE_BACKOFF[min(attempts - 1, len(_WAKE_BACKOFF) - 1)]
                        conn.execute(
                            "UPDATE wake_events SET status = 'pending', attempts = ?, "
                            "next_retry_at = ?, last_error = ? WHERE id = ?",
                            (attempts, now + wait, err, r["id"]),
                        )
                conn.commit()
                failed_rows = []
                try:
                    for r in rows:
                        if int(r["attempts"] or 0) + 1 >= _WAKE_MAX_ATTEMPTS:
                            failed_rows.append(r)
                except Exception:
                    pass
            finally:
                conn.close()
        # 최후 수단: 최종 실패분은 기존 notice 경로로 넘긴다 (다음 사용자 턴 보고)
        for r in failed_rows:
            try:
                if r["target_session_id"] and r["run_id"]:
                    from api.dynamic.job_store import HarnessJobStore as _Store
                    _store = _Store()
                    _store.save_notice(
                        r["target_session_id"], r["run_id"],
                        r["summary"] or "", ok=bool(r["ok"]),
                    )
            except Exception:
                pass
    except Exception as e:
        _logger.warning("[WakeQueue] mark_failed failed: %s", e)


def revert_to_pending(ids: List[int]) -> None:
    """busy로 배달을 못 했을 때 processing → pending으로 되돌린다.

    attempts를 소모하지 않는다 — 바쁨은 실패가 아니다 (§3-3).
    """
    if not ids:
        return
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                ph = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE wake_events SET status = 'pending', next_retry_at = NULL "
                    f"WHERE id IN ({ph}) AND status = 'processing'",
                    list(ids),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] revert_to_pending failed: %s", e)


def drop(ids: List[int], reason: str = "") -> None:
    """배달 불가 확정 (세션 소멸/아카이브 등). 재시도 안 함."""
    if not ids:
        return
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                ph = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE wake_events SET status = 'dropped', last_error = ? "
                    f"WHERE id IN ({ph})",
                    [str(reason or "")[:500]] + list(ids),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] drop failed: %s", e)


def recover_stale() -> int:
    """부팅 시 크래시 복구: processing인데 서버가 죽은 행을 pending으로 되돌린다.

    부팅 시점에 살아 있는 processing은 전부 미완료이므로(배달 중 턴은 서버와
    함께 죽었다) 시간 조건 없이 복구한다. memory_store 크래시 복구 패턴 미러링.
    """
    try:
        with _DB_LOCK:
            conn = _connect()
            try:
                cur = conn.execute(
                    "UPDATE wake_events SET status = 'pending', next_retry_at = NULL "
                    "WHERE status = 'processing'"
                )
                conn.commit()
                n = cur.rowcount or 0
                if n:
                    _logger.info("[WakeQueue] Recovered %d stale processing events to pending.", n)
                return n
            finally:
                conn.close()
    except Exception as e:
        _logger.warning("[WakeQueue] recover_stale failed: %s", e)
        return 0


# ─────────────────────────────────────────────
# 세션 해석 (§4-3, Phase 1: session_id 경로만 사용)
# ─────────────────────────────────────────────
def _resolve_session(session_id: str):
    """살아 있는 세션 객체를 돌려준다. 없거나 아카이브면 None."""
    if not session_id:
        return None
    try:
        from api.models import get_session
        s = get_session(session_id)
        if s is None:
            return None
        if bool(getattr(s, "archived", False)):
            return None
        return s
    except Exception:
        return None


def _is_busy(session_id: str) -> bool:
    try:
        from api.config import ACTIVE_SESSION_STREAMS, ACTIVE_SESSION_STREAMS_LOCK
        with ACTIVE_SESSION_STREAMS_LOCK:
            return session_id in ACTIVE_SESSION_STREAMS
    except Exception:
        return False


def _lookup_task_first_line(run_id: Optional[str]) -> str:
    """원 작업 첫 줄 (best-effort, 실패 시 빈 문자열)."""
    if not run_id:
        return ""
    try:
        from api import dynamic_jobs as _dj
        with getattr(_dj, "_DYNAMIC_JOBS_LOCK", threading.Lock()):
            job = (getattr(_dj, "_DYNAMIC_JOBS", {}) or {}).get(run_id)
        if job:
            for _k in ("task", "prompt", "goal"):
                _v = job.get(_k)
                if _v:
                    return str(_v).strip().splitlines()[0][:200]
    except Exception:
        pass
    try:
        from api.dynamic.job_store import HarnessJobStore as _Store
        _job = _Store().get_job(run_id)
        if _job:
            for _k in ("task", "prompt", "goal"):
                _v = _job.get(_k)
                if _v:
                    return str(_v).strip().splitlines()[0][:200]
    except Exception:
        pass
    return ""


def build_wake_prompt(events: List[Dict[str, Any]]) -> str:
    """깨우기 프롬프트 생성. 결과 전문 금지 — 요약 + 조회 지시만 (§6)."""
    blocks = []
    for ev in events:
        if (ev.get("kind") or "") == "worker_report":
            # Phase 2에서 채움. Phase 1에서는 도달하지 않음.
            blocks.append(
                f"- 종류: 동료 보고 / inbox_id: {ev.get('inbox_id')}\n"
                f"- 요약: {(ev.get('summary') or '')[:_SUMMARY_LIMIT]}"
            )
            continue
        run_id = ev.get("run_id") or ""
        ok = bool(ev.get("ok", True))
        task_line = _lookup_task_first_line(run_id)
        blocks.append(
            f"- 종류: Dynamic Harness / run_id: {run_id} / 결과: {'성공' if ok else '실패'}\n"
            + (f"- 작업 요약: {task_line}\n" if task_line else "")
            + f"- 결과 요약(일부): {(ev.get('summary') or '')[:_SUMMARY_LIMIT]}"
        )
    body = "\n".join(blocks)
    return (
        "[백그라운드 작업 완료 알림]\n"
        f"{body}\n\n"
        "위 작업은 당신이 백그라운드로 걸어둔 것이다. 결과를 조회해 전문을 확인하고,\n"
        "원래 사용자 목표 기준으로 판단하라:\n"
        "1. 목표가 완주됐으면 최종 성과를 종합해 사용자에게 보고하라.\n"
        "2. 후속 작업이 필요하면 적절한 도구/위임으로 이어서 진행하라.\n"
        "3. 실패했으면 원인을 확인하고, 재시도가 의미 있으면 재시도하고 아니면 사용자에게 알려라."
    )


# ─────────────────────────────────────────────
# 디스패처 (데몬 스레드, 폴링 1초 + kick nudge)
# ─────────────────────────────────────────────
_DISPATCH_EVENT = threading.Event()
_DISPATCH_THREAD: Optional[threading.Thread] = None
_DISPATCH_START_LOCK = threading.Lock()


def kick(session_id: Optional[str] = None) -> None:
    """턴 종료 지점 등에서 호출하는 비차단 nudge. 디스패처 Event를 set."""
    try:
        _DISPATCH_EVENT.set()
    except Exception:
        pass


def ensure_dispatcher() -> bool:
    """디스패처 스레드 1회 시작 가드 (_ensure_queue_worker 패턴 미러링)."""
    global _DISPATCH_THREAD
    try:
        with _DISPATCH_START_LOCK:
            if _DISPATCH_THREAD is not None and _DISPATCH_THREAD.is_alive():
                return True
            t = threading.Thread(
                target=_dispatcher_loop,
                name="WakeQueueDispatcher",
                daemon=True,
            )
            _DISPATCH_THREAD = t
            t.start()
            return True
    except Exception as e:
        _logger.warning("[WakeQueue] ensure_dispatcher failed: %s", e)
        return False


def _dispatcher_loop() -> None:
    while True:
        try:
            _DISPATCH_EVENT.wait(timeout=_DISPATCH_POLL_INTERVAL)
            _DISPATCH_EVENT.clear()
        except Exception:
            pass
        try:
            if not wake_enabled():
                continue
            _drain_once()
        except Exception as e:
            _logger.warning("[WakeQueue] dispatcher loop error: %s", e)


def _drain_once() -> None:
    for sid in pending_sessions():
        try:
            if _is_busy(sid):
                # 바쁨 — pending 유지, attempts 소모 안 함 (§3-3)
                continue
            events = claim_pending_for_session(sid)
            if not events:
                continue
            _deliver_async(sid, events)
        except Exception as e:
            _logger.warning("[WakeQueue] drain error (%s): %s", sid, e)


def _deliver_async(session_id: str, events: List[Dict[str, Any]]) -> None:
    try:
        t = threading.Thread(
            target=_deliver_thread,
            args=(session_id, events),
            name=f"WakeDeliver-{session_id[:8]}",
            daemon=True,
        )
        t.start()
    except Exception as e:
        _logger.warning("[WakeQueue] deliver spawn failed: %s", e)
        try:
            revert_to_pending([ev["id"] for ev in events if ev.get("id")])
        except Exception:
            pass


def _deliver_thread(session_id: str, events: List[Dict[str, Any]]) -> None:
    ids = [ev["id"] for ev in events if ev.get("id")]
    if not ids:
        return
    try:
        # 경합 방지: 시작 직전 busy 재확인. 바쁘면 claim 되돌리고 중단.
        if _is_busy(session_id):
            revert_to_pending(ids)
            return
        s = _resolve_session(session_id)
        if s is None:
            drop(ids, reason="target session gone/archived")
            _logger.info("[WakeQueue] Dropped %d event(s): session %s gone.", len(ids), session_id)
            return
        prompt = build_wake_prompt(events)
        # 기존 relay_report_to_raon과 동일한 깨우기 실행 경로
        from api.config import STREAMS, STREAMS_LOCK
        from api.streaming import BroadcastQueue, _run_agent_streaming
        stream_id = uuid.uuid4().hex
        q = BroadcastQueue()
        with STREAMS_LOCK:
            STREAMS[stream_id] = q
        model = getattr(s, "model", None)
        workspace = getattr(s, "workspace", None)
        thr = threading.Thread(
            target=_run_agent_streaming,
            args=(s.session_id, prompt, model, workspace, stream_id),
            daemon=True,
            name="WakeTurn",
        )
        thr.start()
        # 턴 "시작" 성공 기준 delivered (§4-2)
        mark_delivered(ids)
        _logger.info(
            "[WakeQueue] Woke session %s with %d event(s).", session_id, len(ids)
        )
    except Exception as e:
        _logger.warning("[WakeQueue] deliver failed (%s): %s", session_id, e)
        try:
            mark_failed(ids, e)
        except Exception:
            pass
