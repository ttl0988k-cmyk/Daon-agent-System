# -*- coding: utf-8 -*-
"""
api.dynamic.job_store — Dynamic Harness Persistent Job Store
============================================================

Provides SQLite-based persistence for Dynamic Harness jobs, real-time logs,
and delegation lineages. Prevents state loss across server restarts or crashes.

Design Principles:
- Pure Additive & Crash Resilient: Uses SQLite in WAL mode.
- In-memory Write-Through: Low latency memory access with durable DB storage.
- Auto-Recovery: Restores orphaned 'running' / 'clarifying' jobs to 'interrupted' on startup.
- Graceful Fallback: All DB calls are guarded to never break runtime execution.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

try:
    from api.config import STATE_DIR
    _DEFAULT_DB_DIR = Path(STATE_DIR)
except Exception:
    import os
    _local_app_data = os.getenv("LOCALAPPDATA")
    if _local_app_data:
        _DEFAULT_DB_DIR = Path(_local_app_data) / "DAON Agent System" / "data"
    else:
        _DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"

_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "harness_jobs.db"


class HarnessJobStore:
    """Manages SQLite storage for Hermes Dynamic Harness jobs, logs, and lineages."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.executescript("""
                        CREATE TABLE IF NOT EXISTS dynamic_jobs (
                            run_id TEXT PRIMARY KEY,
                            session_id TEXT,
                            status TEXT NOT NULL,
                            started_at REAL NOT NULL,
                            updated_at REAL NOT NULL,
                            result TEXT,
                            error TEXT,
                            clarification TEXT,
                            approval_message TEXT,
                            available_actions TEXT,
                            approval_action TEXT
                        );

                        CREATE TABLE IF NOT EXISTS dynamic_job_logs (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            run_id TEXT NOT NULL,
                            agent_id TEXT,
                            content TEXT NOT NULL,
                            status TEXT DEFAULT 'running',
                            created_at REAL NOT NULL,
                            FOREIGN KEY (run_id) REFERENCES dynamic_jobs(run_id) ON DELETE CASCADE
                        );

                        CREATE INDEX IF NOT EXISTS idx_job_logs_run_id ON dynamic_job_logs(run_id);
                        CREATE INDEX IF NOT EXISTS idx_job_logs_created_at ON dynamic_job_logs(created_at);

                        CREATE TABLE IF NOT EXISTS dynamic_lineage (
                            run_id TEXT PRIMARY KEY,
                            parent_run_id TEXT,
                            root_run_id TEXT,
                            depth INTEGER NOT NULL DEFAULT 0,
                            spawn_reason TEXT,
                            created_at REAL NOT NULL
                        );

                        CREATE INDEX IF NOT EXISTS idx_lineage_parent ON dynamic_lineage(parent_run_id);
                        CREATE INDEX IF NOT EXISTS idx_lineage_root ON dynamic_lineage(root_run_id);
                    """)
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Failed to ensure schema: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Job CRUD
    # ──────────────────────────────────────────────────────────────────────────

    def save_job(self, run_id: str, job_data: Dict[str, Any]) -> None:
        """Insert or update a job record."""
        if not run_id or not isinstance(job_data, dict):
            return
        try:
            now = time.time()
            clarification_json = json.dumps(job_data.get("clarification")) if job_data.get("clarification") else None
            available_actions_json = json.dumps(job_data.get("available_actions")) if job_data.get("available_actions") else None

            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("""
                        INSERT INTO dynamic_jobs (
                            run_id, session_id, status, started_at, updated_at,
                            result, error, clarification, approval_message,
                            available_actions, approval_action
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            session_id = COALESCE(excluded.session_id, dynamic_jobs.session_id),
                            status = excluded.status,
                            updated_at = excluded.updated_at,
                            result = excluded.result,
                            error = excluded.error,
                            clarification = excluded.clarification,
                            approval_message = excluded.approval_message,
                            available_actions = excluded.available_actions,
                            approval_action = COALESCE(excluded.approval_action, dynamic_jobs.approval_action)
                    """, (
                        run_id,
                        job_data.get("session_id"),
                        job_data.get("status", "running"),
                        job_data.get("started_at", now),
                        now,
                        job_data.get("result"),
                        job_data.get("error", ""),
                        clarification_json,
                        job_data.get("approval_message"),
                        available_actions_json,
                        job_data.get("approval_action"),
                    ))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error saving job {run_id}: {e}")

    def get_job(self, run_id: str, include_logs: bool = True) -> Optional[Dict[str, Any]]:
        """Retrieve a job by run_id from DB, optionally attaching logs."""
        if not run_id:
            return None
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("SELECT * FROM dynamic_jobs WHERE run_id = ?", (run_id,))
                    row = cur.fetchone()
                    if not row:
                        return None

                    job = {
                        "run_id": row["run_id"],
                        "session_id": row["session_id"],
                        "status": row["status"],
                        "started_at": row["started_at"],
                        "updated_at": row["updated_at"],
                        "result": row["result"],
                        "error": row["error"] or "",
                        "clarification": json.loads(row["clarification"]) if row["clarification"] else None,
                        "approval_message": row["approval_message"],
                        "available_actions": json.loads(row["available_actions"]) if row["available_actions"] else None,
                        "approval_action": row["approval_action"],
                        "logs": [],
                    }

                    if include_logs:
                        log_cur = conn.execute(
                            "SELECT agent_id, content, status FROM dynamic_job_logs WHERE run_id = ? ORDER BY id ASC",
                            (run_id,)
                        )
                        job["logs"] = [
                            {"agent_id": r["agent_id"], "content": r["content"], "status": r["status"]}
                            for r in log_cur.fetchall()
                        ]

                    return job
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error getting job {run_id}: {e}")
            return None

    def append_log(self, run_id: str, agent_id: str, content: str, status: str = "running") -> None:
        """Append a log entry for a job."""
        if not run_id or not content:
            return
        try:
            now = time.time()
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("""
                        INSERT INTO dynamic_job_logs (run_id, agent_id, content, status, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (run_id, agent_id, content, status, now))
                    conn.execute("UPDATE dynamic_jobs SET updated_at = ? WHERE run_id = ?", (now, run_id))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error appending log for {run_id}: {e}")

    def get_logs_since(self, run_id: str, cursor: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        """Fetch logs since given cursor offset."""
        if not run_id:
            return [], cursor
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        "SELECT agent_id, content, status FROM dynamic_job_logs WHERE run_id = ? ORDER BY id ASC LIMIT -1 OFFSET ?",
                        (run_id, cursor)
                    )
                    rows = cur.fetchall()
                    new_logs = [
                        {"agent_id": r["agent_id"], "content": r["content"], "status": r["status"]}
                        for r in rows
                    ]
                    return new_logs, cursor + len(new_logs)
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error fetching logs for {run_id}: {e}")
            return [], cursor

    # ──────────────────────────────────────────────────────────────────────────
    # Lineage CRUD
    # ──────────────────────────────────────────────────────────────────────────

    def save_lineage(self, run_id: str, parent_run_id: str, root_run_id: str, depth: int, spawn_reason: str = "") -> None:
        """Register child-to-parent lineage relationship."""
        if not run_id:
            return
        try:
            now = time.time()
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("""
                        INSERT INTO dynamic_lineage (run_id, parent_run_id, root_run_id, depth, spawn_reason, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id) DO UPDATE SET
                            parent_run_id = excluded.parent_run_id,
                            root_run_id = excluded.root_run_id,
                            depth = excluded.depth,
                            spawn_reason = excluded.spawn_reason
                    """, (run_id, parent_run_id, root_run_id, int(depth), str(spawn_reason or ""), now))
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error saving lineage for {run_id}: {e}")

    def get_lineage(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve lineage metadata for a given run_id."""
        if not run_id:
            return None
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("SELECT * FROM dynamic_lineage WHERE run_id = ?", (run_id,))
                    row = cur.fetchone()
                    if not row:
                        return None
                    return {
                        "parent_run_id": row["parent_run_id"],
                        "root_run_id": row["root_run_id"],
                        "depth": row["depth"],
                        "spawn_reason": row["spawn_reason"],
                        "created_at": row["created_at"],
                    }
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error getting lineage for {run_id}: {e}")
            return None

    def get_all_lineage(self) -> Dict[str, Dict[str, Any]]:
        """Fetch entire active lineage map."""
        result: Dict[str, Dict[str, Any]] = {}
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("SELECT * FROM dynamic_lineage")
                    for row in cur.fetchall():
                        result[row["run_id"]] = {
                            "parent_run_id": row["parent_run_id"],
                            "root_run_id": row["root_run_id"],
                            "depth": row["depth"],
                            "spawn_reason": row["spawn_reason"],
                            "created_at": row["created_at"],
                        }
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error getting all lineage: {e}")
        return result

    def delete_lineage_subtree(self, run_id: str) -> List[str]:
        """Delete a run_id and all its descendant lineages from DB."""
        if not run_id:
            return []
        try:
            all_lineage = self.get_all_lineage()
            children_map: Dict[str, List[str]] = {}
            for rid, info in all_lineage.items():
                pid = info.get("parent_run_id")
                if pid:
                    children_map.setdefault(pid, []).append(rid)

            to_remove = [run_id]
            queue = [run_id]
            while queue:
                curr = queue.pop(0)
                for child in children_map.get(curr, []):
                    if child not in to_remove:
                        to_remove.append(child)
                        queue.append(child)

            with self._lock:
                conn = self._connect()
                try:
                    placeholders = ",".join("?" for _ in to_remove)
                    conn.execute(f"DELETE FROM dynamic_lineage WHERE run_id IN ({placeholders})", to_remove)
                    conn.commit()
                finally:
                    conn.close()
            return to_remove
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error deleting lineage subtree for {run_id}: {e}")
            return [run_id]

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle & Recovery
    # ──────────────────────────────────────────────────────────────────────────

    def recover_interrupted_jobs(self) -> int:
        """Mark any dangling jobs that were running when the process died as 'interrupted'.

        Returns the count of recovered zombie jobs.
        """
        try:
            now = time.time()
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("""
                        UPDATE dynamic_jobs
                        SET status = 'interrupted',
                            error = '서버가 재시작되어 작업이 중단되었습니다.',
                            updated_at = ?
                        WHERE status IN ('running', 'clarifying', 'awaiting_approval')
                    """, (now,))
                    count = cur.rowcount
                    conn.commit()
                    if count > 0:
                        _logger.info(f"[HarnessJobStore] Recovered {count} interrupted jobs from previous session.")
                    return count
                finally:
                    conn.close()
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error recovering interrupted jobs: {e}")
            return 0

    def load_recent_jobs(self, limit: int = 50) -> Dict[str, Dict[str, Any]]:
        """Load recent jobs into memory cache."""
        jobs: Dict[str, Dict[str, Any]] = {}
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute(
                        "SELECT run_id FROM dynamic_jobs ORDER BY started_at DESC LIMIT ?",
                        (limit,)
                    )
                    run_ids = [r["run_id"] for r in cur.fetchall()]
                finally:
                    conn.close()

            for rid in run_ids:
                job = self.get_job(rid, include_logs=True)
                if job:
                    jobs[rid] = job
        except Exception as e:
            _logger.error(f"[HarnessJobStore] Error loading recent jobs: {e}")
        return jobs
