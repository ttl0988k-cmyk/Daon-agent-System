# DAON 기억 시스템 업그레이드 플랜 — "Argo를 넘어서"

> 작성일: 2026-07-28
> 현재 상태: [`memory_store.py`](../api/api/memory_store.py) 1060라인, SQLite 기반
> 목표: Argo(파일+wikilink+정제데몬)보다 **구조적으로 우월한** 기억 시스템

---

## 철학: "사람의 기억"이 아니라 "AI의 작업 기억"

이 시스템의 목적은 사람의 지식을 저장하는 것이 아니다.
**사람과 같이 일하는 AI가, 다음 대화에서 조금 더 맥락을 알고 시작하게 하는 것**이다.

- 1차 소비자: AI (시스템 프롬프트에 주입되는 컨텍스트)
- 2차 소비자: 사람 (디버깅·신뢰·검수를 위한 열람)

따라서 "가독성"은 부차적 지표다. Argo의 마크다운 파일이 사람 눈에 예쁜 것은
사실이지만, 그 기억을 실제로 읽는 것은 LLM이다.
진짜 지표는 **"AI가 적절한 순간에 적절한 맥락을 꺼내 쓰는가"** 다.

이 관점에서 Argo의 "파일 가독성 우위"는 재평가된다:
- 사람이 열어서 고칠 수 있는 것은 장점 (검수·신뢰)
- 하지만 AI 입장에서는 파일이든 SQLite든 주입된 텍스트만 보일 뿐
- AI의 "기억 체감"을 좌우하는 것은 **저장 형식이 아니라 주입 품질**

결론: 저장 형식(파일 vs DB) 논쟁은 본질이 아니다.
**"무엇을, 언제, 얼마나 주입하느냐"** 가 AI 기억의 전부다.

### 역할 분리: AI 기억 ≠ 사람 기록

사람이 기억하고 싶으면 **옵시디언/노션에 쓰면 된다.** 이미 존재하는 도구다.
AI가 굳이 마크다운으로 "일기"를 쓸 이유가 없다.

| | 사람 기록 | AI 작업 기억 |
|---|---|---|
| 소유자 | 사람 | AI (시스템) |
| 도구 | 옵시디언, 노션 | DAON SQLite |
| 형식 | 마크다운 (사람 가독) | 구조화 데이터 (주입 최적화) |
| 쓰기 주체 | 사람 | 자동 추출 (LLM) |
| 읽기 주체 | 사람 | AI (프롬프트 주입) |
| 목적 | 장기 보존·회고 | 다음 대화 맥락 로드 |

**Argo의 오류**: AI 기억을 마크다운 파일로 만들어 "사람도 읽을 수 있게" 했다.
하지만 사람은 이미 자기 기록 도구가 있다. AI 기억까지 사람이 관리할 필요가 없다.

**DAON의 포지션**: AI 기억은 AI 전용이다. 사람이 열어서 볼 필요도,
마크다운으로 예쁘게 만들 필요도 없다. SQLite에 구조화되어 있고,
주입 시 텍스트 블록으로 변환되어 AI가 소비하면 된다.
사람이 검수하고 싶을 때만 UI에서 열람하면 충분하다.

**교차점**: AI가 옵시디언 vault를 *읽을* 수는 있다 (filesystem MCP).
하지만 AI가 거기에 *쓰는* 것은 아니다. 사람 기록과 AI 기억은 겹치지 않는다.

### 내보내기는 "자동"이 아니라 "한마디"

AI가 사람 기록 영역(옵시디언)에 쓰는 경우는 **사용자가 명시적으로 요청할 때뿐**이다.

> "이 세션 정리해서 옵시디언 노트에 기록해줘" — 한마디면 된다.

- 자동 기록 ❌ : 매 세션마다 AI가 vault에 노트를 생성하면,
  사람은 원치 않는 파일 수백 개를 정리해야 한다.
- 명시적 내보내기 ✅ : 사용자가 "이건 남길 가치가 있다"고 판단한 순간에만
  AI가 filesystem MCP로 마크다운 노트를 생성한다.
- 형식도 사용자가 정한다 : "사람이 보기 좋게" 쓸지, "AI 요약 형태로" 쓸지
  요청 시점에 지시하면 된다. AI가 알아서 형식을 결정하지 않는다.

이 원칙은 Argo의 "자동 노트 생성"과 정반대다.
Argo는 AI가 자동으로 vault에 노트를 쌓지만, DAON은 **사람의 한마디가 트리거**다.
기록의 소유권은 처음부터 끝까지 사람에게 있다.

### 무결성: "열어서 고칠 수 있으면" 기억이 아니다

Argo는 "사람이 열어서 고칠 수 있다"를 장점으로 내세운다.
하지만 이것은 **오염 경로**다.

> 욕 잘하고 신경질적인 사람이, 기억 파일을 열어서
> "아주 얌전하고 신사적인 사람"으로 고치면?
> 그게 AI의 기억이 되는 건가? 그게 바로 오염이다.

- AI 기억 = AI가 대화에서 **학습**한 것
- 사람이 파일을 고치는 것 = 학습이 아니라 **주입**
- 주입된 것은 기억이 아니라 거짓말

따라서 DAON의 원칙:
- **삭제(잊어줘)** ✅ : 사람이 "이건 틀렸어, 지워" → AI가 삭제
- **수정(이렇게 기억해)** ❌ : 사람이 파일을 직접 고치는 것은 오염
- **새 학습** ✅ : "나 사실 신사야"라고 대화에서 말하면 → AI가 새 fact로 학습
  → 기존 "신경질적" fact와 모순 감지 → confidence 조정 → 자동 정제

기억의 무결성은 **AI가 쓰고 AI가 정제**할 때만 유지된다.
사람의 역할은 "삭제 요청"과 "새로운 대화"뿐이다.
파일을 열어서 직접 고치는 순간, 그건 기억 시스템이 아니라 수동 메모장이다.

---

## 0. 현재 문제 요약 (코드 근거)

| 문제 | 위치 | 증상 |
|------|------|------|
| 프로필 key 분산 | [`update_profile_from_messages()`](../api/api/memory_store.py:675) | `선호언어`/`선호 언어`/`사용언어`/`언어` = 5개 행 |
| facts 의미 중복 | [`add_fact()`](../api/api/memory_store.py:167) | 완전 일치만 스킵(177), 의미 중복은 무한 추가 |
| summaries 중복 | [`summarize_session()`](../api/api/memory_store.py:700) | session_id UNIQUE 없음, 같은 세션 N건 |
| 주입 무차별 | [`get_context_block()`](../api/api/memory_store.py:378) | 최근 20건 나열, 관련성 무시 |
| 정제 없음 | [`_run_maintenance()`](../api/api/memory_store.py:906) | 500건 상한 삭제만, 병합/정제 없음 |
| 계보 없음 | 스키마 전체 | fact가 어떤 답변에 기여했는지 기록 없음 |

---

## 1. 설계 원칙 — Argo와 다른 점

| | Argo | DAON 목표 |
|---|---|---|
| 저장 | 마크다운 파일 | SQLite (구조적 쿼리) |
| 중복 제거 | 사람이 파일 편집 | **LLM 의미 비교 자동 병합** |
| 계보 | wikilink (수동) | **SQL 외래키 (자동 기록)** |
| 주입 | 전체 파일 로드 | **관련성 랭킹 주입** |
| 정제 | 1일 1회 데몬 | **상시 워커 + 일일 심화 정제** |
| 모순 | 사람이 발견 | **자동 감지 + 플래그** |
| 감쇠 | 없음 | **사용 빈도 기반 강화/감쇠** |

핵심: Argo는 "사람이 시작하는 역추적", DAON은 **"시스템이 자동으로 기록하고, 사람이 승인하는"** 구조.

---

## 2. 단계별 구현

### Phase 1: 정규화 + 중복 제거 (기초 체력)
**예상: 1~2일, 기존 코드 수정 위주**

#### 1-A. 프로필 key 정규화
- [`update_profile_from_messages()`](../api/api/memory_store.py:675) 수정
- 정규 스키마 정의:

```python
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
```

- LLM 추출 프롬프트에 "반드시 아래 key 중 하나를 사용하라: {keys}" 지시
- 기존 분산 key는 마이그레이션 스크립트로 통합
- `_` 접두사 내부 메타키는 유지 (`_last_chat_ts`)

#### 1-B. facts 의미 중복 제거
- [`add_fact()`](../api/api/memory_store.py:167)에 추가 단계:
  1. 기존 facts 중 최근 50건 로드
  2. LLM 1회 호출: "새 fact와 기존 facts 중 의미적으로 중복/모순되는 것의 id를 반환하라"
  3. 중복 → 기존 fact 갱신(UPDATE), 모순 → 기존 fact에 `superseded_by` 플래그
  4. 신규 → INSERT
- 비용: 세션당 LLM 호출 1회 추가 (facts 추출과 합산 가능)

#### 1-C. summaries 세션당 1건
- 스키마: `summaries.session_id`에 UNIQUE 제약 추가
- [`summarize_session()`](../api/api/memory_store.py:700): `INSERT OR REPLACE`
- 기존 중복 데이터 정리 마이그레이션

#### 1-D. 기존 데이터 일괄 정리
- 마이그레이션 스크립트: 프로필 50개 key → 15개 이내로 병합
- facts 126건 → LLM으로 의미 병합 → 30건 내외로 축소
- summaries 중복 제거

---

### Phase 2: 계보 그래프 (Argo의 wikilink를 SQL로)
**예상: 2~3일, 스키마 확장**

#### 2-A. 스키마 확장

```sql
-- facts에 계보 컬럼 추가
ALTER TABLE facts ADD COLUMN derived_from TEXT;  -- 'fact:12,fact:34' (파생 출처)
ALTER TABLE facts ADD COLUMN confidence REAL DEFAULT 1.0;  -- 신뢰도 (모순 시 감소)
ALTER TABLE facts ADD COLUMN superseded_by INTEGER DEFAULT NULL;  -- 대체된 fact id
ALTER TABLE facts ADD COLUMN use_count INTEGER DEFAULT 0;  -- 주입 사용 횟수
ALTER TABLE facts ADD COLUMN last_used_at TEXT;  -- 마지막 사용 시각

-- 주입 기록: 어떤 세션에 어떤 fact가 주입됐는지
CREATE TABLE IF NOT EXISTS fact_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    injected_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (fact_id) REFERENCES facts(id)
);
CREATE INDEX IF NOT EXISTS idx_fact_usage_fact ON fact_usage(fact_id);
CREATE INDEX IF NOT EXISTS idx_fact_usage_session ON fact_usage(session_id);
```

#### 2-B. 주입 시 사용 기록
- [`get_context_block()`](../api/api/memory_store.py:378) 수정:
  - 주입된 fact id 목록을 `fact_usage`에 기록
  - `facts.use_count += 1`, `last_used_at = now` 갱신

#### 2-C. 삭제 시 역링크 조회
- [`delete_fact()`](../api/api/memory_store.py:205) 수정:
  - 삭제 전 `fact_usage`에서 "이 fact가 주입된 세션 N건" 목록 반환
  - `derived_from`에 이 fact id를 포함한 하위 fact 목록 반환
  - UI에 "이 기억을 근거로 한 항목 N건" 표시

#### 2-D. 추출 시 계보 기록
- [`extract_and_store_facts()`](../api/api/memory_store.py:649) 수정:
  - LLM 프롬프트에 "기존 facts 목록을 보고, 새 fact가 기존 fact에서 파생된 것이면 derived_from에 id 기록"
  - 또는: 같은 세션에서 나온 facts끼리 `derived_from` 자동 연결

---

### Phase 3: 관련성 랭킹 주입 (Argo 못 하는 것)
**예상: 2~3일, 주입 로직 재설계**

#### 3-A. 현재 문제
[`get_context_block()`](../api/api/memory_store.py:378)은 `ORDER BY id DESC LIMIT 20` — 최근 20건 무차별 나열.
126건 중 15건이 같은 버그 이야기면, 다른 유용한 기억이 밀려남.

#### 3-B. 랭킹 공식

```python
def _fact_score(fact: dict, query_keywords: list[str]) -> float:
    """fact의 주입 우선순위 점수 계산."""
    score = 0.0

    # 1) 관련성: 현재 대화 키워드와 fact 내용의 겹침
    content = fact['content'].lower()
    keyword_hits = sum(1 for kw in query_keywords if kw in content)
    score += keyword_hits * 10.0

    # 2) 사용 빈도: 자주 주입된 fact = 검증된 fact
    score += min(fact.get('use_count', 0) * 0.5, 5.0)

    # 3) 최신성: 최근 fact 가중치
    age_days = (now - parse(fact['created_at'])).days
    score += max(0, 10.0 - age_days * 0.1)

    # 4) 신뢰도: 모순 플래그가 있으면 감점
    score *= fact.get('confidence', 1.0)

    # 5) 대체됨: superseded_by가 있으면 주입 제외
    if fact.get('superseded_by'):
        return -1.0

    return score
```

#### 3-C. 주입 파이프라인 변경

```
현재: list_facts(limit=20) → 나열
목표:
  1. 현재 사용자 메시지에서 키워드 추출 (간단한 형태소/명사 추출)
  2. 전체 facts 로드 (500건 이하라 가능)
  3. _fact_score()로 정렬
  4. 상위 15건 + 프로필 주입
  5. fact_usage에 기록
```

#### 3-D. 카테고리별 쿼터
- `general` 10건 + `preference` 3건 + `project` 2건 식으로 카테고리 분산
- 한 주제의 facts가 20건을 독점하는 것 방지

---

### Phase 4: 자동 정제 워커 (Argo의 정제 데몬을 상시로)
**예상: 2일, 기존 워커 확장**

#### 4-A. 상시 정제 (매 세션 후)
- [`_process_session_sync()`](../api/api/memory_store.py:887)에 추가:
  - facts 추출 후, 즉시 의미 중복 검사 (Phase 1-B)
  - 프로필 추출 후, 정규 key 매핑 (Phase 1-A)

#### 4-B. 일일 심화 정제
- [`_run_daily()`](../api/api/memory_store.py:928)에 추가:

```python
def _run_daily_refine():
    """일일 심화 정제: facts 병합 + 모순 해결 + 감쇠."""
    facts = list_facts(limit=500)
    if len(facts) < 10:
        return

    # 1) 의미적 클러스터링: LLM으로 유사 facts 그룹화
    #    "아래 facts를 의미적으로 그룹화하라. 같은 그룹의 id를 배열로 반환."
    clusters = _llm_cluster_facts(facts)

    # 2) 각 클러스터: 대표 fact 1건으로 병합
    #    "이 그룹의 facts를 하나의 문장으로 통합하라"
    for cluster in clusters:
        if len(cluster) > 1:
            merged = _llm_merge_facts(cluster)
            # 대표 fact 갱신, 나머지 superseded_by 처리

    # 3) 모순 감지: "아래 facts 중 서로 모순되는 쌍을 찾아라"
    contradictions = _llm_find_contradictions(facts)
    for old_id, new_id in contradictions:
        # 최신 fact를 정답으로, 오래된 fact에 superseded_by 설정

    # 4) 감쇠: 30일 이상 미사용 fact는 confidence *= 0.8
    #    90일 이상 미사용 + confidence < 0.3 → 자동 삭제
```

#### 4-C. 정제 이력 기록

```sql
CREATE TABLE IF NOT EXISTS refine_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,  -- 'merge', 'supersede', 'decay', 'delete'
    fact_ids TEXT,         -- 영향받은 fact id 목록
    detail TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

- 모든 자동 정제 동작을 기록 → 사람이 사후 검토 가능
- Argo의 "원본 일지 삭제 안 함"에 대응: **정제 전 스냅샷을 refine_log.detail에 보존**

---

### Phase 5: 모순 감지 + 사람 승인 (Argo의 "사람 잠금"을 자동화)
**예상: 2일, UI 연동**

#### 5-A. 모순 자동 감지
- 새 fact 추가 시 기존 facts와 LLM 비교:
  - "기존: 'MCP playwright는 응답 없음' / 신규: 'MCP playwright 정상 작동'"
  - 모순 감지 → 기존 fact에 `confidence = 0.3`, 새 fact에 `derived_from = 'fact:N'`

#### 5-B. 재검토 큐
- [`agent_inbox`](../api/api/memory_store.py:104) 패턴 재사용:

```sql
CREATE TABLE IF NOT EXISTS memory_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,  -- 'contradiction', 'merge_candidate', 'low_confidence'
    fact_ids TEXT,
    suggestion TEXT,     -- LLM이 제안하는 처리
    status TEXT DEFAULT 'pending',  -- pending/approved/rejected
    created_at TEXT DEFAULT (datetime('now'))
);
```

#### 5-C. UI 노출
- 기억 패널에 "재검토 대기 N건" 배지
- 클릭 시 "기존: X / 신규: Y / 제안: Z" 카드 표시
- 승인 → 자동 처리, 거부 → 원복

---

### Phase 6: 인과 그래프 완성 (@kimse7858의 질문에 대한 답)
**예상: 3일, 장기 과제**

#### 6-A. 도구 호출 인자 기록
- 현재: 도구 사용 횟수만 기록
- 목표: [`streaming.py on_tool()`](../api/api/streaming.py:406)에서 도구 인자(파일 경로 등)를 세션 메타에 기록

#### 6-B. fact → 산출물 연결
- fact가 주입된 세션에서 생성된 파일/응답을 `fact_usage`에 연결
- "이 fact를 근거로 생성된 파일: nail-salon.html" 추적 가능

#### 6-C. 폐기 시 영향 범위 보고
- fact 삭제 시:
  ```
  이 기억을 근거로 한 항목:
  ├── 세션 3건 (2026-07-25, 07-26, 07-27)
  ├── 파생 fact 2건 (#45, #67)
  └── 생성 파일 1건 (nail-salon.html)
  [모두 재검토 표시] [취소]
  ```

---

## 3. Argo 대비 우위 정리

| 능력 | Argo | DAON (Phase 1~6 후) |
|------|------|---------------------|
| 저장 가독성 | ✅ 파일 | ⚠️ SQLite (UI로 보완) |
| 중복 제거 | 수동 | ✅ **자동 의미 병합** |
| 계보 추적 | wikilink (반자동) | ✅ **SQL 자동 기록** |
| 관련성 주입 | ❌ 전체 로드 | ✅ **랭킹 주입** |
| 모순 감지 | ❌ | ✅ **자동 + 사람 승인** |
| 감쇠/강화 | ❌ | ✅ **사용 빈도 기반** |
| 정제 주기 | 1일 1회 | ✅ **상시 + 일일 심화** |
| 파생물 역추적 | ❌ (로드맵) | ✅ **Phase 6** |
| 정제 이력 | 원본 보존 | ✅ **refine_log + 스냅샷** |

**결론**: Argo의 강점(가독성, grep)은 파일 기반의 본질적 장점이라 SQLite로는 완전 복제 불가.
하지만 **추적·정제·주입의 자동화**에서는 DAON이 구조적으로 앞설 수 있음.
"사람이 시작하는 역추적"(Argo) → **"시스템이 기록하고 사람이 승인하는 역추적"**(DAON).

---

## 4. 구현 우선순위

```
Phase 1 (즉시)  ← 체감 효과 최대, 기존 코드 수정 위주
  1-A 프로필 정규화
  1-B facts 의미 중복 제거
  1-C summaries UNIQUE
  1-D 기존 데이터 정리

Phase 2 (1주차) ← 계보의 뼈대
  2-A 스키마 확장
  2-B 주입 기록
  2-C 역링크 조회

Phase 3 (2주차) ← 주입 품질
  3-A~D 랭킹 주입

Phase 4 (2주차) ← 자동 정제
  4-A~C 정제 워커

Phase 5 (3주차) ← 안전장치
  5-A~C 모순 + 승인 UI

Phase 6 (장기)  ← 인과 그래프
  6-A~C 도구 인자 → 산출물 연결
```

---

## 5. 비용 추정

| 항목 | 현재 | Phase 1~4 후 |
|------|------|-------------|
| 세션당 LLM 호출 | 3회 (facts/profile/summary) | 5회 (+중복검사 +정규화) |
| 일일 LLM 호출 | 0회 | 1~2회 (심화 정제) |
| DB 크기 | ~1MB | ~2MB (fact_usage, refine_log) |
| 주입 토큰 | ~2000 (20건 나열) | ~1200 (15건 랭킹) |

LLM 호출 증가는 `_call_direct()`(경량 모델) 사용 시 무시 가능한 수준.

---

## 6. 리스크

| 리스크 | 대응 |
|--------|------|
| LLM 중복 검사가 오판 (다른 fact를 중복으로 판정) | confidence만 낮추고 즉시 삭제 안 함, refine_log에 기록 |
| 정규화 스키마가 새 개념을 못 담음 | `notes` 자유 key 유지, 스키마 확장 가능 |
| 랭킹 주입이 현재 대화와 무관한 fact를 놓침 | 키워드 추출 실패 시 폴백: 최근 5건은 무조건 포함 |
| 정제 워커가 사용자 대화 중 facts를 수정 | `_db_lock`으로 직렬화, 정제는 읽기 전용 스냅샷 기반 |
