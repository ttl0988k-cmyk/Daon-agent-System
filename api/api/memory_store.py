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
    STATE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent / 'data'

_MEMORY_DB_PATH = Path(STATE_DIR) / 'memory.db'
_db_lock = threading.Lock()
_MAX_FACTS = 500

# ── 외부 백업 (패치 1): 앱 제거(설치 폴더 삭제)에도 살아남는 위치 ──
# 같은 폴더의 memory.backup.db는 설치 폴더와 함께 사라지므로,
# 사용자 문서 폴더에 사본을 하나 더 둔다. 실패해도 조용히 건너뛴다.
try:
    _EXTERNAL_BACKUP_DIR = Path.home() / 'Documents' / 'DAON-backup'
except Exception:
    _EXTERNAL_BACKUP_DIR = None

# ── 스키마 버전 (패치 2): PRAGMA user_version 기반 마이그레이션 토대 ──
# 향후 새 테이블/컬럼 추가 시 _SCHEMA_VERSION을 올리고 _ensure_schema에서 분기한다.
# v2 (Phase 6): session_artifacts + fact_artifacts 추가 (인과 그래프)
_SCHEMA_VERSION = 2

# ── Phase 1-A: 프로필 key 정규화 ──
# LLM 추출 시 반드시 아래 key 중 하나를 사용하도록 제한한다.
CANONICAL_PROFILE_KEYS = {
    'name': '이름',
    'occupation': '직업',
    'preferred_language': '선호 언어',
    'workspace': '작업 디렉토리',
    'tools': '사용 도구',
    'agents': '관련 에이전트',
    'goals': '목표',
    'style': '대화 스타일',
    'notes': '메모',
}

# 기존 분산 key → 정규 key 매핑 (마이그레이션용)
PROFILE_KEY_ALIASES = {
    '이름': 'name', 'name': 'name', '이름(호칭)': 'name',
    '직업': 'occupation', 'occupation': 'occupation', '하는 일': 'occupation',
    '선호언어': 'preferred_language', '선호 언어': 'preferred_language',
    '사용언어': 'preferred_language', '언어': 'preferred_language',
    'preferred_language': 'preferred_language', 'language': 'preferred_language',
    '작업 디렉토리': 'workspace', 'workspace': 'workspace', '작업디렉토리': 'workspace',
    '사용 도구': 'tools', 'tools': 'tools', '도구': 'tools',
    '관련 에이전트': 'agents', 'agents': 'agents', '에이전트': 'agents',
    '목표': 'goals', 'goals': 'goals', '목표사항': 'goals',
    '대화 스타일': 'style', 'style': 'style', '스타일': 'style',
    '메모': 'notes', 'notes': 'notes', '기타': 'notes',
}

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
                CREATE TABLE IF NOT EXISTS agent_inbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    cc TEXT,
                    body TEXT NOT NULL,
                    run_id TEXT,
                    read_flag INTEGER DEFAULT 0,
                    created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_inbox_recipient ON agent_inbox(recipient, read_flag);
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
            # Phase 2-A: facts 계보/신뢰도/사용 추적 컬럼
            for _ddl in (
                "ALTER TABLE facts ADD COLUMN derived_from TEXT",
                "ALTER TABLE facts ADD COLUMN confidence REAL DEFAULT 1.0",
                "ALTER TABLE facts ADD COLUMN superseded_by INTEGER DEFAULT NULL",
                "ALTER TABLE facts ADD COLUMN use_count INTEGER DEFAULT 0",
                "ALTER TABLE facts ADD COLUMN last_used_at TEXT",
            ):
                try:
                    conn.execute(_ddl)
                except Exception:
                    pass
            # Phase 1-C: summaries 세션당 1건 — 중복 정리 후 UNIQUE 인덱스
            try:
                conn.execute(
                    "DELETE FROM summaries WHERE id NOT IN ("
                    "  SELECT MAX(id) FROM summaries GROUP BY session_id"
                    ")"
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_summaries_session "
                    "ON summaries(session_id)"
                )
            except Exception:
                pass
            # Phase 2-A: fact_usage (주입 기록)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS fact_usage ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  fact_id INTEGER NOT NULL,"
                "  session_id TEXT NOT NULL,"
                "  injected_at TEXT DEFAULT (datetime('now')),"
                "  FOREIGN KEY (fact_id) REFERENCES facts(id)"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_usage_fact ON fact_usage(fact_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_usage_session ON fact_usage(session_id)")
            # Phase 4-C: 정제 이력
            conn.execute(
                "CREATE TABLE IF NOT EXISTS refine_log ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  action TEXT NOT NULL,"
                "  fact_ids TEXT,"
                "  detail TEXT,"
                "  created_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
            # Phase 5-B: 재검토 큐
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_review ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  kind TEXT NOT NULL,"
                "  fact_ids TEXT,"
                "  suggestion TEXT,"
                "  status TEXT DEFAULT 'pending',"
                "  created_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
            # Phase 6-A: 세션 산출물 (도구 호출로 만들어진 것)
            # UNIQUE(session_id, type, path_normalized): 같은 파일 재편집 시 1행 유지.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_artifacts ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  session_id TEXT NOT NULL,"
                "  artifact_type TEXT NOT NULL,"
                "  path TEXT NOT NULL,"
                "  path_normalized TEXT NOT NULL DEFAULT '',"
                "  tool_name TEXT,"
                "  created_at TEXT DEFAULT (datetime('now')),"
                "  UNIQUE(session_id, artifact_type, path_normalized)"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_session ON session_artifacts(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_path ON session_artifacts(path_normalized)")
            # Phase 6-B: fact → 산출물 명시적 엣지 (인과 그래프 본체)
            # confidence: 0.9=직접 영향(파일 생성 턴에 주입), 0.4=간접(같은 세션 이전 턴)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS fact_artifacts ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  fact_id INTEGER NOT NULL,"
                "  artifact_id INTEGER NOT NULL,"
                "  confidence REAL DEFAULT 0.4,"
                "  linked_at TEXT DEFAULT (datetime('now')),"
                "  UNIQUE(fact_id, artifact_id),"
                "  FOREIGN KEY (fact_id) REFERENCES facts(id),"
                "  FOREIGN KEY (artifact_id) REFERENCES session_artifacts(id)"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_artifacts_fact ON fact_artifacts(fact_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_artifacts_artifact ON fact_artifacts(artifact_id)")
            conn.commit()
            # 패치 2: 스키마 버전 기록 (PRAGMA user_version, 멱등).
            try:
                conn.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')
            except Exception:
                pass
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
_FACT_COLUMNS = (
    'id, content, category, source_session, created_at, '
    'derived_from, confidence, superseded_by, use_count, last_used_at'
)


def list_facts(limit: int = 100, category: Optional[str] = None,
               include_superseded: bool = False) -> list:
    """facts 목록. Phase 2-A: 계보/신뢰도 컬럼 포함.
    include_superseded=False면 대체된 fact 제외."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                where = '' if include_superseded else ' WHERE superseded_by IS NULL'
                params: list = []
                if category:
                    where += (' AND' if where else ' WHERE') + ' category=?'
                    params.append(category)
                params.append(int(limit))
                rows = conn.execute(
                    f'SELECT {_FACT_COLUMNS} FROM facts{where} ORDER BY id DESC LIMIT ?',
                    params,
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
    except Exception:
        return []


def add_fact(content: str, category: str = 'general', source_session: Optional[str] = None,
             derived_from: Optional[str] = None, skip_dedup: bool = False) -> Optional[int]:
    """fact 추가. Phase 1-B: LLM 의미 중복 검사 포함.

    - 완전 일치: 스킵 (None 반환)
    - 의미 중복: 기존 fact 갱신 후 기존 id 반환
    - 모순: 기존 fact에 superseded_by 설정, 새 fact INSERT
    - 신규: INSERT

    데드락 방지: 느린 LLM 중복 검사(_llm_check_duplicate)와 재검토 큐 등록(_create_review)은
    전역 _db_lock 밖에서 실행한다. 락은 순수 DB 읽기/쓰기 구간만 보호한다.
    (이전: 락 안에서 LLM 호출 → 최대 1800초 점유 → 두 번째 대화의 build_memory_prompt가
     record_chat_activity→set_profile에서 _db_lock 무한 대기 → 채팅 hang)
    """
    try:
        content = (content or '').strip()
        if not content:
            return None
        _ensure_schema()

        # ── 1단계: 락 안에서 DB 읽기만 (완전 일치 검사 + 기존 facts 스냅샷) ──
        existing = []
        with _db_lock:
            conn = _connect()
            try:
                dup = conn.execute(
                    'SELECT id FROM facts WHERE content=? LIMIT 1', (content,)
                ).fetchone()
                if dup:
                    return None
                if not skip_dedup:
                    existing = [dict(r) for r in conn.execute(
                        'SELECT id, content FROM facts '
                        'WHERE superseded_by IS NULL ORDER BY id DESC LIMIT 50'
                    ).fetchall()]
            finally:
                conn.close()

        # ── 2단계: 락 밖에서 느린 LLM 중복 검사 (데드락 방지 핵심) ──
        dup_info = None
        if existing and not skip_dedup:
            dup_info = _llm_check_duplicate(content, existing)

        # ── 3단계: 락 안에서 DB 쓰기만 (재검사 + 갱신/INSERT + 상한 정리) ──
        review_payload = None
        new_id = None
        with _db_lock:
            conn = _connect()
            try:
                # 재검사: 2단계 사이 다른 스레드가 동일 fact를 넣었을 수 있음
                dup = conn.execute(
                    'SELECT id FROM facts WHERE content=? LIMIT 1', (content,)
                ).fetchone()
                if dup:
                    return None

                if dup_info and dup_info.get('action') == 'duplicate':
                    # 의미 중복: 기존 fact 내용 갱신
                    conn.execute(
                        "UPDATE facts SET content=?, last_used_at=datetime('now') WHERE id=?",
                        (content, dup_info['fact_id']),
                    )
                    conn.commit()
                    return dup_info['fact_id']

                if dup_info and dup_info.get('action') == 'contradiction':
                    # 모순: 기존 fact 신뢰도 하향
                    conn.execute(
                        'UPDATE facts SET confidence=0.3 WHERE id=?',
                        (dup_info['fact_id'],),
                    )
                    derived_from = f"fact:{dup_info['fact_id']}"

                cur = conn.execute(
                    'INSERT INTO facts (content, category, source_session, derived_from) '
                    'VALUES (?,?,?,?)',
                    (content, category or 'general', source_session, derived_from),
                )
                conn.commit()
                new_id = cur.lastrowid

                # 모순인 경우 기존 fact에 superseded_by 설정 (재검토 큐 등록은 락 밖에서)
                if dup_info and dup_info.get('action') == 'contradiction':
                    conn.execute(
                        'UPDATE facts SET superseded_by=? WHERE id=?',
                        (new_id, dup_info['fact_id']),
                    )
                    conn.commit()
                    review_payload = (dup_info['fact_id'], new_id)

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
            finally:
                conn.close()

        # ── 4단계: 락 밖에서 재검토 큐 등록 (_create_review는 자체 락 사용 → 재귀 교착 방지) ──
        if review_payload:
            try:
                _create_review(
                    'contradiction',
                    [review_payload[0], review_payload[1]],
                    f"기존 fact#{review_payload[0]} → 신규 fact#{review_payload[1]} 대체 (대화 중 자동 감지)",
                )
            except Exception:
                pass

        return new_id
    except Exception:
        return None


def delete_fact(fact_id) -> dict:
    """fact 삭제 + Phase 2-C: 영향 범위(역링크) 보고 + Phase 6-C: 산출물 영향.

    반환: {'ok': bool, 'impact': {'sessions': [...], 'derived_facts': [...], 'usage_count': int,
            'artifacts': [...], 'direct_artifacts': [...], 'indirect_artifacts': [...]}}
    기존 bool 호환: truthy/falsy로 동작.
    """
    try:
        _ensure_schema()
        impact = {'sessions': [], 'derived_facts': [], 'usage_count': 0,
                  'artifacts': [], 'direct_artifacts': [], 'indirect_artifacts': []}
        with _db_lock:
            conn = _connect()
            try:
                fid = int(fact_id)
                # 주입 기록 조회
                usage_rows = conn.execute(
                    'SELECT DISTINCT session_id FROM fact_usage WHERE fact_id=?', (fid,)
                ).fetchall()
                impact['sessions'] = [r['session_id'] for r in usage_rows if r['session_id']]
                impact['usage_count'] = len(impact['sessions'])
                # 파생 fact 조회
                derived_rows = conn.execute(
                    "SELECT id, content FROM facts WHERE derived_from LIKE ?",
                    (f'%,fact:{fid},%',),
                ).fetchall()
                derived_rows2 = conn.execute(
                    "SELECT id, content FROM facts WHERE derived_from = ?",
                    (f'fact:{fid}',),
                ).fetchall()
                seen = set()
                for r in list(derived_rows) + list(derived_rows2):
                    if r['id'] not in seen:
                        impact['derived_facts'].append(dict(r))
                        seen.add(r['id'])
                # Phase 6-C: 영향 산출물 조회 (confidence로 직접/간접 분리)
                try:
                    artifact_rows = conn.execute(
                        "SELECT sa.path, sa.path_normalized, sa.artifact_type, sa.tool_name, "
                        "sa.created_at, fa.confidence "
                        "FROM fact_artifacts fa "
                        "JOIN session_artifacts sa ON sa.id = fa.artifact_id "
                        "WHERE fa.fact_id=?", (fid,),
                    ).fetchall()
                    for r in artifact_rows:
                        d = dict(r)
                        impact['artifacts'].append(d)
                        if (d.get('confidence') or 0.0) >= 0.7:
                            impact['direct_artifacts'].append(d)
                        else:
                            impact['indirect_artifacts'].append(d)
                except Exception:
                    pass
                # 삭제 (Phase 6: fact_artifacts 엣지 정리, session_artifacts는 세션 역사로 보존)
                conn.execute('DELETE FROM facts WHERE id=?', (fid,))
                conn.execute('DELETE FROM fact_usage WHERE fact_id=?', (fid,))
                try:
                    conn.execute('DELETE FROM fact_artifacts WHERE fact_id=?', (fid,))
                except Exception:
                    pass
                conn.commit()
                return {'ok': True, 'impact': impact}
            finally:
                conn.close()
    except Exception:
        return {'ok': False, 'impact': {}}


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
    """Phase 1-C: 세션당 1건 — INSERT OR REPLACE (session_id UNIQUE)."""
    try:
        summary = (summary or '').strip()
        if not summary:
            return None
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    'INSERT OR REPLACE INTO summaries (session_id, title, summary, created_at) '
                    "VALUES (?,?,?,datetime('now'))",
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
def get_store_stats(lock_timeout: Optional[float] = None) -> dict:
    """저장소 통계. lock_timeout 지정 시 락 경합 중이면 기다리지 않고 기본값 반환 —
    트레이/대시보드 폴링이 에이전트의 무거운 DB 작업에 끌려가지 않도록 한다."""
    lock_acquired = False
    try:
        _ensure_schema()
        if lock_timeout is not None:
            lock_acquired = _db_lock.acquire(timeout=lock_timeout)
            if not lock_acquired:
                return {'facts': 0, 'profile': 0, 'summaries': 0}
        else:
            _db_lock.acquire()
            lock_acquired = True
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
    finally:
        if lock_acquired:
            _db_lock.release()


def get_queue_stats(lock_timeout: Optional[float] = None) -> dict:
    """job_queue의 상태별 개수 집계 (pending/processing/done/failed).

    lock_timeout 지정 시 락 경합 중이면 기다리지 않고 기본값 반환.
    """
    lock_acquired = False
    try:
        _ensure_schema()
        if lock_timeout is not None:
            lock_acquired = _db_lock.acquire(timeout=lock_timeout)
            if not lock_acquired:
                return {'pending': 0, 'processing': 0, 'done': 0, 'failed': 0}
        else:
            _db_lock.acquire()
            lock_acquired = True
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
    finally:
        if lock_acquired:
            _db_lock.release()


# ── system/status 캐시 (2026-08-28) ──
# 트레이가 10초마다 /api/system/status를 폴링하는데, 이 함수가 전역 _db_lock을
# 3번 획득하므로 에이전트가 무거운 DB 작업 중이면 락 대기로 4~8초 블로킹됐다.
# 그 결과 트레이 툴팁이 "서버정상"과 "서버 오류"를 반복했다. 5초 캐시 + 락
# 타임아웃(1초)으로 폴링이 락 경합의 영향을 받지 않도록 한다.
_sys_status_cache: dict = {'data': None, 'ts': 0.0}
_SYS_STATUS_TTL = 5.0


def get_system_status() -> dict:
    """Always-on ⑦ 관측성: 큐/워커/유지보수/저장소 상태를 한 번에 집계.

    트레이 상태 표시·Wake-up Hook·대시보드가 공유하는 단일 소스.
    실패해도 절대 예외를 던지지 않는다(순수 부가 원칙).
    """
    import os as _os
    now = time.time()
    cached = _sys_status_cache.get('data')
    if cached is not None and (now - _sys_status_cache['ts']) < _SYS_STATUS_TTL:
        return cached
    status = {
        'ok': True,
        'worker_running': _queue_worker_started,
        'queue': get_queue_stats(lock_timeout=1.0),
        'store': get_store_stats(lock_timeout=1.0),
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
        # 패치 1: 외부 백업 상태 노출
        if _EXTERNAL_BACKUP_DIR is not None:
            ext_backup = _EXTERNAL_BACKUP_DIR / 'memory.db'
            status['db']['external_backup_exists'] = ext_backup.exists()
            if ext_backup.exists():
                ext_st = ext_backup.stat()
                status['db']['external_backup_path'] = str(ext_backup)
                status['db']['external_backup_size_bytes'] = ext_st.st_size
                status['db']['external_backup_mtime'] = ext_st.st_mtime
        # 패치 2: 스키마 버전 노출
        status['db']['schema_version'] = _SCHEMA_VERSION
    except Exception:
        pass
    # Phase 6: 인과 그래프 통계 (별도 try — 테이블 미존재 시에도 무영향)
    try:
        _ensure_schema()
        if _db_lock.acquire(timeout=1.0):
            try:
                conn = _connect()
                try:
                    a_cnt = conn.execute('SELECT COUNT(*) AS c FROM session_artifacts').fetchone()['c']
                    e_cnt = conn.execute('SELECT COUNT(*) AS c FROM fact_artifacts').fetchone()['c']
                    status['graph'] = {'artifacts_count': a_cnt, 'fact_edges_count': e_cnt}
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass
            finally:
                _db_lock.release()
    except Exception:
        pass
    _sys_status_cache['data'] = status
    _sys_status_cache['ts'] = now
    return status


def _normalize_profile_key(raw_key: str) -> Optional[str]:
    """Phase 1-A: 임의 key → 정규 key 매핑. 매핑 불가 시 None."""
    k = (raw_key or '').strip().lower()
    if not k:
        return None
    # 내부 메타키는 그대로 유지
    if k.startswith('_'):
        return raw_key.strip()
    # 별칭 매핑
    canonical = PROFILE_KEY_ALIASES.get(k)
    if canonical:
        return canonical
    # 정규 key 직접 매칭
    if k in CANONICAL_PROFILE_KEYS:
        return k
    return None


def _fact_score(fact: dict, query_keywords: list, now_ts: float) -> float:
    """Phase 3-B: fact의 주입 우선순위 점수 계산."""
    score = 0.0
    # 1) 관련성: 현재 대화 키워드와 fact 내용의 겹침
    content = (fact.get('content') or '').lower()
    keyword_hits = sum(1 for kw in query_keywords if kw in content)
    score += keyword_hits * 10.0
    # 2) 사용 빈도: 자주 주입된 fact = 검증된 fact
    score += min((fact.get('use_count') or 0) * 0.5, 5.0)
    # 3) 최신성: 최근 fact 가중치
    try:
        from datetime import datetime
        created = datetime.fromisoformat(fact.get('created_at', ''))
        age_days = max(0, (now_ts - created.timestamp()) / 86400.0)
        score += max(0, 10.0 - age_days * 0.1)
    except Exception:
        score += 5.0  # 파싱 실패 시 중간값
    # 4) 신뢰도: 모순 플래그가 있으면 감점
    score *= (fact.get('confidence') or 1.0)
    # 5) 대체됨: superseded_by가 있으면 주입 제외
    if fact.get('superseded_by'):
        return -1.0
    return score


def _record_fact_usage(fact_ids: list, session_id: str) -> None:
    """Phase 2-B: 주입된 fact id를 fact_usage에 기록 + use_count 갱신."""
    if not fact_ids:
        return
    try:
        with _db_lock:
            conn = _connect()
            try:
                for fid in fact_ids:
                    conn.execute(
                        'INSERT INTO fact_usage (fact_id, session_id) VALUES (?,?)',
                        (fid, session_id or ''),
                    )
                conn.execute(
                    "UPDATE facts SET use_count = use_count + 1, last_used_at = datetime('now') "
                    f"WHERE id IN ({','.join('?' * len(fact_ids))})",
                    fact_ids,
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


# ── Phase 6: 인과 그래프 — 주입 fact 추적 + 산출물 기록 ──
# 엣지 신뢰도: 산출물 생성 턴에 주입된 fact = 직접(0.9),
# 같은 세션 이전 턴에만 주입된 fact = 간접(0.4).
_EDGE_CONF_DIRECT = 0.9
_EDGE_CONF_INDIRECT = 0.4

# 세션별 최근 턴에 주입된 fact id (인메모리, 최근 100세션만 유지)
_LAST_INJECTED_FACTS: dict = {}
_LAST_INJECTED_LOCK = threading.Lock()


def get_last_injected_fact_ids(session_id: str) -> list:
    """Phase 6: 해당 세션의 최근 턴에 주입된 fact id 목록 사본 반환.
    실패 시 빈 리스트. 절대 예외를 던지지 않는다."""
    try:
        with _LAST_INJECTED_LOCK:
            return list(_LAST_INJECTED_FACTS.get(session_id or '', []))
    except Exception:
        return []


def _normalize_artifact_path(path: str, workspace: str) -> str:
    """Phase 6-A: 산출물 경로 정규화 (워크스페이스 기준).
    상대/절대 혼재 → workspace 기준 resolve() → 워크스페이스 상대 POSIX 문자열.
    워크스페이스 밖 파일은 절대 경로 문자열로 폴백. 실패 시 원본 반환."""
    try:
        p = Path(path)
        ws = Path(workspace) if workspace else None
        if not p.is_absolute() and ws is not None:
            p = ws / p
        p = p.resolve()
        if ws is not None:
            try:
                return p.relative_to(ws.resolve()).as_posix()
            except ValueError:
                pass
        return p.as_posix()
    except Exception:
        return path or ''


def record_session_artifact(session_id: str, artifact_type: str, path: str,
                            tool_name: str = '', workspace: str = '',
                            direct_fact_ids: list = None) -> Optional[int]:
    """Phase 6-A/B: 세션 산출물 기록 + 주입 fact들과 자동 엣지 생성.

    - 산출물 행: UNIQUE(session_id, type, path_normalized)로 재편집 시 중복 방지.
    - 엣지: 이 세션에 주입됐던 fact(fact_usage) ↔ 이 산출물.
      direct_fact_ids(이번 턴 주입)면 confidence 0.9, 나머지 0.4.
      기존 엣지는 confidence를 MAX로만 승격(강등 없음).
    반환: artifact id (실패/무시 시 None). 절대 예외를 던지지 않는다(순수 부가).
    """
    try:
        session_id = (session_id or '').strip()
        path = (path or '').strip()
        if not session_id or not path:
            return None
        path_norm = _normalize_artifact_path(path, workspace)
        artifact_type = (artifact_type or 'file').strip() or 'file'
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO session_artifacts "
                    "(session_id, artifact_type, path, path_normalized, tool_name) "
                    "VALUES (?,?,?,?,?)",
                    (session_id, artifact_type, path, path_norm, tool_name or ''),
                )
                artifact_id = cur.lastrowid
                if not artifact_id:
                    # 재편집 등 INSERT가 무시된 경우: 기존 id 조회
                    row = conn.execute(
                        "SELECT id FROM session_artifacts "
                        "WHERE session_id=? AND artifact_type=? AND path_normalized=?",
                        (session_id, artifact_type, path_norm),
                    ).fetchone()
                    artifact_id = row['id'] if row else None
                if artifact_id:
                    # 6-B: 이 세션에 주입됐던 fact들과 엣지 생성
                    injected = conn.execute(
                        "SELECT DISTINCT fact_id FROM fact_usage WHERE session_id=?",
                        (session_id,),
                    ).fetchall()
                    direct_set = set(int(x) for x in (direct_fact_ids or []) if x is not None)
                    for r in injected:
                        fid = r['fact_id']
                        conf = _EDGE_CONF_DIRECT if fid in direct_set else _EDGE_CONF_INDIRECT
                        conn.execute(
                            "INSERT INTO fact_artifacts (fact_id, artifact_id, confidence) "
                            "VALUES (?,?,?) "
                            "ON CONFLICT(fact_id, artifact_id) DO UPDATE SET "
                            "confidence = MAX(confidence, excluded.confidence)",
                            (fid, artifact_id, conf),
                        )
                conn.commit()
                return artifact_id
            finally:
                conn.close()
    except Exception:
        return None


def get_context_block(max_facts: int = 3, query_text: str = '',
                      session_id: str = '') -> str:
    """Phase 3: 관련성 랭킹 주입. 실패 시 빈 문자열.

    query_text에서 키워드를 추출해 facts를 랭킹 정렬 후 상위 max_facts건 주입.
    주입 시 fact_usage에 기록(Phase 2-B).
    Token 절감을 위해 max_facts 기본값을 3으로 제한 (Top 3 핵심 fact만).
    내부 메타키('_' 접두사)는 주입에서 제외.
    """
    try:
        # 키워드 추출 (간단한 공백/쉼표 분리)
        query_keywords = []
        if query_text:
            import re as _re2
            tokens = _re2.findall(r'[\w가-힣]+', query_text.lower())
            query_keywords = [t for t in tokens if len(t) >= 2][:20]

        # 전체 facts 로드 (500건 이하라 가능)
        all_facts = list_facts(limit=500, include_superseded=False)
        profile = get_profile()

        # 랭킹 정렬
        now_ts = time.time()
        if query_keywords:
            scored = [(f, _fact_score(f, query_keywords, now_ts)) for f in all_facts]
            scored.sort(key=lambda x: x[1], reverse=True)
        else:
            # 폴백: 최근 순서 (키워드 추출 실패 시)
            scored = [(f, 0.0) for f in all_facts]

        # Phase 3-D: 카테고리별 쿼터
        selected = []
        cat_counts = {}
        cat_quota = {'general': max(3, max_facts - 1), 'preference': 2, 'project': 1}
        for f, sc in scored:
            if len(selected) >= max_facts:
                break
            cat = f.get('category', 'general')
            quota = cat_quota.get(cat, max_facts)
            if cat_counts.get(cat, 0) >= quota:
                continue
            selected.append(f)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # 사용 기록 (Phase 2-B)
        injected_ids = [f['id'] for f in selected]
        if injected_ids and session_id:
            _record_fact_usage(injected_ids, session_id)
        # Phase 6: 이번 턴 주입 fact id 보관 (인과 그래프 직접 영향 후보)
        if session_id:
            try:
                with _LAST_INJECTED_LOCK:
                    _LAST_INJECTED_FACTS[session_id] = list(injected_ids)
                    if len(_LAST_INJECTED_FACTS) > 100:
                        for _old_sid in list(_LAST_INJECTED_FACTS.keys())[:-100]:
                            _LAST_INJECTED_FACTS.pop(_old_sid, None)
            except Exception:
                pass

        # 텍스트 조립
        parts = []
        if profile:
            prof_items = [(k, v) for k, v in profile.items() if not str(k).startswith('_')]
            if prof_items:
                prof_lines = ', '.join(f'{k}: {v}' for k, v in prof_items[:30])
                parts.append(f'[사용자 프로필] {prof_lines}')
        if selected:
            fact_lines = '; '.join(f['content'] for f in selected)
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


def build_memory_prompt(max_facts: int = 3, query_text: str = '',
                        session_id: str = '') -> str:
    """시스템 프롬프트에 주입할 장기 기억 블록을 만든다.

    Phase 3: query_text(현재 사용자 메시지)로 관련성 랭킹 주입.
    매 채팅마다 마지막 활동 시각을 갱신하고, 8시간 이상 경과 후 재개 시
    'Wake-up' 강조 헤더를 붙여 이전 맥락·선호·약속을 자연스럽게 잇게 한다.
    기억이 하나도 없으면 빈 문자열. 실패해도 절대 예외를 던지지 않는다.
    """
    try:
        wakeup = record_chat_activity()
        block = get_context_block(max_facts=max_facts, query_text=query_text,
                                  session_id=session_id)
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
# 에이전트 간 메시징 (to/cc/inbox) — Dynamic Harness + 일반 채팅 공용 버스
# ---------------------------------------------------------------------------
# 정적 페르소나(빌/셜록/프라다/토니)와 Dynamic Harness 노드가 서로 메시지를
# 주고받을 수 있게 하는 SQLite 기반 공용 메시지함. 인메모리 state와 달리
# 세션/채팅/배치를 넘어 영속된다.
import re as _re

# [MSG to=X task=Y priority=Z context=a,b,c]Body[/MSG] 형태의 블록을 파싱.
# 구조화된 헤더(to, task, priority, context)를 파싱하고 본문을 분리.
# 예: [MSG to=Developer task=implement_auth priority=high context=spec.md,issue_102]
#       spec.md를 읽고 인증을 구현해줘
#     [/MSG]
_MSG_BLOCK_RE = _re.compile(
    r'\[MSG\s+to=([^\]\s]+)'
    r'(?:\s+task=([^\]\s]+))?'
    r'(?:\s+priority=([^\]\s]+))?'
    r'(?:\s+context=([^\]]+?))?'
    r'\](.*?)\[/MSG\]',
    _re.DOTALL | _re.IGNORECASE,
)


def send_agent_message(sender: str, recipient: str, body: str,
                       cc: Optional[str] = None, run_id: Optional[str] = None) -> Optional[int]:
    """에이전트 메시지를 발송한다. cc는 콤마 구분 다중 수신자.
    recipient와 각 cc 수신자에게 각각 한 행씩 배달된다. 반환: 첫 행 id."""
    try:
        sender = (sender or '').strip() or 'unknown'
        body = (body or '').strip()
        if not recipient or not body:
            return None
        targets = [recipient.strip()]
        if cc:
            for c in str(cc).split(','):
                c = c.strip()
                if c and c not in targets:
                    targets.append(c)
        now = time.time()
        first_id = None
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                for t in targets:
                    cur = conn.execute(
                        'INSERT INTO agent_inbox (sender, recipient, cc, body, run_id, read_flag, created_at) '
                        'VALUES (?, ?, ?, ?, ?, 0, ?)',
                        (sender, t, cc, body, run_id, now),
                    )
                    if first_id is None:
                        first_id = cur.lastrowid
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        return first_id
    except Exception:
        return None


def get_agent_inbox(recipient: str, unread_only: bool = False, limit: int = 50) -> list:
    """특정 에이전트의 받은 메시지함을 최신순으로 반환. 실패 시 빈 리스트."""
    try:
        recipient = (recipient or '').strip()
        if not recipient:
            return []
        _ensure_schema()
        q = 'SELECT id, sender, recipient, cc, body, run_id, read_flag, created_at FROM agent_inbox WHERE recipient = ?'
        if unread_only:
            q += ' AND read_flag = 0'
        q += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(q, (recipient, int(limit))).fetchall()
                return [dict(r) for r in rows]
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return []


def mark_inbox_read(recipient: str, up_to_id: Optional[int] = None) -> int:
    """받은 메시지를 읽음 처리. up_to_id가 있으면 그 id 이하만. 반환: 처리 건수."""
    try:
        recipient = (recipient or '').strip()
        if not recipient:
            return 0
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                if up_to_id:
                    cur = conn.execute(
                        'UPDATE agent_inbox SET read_flag = 1 WHERE recipient = ? AND id <= ? AND read_flag = 0',
                        (recipient, int(up_to_id)),
                    )
                else:
                    cur = conn.execute(
                        'UPDATE agent_inbox SET read_flag = 1 WHERE recipient = ? AND read_flag = 0',
                        (recipient,),
                    )
                conn.commit()
                return cur.rowcount or 0
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return 0


def format_inbox_prompt(recipient: str, limit: int = 20, mark_read: bool = True) -> str:
    """시스템 프롬프트에 주입할 '받은 메시지함' 텍스트. 읽지 않은 메시지만.

    구조화된 inbox 포맷 — 토큰 절감을 위해 메시지 헤더(to, from, task, context,
    priority)와 본문을 분리해 표시. LLM이 파일 참조(context)를 직접 처리한다.
    없으면 빈 문자열. mark_read=True면 주입 후 해당 메시지를 읽음 처리."""
    try:
        msgs = get_agent_inbox(recipient, unread_only=True, limit=limit)
        if not msgs:
            return ''
        msgs = list(reversed(msgs))
        max_id = 0
        parts = [f"=== AGENT MESSAGES ({len(msgs)} unread for {recipient}) ==="]

        for m in msgs:
            sender = m.get('sender', 'unknown')
            body = m.get('body', '')
            # Parse structured header from body (set by parse_and_dispatch_messages)
            header_lines = []
            content_lines = []
            in_content = False
            for line in body.split('\n'):
                stripped = line.strip()
                if stripped == '---':
                    in_content = True
                    continue
                if in_content:
                    content_lines.append(line)
                else:
                    header_lines.append(line)
            header = '\n'.join(header_lines)
            content = '\n'.join(content_lines)
            if body and not header_lines:
                # Fallback for legacy unformatted messages
                header = f"from: {sender}"
                content = body

            msg_block = [f"--- From: {sender} ---"]
            if header:
                msg_block.append(header)
            if content:
                msg_block.append(f"---\n{content}")
            parts.append('\n'.join(msg_block))
            if m.get('id', 0) > max_id:
                max_id = m['id']

        result = '\n\n'.join(parts)
        if mark_read and max_id:
            mark_inbox_read(recipient, up_to_id=max_id)
        return result
    except Exception:
        return ''


def parse_and_dispatch_messages(sender: str, text: str, run_id: Optional[str] = None) -> tuple:
    """에이전트 출력 텍스트에서 [MSG to=X task=Y priority=Z context=a,b]본문[/MSG]
    블록을 추출해 발송하고, 구조화된 수신함 포맷으로 변환 후 발송 건수를 반환.

    새 포맷 예:
      [MSG to=Developer task=implement_auth priority=high context=spec.md,issue_102]
      spec.md를 읽고 인증 기능을 구현해줘.
      [/MSG]

    수신 에이전트의 inbox는 구조화된 헤더(to, from, task, context, priority)와
    본문을 분리해 표시하므로 토큰을 절감하고 LLM이 파일 참조를 직접 처리한다."""
    try:
        if not text:
            return (text or '', 0)
        sent = 0

        def _sub(match):
            nonlocal sent
            to = match.group(1)
            task = match.group(2) or ''
            priority = match.group(3) or ''
            context = match.group(4) or ''
            body = (match.group(5) or '').strip()

            # 구조화된 헤더를 본문에 선행시켜 수신 에이전트가 파싱 가능하도록 함.
            # 수신 함 inbox rendering은 이 헤더를 추출해 깔끔하게 표시한다.
            header_parts = [f"from: {sender}", f"to: {to}"]
            if task:
                header_parts.append(f"task: {task}")
            if priority:
                header_parts.append(f"priority: {priority}")
            if context:
                ctx_list = [c.strip() for c in context.split(',') if c.strip()]
                if ctx_list:
                    header_parts.append("context:")
                    for c in ctx_list:
                        header_parts.append(f"  - {c}")
            structured_body = '\n'.join(header_parts)
            if body:
                structured_body += '\n---\n' + body

            if to and structured_body:
                if send_agent_message(sender, to, structured_body, cc=None, run_id=run_id):
                    sent += 1
            return ''  # 출력에서 메시지 블록은 제거

        cleaned = _MSG_BLOCK_RE.sub(_sub, text).strip()
        return (cleaned, sent)
    except Exception:
        return (text or '', 0)


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


def _llm_check_duplicate(new_content: str, existing_facts: list) -> Optional[dict]:
    """Phase 1-B: LLM으로 새 fact와 기존 facts의 의미 중복/모순 판정.

    반환: {'action': 'duplicate'|'contradiction'|'new', 'fact_id': int|None}
    실패 시 None (→ 신규 INSERT로 폴백).
    """
    try:
        if not existing_facts:
            return None
        from api.dynamic.direct_calls import _call_direct
        existing_lines = '\n'.join(
            f"[id={f['id']}] {f['content']}" for f in existing_facts[:50]
        )
        prompt = (
            '아래 "기존 기억" 목록과 "새 기억"을 비교하라.\n'
            '- 의미적으로 동일한 내용이 이미 있으면: {"action": "duplicate", "fact_id": <id>}\n'
            '- 기존 기억과 모순되면(새 내용이 더 최신 사실): {"action": "contradiction", "fact_id": <id>}\n'
            '- 둘 다 아니면: {"action": "new", "fact_id": null}\n'
            '반드시 JSON 객체 하나로만 응답하라.\n\n'
            f'기존 기억:\n{existing_lines}\n\n'
            f'새 기억: {new_content}'
        )
        raw = _call_direct(prompt)
        obj = _parse_json_object(raw)
        action = obj.get('action', 'new')
        fact_id = obj.get('fact_id')
        if action in ('duplicate', 'contradiction') and fact_id is not None:
            return {'action': action, 'fact_id': int(fact_id)}
        return {'action': 'new', 'fact_id': None}
    except Exception:
        return None


def extract_and_store_facts(messages, source_session: Optional[str] = None) -> int:
    """대화에서 장기 기억 facts를 추출해 저장. 반환: 저장된 fact 수.

    Phase 2-D: 같은 세션에서 추출된 facts끼리 derived_from 자동 연결.
    """
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
        session_fact_ids = []
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            # Phase 2-D: 같은 세션 facts끼리 derived_from 연결
            derived = None
            if session_fact_ids:
                derived = ','.join(f'fact:{fid}' for fid in session_fact_ids)
            new_id = add_fact(text, 'general', source_session, derived_from=derived)
            if new_id is not None:
                saved += 1
                session_fact_ids.append(new_id)
    except Exception:
        pass
    return saved


def update_profile_from_messages(messages) -> int:
    """Phase 1-A: 정규 key로 프로필 추출/저장. 반환: 갱신된 키 수."""
    updated = 0
    try:
        transcript = _transcript(messages)
        if not transcript:
            return 0
        from api.dynamic.direct_calls import _call_direct
        keys_hint = ', '.join(f'{k}({v})' for k, v in CANONICAL_PROFILE_KEYS.items())
        prompt = (
            '다음 대화에서 사용자의 프로필 정보를 추출하라.\n'
            f'반드시 아래 key 중 하나만 사용하라: {keys_hint}\n'
            '해당하는 key가 없으면 그 항목은 생략하라.\n'
            '반드시 JSON 객체로만 응답하라. '
            '예: {"name": "홍길동", "preferred_language": "한국어"}. '
            '추출할 것이 없으면 빈 객체 {}를 반환하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_direct(prompt)
        obj = _parse_json_object(raw)
        for k, v in (obj or {}).items():
            # 정규 key 매핑 (LLM이 한글 key를 반환해도 매핑)
            canonical = _normalize_profile_key(str(k))
            if canonical and not canonical.startswith('_'):
                if set_profile(canonical, str(v)):
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
    elif kind == 'refine':
        _run_daily_refine()
    # 향후 kind 추가: 'backup' 등


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


def _log_refine(action: str, fact_ids: list, detail: str = '') -> None:
    """Phase 4-C: 정제 이력 기록."""
    try:
        with _db_lock:
            conn = _connect()
            try:
                conn.execute(
                    'INSERT INTO refine_log (action, fact_ids, detail) VALUES (?,?,?)',
                    (action, json.dumps(fact_ids, ensure_ascii=False), (detail or '')[:2000]),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 5: 재검토 큐 (memory_review)
# ---------------------------------------------------------------------------

def _create_review(kind: str, fact_ids: list, suggestion: str = '') -> Optional[int]:
    """Phase 5-B: 재검토 큐에 항목 등록. kind: contradiction/merge_candidate/low_confidence."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    'INSERT INTO memory_review (kind, fact_ids, suggestion) VALUES (?,?,?)',
                    (kind or 'contradiction',
                     json.dumps(fact_ids or [], ensure_ascii=False),
                     (suggestion or '')[:2000]),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
    except Exception:
        return None


def list_reviews(status: Optional[str] = 'pending', limit: int = 50) -> list:
    """Phase 5-C: 재검토 큐 목록. status=None이면 전체. 각 항목에 fact 내용 해석 포함."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                if status:
                    rows = conn.execute(
                        'SELECT * FROM memory_review WHERE status=? '
                        'ORDER BY id DESC LIMIT ?',
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        'SELECT * FROM memory_review ORDER BY id DESC LIMIT ?',
                        (limit,),
                    ).fetchall()
                out = []
                for r in rows:
                    item = dict(r)
                    # fact_ids 해석 → 실제 fact 내용 첨부
                    try:
                        ids = json.loads(item.get('fact_ids') or '[]')
                    except Exception:
                        ids = []
                    facts_detail = []
                    for fid in ids:
                        try:
                            fr = conn.execute(
                                'SELECT id, content, category, confidence, superseded_by '
                                'FROM facts WHERE id=?', (int(fid),)
                            ).fetchone()
                            if fr:
                                facts_detail.append(dict(fr))
                        except Exception:
                            pass
                    item['facts'] = facts_detail
                    out.append(item)
                return out
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return []


def resolve_review(review_id, action: str) -> dict:
    """Phase 5-C: 재검토 항목 처리. action='approve' → 제안 적용, 'reject' → 원복/무시.

    kind별 approve 동작:
    - contradiction: fact_ids=[old,new] → old에 superseded_by=new, confidence=0.3
    - merge_candidate: fact_ids=[rep, ...others] → others를 rep로 병합(superseded)
    - low_confidence: fact_ids=[id] → 해당 fact 삭제
    """
    result = {'ok': False, 'action': action, 'review_id': review_id}
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    'SELECT * FROM memory_review WHERE id=?', (int(review_id),)
                ).fetchone()
                if not row:
                    result['error'] = 'review not found'
                    return result
                if row['status'] != 'pending':
                    result['error'] = f"already {row['status']}"
                    return result
                kind = row['kind']
                try:
                    ids = json.loads(row['fact_ids'] or '[]')
                    ids = [int(i) for i in ids]
                except Exception:
                    ids = []

                if action == 'approve':
                    if kind == 'contradiction' and len(ids) >= 2:
                        old_id, new_id = ids[0], ids[1]
                        conn.execute(
                            'UPDATE facts SET superseded_by=?, confidence=0.3 WHERE id=?',
                            (new_id, old_id),
                        )
                        result['applied'] = {'superseded': old_id, 'by': new_id}
                    elif kind == 'merge_candidate' and len(ids) >= 2:
                        rep, others = ids[0], ids[1:]
                        for oid in others:
                            conn.execute(
                                'UPDATE facts SET superseded_by=? WHERE id=?',
                                (rep, oid),
                            )
                        result['applied'] = {'kept': rep, 'superseded': others}
                    elif kind == 'low_confidence' and len(ids) >= 1:
                        conn.execute(
                            f"DELETE FROM facts WHERE id IN ({','.join('?' * len(ids))})",
                            ids,
                        )
                        result['applied'] = {'deleted': ids}
                    conn.execute(
                        "UPDATE memory_review SET status='approved' WHERE id=?",
                        (int(review_id),),
                    )
                    result['ok'] = True
                elif action == 'reject':
                    # 거부: 모순인 경우 confidence 복원
                    if kind == 'contradiction' and len(ids) >= 1:
                        conn.execute(
                            'UPDATE facts SET confidence=1.0 WHERE id=? AND superseded_by IS NULL',
                            (ids[0],),
                        )
                    conn.execute(
                        "UPDATE memory_review SET status='rejected' WHERE id=?",
                        (int(review_id),),
                    )
                    result['ok'] = True
                else:
                    result['error'] = f'unknown action: {action}'
                conn.commit()
                return result
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception as e:
        result['error'] = str(e)
        return result


def _run_maintenance() -> None:
    """⑤ 가벼운 정리: 상한 삭제 + Phase 4 감쇠(decay)."""
    try:
        with _db_lock:
            conn = _connect()
            try:
                # 상한 정리
                conn.execute(
                    "DELETE FROM facts WHERE id NOT IN ("
                    "  SELECT id FROM facts ORDER BY id DESC LIMIT ?"
                    ")",
                    (_MAX_FACTS,),
                )
                # Phase 4: 감쇠 — 30일 이상 미사용 fact는 confidence *= 0.8
                conn.execute(
                    "UPDATE facts SET confidence = confidence * 0.8 "
                    "WHERE superseded_by IS NULL "
                    "AND (last_used_at IS NULL OR last_used_at < datetime('now', '-30 days')) "
                    "AND confidence > 0.1"
                )
                # 90일 이상 미사용 + confidence < 0.3 → 자동 삭제
                deleted_rows = conn.execute(
                    "SELECT id FROM facts WHERE superseded_by IS NULL "
                    "AND confidence < 0.3 "
                    "AND (last_used_at IS NULL OR last_used_at < datetime('now', '-90 days'))"
                ).fetchall()
                deleted_ids = [r['id'] for r in deleted_rows]
                if deleted_ids:
                    conn.execute(
                        f"DELETE FROM facts WHERE id IN ({','.join('?' * len(deleted_ids))})",
                        deleted_ids,
                    )
                conn.commit()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        if deleted_ids:
            _log_refine('decay', deleted_ids, 'auto-deleted: confidence<0.3 & unused>90d')
    except Exception:
        pass


def _run_daily_refine() -> None:
    """Phase 4-B: 일일 심화 정제 — LLM으로 facts 클러스터링 → 병합 → 모순 해결."""
    try:
        facts = list_facts(limit=500, include_superseded=False)
        if len(facts) < 10:
            return
        from api.dynamic.direct_calls import _call_direct

        # 1) 의미적 클러스터링
        facts_text = '\n'.join(f"[id={f['id']}] {f['content']}" for f in facts)
        cluster_prompt = (
            '아래 기억 항목들을 의미적으로 그룹화하라. 같은 주제의 항목끼리 묶어라.\n'
            '반드시 JSON 배열의 배열로 응답하라. 예: [[1,5,12],[3,7],[2]]\n'
            '그룹에 속하지 않는 항목은 단독 배열로. 모든 id를 포함하라.\n\n'
            f'항목:\n{facts_text}'
        )
        raw = _call_direct(cluster_prompt)
        clusters = _parse_json_array(raw)

        # 2) 각 클러스터: 2건 이상이면 대표 fact로 병합
        merged_count = 0
        for cluster in clusters:
            if not isinstance(cluster, list) or len(cluster) < 2:
                continue
            ids = [int(i) for i in cluster if isinstance(i, (int, float))]
            if len(ids) < 2:
                continue
            cluster_facts = [f for f in facts if f['id'] in ids]
            if len(cluster_facts) < 2:
                continue
            contents = '\n'.join(f"[id={f['id']}] {f['content']}" for f in cluster_facts)
            merge_prompt = (
                '아래 같은 주제의 기억 항목들을 하나의 문장으로 통합하라.\n'
                '핵심 정보만 남기고 중복은 제거하라. 통합 문장만 출력하라.\n\n'
                f'{contents}'
            )
            merged_text = (_call_direct(merge_prompt) or '').strip()
            if not merged_text:
                continue
            # 대표 fact = 가장 use_count 높은 것, 없으면 첫 번째
            rep = max(cluster_facts, key=lambda f: (f.get('use_count') or 0))
            others = [f for f in cluster_facts if f['id'] != rep['id']]
            with _db_lock:
                conn = _connect()
                try:
                    conn.execute(
                        'UPDATE facts SET content=? WHERE id=?',
                        (merged_text, rep['id']),
                    )
                    for o in others:
                        conn.execute(
                            'UPDATE facts SET superseded_by=? WHERE id=?',
                            (rep['id'], o['id']),
                        )
                    conn.commit()
                finally:
                    conn.close()
            merged_count += 1
            _log_refine('merge', [rep['id']] + [o['id'] for o in others], merged_text)

        # 3) 모순 감지
        active_facts = list_facts(limit=500, include_superseded=False)
        if len(active_facts) >= 2:
            af_text = '\n'.join(f"[id={f['id']}] {f['content']}" for f in active_facts)
            contra_prompt = (
                '아래 기억 항목 중 서로 모순되는 쌍을 찾아라.\n'
                '반드시 JSON 배열로 응답하라. 각 요소는 [오래된_id, 최신_id] 쌍.\n'
                '모순이 없으면 빈 배열 []을 반환하라.\n\n'
                f'{af_text}'
            )
            raw2 = _call_direct(contra_prompt)
            contradictions = _parse_json_array(raw2)
            for pair in contradictions:
                if not isinstance(pair, list) or len(pair) != 2:
                    continue
                old_id, new_id = int(pair[0]), int(pair[1])
                # Phase 5: 자동 처리 대신 재검토 큐에 등록 (사람 승인 대기)
                old_content = next((f['content'] for f in active_facts if f['id'] == old_id), '')
                new_content = next((f['content'] for f in active_facts if f['id'] == new_id), '')
                suggestion = f"기존: '{old_content[:100]}' / 신규: '{new_content[:100]}' → 기존 항목을 대체 제안"
                _create_review('contradiction', [old_id, new_id], suggestion)
                _log_refine('contradiction_detected', [old_id, new_id], 'queued for review')
    except Exception:
        pass


def _run_daily() -> None:
    """⑤⑥ 일일 정비: 심화 정제 → WAL 체크포인트 → 백업 → VACUUM."""
    try:
        # 0) Phase 4-B: 심화 정제 (LLM 호출)
        try:
            _run_daily_refine()
        except Exception:
            pass
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
        # 2-1) 패치 1: 외부 백업 — Documents/DAON-backup에 사본 복사.
        #      앱 제거(설치 폴더 삭제)에도 기억이 살아남도록 한다. best-effort.
        try:
            if _EXTERNAL_BACKUP_DIR is not None and _MEMORY_DB_PATH.exists():
                _EXTERNAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(_MEMORY_DB_PATH), str(_EXTERNAL_BACKUP_DIR / 'memory.db'))
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
