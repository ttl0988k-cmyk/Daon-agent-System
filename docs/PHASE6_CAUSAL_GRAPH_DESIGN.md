# Phase 6: 인과 그래프 설계 (fact → 산출물 연결)

> 상태: **구현 완료** (소스 반영, 재빌드 대기)
> 대상 파일: [`api/api/memory_store.py`](../api/api/memory_store.py), [`api/api/streaming.py`](../api/api/streaming.py)
> 전제: Phase 1~5 구현 완료 상태, 패치 2의 `_SCHEMA_VERSION` 체계 사용 (v1 → v2 승격)

---

## 1. 목표

현재 기억 시스템은 **"fact가 어느 세션에 주입됐는가"**(`fact_usage`)까지만 안다.
Phase 6은 그 세션에서 **"무엇이 만들어졌는가"** 를 연결해 다음을 가능하게 한다:

1. "이 fact를 근거로 생성된 파일: nail-salon.html" 추적 (6-B)
2. fact 삭제 시 생성 파일까지 포함한 영향 범위 보고 (6-C)
3. 장기적으로 fact → 세션 → 산출물 3층 인과 그래프 조회 API

그래프 구조:

```
facts ──(fact_usage)──> sessions ──(session_artifacts)──> artifacts(파일)
   │                                                        │
   └────(fact_artifacts: 명시적 엣지, confidence 0.9/0.4)───┘
```

---

## 2. 현재 코드 사실 (설계의 근거)

| 지점 | 위치 | 현황 |
|------|------|------|
| 도구 이벤트 수신 | [`on_tool()`](../api/api/streaming.py:409) | `tool.started`에서 `args` dict 수신(경로 추출 가능), `tool.completed`는 `args=None` + `is_error` 제공 |
| 파일 경로 추출 선례 | [`streaming.py:476`](../api/api/streaming.py:476) | `args.get('path') or args.get('file_path')` — write_file/patch에서 이미 사용 중 |
| 주입 기록 | [`_record_fact_usage()`](../api/api/memory_store.py:658) | `fact_usage(fact_id, session_id, injected_at)` — 세션 단위 연결의 원천 |
| 주입 시점 | [`streaming.py:1224`](../api/api/streaming.py:1224) | `build_memory_prompt()`는 **매 턴(메시지마다)** 호출되어 facts를 재랭킹 주입 |
| 세션 소유 | [`chat.js`](../static/modules/chat.js:159) | session_id는 서버 소유 — 새로고침/재방문에 재발급되지 않음 |
| 스키마 버전 | [`_SCHEMA_VERSION`](../api/api/memory_store.py:44) | 패치 2로 도입, Phase 6에서 `2`로 승격 |

핵심 통찰: `fact_usage`가 이미 fact↔세션 엣지이므로, **세션↔산출물 엣지만 추가하면**
fact→산출물 경로는 JOIN 하나로 완성된다. 별도 LLM 호출이 필요 없는 순수 기록형 설계.

---

## 3. 스키마 (6-A + 6-B) — 구현 완료

`_ensure_schema()`에 추가, `_SCHEMA_VERSION = 2`. 전부 `CREATE TABLE IF NOT EXISTS`(멱등).

```sql
-- 6-A: 세션 산출물 (도구 호출로 만들어진 것)
CREATE TABLE IF NOT EXISTS session_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,        -- 'file' (향후 'command', 'image' 확장)
    path TEXT NOT NULL,                 -- 원본 경로 (도구 인자 그대로)
    path_normalized TEXT NOT NULL DEFAULT '',  -- 워크스페이스 기준 정규화 키
    tool_name TEXT,                     -- write_file / patch / apply_diff
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(session_id, artifact_type, path_normalized)  -- 재편집 시 1행 유지
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON session_artifacts(session_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_path ON session_artifacts(path_normalized);

-- 6-B: fact → 산출물 명시적 엣지 (인과 그래프 본체)
CREATE TABLE IF NOT EXISTS fact_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL,
    artifact_id INTEGER NOT NULL,
    confidence REAL DEFAULT 0.4,        -- 0.9=직접 / 0.4=간접
    linked_at TEXT DEFAULT (datetime('now')),
    UNIQUE(fact_id, artifact_id),
    FOREIGN KEY (fact_id) REFERENCES facts(id),
    FOREIGN KEY (artifact_id) REFERENCES session_artifacts(id)
);
CREATE INDEX IF NOT EXISTS idx_fact_artifacts_fact ON fact_artifacts(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_artifacts_artifact ON fact_artifacts(artifact_id);
```

### 설계 결정 기록

- **fact_usage에 artifact 컬럼을 추가하지 않고 별도 엣지 테이블**을 쓰는 이유:
  - fact_usage는 "주입 사건" 로그(한 세션에 같은 fact가 여러 번 주입 가능)라 산출물과 1:1이 아님
  - 엣지 테이블은 fact 삭제 시 `DELETE FROM fact_artifacts WHERE fact_id=?`로 깨끗이 정리됨
  - 산출물 역방향 조회("이 파일은 어떤 fact들의 영향을 받았나?")가 인덱스로 즉시 가능
- **UNIQUE(session_id, artifact_type, path_normalized)**: 같은 파일을 10번 고쳐도 산출물 1건.
  정규화 키 기준이라 `test.html`과 `C:\...\test.html`이 같은 행으로 수렴.
- `session_artifacts`는 fact 삭제 후에도 보존 — 세션의 역사이지 fact의 부속이 아니다.

---

## 4. 엣지 신뢰도 (피드백 반영 — "주입 ≠ 사용" 완화)

**문제**: 세션 단위 엣지는 "이 세션에 한 번이라도 주입된 fact"를 전부 연결하므로
세션이 길수록 오탐이 누적된다 (예: "MCP playwright 죽음" fact가 무관한 파일 생성에 연결).

**해법 (채택안)**: 턴 카운터 없이 기존 **매 턴 재랭킹**을 무료 필터로 활용.

| 구분 | 조건 | confidence |
|------|------|-----------|
| 직접 영향 | 산출물 생성 **바로 그 턴**에 주입된 fact | 0.9 |
| 간접 영향 | 같은 세션 **이전 턴**에만 주입된 fact | 0.4 |

근거: 매 턴 쿼리 키워드로 facts를 재랭킹하므로, 파일 생성 턴에 주입됐다는 것은
관련성 점수가 실제로 높았다는 뜻. 무관한 fact는 재랭킹에서 탈락해 자동으로
"간접"으로 밀려난다.

구현 메커니즘:
- [`get_context_block()`](../api/api/memory_store.py:683)이 주입 시 이번 턴 fact id를
  인메모리 `_LAST_INJECTED_FACTS[session_id]`에 보관 (최근 100세션)
- [`streaming.py`](../api/api/streaming.py:1224)가 주입 직후
  `get_last_injected_fact_ids()`로 읽어 `_p6_turn_injected_facts`에 보관
- `record_session_artifact()`가 `direct_fact_ids`로 받아 confidence 분기
- 기존 엣지는 `ON CONFLICT ... confidence = MAX(...)` 로 승격만 되고 강등 없음

**정직한 한계**: 같은 턴 주입도 "증명"은 아니다(관련성 점수가 우연히 높을 수 있음).
엣지는 **근거 가능성**이지 증명이 아님을 UI에서 명시해야 한다.
3단계 세분화(턴 거리 가중치)는 필요 시 턴 카운터 인프라와 함께 추후 추가.

---

## 5. 캡처 파이프라인 (6-A) — 구현 완료

### 5-1. streaming.py `on_tool()`

`tool.completed`는 `args=None`이므로 **started에서 경로 보관 → completed에서 확정** 2단계.
`_p6_pending_artifacts`(경로)와 `_p6_turn_injected_facts`(이번 턴 주입 id)를
`_run_agent_streaming` 스코프에 두고, 기존 `_pending_file_edits`와 분리해
Monaco UX 로직과 상호작용 0. 전부 독립 try/except.

- `tool.started` + write_file/patch/apply_diff → 경로 보관
- `tool.completed` + `not is_error` → `record_session_artifact()` 호출
  (workspace=`s.workspace`, direct_fact_ids=이번 턴 주입 id 전달)

결정 근거:
- `tool.started` 시점에 기록하면 도구 실패 시 존재하지 않는 파일이 그래프에 남는다.
  기존 코드도 같은 이유로 [`streaming.py:450`](../api/api/streaming.py:450)에서
  `tool.completed and not is_error` 조건으로 파일을 재읽기한다.
- 전체 try/except — 캡처 실패는 채팅에 아무 영향 없음(순수 부가 원칙).

### 5-2. 경로 정규화 `_normalize_artifact_path(path, workspace)`

- 상대 경로면 워크스페이스 기준 join → `resolve()` → 워크스페이스 상대 POSIX 문자열
- 워크스페이스 밖 파일은 절대 경로 문자열 폴백
- 실패 시 원본 반환 (절대 예외 없음)

**기준은 STATE_DIR이 아니라 워크스페이스** — 파일 도구의 경로는 전부
[`streaming.py:454`](../api/api/streaming.py:454)처럼 `s.workspace` 기준 상대경로이기 때문.

### 5-3. memory_store.py `record_session_artifact()`

- `INSERT OR IGNORE` + 무시 시 기존 id 폴백 SELECT (재편집 대응)
- 이 세션에 주입됐던 fact(`fact_usage DISTINCT`)와 엣지 생성,
  `direct_fact_ids` 포함 여부에 따라 confidence 0.9/0.4
- 반환: artifact id. 절대 예외를 던지지 않는다.

---

## 6. 영향 범위 보고 확장 (6-C) — 구현 완료

[`delete_fact()`](../api/api/memory_store.py:400)의 impact 확장:

```python
impact = {'sessions': [...], 'derived_facts': [...], 'usage_count': N,
          'artifacts': [...],            # 전체 (confidence 포함)
          'direct_artifacts': [...],     # confidence >= 0.7
          'indirect_artifacts': [...]}   # 그 외
```

- 조회: `fact_artifacts JOIN session_artifacts WHERE fact_id=?`
- 삭제: `DELETE FROM fact_artifacts WHERE fact_id=?` (session_artifacts는 보존)

사용자 노출 형태:

```
이 기억을 근거로 한 항목:
├── 세션 3건
├── 파생 fact 2건 (#45, #67)
└── 생성 파일 1건 (직접: nail-salon.html)
```

[`memory_forget`](../api/api/streaming.py:1065) 도구(패치 3)는 이미 `impact` 전체를
반환하므로 **추가 수정 없이** direct/indirect artifacts가 함께 노출된다.

---

## 7. 관측성 — 구현 완료

`get_system_status()`에 그래프 통계 추가 (별도 try, 테이블 미존재 시 무영향):

```python
status['graph'] = {'artifacts_count': N, 'fact_edges_count': M}
```

---

## 8. 조회 API (보류 — YAGNI)

피드백 합의: `delete_fact` impact가 조회 수요의 90%를 커버하므로 쓰기 경로만 먼저.
아래 3종은 프론트 리뷰 UI(Phase 5-C)와 함께 필요 시 추가.

| 엔드포인트 | 용도 |
|-----------|------|
| `GET /api/memory/facts/<id>/impact` | 삭제 없이 영향 미리보기 |
| `GET /api/memory/artifacts?session_id=` | 세션의 산출물 목록 |
| `GET /api/memory/artifacts/<id>/facts` | 역방향: 이 파일에 영향을 준 fact들 |

---

## 9. 마이그레이션

1. `_SCHEMA_VERSION = 1 → 2` (PRAGMA user_version)
2. `_ensure_schema()`에 DDL 추가 (전부 IF NOT EXISTS → 기존 DB 무손상)
3. 기존 데이터: 과거 세션의 산출물은 기록이 없어 복원 불가 — **구현 시점 이후
   세션부터** 그래프가 자란다. 수용 가능.

---

## 10. 리스크와 대응

| 리스크 | 대응 |
|--------|------|
| on_tool 수정이 채팅 스트림을 깸 | 캡처 로직 전체를 독립 try/except로 격리, 기존 분기문은 건드리지 않고 추가만 함 |
| 실패한 파일 쓰기가 산출물로 기록됨 | `tool.completed and not is_error` 확정 시점 기록 |
| apply_diff의 경로 키 불일치 | `path`/`file_path` 둘 다 확인(기존 476줄 선례와 동일) |
| 같은 파일이 상대/절대 두 행으로 분기 | `path_normalized` 워크스페이스 기준 resolve, UNIQUE에 반영 |
| 세션 ID 불안정으로 엣지 분기 | DAON은 서버 소유 session_id라 해당 없음 (코드 확인 완료) |
| fact_usage에 없는 세션(주입 0건)의 산출물 | session_artifacts에만 기록, 엣지 0건 — 정상 |
| DB 잠금 경쟁 증가 | 파일 생성당 1트랜잭션 수준이라 무시 가능 |
| 그래프가 잘못된 인과를 주장 (주입≠사용) | confidence 2단계(직접 0.9/간접 0.4)로 완화 + "근거 가능성" 명시 |

---

## 11. 구현 체크리스트

- [x] 6-A 스키마: `_ensure_schema()`에 2개 테이블 + 인덱스 3개, `_SCHEMA_VERSION=2`
- [x] 6-A 캡처: `streaming.py on_tool()`에 `_p6_pending_artifacts` 2단계 기록
- [x] 6-B 연결: `record_session_artifact()` + `_normalize_artifact_path()` + confidence 분기
- [x] 6-B 주입 추적: `_LAST_INJECTED_FACTS` + `get_last_injected_fact_ids()` + streaming 보관
- [x] 6-C 보고: `delete_fact()`에 artifacts/direct/indirect + 엣지 정리
- [x] 관측성: `get_system_status()`에 `graph` 통계
- [x] 검증: py_compile 통과
- [ ] 시나리오 테스트 (재빌드 후: 채팅 → 파일 생성 → fact 삭제 시 impact 확인)
- [ ] (보류) 조회 API 3종 + 프론트 노출

## 12. 테스트 시나리오 (재빌드 후)

1. 채팅으로 fact 주입 상태 확인(`fact_usage` 행 존재)
2. 에이전트에 파일 생성 요청 (write_file)
3. `session_artifacts`/`fact_artifacts` 행 생성 확인, confidence 값 확인
4. `delete_fact(id)` → impact.direct_artifacts/indirect_artifacts에 파일 포함 확인
5. 삭제 후 `fact_artifacts` 엣지 제거, `session_artifacts` 보존 확인
6. 같은 파일 재편집 → 행 수 불변(UNIQUE) 확인
7. 같은 파일 상대/절대 경로 혼재 → path_normalized 수렴 확인
