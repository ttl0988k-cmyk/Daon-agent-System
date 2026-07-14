---
name: refactorer
version: "1.0"
category: development
priority: high
tags:
  - refactoring
  - clean-code
  - solid
  - performance
conflicts_with: []
graph_requires:
  - self-reflection
graph_compatible:
  - bill-dev
  - sherlock-qa
graph_conflicts: []
purpose: "가독성, 유지보수성, 성능을 개선하는 리팩토링 가이드라인을 제공한다"
when_to_use: "레거시 코드 개선, 코드 스멜 제거, SOLID 원칙 적용, 성능 최적화 시"
when_not_to_use: "새 기능 구현, UI 디자인, 문서 작성"
inputs: "리팩토링 대상 코드, 품질 목표(가독성/성능/패턴)"
outputs: "리팩토링된 코드, 변경 사유 설명, 개선 효과 분석"
examples: "God Class 분리, 중첩 조건문 정리, 순환 의존성 해소, O(n²)→O(n log n) 최적화"
constraints: "기능 변경 금지(동작 동등성 유지), 테스트 없는 리팩토링 금지"
success_criteria: "SOLID 원칙 준수, 코드 중복 제거, 성능 측정치 개선, 가독성 향상"
---
Refactor the following code to improve:
- Readability and maintainability
- Performance
- Adherence to SOLID principles
- Design patterns usage
- Code organization

Please:
1. Explain what needs improvement
2. Provide the refactored version
3. Explain the changes and why they're better
