# -*- coding: utf-8 -*-
"""
DAON 패치 레지스트리 (Patch Registry)
======================================
파일별 load-bearing 코드 패치를 추적한다.
바이브 코딩 중 AI가 이전 패치를 실수로 되돌리는(회귀) 것을 방지한다.

설계 원칙:
- 순수 부가(pure additive): 이 모듈의 실패가 기존 흐름을 깨뜨리지 않는다.
- 저장소: STATE_DIR/patches.db
- 모든 공개 함수는 try/except로 감싼다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

try:
    from api.config import STATE_DIR
except Exception:
    import os
    STATE_DIR = Path(os.path.dirname(os.path.abspath(__file__))).parent / 'data'

_PATCHES_DB_PATH = Path(STATE_DIR) / 'patches.db'
_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB 연결 / 스키마
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_PATCHES_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema() -> None:
    try:
        with _db_lock:
            conn = _connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS patches (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path   TEXT NOT NULL,
                        description TEXT NOT NULL,
                        commit_hash TEXT DEFAULT '',
                        reason      TEXT DEFAULT '',
                        reverts_if  TEXT DEFAULT '',
                        related_files TEXT DEFAULT '[]',
                        line_hint   TEXT DEFAULT '',
                        active      INTEGER DEFAULT 1,
                        created_at  TEXT DEFAULT (datetime('now')),
                        updated_at  TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_patches_file
                        ON patches(file_path);
                    CREATE INDEX IF NOT EXISTS idx_patches_active
                        ON patches(active);
                """)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


_schema_ready = False


def _init_once() -> None:
    global _schema_ready
    if not _schema_ready:
        _ensure_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def register_patch(
    file_path: str,
    description: str,
    commit_hash: str = '',
    reason: str = '',
    reverts_if: str = '',
    related_files: list = None,
    line_hint: str = '',
) -> int:
    """패치 등록. 반환: patch id (실패 시 -1)."""
    try:
        _init_once()
        file_path = (file_path or '').strip().replace('\\', '/')
        if not file_path or not description:
            return -1
        rf_json = json.dumps(related_files or [], ensure_ascii=False)
        with _db_lock:
            conn = _connect()
            try:
                cur = conn.execute(
                    """INSERT INTO patches
                       (file_path, description, commit_hash, reason,
                        reverts_if, related_files, line_hint)
                       VALUES (?,?,?,?,?,?,?)""",
                    (file_path, description.strip(), commit_hash.strip(),
                     reason.strip(), reverts_if.strip(), rf_json,
                     line_hint.strip()),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()
    except Exception:
        return -1


def query_patches(file_path: str, active_only: bool = True) -> list:
    """특정 파일의 패치 목록 조회. 경로 정규화 후 부분 매칭도 수행."""
    try:
        _init_once()
        file_path = (file_path or '').strip().replace('\\', '/')
        if not file_path:
            return []
        with _db_lock:
            conn = _connect()
            try:
                clauses = ["(file_path = ? OR file_path LIKE ?)"]
                params = [file_path, f'%{file_path}']
                if active_only:
                    clauses.append("active = 1")
                sql = f"SELECT * FROM patches WHERE {' AND '.join(clauses)} ORDER BY id DESC"
                rows = conn.execute(sql, params).fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()
    except Exception:
        return []


def query_all_patches(active_only: bool = True) -> list:
    """전체 패치 목록."""
    try:
        _init_once()
        with _db_lock:
            conn = _connect()
            try:
                sql = "SELECT * FROM patches"
                if active_only:
                    sql += " WHERE active = 1"
                sql += " ORDER BY file_path, id DESC"
                rows = conn.execute(sql).fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()
    except Exception:
        return []


def delete_patch(patch_id: int) -> bool:
    """패치 삭제."""
    try:
        _init_once()
        with _db_lock:
            conn = _connect()
            try:
                conn.execute("DELETE FROM patches WHERE id = ?", (patch_id,))
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


def deactivate_patch(patch_id: int) -> bool:
    """패치 비활성화 (삭제하지 않고 보존)."""
    try:
        _init_once()
        with _db_lock:
            conn = _connect()
            try:
                conn.execute(
                    "UPDATE patches SET active = 0, updated_at = datetime('now') WHERE id = ?",
                    (patch_id,),
                )
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


def update_patch(patch_id: int, **kwargs) -> bool:
    """패치 필드 갱신. 허용 필드: description, commit_hash, reason, reverts_if, related_files, line_hint, active."""
    try:
        _init_once()
        allowed = {'description', 'commit_hash', 'reason', 'reverts_if',
                    'related_files', 'line_hint', 'active', 'file_path'}
        sets = []
        params = []
        for k, v in kwargs.items():
            if k not in allowed:
                continue
            if k == 'related_files' and isinstance(v, list):
                v = json.dumps(v, ensure_ascii=False)
            if k == 'file_path':
                v = (v or '').replace('\\', '/')
            sets.append(f"{k} = ?")
            params.append(v)
        if not sets:
            return False
        sets.append("updated_at = datetime('now')")
        params.append(patch_id)
        with _db_lock:
            conn = _connect()
            try:
                conn.execute(
                    f"UPDATE patches SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                conn.commit()
                return True
            finally:
                conn.close()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 경고 블록 생성 (on_tool 훅 / 시스템 프롬프트 주입용)
# ---------------------------------------------------------------------------

def get_warning_block(file_path: str) -> str:
    """파일 편집 전 주입할 경고 텍스트. 패치 없으면 빈 문자열."""
    try:
        patches = query_patches(file_path, active_only=True)
        if not patches:
            return ''
        lines = [f"⚠️ [{file_path}]에 load-bearing 패치 {len(patches)}건이 등록되어 있습니다:"]
        for p in patches:
            entry = f"  • #{p['id']}: {p['description']}"
            if p.get('commit_hash'):
                entry += f" (commit {p['commit_hash'][:8]})"
            if p.get('line_hint'):
                entry += f" [line {p['line_hint']}]"
            lines.append(entry)
            if p.get('reason'):
                lines.append(f"    이유: {p['reason']}")
            if p.get('reverts_if'):
                lines.append(f"    ⛔ 이 코드를 삭제하면: {p['reverts_if']}")
        lines.append("  → 이 파일 수정 시 위 패치를 보존하세요.")
        return '\n'.join(lines)
    except Exception:
        return ''


def get_system_prompt_block() -> str:
    """시스템 프롬프트에 주입할 패치 레지스트리 요약."""
    try:
        patches = query_all_patches(active_only=True)
        if not patches:
            return ''
        # 파일별로 그룹화
        by_file: dict[str, list] = {}
        for p in patches:
            by_file.setdefault(p['file_path'], []).append(p)
        lines = [
            "[패치 레지스트리 — load-bearing 코드 대장]",
            "아래 파일들에는 의도적으로 추가된 코드가 있습니다. 수정/리팩토링 시 보존하세요.",
        ]
        for fp, plist in by_file.items():
            lines.append(f"\n📄 {fp}:")
            for p in plist:
                desc = p['description']
                if p.get('reverts_if'):
                    desc += f" → 삭제 시: {p['reverts_if']}"
                lines.append(f"  • {desc}")
        return '\n'.join(lines)
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# 시드 데이터 (기존 알려진 패치)
# ---------------------------------------------------------------------------

def seed_known_patches() -> int:
    """이미 알려진 패치를 등록 (중복 스킵). 반환: 신규 등록 수."""
    _init_once()
    seeds = [
        {
            'file_path': 'api/api/streaming.py',
            'description': 'custom_providers.json API 키 fallback — UI 등록 프로바이더 키 해석',
            'commit_hash': 'fc06bef',
            'reason': 'hermes 내부 resolve_runtime_provider()는 UI에서 등록한 커스텀 프로바이더를 모름. '
                      'auth.json credential_pool에도 없음. model_manager._get_api_key()으로 fallback 필요.',
            'reverts_if': 'qwen-token-plan, agnes 등 UI 등록 프로바이더 전부 "no API key" RuntimeError 발생',
            'related_files': ['api/api/agent_runner.py', 'api/api/managers/model_manager.py'],
            'line_hint': '640-650',
        },
        {
            'file_path': 'api/api/agent_runner.py',
            'description': 'custom_providers.json API 키 fallback (agent_runner 경로)',
            'commit_hash': 'fc06bef',
            'reason': 'streaming.py와 동일한 fallback을 agent_runner.py에도 추가. '
                      '하드코딩된 프로바이더 env var 체크 후 model_manager._get_api_key() 호출.',
            'reverts_if': 'agent_runner 경로로 실행 시 UI 등록 프로바이더 API 키 해석 실패',
            'related_files': ['api/api/streaming.py', 'api/api/managers/model_manager.py'],
            'line_hint': '155-162',
        },
        {
            'file_path': 'api/api/models.py',
            'description': 'Session.load() 손상된 JSON 파일 예외 처리',
            'commit_hash': '385f66f',
            'reason': '빈 파일(0 bytes) 또는 깨진 JSON 세션 파일이 있으면 json.loads()에서 '
                      'JSONDecodeError 발생 → _write_session_index() 전체가 중단됨.',
            'reverts_if': '빈 세션 파일 1개만 있어도 세션 인덱스 재구축 전체 실패, '
                          'GET /api/sessions 500 에러',
            'related_files': [],
            'line_hint': '78-90',
        },
        {
            'file_path': 'api/api/managers/model_manager.py',
            'description': 'resolve_model_provider() str 모델 요소 방어 처리',
            'commit_hash': '0a0e39a',
            'reason': 'custom_providers.json의 models 배열에 str 요소가 있으면 '
                      'm.get("id")에서 AttributeError 발생.',
            'reverts_if': 'models에 str이 포함된 프로바이더 선택 시 스트림 전체 실패',
            'related_files': ['api/api/streaming.py'],
            'line_hint': '300-310',
        },
        {
            'file_path': 'api/api/routes/admin_routes.py',
            'description': 'GET /api/skills에서 registry.reload() 호출 — 신규 스킬 즉시 반영',
            'commit_hash': '0a0e39a',
            'reason': 'SkillRegistry는 싱글턴이라 서버 시작 시 1회만 스캔. '
                      '이후 추가된 스킬은 재시작 전까지 UI에 안 보임.',
            'reverts_if': '서버 시작 후 추가한 스킬이 UI 스킬 목록에 나타나지 않음',
            'related_files': ['api/api/skill_registry.py'],
            'line_hint': '287',
        },
        {
            'file_path': 'static/modules/core.js',
            'description': '인라인 비디오 플레이어 — renderMd()에서 <video> 태그 렌더링',
            'commit_hash': '18efb85',
            'reason': '비디오 생성 도구 결과가 마크다운에 포함되어도 <video> 태그가 렌더링되지 않음.',
            'reverts_if': '채팅에서 비디오가 링크로만 표시되고 인라인 재생 불가',
            'related_files': ['api/static/modules/core.js'],
            'line_hint': '46-127',
        },
        {
            'file_path': 'electron/main.js',
            'description': '트레이 풍선 2초 자동삭제',
            'commit_hash': 'ebbbc06',
            'reason': '트레이 balloonTip이 영구 표시되어 작업표시줄을 가림.',
            'reverts_if': '트레이 풍선이 사라지지 않고 영구 표시',
            'related_files': [],
            'line_hint': '198-219',
        },
    ]

    count = 0
    for seed in seeds:
        # 중복 체크: 같은 file_path + commit_hash + description
        existing = query_patches(seed['file_path'], active_only=False)
        dup = any(
            p.get('commit_hash') == seed['commit_hash']
            and p.get('description') == seed['description']
            for p in existing
        )
        if dup:
            continue
        pid = register_patch(**seed)
        if pid > 0:
            count += 1
    return count


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # related_files JSON 파싱
    try:
        d['related_files'] = json.loads(d.get('related_files') or '[]')
    except (json.JSONDecodeError, TypeError):
        d['related_files'] = []
    return d
