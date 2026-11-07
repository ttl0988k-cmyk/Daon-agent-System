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
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.request
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
# v3 (Phase 7): Ruflo 장기기억 이식 (embedding, namespace, tier, verdict, source_pid, pinned, valid_from/until)
_SCHEMA_VERSION = 3

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
# 재시작 폭주 수정(2026-09-14): 0.0 으로 두면 프로세스가 뜰 때마다 now-0>=주기가
# 참이 되어 매 재시작마다 일일정제(LLM)가 즉시 1회씩 실행됐다. 워커 시작 시각으로
# 초기화해 '실제 주기 도래'에만 실행되게 한다.
_last_maintenance_ts = time.time()   # 워커 시작 시각 기준(즉시 실행 방지)
_last_daily_ts = time.time()         # 일일 정비도 시작 시각 기준

# ── Ruflo 포팅: Write Guard 비밀정보 정규식 및 네임스페이스 매핑 ──
_SECRET_PATTERNS = re.compile(
    r'(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{20,}|bearer\s+[a-zA-Z0-9_\-\.]{20,}|(?:password|passwd|secret|token)\s*[:=]\s*[^\s]{6,})',
    re.IGNORECASE
)

_CATEGORY_TO_NAMESPACE = {
    'general': 'patterns',
    'preference': 'feedback',
    'project': 'tasks',
}

# ── 기억 자동화 게이트 (Ruflo 포팅: on/off 단순화, OmniRoute 의존성 완전 제거) ──
_DAILY_REFINE_MAX_FACTS = 200    # 정제 facts 상한

def memory_auto_enabled() -> bool:
    """기억 자동화(PID 증류/정제) 활성 여부.
    명시 설정: config.yaml memory.auto_extract 또는 env DAON_MEMORY_AUTO_EXTRACT.
    기본값: True (명시적으로 'off'/'false'/'0'이 아니면 ON).
    """
    try:
        from api.config import _load_config_value
        val = _load_config_value('memory.auto_extract', 'DAON_MEMORY_AUTO_EXTRACT', 'on')
    except Exception:
        val = os.getenv('DAON_MEMORY_AUTO_EXTRACT', 'on')
    s = str(val if val is not None else 'on').strip().lower()
    return s not in ('off', 'false', '0', 'no', 'none', 'disable', 'disabled')


def _omniroute_available() -> bool:
    """Deprecated: OmniRoute 의존성 제거됨. 항상 False 반환 (무료 큐 오염 방지)."""
    return False


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
                "ALTER TABLE facts ADD COLUMN embedding BLOB DEFAULT NULL",
                "ALTER TABLE facts ADD COLUMN namespace TEXT DEFAULT 'patterns'",
                "ALTER TABLE facts ADD COLUMN tier TEXT DEFAULT 'semantic'",
                "ALTER TABLE facts ADD COLUMN verdict TEXT DEFAULT NULL",
                "ALTER TABLE facts ADD COLUMN source_pid TEXT DEFAULT NULL",
                "ALTER TABLE facts ADD COLUMN pinned INTEGER DEFAULT 0",
                "ALTER TABLE facts ADD COLUMN valid_from TEXT DEFAULT NULL",
                "ALTER TABLE facts ADD COLUMN valid_until TEXT DEFAULT NULL",
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
    'derived_from, confidence, superseded_by, use_count, last_used_at, '
    'embedding, namespace, tier, verdict, source_pid, pinned, valid_from, valid_until'
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


def backfill_facts_embeddings() -> int:
    """embedding이 NULL인 facts를 로컬에서 배치 임베딩하여 1회 백필한다 (비용 0원)."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT id, content FROM facts WHERE embedding IS NULL AND content != ''"
                ).fetchall()
                if not rows:
                    return 0

                from api.api.memory_embed import embed_batch

                ids = [r["id"] for r in rows]
                contents = [r["content"] for r in rows]
                blobs = embed_batch(contents, is_query=False)

                updates = [(b, i) for b, i in zip(blobs, ids)]
                conn.executemany("UPDATE facts SET embedding = ? WHERE id = ?", updates)
                conn.commit()
                print(f"[MemoryStore] facts 임베딩 백필 완료: {len(updates)}건 (비용 0원)", flush=True)
                return len(updates)
            finally:
                conn.close()
    except Exception as e:
        print(f"[MemoryStore] backfill_facts_embeddings 에러: {e}", flush=True)
        return 0


def add_fact(
    content: str,
    category: str = 'general',
    source_session: Optional[str] = None,
    derived_from: Optional[str] = None,
    skip_dedup: bool = False,
    confidence: float = 1.0,
    namespace: Optional[str] = None,
    tier: str = 'semantic',
    verdict: Optional[str] = None,
    source_pid: Optional[str] = None,
    pinned: int = 0,
    valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> Optional[int]:
    """fact 추가 (Ruflo 로직 포팅: Write Guard + 로컬 벡터 코사인 0.95 병합 + pinned 보호).

    - Write Guard: API 키, 토큰, 비밀번호 등 민감정보 감지 시 즉시 저장 거부 (0 API 비용)
    - 384차원 로컬 다국어 임베딩 자동 생성 (API 비용 0원)
    - 코사인 0.95 이상 유사 항목 존재 시 최신 내용으로 자동 병합 (use_count 합산, LLM 호출 없음)
    - 500건 초과 시: pinned=1은 영구 보존, unpinned 중 신뢰도/사용빈도 최하위부터 삭제
    """
    try:
        content = (content or '').strip()
        if not content:
            return None

        # ── 1. Write Guard (비밀키/토큰/비밀번호 차단) ──
        if _SECRET_PATTERNS.search(content):
            print(f"[MemoryStore] WriteGuard: 민감정보 포함되어 fact 저장 차단: {content[:30]}...")
            return None

        _ensure_schema()

        if not namespace:
            namespace = _CATEGORY_TO_NAMESPACE.get(category or 'general', 'patterns')

        # ── 2. 로컬 384차원 다국어 임베딩 계산 (API 0원) ──
        embedding_blob = None
        try:
            from api.memory_embed import embed_text, cosine
            embedding_blob = embed_text(content, is_query=False)
        except Exception as _emb_err:
            print(f"[MemoryStore] embed_text 호출 에러: {_emb_err}")
            cosine = None

        new_id = None
        with _db_lock:
            conn = _connect()
            try:
                # ── 3. 완전 일치 검사 ──
                dup = conn.execute(
                    'SELECT id FROM facts WHERE content=? LIMIT 1', (content,)
                ).fetchone()
                if dup:
                    conn.execute(
                        "UPDATE facts SET last_used_at=datetime('now'), use_count=COALESCE(use_count,0)+1 WHERE id=?",
                        (dup['id'],)
                    )
                    conn.commit()
                    return dup['id']

                # ── 4. 벡터 코사인 >= 0.95 유사 중복 병합 (LLM 없음) ──
                if not skip_dedup and embedding_blob and len(embedding_blob) >= 1536 and cosine is not None:
                    rows = conn.execute(
                        "SELECT id, content, embedding, use_count FROM facts "
                        "WHERE superseded_by IS NULL AND (valid_until IS NULL OR valid_until >= datetime('now')) "
                        "AND embedding IS NOT NULL"
                    ).fetchall()
                    for r in rows:
                        sim = cosine(embedding_blob, r['embedding'])
                        if sim >= 0.95:
                            # 0.95 이상이면 동일/유사 사실로 병합: 최신 내용 갱신 + use_count 증가
                            conn.execute(
                                "UPDATE facts SET content=?, embedding=?, last_used_at=datetime('now'), "
                                "use_count=COALESCE(use_count, 0) + 1 WHERE id=?",
                                (content, embedding_blob, r['id']),
                            )
                            conn.commit()
                            return r['id']

                # ── 5. 신규 fact INSERT ──
                cur = conn.execute(
                    'INSERT INTO facts (content, category, source_session, derived_from, confidence, '
                    'embedding, namespace, tier, verdict, source_pid, pinned, valid_from, valid_until) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (content, category or 'general', source_session, derived_from, confidence,
                     embedding_blob, namespace, tier, verdict, source_pid, pinned, valid_from, valid_until),
                )
                conn.commit()
                new_id = cur.lastrowid

                # ── 6. 상한 정리 (pinned=1 보호, unpinned 최하위부터 삭제) ──
                count = conn.execute('SELECT COUNT(*) AS c FROM facts').fetchone()['c']
                if count > _MAX_FACTS:
                    overflow = count - _MAX_FACTS
                    conn.execute(
                        'DELETE FROM facts WHERE id IN ('
                        '  SELECT id FROM facts WHERE pinned=0 '
                        '  ORDER BY (COALESCE(confidence, 1.0) * 10 + COALESCE(use_count, 0)) ASC, id ASC LIMIT ?'
                        ')',
                        (overflow,),
                    )
                    conn.commit()
            finally:
                conn.close()

        return new_id
    except Exception as e:
        print(f"[MemoryStore] add_fact 실패: {e}")
        return None


def pin_fact(fact_id: int, pinned: int = 1) -> bool:
    """fact를 pinned(핵심 기억)로 지정하거나 해제한다. pinned=1은 감쇠/삭제에서 영구 보존된다."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                conn.execute('UPDATE facts SET pinned=? WHERE id=?', (int(pinned), int(fact_id)))
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


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


def backfill_facts_embeddings() -> int:
    """embedding이 NULL인 facts를 로컬에서 배치 임베딩하여 1회 백필한다 (비용 0원)."""
    try:
        _ensure_schema()
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT id, content FROM facts WHERE embedding IS NULL AND content != ''"
                ).fetchall()
                if not rows:
                    return 0

                from api.api.memory_embed import embed_batch

                ids = [r["id"] for r in rows]
                contents = [r["content"] for r in rows]
                blobs = embed_batch(contents, is_query=False)

                updates = [(b, i) for b, i in zip(blobs, ids)]
                conn.executemany("UPDATE facts SET embedding = ? WHERE id = ?", updates)
                conn.commit()
                print(f"[MemoryStore] facts 임베딩 백필 완료: {len(updates)}건 (비용 0원)", flush=True)
                return len(updates)
            finally:
                conn.close()
    except Exception as e:
        print(f"[MemoryStore] backfill_facts_embeddings 에러: {e}", flush=True)
        return 0


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


def get_context_block(max_facts: int = 6, query_text: str = '',
                      session_id: str = '', query: Optional[str] = None) -> str:
    """DAON SmartRetrieval 5단계 주입 파이프라인 (Ruflo 로직 포팅, API 비용 0원).

    1. 전체 facts(500건 이하) 로드 후 로컬 다국어 임베딩 + SmartRetrieval(확장·RRF·최신성·MMR·라운드로빈) 5~8개 추출
    2. summaries 테이블의 최근 PID 요약 1~2개를 [최근 PID 요약] episodic 블록으로 주입
    3. 사용자 프로필을 [사용자 프로필] 블록으로 주입
    """
    if query and not query_text:
        query_text = query
    try:
        # 전체 facts 로드 (500건 이하라 전수 코사인 랭킹 초고속 수행)
        all_facts = list_facts(limit=500, include_superseded=False)
        profile = get_profile()

        # Ruflo SmartRetrieval 5단계 실행
        selected = []
        if all_facts:
            try:
                from api.api.memory_retrieval import smart_search
                target_k = max(5, min(8, max_facts))
                selected = smart_search(query=query_text or "최근 작업 및 중요 사실", all_facts=all_facts, limit=target_k)
            except Exception as s_err:
                # 안전 폴백: SmartRetrieval 모듈 로드 실패 시 기존 최근 순 정렬
                selected = all_facts[:max_facts]

        # 사용 기록 (Phase 2-B)
        injected_ids = [f['id'] for f in selected if f.get('id')]
        if injected_ids and session_id:
            _record_fact_usage(injected_ids, session_id)
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

        # 1. 사용자 프로필 블록
        if profile:
            prof_items = [(k, v) for k, v in profile.items() if not str(k).startswith('_')]
            if prof_items:
                prof_lines = ', '.join(f'{k}: {v}' for k, v in prof_items[:30])
                parts.append(f'[사용자 프로필] {prof_lines}')

        # 2. 최근 PID/세션 요약 (Episodic 층, 1~2건)
        try:
            recent_sums = list_summaries(limit=2)
            if recent_sums:
                sum_lines = []
                for s in recent_sums:
                    s_txt = (s.get('summary') or s.get('title') or '').strip()
                    if s_txt:
                        sum_lines.append(s_txt.replace('\n', ' '))
                if sum_lines:
                    parts.append(f'[최근 PID 요약] {" // ".join(sum_lines)}')
        except Exception:
            pass

        # 3. 장기 기억 블록 (Semantic 층, 5~8개 정선)
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


_RECIPIENT_ALIASES = {
    "raon": ["raon", "라온"],
    "라온": ["raon", "라온"],
    "bill": ["bill", "빌", "빌(개발)", "dev"],
    "빌": ["bill", "빌", "빌(개발)", "dev"],
    "빌(개발)": ["bill", "빌", "빌(개발)", "dev"],
    "sherlock": ["sherlock", "셜록", "셜록(검수)", "qa"],
    "셜록": ["sherlock", "셜록", "셜록(검수)", "qa"],
    "셜록(검수)": ["sherlock", "셜록", "셜록(검수)", "qa"],
    "tony": ["tony", "토니", "토니(기획)", "planner"],
    "토니": ["tony", "토니", "토니(기획)", "planner"],
    "토니(기획)": ["tony", "토니", "토니(기획)", "planner"],
    "prada": ["prada", "프라다", "프라다(디자인)", "designer"],
    "프라다": ["prada", "프라다", "프라다(디자인)", "designer"],
    "프라다(디자인)": ["prada", "프라다", "프라다(디자인)", "designer"],
    "daon": ["daon", "다온", "다온(응대)"],
    "다온": ["daon", "다온", "다온(응대)"],
}

def _resolve_aliases(name: str) -> list:
    name = (name or '').strip().lower()
    return _RECIPIENT_ALIASES.get(name, [name])


def get_agent_inbox(recipient: str, unread_only: bool = False, limit: int = 50) -> list:
    """특정 에이전트의 받은 메시지함을 최신순으로 반환. 별칭(한/영) 자동 매핑."""
    try:
        recipient = (recipient or '').strip()
        if not recipient:
            return []
        _ensure_schema()
        targets = _resolve_aliases(recipient)
        placeholders = ', '.join(['?'] * len(targets))
        q = f'SELECT id, sender, recipient, cc, body, run_id, read_flag, created_at FROM agent_inbox WHERE recipient IN ({placeholders})'
        if unread_only:
            q += ' AND read_flag = 0'
        q += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        params = list(targets) + [int(limit)]
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(q, params).fetchall()
                return [dict(r) for r in rows]
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    except Exception:
        return []


def mark_inbox_read(recipient: str, up_to_id: Optional[int] = None) -> int:
    """받은 메시지를 읽음 처리. 별칭(한/영) 자동 매핑. 반환: 처리 건수."""
    try:
        recipient = (recipient or '').strip()
        if not recipient:
            return 0
        _ensure_schema()
        targets = _resolve_aliases(recipient)
        placeholders = ', '.join(['?'] * len(targets))
        with _db_lock:
            conn = _connect()
            try:
                if up_to_id:
                    cur = conn.execute(
                        f'UPDATE agent_inbox SET read_flag = 1 WHERE recipient IN ({placeholders}) AND id <= ? AND read_flag = 0',
                        list(targets) + [int(up_to_id)],
                    )
                else:
                    cur = conn.execute(
                        f'UPDATE agent_inbox SET read_flag = 1 WHERE recipient IN ({placeholders}) AND read_flag = 0',
                        list(targets),
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


_MSG_FLEXIBLE_RE = _re.compile(r'\[MSG\s+([^\]]+)\](.*?)\[/MSG\]', _re.DOTALL | _re.IGNORECASE)
_ATTR_RE = _re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s\]]+))')


def parse_and_dispatch_messages(sender: str, text: str, run_id: Optional[str] = None, return_details: bool = False) -> tuple:
    """에이전트 출력 텍스트에서 [MSG to=X task=Y priority=Z context=a,b]본문[/MSG]
    블록을 추출해 발송하고, 구조화된 수신함 포맷으로 변환 후 발송 건수(및 상세 목록)를 반환.

    새 포맷 예:
      [MSG to=raon task=implement_auth priority=high context=spec.md]
      인증 기능을 구현 완료했습니다.
      [/MSG]
    """
    try:
        if not text or '[MSG' not in text:
            return (text or '', 0, []) if return_details else (text or '', 0)
        sent = 0
        details = []

        def _sub(match):
            nonlocal sent
            header_str = match.group(1)
            body = (match.group(2) or '').strip()

            attrs = {}
            for m in _ATTR_RE.finditer(header_str):
                k = m.group(1).lower()
                v = m.group(2) or m.group(3) or m.group(4) or ''
                attrs[k] = v.strip()

            to = attrs.get('to', '')
            task = attrs.get('task', '')
            priority = attrs.get('priority', '')
            context = attrs.get('context', '')
            cc = attrs.get('cc', '')

            # 구조화된 헤더를 본문에 선행시켜 수신 에이전트가 파싱 가능하도록 함
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
                _mid = send_agent_message(sender, to, structured_body, cc=cc or None, run_id=run_id)
                if _mid:
                    sent += 1
                    details.append({
                        "sender": sender,
                        "recipient": to,
                        "task": task,
                        "priority": priority,
                        "context": context,
                        "cc": cc,
                        "body": body,
                        # Wake Queue Phase 2: dedup 키(inbox:{id}) 연결용 (§5-E)
                        "inbox_id": _mid,
                    })
            return ''  # 출력에서 메시지 블록 제거

        cleaned = _MSG_FLEXIBLE_RE.sub(_sub, text).strip()
        if return_details:
            return (cleaned, sent, details)
        return (cleaned, sent)
    except Exception:
        if return_details:
            return (text or '', 0, [])
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


def _call_memory_distill(prompt: str, system_instruction: Optional[str] = None) -> str:
    """PID 기억 증류 전용 LLM 호출 함수 (DeepSeek V4.1 Flash 저비용 모델 전용).

    원칙:
    1. OmniRoute 전면 제거.
    2. DeepSeek V4.1 Flash 단일 저비용 모델로 1회 호출.
    3. 실패 시 1회 재시도 후 실패 시 고가 모델로 폴백하지 않고 빈 문자열('') 반환.
    """
    if not memory_auto_enabled():
        return ""
    try:
        from api.dynamic.direct_calls import _call_direct
        return _call_direct(
            prompt,
            system_instruction=system_instruction or "당신은 AI 에이전트 시스템의 장기 기억 증류기입니다. 핵심 교훈과 패턴만 엄격한 JSON으로 추출하십시오.",
            preferred_model="deepseek-v4.1-flash",
            max_tokens=1500,
        )
    except Exception as e:
        print(f"[MemoryStore] Memory distillation call failed: {e}")
        return ""


def _call_memory_direct(prompt: str, system_instruction: Optional[str] = None) -> str:
    """하위 호환 래퍼: _call_memory_distill 로 위임."""
    return _call_memory_distill(prompt, system_instruction=system_instruction)


def _llm_check_duplicate(new_content: str, existing_facts: list) -> Optional[dict]:
    """Deprecated: LLM 대신 로컬 384차원 임베딩 코사인 유사도(>=0.95)로 대체됨.
    하위 호환을 위해 유지하되 로컬 코사인 계산으로 동작 (비용 0원).
    """
    try:
        if not existing_facts:
            return None
        from api.memory_embed import embed_text, cosine
        new_vec = embed_text(new_content, is_query=False)
        for f in existing_facts[:50]:
            emb = f.get('embedding')
            if emb:
                sim = cosine(new_vec, emb)
                if sim >= 0.95:
                    return {'action': 'duplicate', 'fact_id': f['id']}
        return {'action': 'new', 'fact_id': None}
    except Exception:
        return None


def process_pid_memory(pid: str, outcome: dict) -> dict:
    """PID 종료 후 결과 및 판정을 바탕으로 1회 증류(distill)하여 장기 기억에 저장한다.
    - DeepSeek V4.1 Flash 저비용 모델로 1회 호출 (PID당 비용 고정)
    - 성공/실패 판정이 난 결과만 저장 (잡담/추측/일회성 사실 제외)
    - 최대 5개 패턴, 프로필 변경분, 3~5문장 요약 추출
    - 코사인 0.95 이상은 자동 병합, 0개면 저장 안 함
    """
    if not pid:
        return {"patterns": 0, "profile_updates": 0, "summary": False}
    if not memory_auto_enabled():
        return {"patterns": 0, "profile_updates": 0, "summary": False}

    status = outcome.get('status', 'unknown')
    task = outcome.get('task', '') or ''
    result = outcome.get('result', '') or outcome.get('summary', '') or outcome.get('error', '') or ''
    logs = outcome.get('logs', [])

    log_summary = ""
    if logs:
        recent_logs = logs[-10:] if len(logs) > 10 else logs
        log_lines = []
        for l in recent_logs:
            if isinstance(l, dict):
                agent = l.get('agent_id') or l.get('agent') or 'agent'
                c = str(l.get('content') or '')[:150]
                log_lines.append(f"[{agent}] {c}")
            else:
                log_lines.append(str(l)[:150])
        log_summary = "\n".join(log_lines)

    prompt = (
        f"당신은 하네스 작업(PID={pid})의 최종 결과를 분석하여 다음 작업에 재사용할 핵심 교훈을 증류하는 전문가입니다.\n\n"
        f"[작업 정보]\n"
        f"- PID: {pid}\n"
        f"- 판정: {status}\n"
        f"- 작업 목표: {task}\n"
        f"- 작업 결과/산출물: {str(result)[:2000]}\n"
    )
    if log_summary:
        prompt += f"- 실행 로그 요약:\n{log_summary}\n\n"

    prompt += (
        "다음 규칙에 따라 엄격한 JSON 객체 하나로만 응답하라:\n"
        "1. 'patterns': 다음 작업에서 실제로 써먹을 수 있는 판단·방법·주의점 (최대 5개).\n"
        "   - 단순 사실이나 일회성 결과가 아닌 일반화 가능한 패턴/규칙이어야 함.\n"
        "   - 'content': 한 문장, 200자 이내, 동사 포함 명확한 서술\n"
        "   - 'namespace': 'patterns' (성공패턴), 'solutions' (해결책/에러대응), 'feedback' (사용자선호/정정) 중 하나\n"
        "   - 'confidence': 0.7 ~ 1.0\n"
        "   - 가치 있는 패턴이 없으면 빈 배열 [] 반환 (절대 억지로 만들지 말 것)\n"
        "2. 'profile_updates': 사용자의 선호나 개발 환경 설정에 대한 새로운 정보가 있다면 갱신할 key-value. 없으면 {}.\n"
        "3. 'summary': 이번 PID의 핵심 목표와 결과, 성패 원인을 3~5문장으로 요약한 한국어 텍스트.\n\n"
        "응답 JSON 형식 예시:\n"
        "{\n"
        '  "patterns": [{"content": "다이나믹 하네스 실행 시 429 에러는 즉시 페일오버 모델로 전환해야 지연을 방지할 수 있다", "namespace": "solutions", "confidence": 0.95}],\n'
        '  "profile_updates": {},\n'
        '  "summary": "PID ... 요약"\n'
        "}\n"
    )

    system_instruction = (
        "당신은 AI 에이전트 시스템의 장기 기억 증류기입니다. 잡담이나 단순 경과는 제외하고 "
        "검증된 성공/실패 패턴과 재사용 가능한 해결책만 엄격하게 JSON으로 추출하십시오."
    )

    raw = _call_memory_distill(prompt, system_instruction=system_instruction)
    obj = _parse_json_object(raw)

    saved_patterns = 0
    patterns = obj.get('patterns', [])
    if isinstance(patterns, list):
        for p in patterns[:5]:
            if not isinstance(p, dict):
                continue
            c = str(p.get('content') or '').strip()
            if not c:
                continue
            ns = str(p.get('namespace') or 'patterns').strip()
            conf = float(p.get('confidence', 0.9))
            fid = add_fact(
                content=c,
                namespace=ns,
                source_pid=pid,
                verdict=status,
                confidence=conf,
                tier='semantic',
            )
            if fid:
                saved_patterns += 1

    profile_updates = obj.get('profile_updates', {})
    updated_profiles = 0
    if isinstance(profile_updates, dict):
        for k, v in profile_updates.items():
            k_clean = str(k).strip()
            v_clean = str(v).strip()
            if k_clean and v_clean:
                if set_profile(k_clean, v_clean):
                    updated_profiles += 1

    summary_text = str(obj.get('summary') or '').strip()
    saved_summary = False
    if summary_text:
        title = f"PID {pid} ({status})"
        if add_summary(pid, title, summary_text):
            saved_summary = True

    print(f"[MemoryStore] PID {pid} 기억 증류 완료: 패턴 {saved_patterns}건, 프로필 {updated_profiles}건, 요약 {saved_summary}")
    return {
        "patterns": saved_patterns,
        "profile_updates": updated_profiles,
        "summary": saved_summary
    }


def process_pid_memory_async(pid: str, outcome: dict) -> None:
    """PID 종료 후 memory distill 작업을 SQLite 큐에 등록하고 워커가 비동기로 처리."""
    try:
        if not memory_auto_enabled():
            return
        _queue_put('pid', {'pid': pid, 'outcome': outcome})
        _ensure_queue_worker()
    except Exception as e:
        print(f"[MemoryStore] process_pid_memory_async error: {e}")


def extract_and_store_facts(messages, source_session: Optional[str] = None) -> int:
    """대화에서 장기 기억 facts를 추출해 저장. 반환: 저장된 fact 수.

    Phase 2-D: 같은 세션에서 추출된 facts끼리 derived_from 자동 연결.
    """
    saved = 0
    try:
        transcript = _transcript(messages)
        if not transcript:
            return 0
        prompt = (
            '다음 대화에서 사용자의 선호, 사실, 습관, 결정사항 등 장기적으로 기억할 '
            '가치 있는 정보를 추출하라. 각 항목은 짧고 독립적인 문장으로 작성하라. '
            '반드시 JSON 배열(문자열 요소)로만 응답하라. 예: ["사용자는 한국어를 선호한다"]. '
            '추출할 것이 없으면 빈 배열 []을 반환하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_memory_direct(prompt)
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
        raw = _call_memory_direct(prompt)
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
        prompt = (
            '다음 대화를 3~5문장의 한국어로 간결하게 요약하라. '
            '주요 주제, 결정사항, 결과를 포함하라. 요약 텍스트만 출력하라.\n\n'
            f'대화:\n{transcript}'
        )
        raw = _call_memory_direct(prompt)
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
    elif kind == 'pid':
        process_pid_memory(payload.get('pid'), payload.get('outcome', {}))
    elif kind == 'refine':
        consolidate_memory()
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
    """⑤ 가벼운 정리: 상한 삭제 + Phase 4 감쇠(decay) (pinned=1 영구 보호)."""
    try:
        deleted_ids = []
        with _db_lock:
            conn = _connect()
            try:
                # 상한 정리 (pinned=1은 삭제에서 영구 제외, unpinned 중 신뢰도/사용빈도 최하위부터 삭제)
                count = conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()['c']
                if count > _MAX_FACTS:
                    overflow = count - _MAX_FACTS
                    conn.execute(
                        "DELETE FROM facts WHERE id IN ("
                        "  SELECT id FROM facts WHERE pinned=0 "
                        "  ORDER BY (COALESCE(confidence, 1.0) * 10 + COALESCE(use_count, 0)) ASC, id ASC LIMIT ?"
                        ")",
                        (overflow,)
                    )
                # Phase 4: 감쇠 — 30일 이상 미사용 fact는 confidence *= 0.8 (pinned=0 에만 적용)
                conn.execute(
                    "UPDATE facts SET confidence = confidence * 0.8 "
                    "WHERE pinned=0 AND superseded_by IS NULL "
                    "AND (last_used_at IS NULL OR last_used_at < datetime('now', '-30 days')) "
                    "AND confidence > 0.1"
                )
                # 90일 이상 미사용 + confidence < 0.3 → 자동 삭제 (pinned=0 에만 적용)
                deleted_rows = conn.execute(
                    "SELECT id FROM facts WHERE pinned=0 AND superseded_by IS NULL "
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


def consolidate_memory() -> dict:
    """Ruflo MemoryConsolidator logic ported to Python/SQLite.
    실행 순서: sweepExpired() -> dedup() -> confidence decay -> cap enforcement.
    1. sweepExpired(): valid_until 지난 만료 항목 제외 점검
    2. dedup(): 384차원 임베딩 코사인 >= 0.95 유사 쌍 병합 (최신본 유지, use_count 합산, superseded_by 설정)
    3. confidence decay: 시간 경과에 따른 감쇠 적용 (floor 0.1, pinned=1 완전 보호)
    4. cap enforcement: 500건 초과 시 pinned=0 중 신뢰도/사용빈도 최하위부터 정리
    비용: 0원 (순수 로컬 임베딩 벡터 연산, LLM 호출 없음).
    """
    _ensure_schema()
    res = {'swept': 0, 'merged': 0, 'decayed': 0, 'pruned': 0}
    try:
        from api.memory_embed import cosine, vector_from_blob

        # 1. sweepExpired()
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    "SELECT COUNT(*) AS c FROM facts WHERE valid_until IS NOT NULL AND valid_until < datetime('now')"
                )
                res['swept'] = cur.fetchone()['c']
            finally:
                conn.close()

        # 2. dedup() — pairwise cosine >= 0.95
        with _db_lock:
            conn = _connect()
            try:
                rows = conn.execute(
                    "SELECT id, content, embedding, use_count, pinned, created_at FROM facts "
                    "WHERE superseded_by IS NULL AND (valid_until IS NULL OR valid_until >= datetime('now')) "
                    "ORDER BY id DESC"
                ).fetchall()
            finally:
                conn.close()

        active_facts = [dict(r) for r in rows if r['embedding']]
        merged_ids = set()
        merged_pairs = []

        for i in range(len(active_facts)):
            f_new = active_facts[i]
            if f_new['id'] in merged_ids:
                continue
            v_new = vector_from_blob(f_new['embedding'])
            if v_new is None:
                continue

            for j in range(i + 1, len(active_facts)):
                f_old = active_facts[j]
                if f_old['id'] in merged_ids:
                    continue
                v_old = vector_from_blob(f_old['embedding'])
                if v_old is None:
                    continue

                sim = cosine(v_new, v_old)
                if sim >= 0.95:
                    merged_ids.add(f_old['id'])
                    merged_pairs.append((f_new['id'], f_old['id'], f_old.get('use_count', 0)))

        if merged_pairs:
            with _db_lock:
                conn = _connect()
                try:
                    for new_id, old_id, old_use in merged_pairs:
                        conn.execute(
                            "UPDATE facts SET superseded_by=?, valid_until=datetime('now') WHERE id=?",
                            (new_id, old_id)
                        )
                        conn.execute(
                            "UPDATE facts SET use_count=COALESCE(use_count, 0) + ? WHERE id=?",
                            (old_use, new_id)
                        )
                    conn.commit()
                finally:
                    conn.close()
            res['merged'] = len(merged_pairs)
            _log_refine('dedup_cosine', [p[1] for p in merged_pairs], f"Merged {len(merged_pairs)} pairs with cosine >= 0.95")

        # 3. confidence decay (pinned=1 보호)
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    "UPDATE facts SET confidence = MAX(0.1, COALESCE(confidence, 1.0) - 0.05) "
                    "WHERE pinned=0 AND superseded_by IS NULL "
                    "AND (last_used_at IS NULL OR julianday('now') - julianday(last_used_at) >= 7.0) "
                    "AND (julianday('now') - julianday(created_at) >= 7.0) "
                    "AND COALESCE(confidence, 1.0) > 0.1"
                )
                conn.commit()
                res['decayed'] = cur.rowcount
            finally:
                conn.close()

        # 4. cap enforcement (500건 상한, pinned=1 보호)
        with _db_lock:
            conn = _connect()
            try:
                count = conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()['c']
                if count > _MAX_FACTS:
                    overflow = count - _MAX_FACTS
                    cur = conn.execute(
                        "DELETE FROM facts WHERE id IN ("
                        "  SELECT id FROM facts WHERE pinned=0 "
                        "  ORDER BY (COALESCE(confidence, 1.0) * 10 + COALESCE(use_count, 0)) ASC, id ASC LIMIT ?"
                        ")",
                        (overflow,)
                    )
                    conn.commit()
                    res['pruned'] = cur.rowcount
            finally:
                conn.close()

        print(f"[MemoryStore] consolidate_memory 완료: {res}")
        return res
    except Exception as e:
        print(f"[MemoryStore] consolidate_memory 에러: {e}")
        return res


def _run_daily_refine() -> None:
    """일일 정제 — Ruflo Consolidator 로직으로 위임 (LLM 비용 0원, 코사인 0.95 병합 및 pinned 보호)."""
    try:
        consolidate_memory()
    except Exception as e:
        print(f"[MemoryStore] _run_daily_refine 에러: {e}")


def _run_daily() -> None:
    """⑤⑥ 일일 정비: 심화 정제 → WAL 체크포인트 → 백업 → VACUUM."""
    try:
        # 0) Phase 4-B: 심화 정제 (LLM 호출) — 무료 로컬 라우터(OmniRoute) 감지 시에만.
        #    미감지(지인 기본 env)면 LLM 호출 없이 DB 정비(WAL/백업/VACUUM)만 수행한다.
        if memory_auto_enabled():
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

        # 4) Hermes 세션 로그 자동 정리: 3일 이상 지난 세션 로그 삭제 (I/O 병목 및 파일 폭주 영구 방지)
        try:
            _hermes_sessions_dir = Path.home() / '.hermes' / 'profiles' / 'raon' / 'sessions'
            if _hermes_sessions_dir.exists():
                _cutoff = time.time() - 3 * 86400
                for _p in _hermes_sessions_dir.glob('session_*.json'):
                    try:
                        if _p.stat().st_mtime < _cutoff:
                            _p.unlink(missing_ok=True)
                    except Exception:
                        pass
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
        # 무료 로컬 라우터(OmniRoute)가 없으면 자동 추출을 건너뛴다(유료 토큰 소모 방지).
        if not memory_auto_enabled():
            return
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
