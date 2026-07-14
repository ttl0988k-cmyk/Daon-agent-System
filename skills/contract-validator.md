---
name: contract-validator
version: "1.0"
category: quality-assurance
priority: critical
tags:
  - contract
  - schema
  - validation
  - preflight
conflicts_with: []
graph_requires: []
graph_compatible:
  - sherlock-qa
  - bill-dev
  - self-reflection
graph_conflicts: []
purpose: "Backend/Frontend 계약의 일관성을 사전 검증하여 에이전트 생성 전 오류를 차단한다"
when_to_use: "DAG 계획 수립 후 Backend/Frontend 에이전트 생성 직전의 사전 검증 단계"
when_not_to_use: "단일 에이전트 작업, 디자인 작업, 문서화 작업"
inputs: "CEO DAG 계획, shared/schema.py, shared/schema.js, API 라우트 정의"
outputs: "Contract Validation Report (Pass/Fail), 불일치 항목 목록"
examples: "API 엔드포인트 일관성 검증, Request/Response 스키마 매칭, 공통 필드 타입 검증"
constraints: "하나라도 위반 발견 시 ❌ FAIL, 부분 허용 없음, shared/schema.*가 Single Source of Truth"
success_criteria: "모든 API 경로/메서드 일치, 모든 스키마 필드 타입 일치, Contract Gap 없음"
---

# Contract Validator - 사전 계약 검증기

> 이 스킬은 Backend/Frontend 에이전트가 코드를 생성하기 **전에** 계약의 일관성을 검사합니다.
> 통과하지 못하면 에이전트 생성이 차단됩니다. ❌ FAIL 시 CEO에게 계약 수정을 요청합니다.

## 검증 대상

CEO가 DAG 계획에서 확정한 다음 계약 요소들을 사전 검증합니다:

### 1. API 엔드포인트 일관성
- Frontend가 호출할 모든 `/api/*` 경로가 Backend route handler 정의와 일치하는가?
- HTTP 메서드 (GET/POST)가 Frontend 호출과 Backend handler 구현 간에 일치하는가?
- URL 파라미터, 쿼리스트링 구조가 양측에서 동일한가?

### 2. Request/Response 스키마 일관성
- 모든 API 응답이 `shared/schema.py`의 `ApiResponse` 래퍼를 사용하는가?
- `Message`, `SessionCompact`, `SessionFull`, `ToolCall` 등 공통 스키마가 사용되는가?
- Frontend의 `Schema.createUserMessage()`, `Schema.validateMessage()` 호출이 Backend의 `Message.create_user()`, `validate_message()`와 대응되는가?
- SSE 이벤트 타입 (`SSEClientEvent`, `SSEToolPayload`, `SSEDonePayload`, `SSEErrorPayload`)이 Frontend/Backend에서 일관되게 정의되었는가?

### 3. 공통 필드 일관성
- `timestamp`, `avatar_url`, `session_id`, `message_id`, `role`, `content` 등 공통 필드의 타입과 형식이 Frontend/Backend 스키마에서 동일한가?
- `created_at` vs `timestamp`, `id` vs `session_id` 등 필드명 불일치가 없는가?
- Enum 값 (`MessageRole.USER`, `MessageRole.ASSISTANT`, `MessageRole.SYSTEM`)이 양측에서 동일한가?

### 4. 계약 갭 (Contract Gap) 탐지
- Frontend가 사용하지만 Backend 스키마에 정의되지 않은 필드가 있는가?
- Backend가 응답하지만 Frontend가 처리하지 않는 필드가 있는가?
- 새로 추가되는 API 경로가 `shared/integration_checklist.yaml`에 등록되어 있는가?

## 검증 절차

1. **계약 문서 수집**: CEO가 생성한 DAG 계획에서 API 목록, 스키마 정의, 공통 필드를 추출
2. **정적 분석**: `shared/schema.py`, `shared/schema.js`, `api/routes/*.py`, `static/*.js` 파일을 교차 분석
3. **계약 일관성 매트릭스** 생성:

```
| API 경로 | Frontend 호출 | Backend Handler | Request Schema | Response Schema | 일치 |
|----------|---------------|-----------------|----------------|-----------------|------|
| /api/chat/start | messages.js:82 | chat_routes.py | {task, session_id} | StreamStartResponse | ✅/❌ |
```

4. **판정**:
   - 모든 항목 일치 → ✅ VERIFIED PASS → 에이전트 생성 허용
   - 불일치 발견 → ❌ FAIL → CEO에게 구체적 계약 위반 항목 보고 → 계약 수정 후 재검증

## 출력 형식

```markdown
# Contract Validation Report

## Summary
- Total checks: N
- Passed: N
- Failed: N

## Endpoint Consistency
| Endpoint | Status | Issue |
|----------|--------|-------|
| /api/chat/start | ✅ | - |
| /api/session/new | ✅ | - |

## Schema Consistency
| Schema | Frontend | Backend | Status |
|--------|----------|---------|--------|
| Message | Schema.createUserMessage() | Message.create_user() | ✅ |
| SessionCompact | Schema.validateSessionCompact() | validate_session_compact() | ✅ |

## Common Field Consistency
| Field | Frontend Type | Backend Type | Status |
|-------|---------------|--------------|--------|
| timestamp | string (ISO) | str (ISO) | ✅ |
| avatar_url | string | str | ✅ |

## Contract Gaps
| Gap | Description | Recommendation |
|-----|-------------|----------------|
| (none) | - | - |

## Final Verdict
✅ ALL CONTRACTS VALID — Agent generation approved
OR
❌ CONTRACT VIOLATIONS FOUND — Agent generation blocked. Fix the following: ...
```

## 규칙

- 계약 위반이 하나라도 발견되면 ❌ FAIL. 부분 허용 없음.
- CEO가 계약을 수정할 때까지 Backend/Frontend 에이전트 생성 차단.
- `shared/schema.py` + `shared/schema.js` + `shared/integration_checklist.yaml`이 항상 최신 상태인지 확인.
- Schema is the Single Source of Truth. 검증 기준은 항상 shared/schema.* 파일.
