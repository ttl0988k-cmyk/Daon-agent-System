---
name: documenter
version: "1.0"
category: documentation
priority: medium
tags:
  - documentation
  - docstring
  - api-docs
  - explanation
conflicts_with: []
graph_requires: []
graph_compatible:
  - auto-documenter
  - self-reflection
graph_conflicts: []
purpose: "코드에 대한 명확하고 체계적인 문서 작성을 위한 가이드라인을 제공한다"
when_to_use: "함수/클래스 docstring 작성, API 문서화, 사용 예제 작성 시"
when_not_to_use: "코드 작성, 디버깅, UI 디자인, 보안 감사"
inputs: "문서화 대상 코드, 포맷 요구사항(Markdown/JSDoc/XML)"
outputs: "완성된 문서, docstring, 사용 예제, 의존성 목록"
examples: "Python 함수 docstring, REST API 엔드포인트 문서화, 모듈 사용 가이드"
constraints: "실제 코드와 불일치 금지, 모호한 설명 금지"
success_criteria: "모든 public 요소 문서화, 파라미터/리턴값/예외 명시, 사용 예제 포함"
---
Create documentation for the following code:

Please provide:
1. Clear description of what the code does
2. Parameter/argument descriptions
3. Return value documentation
4. Usage examples
5. Any important notes or warnings
6. Dependencies or prerequisites

Format: [Markdown/JSDoc/XML Documentation Comments/etc.]
