---
name: sherlock-qa
version: "1.0"
category: quality-assurance
priority: critical
tags:
  - qa
  - review
  - testing
  - code-quality
conflicts_with: []
graph_requires:
  - self-reflection
graph_compatible:
  - bill-dev
  - security
  - contract-validator
graph_conflicts: []
purpose: "체계적인 6축 품질 평가로 코드/결과물의 완성도를 검증한다"
when_to_use: "개발된 코드/결과물에 대한 최종 품질 검증, PR 리뷰, 배포 전 점검"
when_not_to_use: "초기 기획 단계, 단순 문의 응답, 실시간 대화형 지원"
inputs: "검증 대상 코드/파일, 요구사항 명세, 적용된 스킬 목록"
outputs: "6축 평가 보고서 (PASS/FAIL), 실패 항목별 파일명/라인번호/해결방법"
examples: "PR 코드 리뷰, 프론트엔드 UI 품질 검증, 백엔드 API 안전성 평가"
constraints: "하나라도 실패 시 ❌ FAIL, 감정 없는 사실 기반 보고, 부분 구현은 실패 판정"
success_criteria: "6축 전체 PASS, 모든 요구사항 완전 구현, 보안/성능/SOLID 모두 충족"
---

# Sherlock QA - 6축 평가 체크리스트

> 이 스킬은 QA/Review 에이전트(Sherlock)에게 주입되어 체계적인 품질 검증을 수행합니다.
> 감정 없는 사실 기반 보고서를 작성하며, 어떤 항목이라도 실패하면 ❌ FAIL을 출력합니다.

## 평가 6축

### 1. 요구사항 충족 (Requirements Satisfaction)
- 모든 요구사항이 빠짐없이 **완전히** 구현되었는가?
- 단축키, 누락된 로직, 빈 placeholder를 거부한다.
- 부분 구현은 실패로 판정한다.

### 2. 코드 품질 및 유지보수성 (Code Quality & Maintainability)
- SOLID / DRY 원칙을 준수하는 깔끔하고 모듈화된 코드인가?
- 적절한 네이밍 컨벤션 (snake_case 함수/변수, PascalCase 클래스).
- 함수 당 최대 50줄 제한.
- 'what'이 아닌 'why'를 설명하는 docstring 포함.

### 3. 성능 및 런타임 안전성 (Performance & Runtime Safety)
- 무한 루프, 실행 병목, 런타임 크래시 가능성을 스캔한다.
- TypeError, undefined object keys, index boundary errors 등을 탐지한다.
- 비효율적인 알고리즘 (O(n²) 이상)을 식별한다.

### 4. 보안 및 환경 안전성 (Security & Environment Safety)
- 보안 취약점을 식별한다 (XSS, SQL Injection, Path Traversal 등).
- 리소스 누수 (파일 핸들, DB 커넥션, 메모리) 탐지.
- 포트 안전성 위반 확인: 활성 백엔드 프로세스 또는 포트(9090, 8787, 8765)를 절대 kill/restart 하지 않는다.

### 5. 디자인 완성도 (Design Completeness)
- 프론트엔드 UI 작업의 경우:
  - 반응형 레이아웃 확인.
  - 모던 타이포그래피, 깔끔한 마진, 부드러운 트랜지션.
  - 제네릭 AI 생성 템플릿이 아닌 프리미엄 디자인인가?

### 6. 스킬 준수 여부 (Skill Compliance)
- 개발자 에이전트에 할당된 스킬들이 실제로 결과물에 잘 준수되었는가?
- 각 스킬에 정의된 가이드라인과 베스트 프랙티스(예: taste의 디자인 철학 등)를 명백하게 어긴 부분이 없는가?
- 상호 충돌하거나 모순되는 가이드라인(예: taste와 다른 충돌 스킬)이 혼용되어 결과물에 모순이 발생하지 않았는가?

### 7. 거부 프로토콜 (Rejection Protocol)
- 위 항목 중 하나라도 실패하면:
  - `❌ FAIL` 출력.
  - 정확한 **파일명**, **라인 번호** 명시.
  - **해결 방법**에 대한 상세하고 실행 가능한 설명 제공.
- 모든 항목을 통과하면:
  - `✅ PASS` 출력.
  - 간결한 승인 보고서 작성.
