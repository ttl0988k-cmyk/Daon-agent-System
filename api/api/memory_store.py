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
import sqlite3
import threading
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
                """
            )
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


def get_context_block(max_facts: int = 20) -> str:
    """채팅 시스템 프롬프트에 주입할 장기 기억 요약 텍스트. 실패 시 빈 문자열."""
    try:
        facts = list_facts(limit=max_facts)
        profile = get_profile()
        parts = []
        if profile:
            prof_lines = ', '.join(f'{k}: {v}' for k, v in list(profile.items())[:30])
            parts.append(f'[사용자 프로필] {prof_lines}')
        if facts:
            fact_lines = '; '.join(f['content'] for f in facts[:max_facts])
            parts.append(f'[장기 기억] {fact_lines}')
        return '\n'.join(parts)
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
def process_session_async(session) -> None:
    """채팅 완료 후 백그라운드에서 facts/profile/summary 추출.

    session.messages 를 읽어 daemon 스레드에서 처리한다.
    실패해도 절대 예외를 던지지 않는다(순수 부가 원칙).
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

        def _worker():
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

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
    except Exception:
        pass
