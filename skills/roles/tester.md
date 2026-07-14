---
name: tester
version: "1.0"
category: quality-assurance
priority: high
tags:
  - testing
  - unit-test
  - coverage
  - quality
conflicts_with: []
graph_requires: []
graph_compatible:
  - bill-dev
  - sherlock-qa
  - self-reflection
graph_conflicts: []
purpose: "종합적인 단위 테스트 작성을 위한 가이드라인을 제공한다"
when_to_use: "새로운 함수/클래스 테스트 작성, 테스트 커버리지 개선, TDD 수행 시"
when_not_to_use: "프로덕션 코드 작성, UI 디자인, 문서 작성"
inputs: "테스트 대상 코드, 테스트 프레임워크(Jest/pytest/xUnit), 테스트 패턴(AAA/Given-When-Then)"
outputs: "테스트 코드 파일, 테스트 실행 결과, 커버리지 리포트"
examples: "pytest 단위 테스트, Jest 컴포넌트 테스트, 경계값 테스트 케이스"
constraints: "happy path만 테스트하지 말 것, 실제 환경 의존성은 mocking 처리"
success_criteria: "happy path + edge case + error condition + boundary values 모두 포함, 높은 커버리지"
---
Generate comprehensive unit tests for the following code:

Requirements:
- Test happy path scenarios
- Test edge cases and error conditions
- Test boundary values
- Include setup and teardown if needed
- Use [specify testing framework: Jest/pytest/xUnit/etc.]
- Follow [specify pattern: AAA/Given-When-Then/etc.]
- Aim for high code coverage
