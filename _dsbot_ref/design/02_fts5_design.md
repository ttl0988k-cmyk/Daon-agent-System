# 02. ds-bot FTS5 원문검색 → 다온(Hermes) 이식 상세 설계서

- 작성일: 2026-10-12
- 범위: **설계 문서만** (코드 수정 없음)
- 참조 원본: `_dsbot_ref/chat-index.js` (293줄), `_dsbot_ref/text.js`, `_dsbot_ref/constants.js`
- 이식 대상: `hermes-agent/hermes_state.py` (1462줄), `api/api/models.py` (677줄)
- 부수 대상: `hermes-agent/tools/session_search_tool.py` (590줄)

> 전제: 다온은 이미 `hermes_state.py`에 FTS5 검색(`messages_fts`, `search_messages`)과
> 라온의 `session_search`(LLM 요약 기반) 도구를 보유한다. 본 설계는 "새로 만드는" 것이 아니라
> ds-bot `chat-index.js`가 가진 **trigram tokenizer / around 검색 / part seal / 인덱스 버전관리 /
> 다중 term any-hit 폴백**을 다온 현행 FTS5 위에 **보강 이식**하는 것을 목표로 한다.

---

## 1. ds-bot `chat-index.js` 알고리즘 분석

### 1.1 파일 구조와 상수 (실측 라인)

`chat-index.js`는 봇 자기 자신의 대화를 FTS5로 인덱싱하고 `read_own_chat` 도구의
search / latest / around 읽기를 제공한다.

| 상수 | 값 | 라인 | 의미 |
|---|---|---|---|
| `INDEX_VERSION` | `3` | 15 | `PRAGMA user_version` 스키마 버전. 불일치 시 DROP+재생성 |
| `AROUND_LINES` | `6` | 16 | around 검색 시 앞뒤로 읽는 라인 수 |
| `SEARCH_TERMS_MAX` | `6` | 17 | 검색어 최대 토큰 수 |
| `ANY_TERM_HITS` | `50` | 18 | term별 any-hit 수집 상한 |
| `INDEX_TIMEOUT_MS` | `120_000` | 19 | 인덱싱 catch-up 타임아웃 |
| `SEAL_AFTER_MS` | `60_000` | 21 | part 종료 후 seal 확정 대기시간 |

### 1.2 FTS5 테이블 스키마 (라인 103–126)

```sql
CREATE VIRTUAL TABLE lines USING fts5(
    text,
    session UNINDEXED, seq UNINDEXED, time UNINDEXED, who UNINDEXED,
    tokenize = 'trigram'          -- ★ CJK 무분할 핵심
);
CREATE TABLE marks (
    session TEXT PRIMARY KEY,
    through INTEGER NOT NULL,     -- 마지막으로 인덱싱된 seq
    keep INTEGER NOT NULL,        -- answer 유지 플래그
    sealed INTEGER NOT NULL,      -- seal 확정 여부
    turn INTEGER NOT NULL         -- 현재 turn/start seq
);
PRAGMA user_version = 3;
```

- `tokenize='trigram'`은 **3글자 이상 연속 문자열**을 토큰으로 취급하므로,
  중국어·일본어·한국어처럼 단어 분할이 없는 언어도 **무분할로 매칭**된다(파일 상단 주석 라인 4–6).
- `session/seq/time/who`는 `UNINDEXED` — 검색 대상이 아니라 반환 메타데이터 컬럼.
- `marks` 테이블은 세션별 catch-up 진행 상태를 영속화하는 체크포인트.

### 1.3 BM25 rank + term 쿼리 생성 `termQuery` (라인 23–43)

```js
const likeOf = term => `%${term.replace(/[\\%_]/g, c => `\\${c}`)}%`   // LIKE 이스케이프

export function termQuery(terms, sessions, limit, hidden) {
  const long  = terms.filter(t => [...t].length >= 3)   // 3글자 이상 → FTS MATCH
  const short = terms.filter(t => [...t].length < 3)    // 3글자 미만 → LIKE
  const where = ['session IN (SELECT value FROM json_each(?))']
  const args  = [JSON.stringify(sessions)]
  if (hidden !== undefined) {                            // 현재 turn 숨김
    where.push('NOT (session = ? AND seq >= ?)')
    args.push(hidden.session, hidden.from)
  }
  if (long.length > 0) {
    where.unshift('lines MATCH ?')
    args.unshift(long.map(t => `"${t.replace(/"/g,'""')}"`).join(' '))  // AND 결합
  }
  for (const term of short) {
    where.push("text LIKE ? ESCAPE '\\'")
    args.push(likeOf(term))
  }
  return { sql: `SELECT text, session, seq, time, who FROM lines
                 WHERE ${where.join(' AND ')}
                 ORDER BY ${long.length > 0 ? 'rank' : 'time DESC'}
                 LIMIT ${limit}`, args }
}
```

핵심 규칙:
- **길이에 따른 하이브리드**: 3글자 이상 terme는 trigram FTS `MATCH`, 미만은 `LIKE` 필터.
  trigram은 3글자 미만을 매칭할 수 없기 때문.
- **implicit AND**: long term들을 공백으로 이어 붙이면 FTS5가 AND로 해석 → 모든 term 포함.
- **BM25 정렬**: `ORDER BY rank` (FTS5 `rank` = bm25() 내림차순, 작을수록 높은 점수).
  trigram이 못 쓰는 짧은 단어만 있는 경우엔 `time DESC`.
- **hidden**: 현재 turn(`turn/start` 이후)을 `NOT (session=? AND seq>=?)`로 배제 —
  이미 컨텍스트에 있으므로 자기 질문이 매칭되는 것을 방지.

### 1.4 part seal / catch-up (라인 133–178)

```
catchUp(index, part):
  running = catchingUp.get(part.sessionId)   // 같은 part 동시 실행 방지(중복 삽입 차단)
  mark = index.mark.get(part.sessionId)
  if mark.sealed: return                     // seal된 part는 재인덱싱 금지
  through = mark.through ?? -1
  eventsBackwards(...) 로 seq > through 수집, gap 검사(events.at(-1).seq !== through+1 → throw)
  events.reverse()
  seal = part.endedAt !== undefined && Date.now() - part.endedAt > SEAL_AFTER_MS   // 60초
  BEGIN → INSERT ... → setMark(through=last, keep, seal, turn) → COMMIT / 실패시 ROLLBACK
```

- **seal**: part가 끝난 뒤 60초 지나면 `sealed=1`로 확정. 종료 직후 늦게 오는 이벤트
  (제목, 지연 응답)까지 반영한 뒤 인덱스를 동결한다.
- **gap 검사**: 히스토리가 중간에 빠지면 예외 → 손상 방지.
- **keep**: `answer` 이벤트는 직전 `keepAnswers` 플래그에 따라 `you`로 삽입하거나 skip.

### 1.5 검색/읽기 3종 (라인 206–259)

- `searchOwnChat` (206–232):
  1) terms = 소문자·중복제거·공백분할 → `SEARCH_TERMS_MAX(6)` slice.
  2) `run(terms, limit)` → AND 매칭. 행이 있으면 `every=true`.
  3) 0건이고 term>1이면 **any-hit 폴백**: term별 `ANY_TERM_HITS(50)` 수집,
     `${session}#${seq}` 키로 히트 카운트 → `count desc, time desc` 정렬 → `limit` slice.
  4) 최종 `byTime` 정렬: `(time, session index, seq)`.
- `latestOwnChat` (235–245): part 역순으로 `before.all(sessionId, below, limit)` 수집.
- `aroundOwnChat` (248–259): anchor 정규식 `/(?:part\s*)?(\d+)?\s*#\s*(\d+)/i`.
  `part N #seq` 또는 `#seq`. 앞 `AROUND_LINES(6)` + 뒤 `AROUND_LINES+1(7)`.
- 인덱스 불가 시 `scanOwnChat` (263–277): 로그 직접 스캔 (LIKE 대신 `includes`).

### 1.6 표시 헬퍼 (`text.js`, `constants.js`)

- `excerpt(text, wanted, max=OWN_CHAT_LINE_MAX)` (text.js 27–31):
  매칭 위치 **앞 120자**부터 `max` 길이로 잘라 `…` 표기. (검색 히트를 항상 노출)
- `OWN_CHAT_LINE_MAX=400` (53), `ANCHOR_LINE_MAX=8000` (56): anchor 대상 메시지는
  최대 8000자까지 통째로 노출 → 긴 초안을 한 번에 읽게 함.
- `OWN_CHAT_BRIEF=10`(57), `OWN_CHAT_READ=12`(58), `OWN_CHAT_SEARCH=20`(59).

### 1.7 인덱스 버전관리 (라인 103–113)

```js
if (db.prepare('PRAGMA user_version').get().user_version !== INDEX_VERSION) {
  DROP TABLE IF EXISTS lines; DROP TABLE IF EXISTS marks;
  CREATE VIRTUAL TABLE lines ...; CREATE TABLE marks ...;
  PRAGMA user_version = 3;
}
```

- 스키마 변경 시 `user_version` 불일치로 감지 → **자동 전체 재생성**.
- 인덱스는 **파생물**: 삭제해도 로그에서 재빌드 가능(파일 주석 라인 7–9).
- 손상 파일은 1회 `unlink` 후 재생성 (라인 82–90, `openIndex`).

---

## 2. 다온 현행 `hermes_state.py` FTS5 실태 (실측 인용)

### 2.1 테이블 스키마 — 기본 토크나이저, external content (라인 94–113)

```python
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,          # external content 테이블
    content_rowid=id
);                             # ※ tokenize 옵션 없음 → 기본 unicode61

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""
```

- `schema_version = 7` (라인 34), `SCHEMA_VERSION` 마이그레이션은 라인 253–357.
- FTS는 `_init_schema`에서 별도 실행 (라인 351–355, `SELECT * FROM messages_fts LIMIT 0` 후 실패 시 생성).
- **핵심 차이**: ds-bot은 `tokenize='trigram'` + `UNINDEXED` 메타 컬럼, 다온은
  **external content + 기본 unicode61 + 트리거 동기화**. → CJK가 개별 문자로 쪼개짐.

### 2.2 `_sanitize_fts5_query` (라인 1079–1130)

- 6단계 정규식: (1) 따옴표 구문 보존 → (2) 특수문자 `[+{}()"^]` 제거 → (3) `*` 정리
  → (4) dangling `AND/OR/NOT` 제거 → (5) **점/하이픈 포함 토큰을 따옴표로 감쌈**
  (`chat-send`, `P2.2`) → (6) 보존 구문 복원.
- FTS5 기본 토크나이저가 점/하이픈에서 분할하므로 이를 phrase로 강제.

### 2.3 `_contains_cjk` (라인 1133–1146)

- CJK Unified(0x4E00–0x9FFF), Ext-A/B, CJK 기호, 히라가나/가타카나, **한글 음절(0xAC00–0xD7AF)** 포함 여부 검사.
- LIKE 폴백 트리거 판정용.

### 2.4 `search_messages` 시그니처와 본문 (라인 1148–1315)

```python
def search_messages(self, query, source_filter=None, exclude_sources=None,
                    role_filter=None, limit=20, offset=0) -> List[Dict]:
```

- query → `_sanitize_fts5_query` (1172).
- WHERE: `messages_fts MATCH ?` + source/exclude/role 필터 (1177–1195).
- SELECT: `snippet(messages_fts, 0, '>>>', '<<<', '...', 40)`, join messages + sessions (1198–1216).
- **`ORDER BY rank`** — 이미 BM25 정렬 적용 (1214). ★ ds-bot과 동일한 정렬 기준 보유.
- 예외 처리 (1218–1228): MATCH 실패 시 CJK면 계속, 아니면 `[]`.
- **CJK LIKE 폴백** (1230–1263): FTS 매칭이 없고 CJK 포함 시
  `m.content LIKE '%q%'`, `ORDER BY m.timestamp DESC`. (BM25 없음, 시간순)
- **주변 컨텍스트** (1265–1309): 매치마다 CTE로 직전 1 + 자기 + 직후 1 메시지(`context`) 추가.
- 결과에서 `content` 제거(스니펫만) → 토큰 절약 (1311–1313).

### 2.5 `session_search` 도구 현황 (`tools/session_search_tool.py`)

- 시그니처: `session_search(query, role_filter=None, limit=3, db=None, current_session_id=None)` (318–324).
- query 빈 문자열 → `_list_recent_sessions` (최근 세션 메타, 무 LLM) (266–315, 346–347).
- query 있음 → `db.search_messages(query, role_filter, exclude_sources=('tool',), limit=50)` (358–364).
  → 세션별 그룹핑·부모 해석(`_resolve_to_parent`) → 상위 `limit`(≤5) 세션 병렬 LLM 요약 (426–512).
- **요약 기반**: 원문 스니펫은 최종 응답에 직접 노출되지 않고 LLM 요약으로 흡수됨.
- `_truncate_around_matches` (111–193): 쿼리 매치 위치 중심 100k char 윈도우.
- 스키마 `SESSION_SEARCH_SCHEMA` (528–572), registry 등록 (578–590).

### 2.6 세션 저장/삭제 경로 (`api/api/models.py`)

- `_save_session_to_db(session)` (571–678): WebUI 세션을 `state.db`로 동기화
  (`source='webui'`). 세션 존재 시 `DELETE FROM messages WHERE session_id=?` 후 재삽입 (626).
  → FTS 트리거가 delete/insert 처리. `messages.timestamp`에 원본 ts 저장.
- `delete_cli_session(sid)` (541–568): `messages` + `sessions` row 삭제 → FTS 트리거로 인덱스도 제거.
- 세션 JSON: `SESSION_DIR/{sid}.json` (179–190). Hermes 트랜스크립트:
  `~/.hermes/profiles/<profile>/sessions/session_{sid}.json` (`run_agent.py` 1462–1466 `logs_dir`).
  **세션 삭제 시 이 트랜스크립트는 지우지 않음** → 인덱스 소스로 재활용 가능.

---

## 3. 이식 설계 — 무엇을 보강할지

다온은 이미 FTS5 + BM25(rank) + CJK LIKE 폴백을 갖고 있으므로, ds-bot에서 가져올 것은
**CJK 검색 품질, 원문 스니펫 노출, around 검색, 인덱스 소스 확장, 버전관리**이다.

### 3.1 (핵심) trigram tokenizer 도입 → CJK 무분할 검색

**현행 문제**: 다온 FTS는 기본 unicode61 → 한글/중국어가 글자 단위로 토큰화되어
`"다온 에이전트"` 같은 다중 음절 구문이 FTS에서 잘 안 잡혀 **LIKE 폴백(시간순, BM25 없음)**으로만 검색됨.

**보강안**: `messages_fts`를 trigram 토크나이저로 재생성.
- ds-bot 방식(`tokenize='trigram'`)을 그대로 차용.
- 다온은 external content 구조이므로 다음 둘 중 택1 (설계 권고: **B안**):

  - **A안 — 기존 테이블에 `tokenize` 추가 재생성**:
    ```sql
    DROP TABLE messages_fts;  -- 트리거는 유지, 재생성 후 rebuild
    CREATE VIRTUAL TABLE messages_fts USING fts5(
        content, content=messages, content_rowid=id, tokenize='trigram'
    );
    INSERT INTO messages_fts(messages_fts) VALUES('rebuild');
    ```
  - **B안 — 별도 trigram 인덱스 병행, 검색 함수가 라우팅**:
    `messages_fts`(unicode61, 영문/BM25 유지) + `messages_fts_trigram`(trigram) 이중화.
    `search_messages`가 CJK 비중에 따라 인덱스를 선택. → 기존 영문 검색 회귀 0.
    > 트레이드오프: 인덱스 2배. §6 리스크 참조.

- `SQLITE_ENABLE_FTS5` + trigram은 SQLite 3.34+ 필요 → 시작 시 capability probe 필요(§6).

### 3.2 원문 스니펫 노출 강화 (ds-bot `rowLine`/`excerpt`)

**현행**: `search_messages`가 `snippet(..., 40)`(40토큰) + `context`(앞뒤 1개)를 반환하고
`content`는 제거. `session_search`는 이를 LLM 요약으로만 흡수 → **원문이 사용자에게 안 보임**.

**보강안**:
- `search_messages`에 `excerpt`(ds-bot `text.js` 27–31 이식) 개념 도입:
  스니펫 대신 **매치 앞 120자 + `…`** 방식의 고정폭 원문 절취 옵션 (`raw_excerpt=True` 파라미터).
- `session_search`에 **`mode="raw"`(원문 모드)** 분기 신설: LLM 요약 대신 매치 원문 라인을
  `[session 短縮 · 시각 · role] excerpt` 형식으로 반환(ds-bot `rowLine` 라인 185–190 이식).
- 라인 길이 상한 `OWN_CHAT_LINE_MAX=400` → 다온에서는 `SEARCH_LINE_MAX`(권고 400)로.

### 3.3 around 검색 (`aroundOwnChat` 이식)

**현행**: `search_messages`의 `context`는 매치 기준 앞뒤 1개뿐, anchor 개념 없음.

**보강안**: `hermes_state.py`에 신규 메서드 `get_around(session_id, message_id, before=6, after=7)`
- ds-bot `aroundOwnChat`(248–259) + `index.before/from`(119–120) 이식.
- anchor 형식은 다온에 맞춰 조정: `session_id#message_id` 또는 `#rowid`.
- 정렬은 `messages.timestamp, messages.id`.
- 반환: `{ exact: bool, messages: [...] }`, anchor 행은 최대 8000자(ANCHOR_LINE_MAX 상당)까지.

### 3.4 인덱스 소스 확장 (Hermes 트랜스크립트)

**현행**: `_save_session_to_db`가 WebUI 세션만 `source='webui'`로 sync.
세션 삭제 시 트랜스크립트 `session_{sid}.json`은 남음(미삭제).

**보강안**: 트랜스크립트 JSON을 **재인덱싱 소스**로 삼는 경량 catch-up 추가.
- 대상 함수(신규): `SessionDB.reindex_from_transcripts(sessions_dir)` — `messages`에 없는
  (삭제된) 세션의 트랜스크립트를 읽어 `sessions`/`messages`에 `source='archive'`로 복원.
  → trigram 인덱스에 편입되어 검색 가능.
- ds-bot `marks` 테이블의 `through/sealed/turn` 체크포인트 개념을
  `index_marks(session, through_message_id, sealed)`로 이식 (증분 재인덱싱).
- **주의**: 이건 "삭제 후 복원"이 아니라 "검색 인덱스 잔존" 목적. 삭제 요청 시 정책 결정 필요(§6).

### 3.5 인덱스 버전관리

**현행**: `schema_version`(세션 스키마)만 존재, **FTS 인덱스 자체 버전은 없음**.

**보강안**: ds-bot `PRAGMA user_version`(라인 106) 방식을 별도 키로 차용.
- `hermes_state`의 `schema_version` 테이블과 **충돌 회피**를 위해 별도 테이블
  `fts_meta(key TEXT PRIMARY KEY, value TEXT)`에 `fts_index_version`, `fts_tokenizer` 저장.
- 인덱스 버전 불일치/손상 시 `INSERT INTO messages_fts(messages_fts) VALUES('rebuild')` 자동 실행.
  ds-bot의 `openIndex` fresh+unlink 재생성(82–90)에 대응.

### 3.6 어느 함수에 어떻게 (요약 매핑)

| ds-bot | 다온 대상 | 방식 |
|---|---|---|
| `tokenize='trigram'` | `FTS_SQL` (hermes_state.py 94–113) | A안 재생성 / B안 병행 인덱스 |
| `termQuery` long/short 분기 | `search_messages` (1148) | 길이별 MATCH/LIKE 라우팅 파라미터 |
| `ORDER BY rank` | `search_messages` 1214 | **이미 적용** (추가 불필요) |
| any-hit 폴백 (218–230) | `search_messages` 신규 헬퍼 | term별 수집 → hit count 정렬 |
| `aroundOwnChat` | `SessionDB.get_around` (신규) | before/from 조회 |
| `rowLine`/`excerpt` | `search_messages` + `session_search_tool` | `raw_excerpt` 옵션 |
| `marks` catch-up | `index_marks` (신규) | through/sealed 체크포인트 |
| `user_version` | `fts_meta` (신규) | 버전+토크나이저 저장 |
| transcript 재인덱싱 | `reindex_from_transcripts` (신규) | `source='archive'` 복원 |

---

## 4. 라온 `session_search` 도구와의 결합 설계

목표: **LLM 요약(기존) 과 원문검색(ds-bot 이식) 을 한 도구에서 노출**.

### 4.1 도구 인터페이스 확장 (하위호환)

`SESSION_SEARCH_SCHEMA`(session_search_tool.py 528–572)에 파라미터 추가:

- `mode`: `"summary"(기본) | "raw" | "around"`
  - `"summary"` — 현행 동작(LLM 요약). 회귀 0.
  - `"raw"` — `search_messages(raw_excerpt=True)` 결과를 원문 라인으로 직접 반환(요약 skip, 무 LLM).
  - `"around"` — `anchor`(예 `sess_abc#1234`) 파라미터로 `get_around` 호출.
- `anchor`: `mode="around"` 전용. ds-bot `aroundOwnChat` 문법 계승(`#seq` / `session#seq`).

`session_search()` 함수(318–324) 내부 분기:
```
if mode == "raw":   return _raw_search(db, query, role_filter, limit)
if mode == "around": return _around_search(db, anchor)
else: <현행 요약 경로>
```

### 4.2 노출 방식

- `raw` 모드는 **무 LLM·즉답**: ds-bot `rowLine` 형식
  `[세션short · YYYY-MM-DD HH:MM · role] excerpt(앞120자…, ≤400자)`.
- `raw` 결과에 `session_id` 포함 → 후속 `around` 호출의 anchor로 활용.
- 요약 모드는 그대로 유지하되, 각 요약 entry에 **`match_anchors`**(상위 매치 `#rowid` 목록)
  를 추가 → 라온이 원하면 `mode="around"`로 원문 맥락 확인. (요약→원문 2단 파이프라인)
- `session_search` 스키마 설명(530–551)에 "원문 확인이 필요하면 mode=raw / around" 안내문 추가.

### 4.3 도구 호출 흐름 예시

```
session_search(query="다온 FTS5 이전")               → 요약 + match_anchors
session_search(mode="around", anchor="sess_x#42")    → 해당 지점 앞뒤 원문 6/7개
session_search(mode="raw", query="trigram tokenizer")→ 원문 라인 즉답, 무 LLM
```

---

## 5. 단계별 구현 순서

각 단계는 독립 검증 가능(코드 수정은 본 설계 범위 밖, 순서만 정의).

1. **Capability probe** — SQLite 버전/`fts5` + `trigram` 가용성 확인
   (`SELECT fts5(?1)` x, `PRAGMA compile_options`로 `ENABLE_FTS5`), 결과를 `fts_meta`에 저장.
2. **`fts_meta` 테이블 + 버전관리 도입** — `fts_index_version`, `fts_tokenizer` 기록.
3. **trigram 인덱스 도입 (B안 권고)** — `messages_fts_trigram` 생성 + `rebuild` + 트리거 확장
   (`messages_fts_insert/delete/update`에 trigram 동기화 추가).
4. **`search_messages` 라우팅** — `_contains_cjk(query)` 기준 인덱스 선택,
   CJK면 LIKE 폴백 대신 trigram MATCH(여전히 `ORDER BY rank`).
5. **raw excerpt 옵션** — `excerpt` 헬퍼 이식 + `raw_excerpt` 반환 경로.
6. **`get_around`** 신규 메서드 + 프리페치(before/after).
7. **`session_search` 도구 `mode`/`anchor` 확장** + 스키마 설명 갱신(하위호환 유지).
8. **any-hit 폴백** (선택) — 다중 term 부분 매치 시 hit count 정렬.
9. **`reindex_from_transcripts`** + `index_marks` (선택, 트랜스크립트 재활용).
10. **문서/테스트 갱신** — toolset 설명, `tests/test_hermes_state.py`에 trigram/around 케이스.

---

## 6. 리스크 + 완화책

| 리스크 | 영향 | 완화책 |
|---|---|---|
| **CJK trigram 미지원 SQLite** | trigram 생성 실패 | 1단계 probe로 감지 → 실패 시 기존 unicode61 + LIKE 폴백 유지(회귀 방지). ds-bot `openIndex` catch(78–90)처럼 graceful degrade |
| **인덱스 크기 2배**(이중화 B안) | 디스크·WAL 증가 | B안이면 trigram 컬럼을 `content` 1개로 최소화. 영문 세션은 trigram 제외 또는 주기적 vacuum. A안(단일 trigram)이면 영문 BM25 품질 저하 여부 측정 후 결정 |
| **trigram 재빌드 비용** | 대량 messages `rebuild` 시 장시간 잠금 | `rebuild`는 오프라인 배치로. WAL checkpoint(217–236) 타이밍 조정. `index_marks.through`로 증분만 |
| **seal/재인덱싱 정책** | 삭제 세션 원문이 검색에 남음 | `reindex_from_transcripts`는 옵션(기본 off). 삭제 시 트랜스크립트 삭제 or 검색 제외 정책 명시적 결정 필요 |
| **기본 토크나이저 vs trigram 불일치** | `_sanitize_fts5_query`(1079)의 점/하이픈 처리·`*` prefix가 trigram에서 다르게 동작 | trigram 라우팅 경로에서는 sanitizer를 trigram용(따옴표 구문 위주)으로 분리 적용 |
| **3글자 미만 짧은 단어** | trigram이 1~2글자 매칭 불가 | ds-bot `termQuery`(26–41)처럼 `<3`은 LIKE 필터 병용 |
| **검색 결과 토큰 폭증** | raw 모드가 원문을 그대로 노출 | `SEARCH_LINE_MAX`(400) 절취 + `limit`(≤20) + excerpt 120자 윈도우 |
| **부모/자식 세션 중복** | around anchor가 자식 세션 향함 | `session_search`의 `_resolve_to_parent`(377–400) 로직 재사용 |

---

## 7. 검증 시나리오

### 7.1 기능 검증

1. **CJK 원문검색**: `search_messages("다온 에이전트")` →
   trigram 경로에서 `ORDER BY rank` 결과 반환(기존 LIKE 폴백·시간순 탈피 확인).
   - 대조군: trigram 비활성 시 빈 결과 또는 시간순 폴백.
2. **BM25 정렬 보존**: 영문 다중어 쿼리에서 `rank` 오름차순(점수 내림차순) 확인.
3. **raw excerpt**: 매치 위치 **앞 120자**부터 잘려 앞뒤 `…` 표기되는지 경계 케이스
   (매치가 문자열 맨 앞/맨 뒤) 확인.
4. **around**: `get_around(sid, mid)`가 앞 6 + 자기 + 뒤 7(총 14) 반환,
   `exact=True`, anchor 행이 8000자까지 노출되는지.
5. **anchor 문법**: `#1234`(현재 세션), `sess_x#1234` 모두 파싱, 잘못된 형식은 오류 메시지.
6. **any-hit 폴백**: 다중 term 중 일부만 일치할 때 hit count 우선 정렬 확인.

### 7.2 회귀 검증

7. **기존 요약 모드**: `mode="summary"`(기본) 결과가 이식 전과 동일 JSON 구조인지.
8. **Unicode 영문 검색**: trigram 도입 후에도 영문 구문 검색 품질 비회귀.
9. **트리거 동기화**: `_save_session_to_db`(models.py 571) 후 신규 메시지가
   trigram 인덱스에도 즉시 반영(insert/delete 트리거).
10. **세션 삭제**: `delete_cli_session`(541) 후 FTS 두 인덱스 모두에서 결과 제거.

### 7.3 내구성 검증

11. **trigram 미지원 환경**: probe 실패 시 예외 없이 기존 경로 유지(graceful degrade).
12. **rebuild 멱등성**: `INSERT INTO ... ('rebuild')` 2회 실행해도 중복 rowid 없음.
13. **버전 불일치**: `fts_index_version` 강제 변경 후 자동 rebuild 트리거 확인.
14. **동시성**: `_execute_write`(165) BEGIN IMMEDIATE 재시도 하에서 인덱싱 무결성.

### 7.4 성능 검증

15. **재빌드 시간·잠금**: 대량 messages(예 10만) rebuild 소요 및 WAL 크기 측정.
16. **쿼리 지연**: trigram MATCH vs 기존 LIKE 폴백 p50/p95 비교.
17. **인덱스 크기**: 단일(A) vs 이중(B) 안의 `state.db` 크기 비교 → 최종안 확정.

---

## 부록: 인용 라인 색인

- ds-bot `chat-index.js`: 상수 15–21, `termQuery` 25–43, `createIndex` 103–126,
  `catchUp` 136–178, `searchOwnChat` 206–232, `latestOwnChat` 235–245,
  `aroundOwnChat` 248–259, `scanOwnChat` 263–277.
- ds-bot `text.js`: `excerpt` 27–31, `estimateTokens` 34–38.
- ds-bot `constants.js`: `OWN_CHAT_LINE_MAX` 53, `ANCHOR_LINE_MAX` 56, `OWN_CHAT_*` 57–59.
- 다온 `hermes_state.py`: `FTS_SQL` 94–113, `_sanitize_fts5_query` 1079–1130,
  `_contains_cjk` 1133–1146, `search_messages` 1148–1315, CJK LIKE 폴백 1230–1263,
  컨텍스트 CTE 1265–1309, `list_sessions_rich` 781–892, `sync`/삭제 경로 models.py 541–678.
- 다온 `session_search_tool.py`: `session_search` 318–516, 스키마 528–572, 등록 578–590.
