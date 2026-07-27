# -*- coding: utf-8 -*-
"""
DAON 기억 시스템 (Memory Store)
================================
대화에서 장기 기억(facts), 사용자 프로필(profile), 세션 요약(summaries)을
자동 추출해 SQLite에 저장한다.

설계 원칙:
- 순수 부가(pure additive): 이 모듈의 실패가 기존 채팅/에이전트 흐름을
  절대 깨뜨리지 않도록 모든 공개 함수를 try/except로 감싼다.
- 백그라운드: 추출/요약은 daemon 스레드에서 비동기로 수행한다.
- 저장소: STATE_DIR/memory.db (설치빌드=LOCALAPPDATA, 개발=BASE_DIR/data).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from api.config import STATE_DIR
except Exception:  # pragma: no cover - config 임포트 실패 대비
    import os
    STATE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent / 'data'

_MEMORY_DB_PATH = Path(STATE_DIR) / 'memory.db'
_db_lock = threading.Lock()
_MAX_FACTS = 500

# ── Job Queue (Always-on ②): 단일 상주 워커가 순차 처리 ──
# fire-and-forget daemon 스레드 대신 SQLite 큐 + 워커 1개로 쓰기 경쟁을 제거한다.
_QUEUE_POLL_INTERVAL = 1.0      # 워커 폴링 주기(초)
_QUEUE_MAX_ATTEMPTS = 3         # 작업당 최대 시도 횟수
_QUEUE_KEEP_DONE = 200          # 완료/실패 이력 보존 개수
_QUEUE_RETRY_BACKOFF = (5.0, 30.0, 120.0)  # 시도별 재시도 대기(초, 지수 백오프)
_queue_worker_started = False
_queue_worker_lock = threading.Lock()

# ── 유지보수 (Always-on ①⑤⑥): 워커가 자체 주기 점검 (외부 cron 불필요) ──
_MAINTENANCE_INTERVAL = 3600.0   # facts 상한 정리 점검 주기(초, 1시간)
_DAILY_INTERVAL = 86400.0        # VACUUM + 백업 주기(초, 24시간)
_last_maintenance_ts = 0.0       # 마지막 정리 시각(워커 시작 시 즉시 1회 실행)
_last_daily_ts = 0.0             # 마지막 일일 정비 시각


# ---------------------------------------------------------------------------
# DB 연결 / 스키마
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    _MEMORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_MEMORY_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass
    return conn


def _ensure_schema() -> None:
    with _db_lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    source_session TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS profile (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    title TEXT,
                    summary TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS job_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload TEXT,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    attempts INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL,
                    started_at REAL,
                    finished_at REAL,
                    next_retry_at REAL,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_job_queue_status ON job_queue(status);
                """
            )
            # 기존 설치본 마이그레이션: 새 컬럼이 없으면 추가(이미 있으면 무시)
            for _ddl in (
                "ALTER TABLE job_queue ADD COLUMN priority INTEGER DEFAULT 0",
                "ALTER TABLE job_queue ADD COLUMN started_at REAL",
                "ALTER TABLE job_queue ADD COLUMN finished_at REAL",
                "ALTER TABLE job_queue ADD COLUMN next_retry_at REAL",
                "ALTER TABLE job_queue ADD COLUMN last_error TEXT",
            ):
                try:
                    conn.execute(_ddl)
                except Exception:
                    pass
            conn.commit()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def list_facts(limit: int = 100, category: Optional[str] = None) -> list:
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                if category:
                    rows = conn.execute(
                        'SELECT id, content, category, source_session, created_at '
                        'FROM facts WHERE category=? ORDER BY id DESC LIMIT ?',
                        (category, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        'SELECT id, content, category, source_session, created_at '
                        'FROM facts ORDER BY id DESC LIMIT ?',
                        (int(limit),),
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
    except Exception:
        return []


def add_fact(content: str, category: str = 'general', source_session: Optional[str] = None) -> Optional[int]:
    try:
        content = (content or '').strip()
        if not content:
            return None
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                # 중복 스킵
                dup = conn.execute(
                    'SELECT id FROM facts WHERE content=? LIMIT 1', (content,)
                ).fetchone()
                if dup:
                    return None
                cur = conn.execute(
                    'INSERT INTO facts (content, category, source_session) VALUES (?,?,?)',
                    (content, category or 'general', source_session),
                )
                conn.commit()
                new_id = cur.lastrowid
                # 상한 정리: 오래된 fact부터 삭제
                count = conn.execute('SELECT COUNT(*) AS c FROM facts').fetchone()['c']
                if count > _MAX_FACTS:
                    overflow = count - _MAX_FACTS
                    conn.execute(
                        'DELETE FROM facts WHERE id IN '
                        '(SELECT id FROM facts ORDER BY id ASC LIMIT ?)',
                        (overflow,),
                    )
                    conn.commit()
                return new_id
            finally:
                conn.close()
    except Exception:
        return None


def delete_fact(fact_id) -> bool:
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                conn.execute('DELETE FROM facts WHERE id=?', (int(fact_id),))
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
def set_profile(key: str, value: str) -> bool:
    """프로필 key/value 저장. 빈 value는 해당 key 삭제."""
    key = (key or '').strip()
    if not key:
        return False
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                value = (value or '').strip()
                if not value:
                    conn.execute('DELETE FROM profile WHERE key=?', (key,))
                else:
                    conn.execute(
                        'INSERT INTO profile (key, value, updated_at) VALUES (?,?,datetime(\'now\')) '
                        'ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
                        (key, value),
                    )
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


def get_profile() -> dict:
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute('SELECT key, value FROM profile ORDER BY key').fetchall()
                return {r['key']: r['value'] for r in rows}
            finally:
                conn.close()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def add_summary(session_id: str, title: str, summary: str) -> Optional[int]:
    try:
        summary = (summary or '').strip()
        if not summary:
            return None
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    'INSERT INTO summaries (session_id, title, summary) VALUES (?,?,?)',
                    (session_id, title or 'Untitled', summary),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
    except Exception:
        return None


def list_summaries(limit: int = 50) -> list:
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    'SELECT id, session_id, title, summary, created_at '
                    'FROM summaries ORDER BY id DESC LIMIT ?',
                    (int(limit),),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Stats / Context
# ---------------------------------------------------------------------------
def get_store_stats() -> dict:
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                facts = conn.execute('SELECT COUNT(*) AS c FROM facts').fetchone()['c']
                profile = conn.execute('SELECT COUNT(*) AS c FROM profile').fetchone()['c']
                summaries = conn.execute('SELECT COUNT(*) AS c FROM summaries').fetchone()['c']
                return {'facts': facts, 'profile': profile, 'summaries': summaries}
            finally:
                conn.close()
    except Exception:
        return {'facts': 0, 'profile': 0, 'summaries': 0}


def get_queue_stats() -> dict:
    """job_queue의 상태별 개수 집계 (pending/processing/done/failed)."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS c FROM job_queue GROUP BY status"
                ).fetchall()
                stats = {'pending': 0, 'processing': 0, 'done': 0, 'failed': 0}
                for r in rows:
                    stats[r['status']] = r['c']
                return stats
            finally:
                conn.close()
    except Exception:
        return {'pending': 0, 'processing': 0, 'done': 0, 'failed': 0}


def get_system_status() -> dict:
    """Always-on ⑦ 관측성: 큐/워커/유지보수/저장소 상태를 한 번에 집계.

    트레이 상태 표시·Wake-up Hook·대시보드가 공유하는 단일 소스.
    실패해도 절대 예외를 던지지 않는다(순수 부가 원칙).
    """
    import os as _os
    status = {
        'ok': True,
        'worker_running': _queue_worker_started,
        'queue': get_queue_stats(),
        'store': get_store_stats(),
        'maintenance': {
            'last_maintenance_ts': _last_maintenance_ts,
            'last_daily_ts': _last_daily_ts,
        },
        'db': {},
    }
    try:
        if _MEMORY_DB_PATH.exists():
            status['db']['path'] = str(_MEMORY_DB_PATH)
            status['db']['size_bytes'] = _MEMORY_DB_PATH.stat().st_size
        backup = _MEMORY_DB_PATH.with_name('memory.backup.db')
        status['db']['backup_exists'] = backup.exists()
        if backup.exists():
            st = backup.stat()
            status['db']['backup_size_bytes'] = st.st_size
            status['db']['backup_mtime'] = st.st_mtime
    except Exception:
        pass
    return status


def get_context_block(max_facts: int = 20) -> str:
    """채팅 시스템 프롬프트에 주입할 장기 기억 요약 텍스트. 실패 시 빈 문자열.

    내부 메타키('_' 접두사, 예: _last_chat_ts)는 주입에서 제외한다.
    """
    try:
        facts = list_facts(limit=max_facts)
        profile = get_profile()
        parts = []
        if profile:
            prof_items = [(k, v) for k, v in profile.items() if not str(k).startswith('_')]
            if prof_items:
                prof_lines = ', '.join(f'{k}: {v}' for k, v in prof_items[:30])
                parts.append(f'[사용자 프로필] {prof_lines}')
        if facts:
            fact_lines = '; '.join(f['content'] for f in facts[:max_facts])
            parts.append(f'[장기 기억] {fact_lines}')
        return '\n'.join(parts)
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Always-on Wake-up Hook: 마지막 채팅 시각 추적 + 장기 기억 주입
# ---------------------------------------------------------------------------
_WAKEUP_THRESHOLD_SECONDS = 8 * 3600.0  # 8시간


def record_chat_activity() -> dict:
    """채팅 시작 시 마지막 활동 시각을 갱신하고 Wake-up 여부를 판정.

    profile 테이블의 내부 메타키 '_last_chat_ts'를 사용한다.
    반환: {'is_wakeup': bool, 'elapsed_seconds': float, 'elapsed_hours': float}
    실패해도 절대 예외를 던지지 않는다.
    """
    now = time.time()
    is_wakeup = False
    elapsed = 0.0
    try:
        prof = get_profile()
        raw = prof.get('_last_chat_ts')
        if raw:
            try:
                last = float(raw)
                elapsed = now - last
                if elapsed >= _WAKEUP_THRESHOLD_SECONDS:
                    is_wakeup = True
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    try:
        set_profile('_last_chat_ts', str(now))
    except Exception:
        pass
    return {'is_wakeup': is_wakeup, 'elapsed_seconds': elapsed, 'elapsed_hours': round(elapsed / 3600.0, 1)}


def build_memory_prompt(max_facts: int = 20) -> str:
    """시스템 프롬프트에 주입할 장기 기억 블록을 만든다.

    매 채팅마다 마지막 활동 시각을 갱신하고, 8시간 이상 경과 후 재개 시
    'Wake-up' 강조 헤더를 붙여 이전 맥락·선호·약속을 자연스럽게 잇게 한다.
    기억이 하나도 없으면 빈 문자열. 실패해도 절대 예외를 던지지 않는다.
    """
    try:
        wakeup = record_chat_activity()
        block = get_context_block(max_facts=max_facts)
        if not block:
            return ''
        if wakeup.get('is_wakeup'):
            hours = wakeup.get('elapsed_hours', 0)
            header = (
                f"[DAON WAKE-UP — 마지막 대화로부터 약 {hours}시간 경과]\n"
                "오랜만에 대화를 재개합니다. 아래 장기 기억과 사용자 프로필을 다시 불러왔으니, "
                "이전의 맥락·선호·약속을 자연스럽게 이어가세요.\n\n"
            )
            return header + block
        return (
            "[DAON 장기 기억 — 이전 대화에서 학습한 사용자 정보]\n"
            "아래는 당신이 기억하고 있는 사용자 프로필과 핵심 사실입니다. 대화에 자연스럽게 활용하세요.\n\n"
            + block
        )
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# LLM 기반 추출
# ---------------------------------------------------------------------------
def _transcript(messages, max_turns: int = 20) -> str:
    try:
        lines = []
        for m in (messages or [])[-max_turns * 2:]:
            if not isinstance(m, dict):
                continue
            role = m.get('role', '')
            if role not in ('user', 'assistant'):
                continue
            content = m.get('content', '')
            if isinstance(content, list):
                content = ' '.join(
                    c.get('text', '') for c in content if isinstance(c, dict)
                )
            content = str(content or '').strip()
            if content:
                lines.append(f'{role}: {content}')
        return '\n'.join(lines)
    except Exception:
        return ''


def extract_and_store_facts(messages, source_session: Optional[str] = None) -> int:
    """대화에서 장기 기억 facts를 추출해 저장. 반환: 저장된 fact 수."""
    saved = 0
    try:
        transcript = _transcript(messages)
        if not transcript:
            return 0
        from api.dynamic.direct_calls import _call_direct
        prompt = (
            '다음 대화에서 사용자의 선호, 사실, 습관, 결정사항 등 장기적으로 기억할 '
            '가치 있는 정보를 추출하라. 각 항목은 짧고 독립적인 문장으로 작성하라. '
            '반드시 JSON 배열(문자열 요소)로만 응답하라. 예: ["사용자는 한국어를 선호한다"]. '
            '추출할 것이 없으면 빈 배열 []을 반환하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_direct(prompt)
        items = _parse_json_array(raw)
        for item in items:
            text = str(item).strip()
            if text and add_fact(text, 'general', source_session) is not None:
                saved += 1
    except Exception:
        pass
    return saved


def update_profile_from_messages(messages) -> int:
    """대화에서 사용자 프로필 key/value를 추출해 저장. 반환: 갱신된 키 수."""
    updated = 0
    try:
        transcript = _transcript(messages)
        if not transcript:
            return 0
        from api.dynamic.direct_calls import _call_direct
        prompt = (
            '다음 대화에서 사용자의 프로필 정보(이름, 직업, 선호 언어, 사용 도구, '
            '목표 등)를 key/value로 추출하라. 반드시 JSON 객체로만 응답하라. '
            '예: {"이름": "홍길동", "선호언어": "한국어"}. '
            '추출할 것이 없으면 빈 객체 {}를 반환하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_direct(prompt)
        obj = _parse_json_object(raw)
        for k, v in (obj or {}).items():
            if set_profile(str(k), str(v)):
                updated += 1
    except Exception:
        pass
    return updated


def summarize_session(messages, session_id: Optional[str] = None, title: Optional[str] = None) -> Optional[int]:
    """세션 요약을 생성해 저장. 반환: summary id."""
    try:
        transcript = _transcript(messages)
        if not transcript:
            return None
        from api.dynamic.direct_calls import _call_direct
        prompt = (
            '다음 대화를 3~5문장의 한국어로 간결하게 요약하라. '
            '주요 주제, 결정사항, 결과를 포함하라. 요약 텍스트만 출력하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_direct(prompt)
        summary = (raw or '').strip()
        if not summary:
            return None
        return add_summary(session_id or '', title or 'Untitled', summary)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# JSON 파싱 헬퍼
# ---------------------------------------------------------------------------
def _parse_json_array(raw: str) -> list:
    try:
        raw = _strip_code_fence(raw)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _parse_json_object(raw: str) -> dict:
    try:
        raw = _strip_code_fence(raw)
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _strip_code_fence(raw: str) -> str:
    try:
        s = (raw or '').strip()
        if s.startswith('```'):
            s = s.strip('`')
            if s.lower().startswith('json'):
                s = s[4:]
            s = s.strip()
        return s
    except Exception:
        return raw or ''


# ---------------------------------------------------------------------------
# 백그라운드 처리
# ---------------------------------------------------------------------------
# Job Queue (Always-on ②): 단일 상주 워커
# ---------------------------------------------------------------------------
def _queue_put(kind: str, payload: dict, priority: int = 0) -> Optional[int]:
    """큐에 작업을 등록한다. priority가 높을수록 먼저 처리. 실패 시 None."""
    try:
        _ensure_schema()
        now = time.time()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    "INSERT INTO job_queue "
                    "(kind, payload, status, priority, attempts, created_at, updated_at) "
                    "VALUES (?, ?, 'pending', ?, 0, ?, ?)",
                    (kind, json.dumps(payload, ensure_ascii=False), priority, now, now),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return None


def _queue_claim() -> Optional[dict]:
    """처리 가능한 작업 1건을 processing으로 전환해 반환. 없으면 None.

    우선순위(priority DESC) → 대기순(id ASC)으로 선택하며,
    next_retry_at이 미래인 재시도 대기 작업은 건너뛴다.
    """
    try:
        now = time.time()
        with _db_lock:
            conn = _connect()
            try:
                row = conn.execute(
                    "SELECT id, kind, payload, attempts FROM job_queue "
                    "WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at <= ?) "
                    "ORDER BY priority DESC, id ASC LIMIT 1",
                    (now,),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE job_queue SET status='processing', attempts=attempts+1, "
                    "started_at=?, updated_at=? WHERE id=?",
                    (now, now, row['id']),
                )
                conn.commit()
                return {
                    'id': row['id'],
                    'kind': row['kind'],
                    'payload': row['payload'],
                    'attempts': (row['attempts'] or 0) + 1,
                }
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return None


def _queue_finish(job_id: int, ok: bool, attempts: int, last_error: Optional[str] = None) -> None:
    """작업 완료/실패/재시도 상태를 기록하고 이력을 정리한다.

    - done: finished_at 기록.
    - failed(최대 시도 초과): finished_at + last_error 기록.
    - pending(재시도): next_retry_at을 지수 백오프로 설정, last_error 기록.
    """
    try:
        now = time.time()
        with _db_lock:
            conn = _connect()
            try:
                if ok:
                    status = 'done'
                    next_retry_at = None
                    finished_at = now
                elif attempts >= _QUEUE_MAX_ATTEMPTS:
                    status = 'failed'
                    next_retry_at = None
                    finished_at = now
                else:
                    status = 'pending'  # 재시도 대기
                    idx = min(max(attempts - 1, 0), len(_QUEUE_RETRY_BACKOFF) - 1)
                    next_retry_at = now + _QUEUE_RETRY_BACKOFF[idx]
                    finished_at = None
                conn.execute(
                    "UPDATE job_queue SET status=?, updated_at=?, finished_at=?, "
                    "next_retry_at=?, last_error=? WHERE id=?",
                    (status, now, finished_at, next_retry_at,
                     (last_error or '')[:500] or None, job_id),
                )
                # 완료/실패 이력이 보존 개수를 넘으면 오래된 것부터 삭제
                conn.execute(
                    "DELETE FROM job_queue WHERE status IN ('done','failed') AND id NOT IN ("
                    "  SELECT id FROM job_queue WHERE status IN ('done','failed') "
                    "  ORDER BY id DESC LIMIT ?"
                    ")",
                    (_QUEUE_KEEP_DONE,),
                )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        pass


def _dispatch_job(kind: str, payload_raw: Optional[str]) -> None:
    """kind별로 실제 처리 함수를 호출한다. 예외는 호출 쪽에서 잡는다."""
    payload = {}
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {}
    if kind == 'session':
        _process_session_sync(payload)
    # 향후 kind 추가: 'maintenance', 'backup' 등 (Always-on ①⑤⑥)


def _process_session_sync(payload: dict) -> None:
    """워커가 실행하는 세션 기억 추출. payload에서 snapshot을 복원한다."""
    snapshot = payload.get('messages') or []
    session_id = payload.get('session_id')
    title = payload.get('title')
    try:
        extract_and_store_facts(snapshot, session_id)
    except Exception:
        pass
    try:
        update_profile_from_messages(snapshot)
    except Exception:
        pass
    try:
        summarize_session(snapshot, session_id, title)
    except Exception:
        pass


def _run_maintenance() -> None:
    """⑤ 가벼운 정리: facts가 상한(_MAX_FACTS)을 넘으면 오래된 것부터 삭제."""
    try:
        with _db_lock:
            conn = _connect()
            try:
                conn.execute(
                    "DELETE FROM facts WHERE id NOT IN ("
                    "  SELECT id FROM facts ORDER BY id DESC LIMIT ?"
                    ")",
                    (_MAX_FACTS,),
                )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        pass


def _run_daily() -> None:
    """⑤⑥ 일일 정비: WAL 체크포인트 → 백업 복사 → VACUUM."""
    try:
        # 1) WAL을 본 DB에 반영(백업 정합성) + 용량 축소
        with _db_lock:
            conn = _connect()
            try:
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        # 2) ⑥ 백업: memory.db → memory.backup.db (잠금 밖에서 파일 복사)
        try:
            if _MEMORY_DB_PATH.exists():
                backup_path = _MEMORY_DB_PATH.with_name('memory.backup.db')
                shutil.copy2(str(_MEMORY_DB_PATH), str(backup_path))
        except Exception:
            pass
        # 3) ⑤ VACUUM으로 단편화 정리(단일 워커라 잠금 경쟁 없음)
        with _db_lock:
            conn = _connect()
            try:
                conn.execute('VACUUM')
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        pass


def _maybe_run_maintenance() -> None:
    """워커 루프에서 매 폴링 호출. 주기가 됐을 때만 정리/일일 정비를 실행."""
    global _last_maintenance_ts, _last_daily_ts
    now = time.time()
    if now - _last_maintenance_ts >= _MAINTENANCE_INTERVAL:
        _last_maintenance_ts = now
        try:
            _run_maintenance()
        except Exception:
            pass
    if now - _last_daily_ts >= _DAILY_INTERVAL:
        _last_daily_ts = now
        try:
            _run_daily()
        except Exception:
            pass


def _queue_worker_loop() -> None:
    """단일 상주 워커: 폴링 → claim → 처리 → 완료. 크래시 복구 포함."""
    # 크래시 복구: 이전 실행 중 processing에 멈춘 작업을 pending으로 되돌린다.
    try:
        with _db_lock:
            conn = _connect()
            try:
                conn.execute(
                    "UPDATE job_queue SET status='pending', updated_at=? WHERE status='processing'",
                    (time.time(),),
                )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        pass

    while True:
        _maybe_run_maintenance()
        job = None
        try:
            job = _queue_claim()
            if job is None:
                time.sleep(_QUEUE_POLL_INTERVAL)
                continue
            _dispatch_job(job['kind'], job['payload'])
            _queue_finish(job['id'], True, job['attempts'])
        except Exception as exc:
            if job is not None:
                try:
                    _queue_finish(job['id'], False, job['attempts'], str(exc))
                except Exception:
                    pass
            time.sleep(_QUEUE_POLL_INTERVAL)


def _ensure_queue_worker() -> None:
    """단일 워커 스레드를 1회만 시작한다(가드)."""
    global _queue_worker_started
    with _queue_worker_lock:
        if _queue_worker_started:
            return
        _queue_worker_started = True
        t = threading.Thread(target=_queue_worker_loop, daemon=True)
        t.start()


def process_session_async(session) -> None:
    """채팅 완료 후 facts/profile/summary 추출을 큐에 등록한다.

    session.messages 를 직렬화해 job_queue에 넣고, 단일 상주 워커가
    순차 처리한다(SQLite 쓰기 경쟁 제거). 실패해도 절대 예외를 던지지
    않는다(순수 부가 원칙).
    """
    try:
        messages = getattr(session, 'messages', None)
        if not messages:
            return
        # 사용자 턴이 2개 미만이면 추출 가치 낮음 → 스킵
        user_turns = sum(
            1 for m in messages if isinstance(m, dict) and m.get('role') == 'user'
        )
        if user_turns < 2:
            return
        session_id = getattr(session, 'id', None) or getattr(session, 'session_id', None)
        title = getattr(session, 'title', None)
        snapshot = list(messages)
        _queue_put('session', {
            'messages': snapshot,
            'session_id': session_id,
            'title': title,
        })
        _ensure_queue_worker()
    except Exception:
        pass
