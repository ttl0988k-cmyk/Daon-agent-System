---
name: self-reflection
version: "1.0"
category: meta
priority: medium
tags:
  - self-check
  - verification
  - quality
conflicts_with: []
graph_requires: []
graph_compatible:
  - bill-dev
  - sherlock-qa
  - taste
  - taste-design
  - full-output
  - premium-ui
  - minimalist-ui
  - brutalist-ui
  - redesign-audit
  - landing-page
  - security
  - auto-documenter
  - contract-validator
  - html-anything
graph_conflicts: []
purpose: "모든 하위 에이전트가 최종 출력 전 자체 품질 점검을 수행하게 한다"
when_to_use: "모든 에이전트 작업 완료 직전, 최종 결과 제출 전"
when_not_to_use: "작업 중간 단계, 실시간 대화, 단순 질의응답"
inputs: "완료된 작업 결과물, 원본 요구사항"
outputs: "4항목 Self-Check 결과 (Requirements/Missing/Testing/Optimization)"
examples: "코드 작성 완료 후 자체 점검, 문서 작성 완료 후 누락 확인"
constraints: "4항목 중 하나라도 미흡 시 결과 제출 전 보완 필수"
success_criteria: "4항목 모두 ✅, 모든 요구사항 충족, 누락 파일 없음, 테스트 완료, 최적 구현"
---

# Self-Reflection - 에이전트 자기 점검 체크리스트

> 이 스킬은 모든 하위 에이전트에게 주입되어, 최종 결과 출력 전 자체 품질 점검을 수행하게 합니다.

## 최종 출력 전 필수 점검 4항목

작업을 완료하고 결과를 출력하기 **직전에**, 아래 4가지 질문에 스스로 답하라:

### 1. ✅ 요구사항을 모두 만족했는가?
- subtask에 명시된 모든 항목이 빠짐없이 구현되었는가?
- 일부분만 구현하고 넘어간 것은 없는가?
- edge case를 고려했는가?

### 2. ✅ 누락된 파일은 없는가?
- 모든 필요한 파일이 생성되었는가?
- import/include 경로가 올바른가?
- 설정 파일, 환경 변수 등이 누락되지 않았는가?

### 3. ✅ 테스트를 수행했는가?
- 작성한 코드가 실제로 동작하는지 확인했는가?
- 터미널 도구가 있다면 실행하여 결과를 검증했는가?
- 에러가 발생하면 수정하고 재검증했는가?

### 4. ✅ 더 나은 방법이 있었는가?
- 현재 구현이 최선인가, 아니면 더 깔끔한 방법이 존재하는가?
- 불필요한 복잡성을 추가하지는 않았는가?
- 유지보수하기 쉬운 구조인가?

## 출력 형식

점검 결과를 아래 형식으로 간결하게 포함한다:

```
[Self-Check]
1. Requirements: ✅ All met
2. Missing files: ✅ None
3. Testing: ✅ Verified
4. Optimization: ✅ Current approach is optimal
```
