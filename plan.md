# Daon Creative Director + Reference Library 통합 계획

- 작성일: 2026-08-06
- 상태: **승인 대기**
- 범위: Phase 1 코어 통합 + 일반 디자인 요청의 Creative Director 강제 연결 설계

## 1. 목표

다온 에이전트 시스템이 디자인/UI 생성 요청을 받을 때 평균적인 템플릿으로 바로 코드를 생성하지 않고, 다음 의사결정 단계를 거치도록 한다.

```text
디자인 요청
  → 디자인 요청 판별
  → Reference Library 검색
  → Creative Director Design Brief 생성
  → Frontend Agent에 Brief 전달
  → 기존 코드 생성/검수 흐름
```

핵심 성공 기준은 기능 개수가 아니라 다음 질문에 대한 답이다.

> 디자인 요청이 들어왔을 때 Creative Director와 Style Card 검색을 우회해서 코드가 생성되지 않는가?

## 2. 사전 조사 결과

### 이미 존재하는 구현

- `api/api/style_card.py`
  - `StyleCard`, `StyleCardRegistry`, `DesignGraph`, `ComponentCard`가 이미 구현되어 있음
  - YAML 직렬화/역직렬화, 평가 점수, Brief 요약, 컴포넌트 분해 기능이 존재
- `api/api/dynamic/style_card_retriever.py`
  - TF-IDF 기반 검색기와 `retrieve()`/`rebuild_index()`가 이미 구현됨
- `api/api/dynamic/style_mixer.py`
  - 컴포넌트 검색 및 Unified Design Brief 생성 흐름이 이미 구현됨
- `api/api/mcp/daon_design_mcp.py`
  - Style Card 검색/오케스트레이션 MCP 도구가 이미 연결됨
- `api/api/routes/style_card_routes.py`
  - 목록/검색/상세/추출/저장/삭제/인덱스 재생성 REST 라우트가 이미 존재
- `api/api/dynamic/style_card_extractor.py`
  - 텍스트/완성된 UI 결과에서 Style Card 후보를 추출하는 흐름이 존재
- `skills/Design/creative-director/skill.yaml`
  - 스켈레톤 메타데이터만 존재하며 실제 판단 절차와 필수 출력 규칙은 없음

### 현재 통합상의 문제/주의점

1. 기존 Reference Library 경로는 `~/.hermes/references/` 또는 프로필별 경로이며, 합의한 공용 저장소 `data/reference_library/`와 다름.
2. `StyleCardRegistry.rebuild_index()`는 현재 활성 프로필 경로를 직접 사용하므로 저장/검색 경로를 한 곳에서 주입할 수 있게 정리해야 함.
3. `StyleCard`와 Retriever는 존재하지만 일반 에이전트 요청에서 Creative Director를 반드시 선행시키는 게이트가 확인되지 않음.
4. Creative Director 스킬은 `skill.yaml`만 있고 `SKILL.md`가 없음.
5. 현재 REST 저장/삭제 라우트에는 대표님 승인 상태를 명시하는 운영 경계가 아직 문서/모델로 고정되어 있지 않음. Phase 1에서는 기존 승인 UI/흐름을 깨지 않도록 자동 등록을 새로 만들지 않고, 정책과 확장 지점만 명확히 한다.
6. 기존 구현을 새 `models/style_card.py`로 중복 복사하지 않는다. 먼저 현재 `api.style_card.StyleCard`를 정식 코어 모델로 유지하고, 필요하면 이후 호환 모듈을 추가한다.

## 3. 설계 원칙

- Reference Library는 다온 시스템 공용 단일 라이브러리로 운영한다.
- 외부 탐색/후보 분석과 실제 라이브러리 등록은 분리한다.
- 신규 등록·수정·삭제는 대표님 승인 없이는 실행하지 않는다.
- Creative Director는 디자인 요청의 필수 중간 단계다.
- 버그 수정, 작은 CSS 수정, 단순 텍스트 수정 등은 강제 게이트에서 제외한다.
- Frontend Agent는 Style Card를 직접 검색하지 않고 Design Brief만 받는다.
- Phase 1 검색은 TF-IDF/메타데이터 기반으로 제한하고, 이미지 임베딩 인터페이스는 확장 지점만 만든다.
- 기존 Style Card/Style Mixer/MCP API를 재사용하여 중복 시스템을 만들지 않는다.
- 모든 변경은 기존 API 계약과 기존 사용자 라이브러리를 보존하는 하위 호환 방식으로 진행한다.

## 4. 구현 범위

### 4.1 공용 Reference Library 경로 및 스키마

대상:

- `data/reference_library/`
  - `cards/` — 승인된 Style Card YAML
  - `screenshots/` — 선택적 원본/미리보기 이미지
  - `embeddings/` — Phase 3용 예약 디렉토리
  - `schema/style_card_schema.yaml` — 문서화/검증용 스키마
  - `index.yaml` — 검색 인덱스 메타데이터

작업:

- 공용 경로 resolver를 추가하고 기존 `~/.hermes/references` 사용자를 마이그레이션/폴백 대상으로 둔다.
- 경로를 모듈 전역에 흩뿌리지 않고 Registry/Retriever가 같은 resolver를 사용하게 한다.
- YAML 스키마에 다음 필드를 고정한다.
  - `id`, `name`, `version`, `created`, `updated`
  - `category`, `sub_category`, `tags`
  - `source_url`, `source_type`, `source_author`
  - `design_dna.colors`, `typography`, `layout`, `animation`, `spacing`
  - `composition`, `guidelines.do`, `guidelines.dont`
  - `compatible_with`, `conflicts_with`
  - `evaluation`
  - `visual_embedding`은 Phase 3 예약 필드로만 정의
  - `approval`/`provenance`는 승인 경계 추적을 위해 정의하되 자동 승인하지 않음
- 기존 YAML의 누락 필드는 현재 기본값으로 읽히도록 한다.

### 4.2 StyleCard 모델 정리

대상:

- `api/api/style_card.py`

작업:

- 현재 모델을 단일 정식 모델로 명시하고, 새 모델 파일을 중복 생성하지 않는다.
- 공용 경로를 주입할 수 있도록 `get_references_dir()`/인덱스 관련 메서드를 정리한다.
- `from_dict`, `to_dict`, `to_yaml`, `from_yaml`, `to_brief_text`의 스키마 호환성을 테스트한다.
- 승인 상태와 출처 메타데이터가 손실되지 않도록 선택 필드를 추가한다.
- 라이브러리 파일 삭제 시 Registry 메모리만 삭제되고 디스크 파일이 남는 현재 동작을 점검하여 일관되게 만든다. 단, 승인 정책을 우회하는 자동 삭제는 추가하지 않는다.

### 4.3 Retriever 공개 도구 계약

대상:

- `api/api/dynamic/style_card_retriever.py`
- `api/api/mcp/daon_design_mcp.py`
- 관련 테스트

작업:

- 다음 논리 계약을 고정한다.

```python
retrieve_style_cards(
    component: str | None,
    intent: str,
    constraints: dict | None = None,
    top_k: int = 5,
) -> list[StyleCardMatch]
```

- `component`, `intent`, `constraints`를 기존 Retriever의 category/filter/검색어로 변환한다.
- constraints는 최소한 `originality`, `motion_intensity`, `visual_density`를 지원한다.
- 검색 결과에는 `card_id`, `score`, `brief_text`, `decision_relevant_fields`를 포함한다.
- 결과가 없을 때 빈 결과를 명확히 반환하고, 검색 실패가 디자인 요청 전체를 조용히 우회시키지 않게 한다.
- MCP 도구와 내부 Python 호출이 같은 검색 로직을 사용하게 한다.
- Phase 3에서 visual embedding을 추가할 수 있도록 Retriever interface에 optional backend 포인트만 둔다.

### 4.4 Creative Director SKILL.md 및 Design Brief 계약

대상:

- `skills/Design/creative-director/SKILL.md`
- `skills/Design/creative-director/skill.yaml`
- 필요 시 Brief template 파일

작업:

- 4-layer 절차를 명시한다.
  1. UX Researcher/Analyst — 사용자·목표·콘텐츠·제약 분석
  2. Design Librarian/Intelligence — 공용 Reference Library 검색 및 근거 수집
  3. Art Director/Critic — 평범함·충돌·접근성·구현 위험 비평
  4. Creative Director/Decision — 하나의 방향으로 결정
- 필수 출력 블록을 고정한다.
  - `[DESIGN_BRIEF]`
  - `[DESIGN_DNA]`
  - `[SELECTED_REFERENCES]`
  - `[DECISION_RATIONALE]`
- “검색 없이 임의 스타일 선택”, “Frontend Agent에 raw Style Card 전달”, “여러 방향을 결정 없이 나열”을 금지 규칙으로 둔다.
- 평범한 SaaS/히어로/카드/그라디언트 조합으로 수렴하지 않도록 차별화 결정과 금지 목록을 Brief에 포함한다.
- 단순 버그/CSS 수정 예외를 명시한다.
- Frontend Agent 입력은 Brief 중심이며 원본 라이브러리 검색 권한을 요구하지 않도록 한다.

### 4.5 일반 모드 Creative Director 게이트

대상 후보:

- `api/api/dynamic/compiler.py`
- `api/api/dynamic/planner.py`
- 일반 채팅 프롬프트 조립/도구 라우팅 모듈
- 필요 시 `api/api/routes/chat_routes.py`

작업:

- 요청이 디자인/UI 생성인지 판별하는 순수 함수와 예외 판별 함수를 만든다.
- 디자인 요청이면 다음 순서를 강제한다.
  1. Creative Director 스킬 로드
  2. Retriever 호출
  3. 4개 필수 블록이 있는 Design Brief 생성
  4. 이후 Frontend Agent/코드 생성 단계 진행
- Brief가 없거나 필수 블록이 누락되면 Frontend 단계로 진행하지 않도록 한다.
- 일반 모드와 Dynamic Harness가 동일한 판별/계약을 공유하도록 한다.
- 하네스의 4-layer DAG 분리는 Phase 2로 넘기되, Phase 1 게이트가 재사용 가능한 인터페이스를 제공하게 한다.
- 버그 수정/CSS 미세 변경은 기존 흐름을 보존한다.

### 4.6 검증

추가/갱신할 테스트:

- StyleCard YAML round-trip 및 누락 필드 기본값
- 공용 경로 resolver 및 기존 레거시 경로 폴백
- Registry load/add/remove/index 동작
- Retriever 검색/카테고리/constraints/top_k/빈 결과
- MCP retriever contract가 내부 retriever와 동일한 결과를 반환하는지
- 디자인 요청 판별 및 예외 판별
- Brief 필수 블록 검증
- 디자인 요청에서 Brief 없는 Frontend 진행 차단
- 비디자인 요청과 단순 버그/CSS 수정이 불필요하게 차단되지 않는지
- 기존 Style Card REST endpoint 회귀 테스트

검증 명령은 구현 시 저장소의 실제 Python 환경/테스트 구조를 확인한 뒤 확정한다. 우선 `api` 모듈 import smoke test와 관련 pytest만 실행하고, 이후 전체 테스트를 실행한다.

## 5. 단계별 실행 순서

1. **승인 전 조사 완료** — 현재 구현/경로/진입점을 이 계획에 반영함
2. **모델·경로·스키마 정리** — 기존 구현을 보존하면서 공용 라이브러리 계약 확정
3. **Retriever 계약 고정** — 내부 호출과 MCP 호출 통합
4. **Creative Director SKILL.md 작성** — 판단 절차·필수 출력·금지 규칙 확정
5. **일반 모드 게이트 구현** — 디자인 요청만 선행 Brief 강제
6. **테스트 및 회귀 검증** — 기존 REST/MCP/Style Mixer 영향 확인
7. **결과 보고** — 변경 파일, 테스트 결과, 남은 Phase 2 항목 보고

## 6. Phase 1에서 하지 않는 것

- CLIP/SigLIP 등 실제 이미지 임베딩 백엔드
- 자동 외부 레퍼런스 수집 및 무승인 등록
- 사용자별 Design Memory
- Dynamic Harness 4개 에이전트 DAG의 완전한 분리
- Frontend Agent 자체의 대규모 리팩터링
- UI 대시보드 전면 개편

## 7. 위험요소와 대응

| 위험 | 대응 |
|---|---|
| 기존 프로필별 라이브러리 사용자 데이터 손실 | 공용 경로 우선 전환 전 읽기 폴백/마이그레이션 테스트 |
| 기존 API/MCP 호출 회귀 | 기존 함수 시그니처 유지, 계약 테스트 추가 |
| 디자인 판별 오탐으로 일반 작업 차단 | 명시적 예외 규칙과 순수 판별 테스트 |
| Creative Director가 형식만 출력하고 실질 검색을 생략 | Retriever 호출 결과를 Brief의 `SELECTED_REFERENCES`에 연결하고 검증 |
| 승인 정책 우회 | Phase 1 자동 등록/삭제를 만들지 않고 승인 메타데이터와 경계만 정의 |
| 현재 구현과 계획의 중복 | 새 StyleCard 클래스를 만들지 않고 기존 코어 모델을 정식화 |

## 8. 완료 기준

- [ ] `data/reference_library/` 공용 구조와 스키마가 존재한다.
- [ ] 기존 StyleCard 모델이 공용 경로/스키마와 호환된다.
- [ ] `retrieve_style_cards()` 계약이 내부 및 MCP에서 사용된다.
- [ ] Creative Director SKILL.md가 4-layer와 필수 출력 블록을 정의한다.
- [ ] 디자인 요청은 Brief 없이 코드 생성 단계로 진행되지 않는다.
- [ ] 비디자인 요청과 단순 수정은 기존 동작을 유지한다.
- [ ] 관련 테스트와 회귀 테스트가 통과한다.
- [ ] 대표님께 변경 사항과 Phase 2 이월 범위를 보고한다.

## 9. 승인 후 첫 작업

승인되면 먼저 `StyleCard`의 실제 import 경로와 공용/레거시 경로 정책을 코드로 고정하고, 그 다음 스키마·Retriever·SKILL.md·게이트 순서로 구현한다. 승인 전에는 코드/설정 파일을 변경하지 않는다.
