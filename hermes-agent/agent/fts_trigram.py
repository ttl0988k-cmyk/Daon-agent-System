"""FTS5 trigram companion index SQL builders (B안: 병행 인덱스).

ds-bot ``_dsbot_ref/chat-index.js`` (trigram tokenizer / termQuery 하이브리드 /
around 검색 / excerpt / user_version 버전관리 — 상세 설계
``_dsbot_ref/design/02_fts5_design.md`` §3.1 B안, §3.3, §3.5)의 다온 이식용
신규 모듈.

규칙:
- 이 모듈은 **SQL 문자열과 순수 헬퍼만 제공**한다. import 시점에 어떤 DB에도
  접속하지 않고, 마이그레이션을 적용하지 않는다.
- 기존 ``messages_fts`` (unicode61) 테이블과 ``hermes_state.py``는 손대지
  않는다. B안대로 ``messages_fts_trigram`` 을 병행 운용하고, 검색 함수가
  쿼리 특성에 따라 인덱스를 선택한다.
- 적용(``executescript`` 실행)은 호출자(향후 마이그레이션 단계)가 수행한다.

사용 예::

    import sqlite3
    from agent.fts_trigram import get_trigram_schema_sql, term_query_hybrid

    conn = sqlite3.connect(state_db_path)
    conn.executescript(get_trigram_schema_sql())   # 호출자가 명시적으로 적용
    q = term_query_hybrid(["다온 에이전트", "검색"])
    rows = conn.execute(q["sql"], q["args"]).fetchall()
"""

from __future__ import annotations

import re
import sqlite3

# ── 상수 (ds-bot chat-index.js 라인 15–21 이식) ──────────────────────────────

#: trigram 동반 인덱스 자체 버전. ``fts_meta.fts_trigram_version`` 에 저장된다.
#: ds-bot ``INDEX_VERSION = 3`` 과는 별개(다온 기존 FTS와 충돌 회피, 설계 §3.5).
TRIGRAM_INDEX_VERSION = 1
TRIGRAM_INDEX_VERSION_STR = str(TRIGRAM_INDEX_VERSION)

#: 이 동반 인덱스가 사용하는 토크나이저. ``fts_meta.fts_tokenizer`` 에 저장.
TRIGRAM_TOKENIZER = "trigram"

#: around 검색 기본 창: anchor 앞 6 + anchor 포함 뒤 7 (설계 §3.3, js AROUND_LINES).
AROUND_BEFORE = 6
AROUND_AFTER = 7

#: 검색어 최대 토큰 수 (js SEARCH_TERMS_MAX).
SEARCH_TERMS_MAX = 6

#: 원문 1줄 상한 (js OWN_CHAT_LINE_MAX → SEARCH_LINE_MAX, 설계 §3.2).
SEARCH_LINE_MAX = 400

#: anchor 대상 행 최대 노출 (js ANCHOR_LINE_MAX, 설계 §3.3).
ANCHOR_LINE_MAX = 8000

#: excerpt 매치 앞 윈도우 (js text.js excerpt: 매칭 위치 앞 120자).
EXCERPT_LEAD = 120

#: ``fts_meta`` 에 기록하는 키.
FTS_META_KEY_VERSION = "fts_trigram_version"
FTS_META_KEY_TOKENIZER = "fts_tokenizer"


# ── (1) TRIGRAM_SCHEMA SQL ──────────────────────────────────────────────────

#: B안 동반 인덱스 + 트리거 동기화 + 버전 테이블 SQL 전문.
#: ``content=messages, content_rowid=id`` external-content 구조는 기존
#: ``hermes_state.FTS_SQL`` (messages_fts) 과 동일하게 유지한다.
TRIGRAM_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
    content,
    content=messages,
    content_rowid=id,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content)
        VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content)
        VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts_trigram(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS fts_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
""".strip() + "\n"


def get_trigram_schema_sql() -> str:
    """동반 trigram 인덱스 + 동기화 트리거 + ``fts_meta`` 생성 SQL 전문 반환.

    마이그레이션을 적용하지 않고 문자열만 돌려준다. 호출자가
    ``conn.executescript(get_trigram_schema_sql())`` 형태로 적용한다.
    """
    return TRIGRAM_SCHEMA


def get_trigram_table_sql() -> str:
    """``messages_fts_trigram`` 가상테이블 생성 SQL만 반환."""
    return (
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(\n"
        "    content,\n"
        "    content=messages,\n"
        "    content_rowid=id,\n"
        "    tokenize='trigram'\n"
        ");\n"
    )


def get_trigram_sync_triggers_sql() -> str:
    """``messages`` → ``messages_fts_trigram`` 동기화 트리거 3종 SQL 반환."""
    return (
        "CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert "
        "AFTER INSERT ON messages BEGIN\n"
        "    INSERT INTO messages_fts_trigram(rowid, content) "
        "VALUES (new.id, new.content);\n"
        "END;\n"
        "\n"
        "CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete "
        "AFTER DELETE ON messages BEGIN\n"
        "    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content)\n"
        "        VALUES ('delete', old.id, old.content);\n"
        "END;\n"
        "\n"
        "CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update "
        "AFTER UPDATE ON messages BEGIN\n"
        "    INSERT INTO messages_fts_trigram(messages_fts_trigram, rowid, content)\n"
        "        VALUES ('delete', old.id, old.content);\n"
        "    INSERT INTO messages_fts_trigram(rowid, content) "
        "VALUES (new.id, new.content);\n"
        "END;\n"
    )


def get_rebuild_sql(table: str = "messages_fts_trigram") -> str:
    """external-content FTS 테이블 전체 재빌드 SQL 반환.

    ds-bot ``openIndex`` 재생성(손상 시 DROP+재생성)에 대응하는 다온식
    멱등 재빌드. 2회 실행해도 rowid 중복이 생기지 않는다.
    """
    ident = table.replace('"', '""')
    return f'INSERT INTO "{ident}"("{ident}") VALUES (\'rebuild\');\n'


def get_capability_probe_sql() -> str:
    """trigram 토크나이저 가용성 probe용 SQL 반환 (읽기 전용, 부작용 없음).

    호출자가 ``conn.execute(...)`` 해보고 ``OperationalError`` 면 trigram
    미지원 환경으로 보고 기존 unicode61 + LIKE 폴백을 유지한다 (설계 §6).
    """
    return (
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='fuzz_probe_never_created';\n"
    )


def probe_trigram_support(conn: sqlite3.Connection) -> bool:
    """현재 SQLite 빌드가 FTS5 trigram 을 지원하는지 probe (임시테이블, 부작용 없음).

    마이그레이션이 아니라 capability 검사이므로 여기서 직접 실행해도 된다.
    """
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS "
            "__trigram_probe__ USING fts5(x, tokenize='trigram')"
        )
        conn.execute("DROP TABLE IF EXISTS __trigram_probe__")
        return True
    except Exception:
        return False


# ── LIKE 이스케이프 (js likeOf 이식) ─────────────────────────────────────────

def like_of(term: str) -> str:
    """``%``/``\\``/``_`` 를 이스케이프한 LIKE 패턴 반환 (js ``likeOf``)."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", term) + "%"


def _quote_phrase(term: str) -> str:
    """FTS5 phrase용 큰따옴표 이스케이프 (js ``term.replace(/"/g,'""')``)."""
    return '"' + term.replace('"', '""') + '"'


def split_terms(query: str, maximum: int = SEARCH_TERMS_MAX) -> list[str]:
    """쿼리를 소문자·중복제거·공백분할 후 최대 ``maximum`` 개로 자른다 (js 207행)."""
    seen: list[str] = []
    for tok in query.lower().split():
        if tok and tok not in seen:
            seen.append(tok)
        if len(seen) >= maximum:
            break
    return seen


# ── (2) term_query_hybrid ────────────────────────────────────────────────────

def term_query_hybrid(
    terms: list[str] | tuple[str, ...],
    limit: int = 20,
    session_ids: list[str] | tuple[str, ...] | None = None,
) -> dict:
    """길이별 하이브리드 검색 쿼리 생성 (js ``termQuery`` 25–43행 이식).

    - 3자 이상 term → trigram FTS ``MATCH`` 에 phrase AND 결합
      (trigram 은 3자 미만 매칭 불가이므로 분리).
    - 3자 미만 term → ``content LIKE ? ESCAPE '\\\\'`` 필터 병용.
    - long term 이 있으면 ``ORDER BY rank`` (BM25), 짧은 단어만 있으면
      ``ORDER BY m.timestamp DESC`` (js 와 동일 규칙).
    - 다온 스키마에 맞춰 FTS 테이블과 ``messages`` 를 JOIN 해 메타컬럼 반환.

    반환: ``{"sql": ..., "args": [...]}``. 실행만 하면 되고 스키마 변경 없음.
    """
    terms = [t for t in (terms or []) if t]
    long = [t for t in terms if len(t) >= 3]
    short = [t for t in terms if len(t) < 3]

    where: list[str] = []
    args: list = []

    if long:
        where.append("messages_fts_trigram MATCH ?")
        args.append(" ".join(_quote_phrase(t) for t in long))
    if session_ids:
        where.append(
            "m.session_id IN (%s)" % ", ".join("?" for _ in session_ids)
        )
        args.extend(session_ids)
    for term in short:
        where.append("m.content LIKE ? ESCAPE '\\'")
        args.append(like_of(term))

    order = "rank" if long else "m.timestamp DESC"
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT m.id, m.session_id, m.role, m.content, m.timestamp "
        "FROM messages_fts_trigram "
        "JOIN messages m ON m.id = messages_fts_trigram.rowid"
        f"{where_sql} ORDER BY {order} LIMIT {int(limit)}"
    )
    return {"sql": sql, "args": args}


# ── (3) get_around SQL ───────────────────────────────────────────────────────

def get_around_sql(
    session_id: str,
    message_id: int,
    before: int = AROUND_BEFORE,
    after: int = AROUND_AFTER,
) -> dict:
    """anchor(``session_id#message_id``) 주변 메시지 조회 SQL 반환.

    ds-bot ``aroundOwnChat`` (248–259행) + ``index.before/from`` (119–120행)
    이식. 정렬은 설계 §3.3 대로 ``messages.timestamp, messages.id``.

    - ``before_sql``: anchor 이전 ``before`` 개 (최신→과거 순 반환, 호출자가 뒤집기).
    - ``from_sql``: anchor 포함 이후 ``after`` 개 (과거→최신 순).
    - 합계 ``before + after`` 행, anchor 행 존재 여부가 ``exact`` 판정에 쓰인다.

    반환: ``{"before_sql", "before_args", "from_sql", "from_args"}``.
    """
    columns = "m.id, m.session_id, m.role, m.content, m.timestamp"
    before_sql = (
        f"SELECT {columns} FROM messages m "
        "WHERE m.session_id = ? AND (m.timestamp, m.id) < "
        "(SELECT timestamp, id FROM messages WHERE session_id = ? AND id = ?) "
        "ORDER BY m.timestamp DESC, m.id DESC "
        f"LIMIT {int(before)}"
    )
    from_sql = (
        f"SELECT {columns} FROM messages m "
        "WHERE m.session_id = ? AND (m.timestamp, m.id) >= "
        "(SELECT timestamp, id FROM messages WHERE session_id = ? AND id = ?) "
        "ORDER BY m.timestamp ASC, m.id ASC "
        f"LIMIT {int(after)}"
    )
    return {
        "before_sql": before_sql,
        "before_args": [session_id, session_id, int(message_id)],
        "from_sql": from_sql,
        "from_args": [session_id, session_id, int(message_id)],
    }


# ── (4) excerpt / anchor 헬퍼 ────────────────────────────────────────────────

def excerpt(text: str | None, wanted: str = "", max_chars: int = SEARCH_LINE_MAX) -> str:
    """매치 주변 원문 절취 (js ``text.js`` excerpt 27–31행 이식).

    - ``len(text) <= max`` 면 통째로 반환.
    - 아니면 매칭 위치 **앞 120자**부터 ``max`` 길이로 잘라 앞뒤 ``…`` 표기.
    - 검색 히트가 항상 노출되도록 보장한다.
    """
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    at = 0
    if wanted:
        at = max(0, text.lower().find(wanted.lower()) - EXCERPT_LEAD)
    prefix = "…" if at > 0 else ""
    return f"{prefix}{text[at:at + max_chars]}…"


#: anchor 문법: ``session_id#id`` / ``#id``(현재 세션) / 구형 ``part N #seq`` 허용.
_ANCHOR_RE = re.compile(
    r"^\s*(?:part\s*\d+\s*)?(?:(?P<session>.+?)\s*)?#\s*(?P<seq>\d+)\s*$",
    re.IGNORECASE,
)


def parse_anchor(anchor: str, default_session: str | None = None) -> dict:
    """anchor 문자열 파싱. 성공 시 ``{"session_id", "message_id"}``,
    실패 시 ``{"error": ...}`` (js aroundOwnChat 251–255행 대응, 다온형)."""
    found = _ANCHOR_RE.match(str(anchor or ""))
    if found is None:
        return {
            "error": (
                f'"{anchor}" is not an anchor. '
                'Anchors look like "session_id#123" or "#123" in the current session.'
            )
        }
    session = (found.group("session") or "").strip() or default_session
    if not session:
        return {
            "error": (
                f'"{anchor}" has no session. '
                'Use "session_id#123" or pass the current session id.'
            )
        }
    return {"session_id": session, "message_id": int(found.group("seq"))}


def format_anchor(session_id: str, message_id: int) -> str:
    """``session_id#message_id`` 형식 anchor 생성."""
    return f"{session_id}#{int(message_id)}"


def row_line(
    anchor: str,
    clock: str,
    role: str,
    text: str,
    terms: list[str] | tuple[str, ...] = (),
    max_chars: int = SEARCH_LINE_MAX,
) -> str:
    """원문 1줄 표시 (js ``rowLine`` 185–190행 이식, 다온형).

    ``[anchor · 시각 · role] excerpt`` 형식. anchor 행은 ``max_chars`` 로
    ``ANCHOR_LINE_MAX`` 을 넘기면 8000자까지 통째 노출.
    """
    lowered = str(text or "").lower()
    wanted = next((t for t in (terms or []) if t and t.lower() in lowered), "")
    return f"[{anchor} · {clock} · {role}] {excerpt(text, wanted, max_chars)}"


# ── (5) fts_meta 버전관리 ────────────────────────────────────────────────────

def get_fts_meta_schema_sql() -> str:
    """``fts_meta(key TEXT PRIMARY KEY, value TEXT)`` 생성 SQL 반환 (설계 §3.5).

    기존 ``schema_version`` 테이블과 충돌하지 않는 별도 키 공간.
    """
    return (
        "CREATE TABLE IF NOT EXISTS fts_meta(\n"
        "    key TEXT PRIMARY KEY,\n"
        "    value TEXT NOT NULL\n"
        ");\n"
    )


def get_fts_meta_get_sql() -> str:
    """버전 조회 SQL 반환 (``args=[key]`` 로 실행)."""
    return "SELECT value FROM fts_meta WHERE key = ?;\n"


def get_fts_meta_set_sql() -> str:
    """버전 기록 SQL 반환 (``args=[key, value]`` 로 실행)."""
    return "INSERT OR REPLACE INTO fts_meta(key, value) VALUES (?, ?);\n"


def desired_fts_meta_entries() -> dict[str, str]:
    """기대되는 ``fts_meta`` 키/값 (현 코드 버전 기준)."""
    return {
        FTS_META_KEY_VERSION: TRIGRAM_INDEX_VERSION_STR,
        FTS_META_KEY_TOKENIZER: TRIGRAM_TOKENIZER,
    }


def is_trigram_meta_current(stored: dict[str, str | None]) -> bool:
    """저장된 ``fts_meta`` 값이 현 코드 버전과 일치하는지 판정 (순수 함수)."""
    desired = desired_fts_meta_entries()
    return all(stored.get(k) == v for k, v in desired.items())


def needs_rebuild(stored_version: str | None) -> bool:
    """저장된 trigram 버전이 현 버전과 다르면 재빌드 필요 (순수 함수)."""
    return stored_version != TRIGRAM_INDEX_VERSION_STR


__all__ = [
    "TRIGRAM_INDEX_VERSION",
    "TRIGRAM_INDEX_VERSION_STR",
    "TRIGRAM_TOKENIZER",
    "AROUND_BEFORE",
    "AROUND_AFTER",
    "SEARCH_TERMS_MAX",
    "SEARCH_LINE_MAX",
    "ANCHOR_LINE_MAX",
    "EXCERPT_LEAD",
    "FTS_META_KEY_VERSION",
    "FTS_META_KEY_TOKENIZER",
    "TRIGRAM_SCHEMA",
    "get_trigram_schema_sql",
    "get_trigram_table_sql",
    "get_trigram_sync_triggers_sql",
    "get_rebuild_sql",
    "get_capability_probe_sql",
    "probe_trigram_support",
    "like_of",
    "split_terms",
    "term_query_hybrid",
    "get_around_sql",
    "excerpt",
    "parse_anchor",
    "format_anchor",
    "row_line",
    "get_fts_meta_schema_sql",
    "get_fts_meta_get_sql",
    "get_fts_meta_set_sql",
    "desired_fts_meta_entries",
    "is_trigram_meta_current",
    "needs_rebuild",
]
