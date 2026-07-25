---
name: explainer
version: "1.0"
category: education
priority: medium
tags:
  - explanation
  - code-review
  - walkthrough
  - algorithm
conflicts_with: []
graph_requires: []
graph_compatible:
  - auto-documenter
  - self-reflection
graph_conflicts: []
purpose: "복잡한 코드를 단계별로 명확하게 설명하는 가이드라인을 제공한다"
when_to_use: "코드 리뷰 설명, 온보딩 문서, 알고리즘 해설, 아키텍처 설명 시"
when_not_to_use: "코드 작성, 디버깅, UI 디자인"
inputs: "설명 대상 코드, 독자 수준(입문/중급/고급), 시스템 컨텍스트"
outputs: "단계별 코드 설명, 아키텍처 개요, 알고리즘 시각화"
examples: "복잡한 정렬 알고리즘 설명, 비동기 처리 흐름 해설, 의존성 그래프 분석"
constraints: "실제 동작과 다른 설명 금지, 독자 수준에 맞는 용어 사용"
success_criteria: "모든 주요 로직 설명, 알고리즘 단계별 분해, 전체 시스템 내 역할 명시"
---
Explain the following code in detail:

Please provide:
1. High-level overview of what it does
2. Step-by-step breakdown of the logic
3. Explanation of any complex algorithms or patterns
4. Purpose of each major component
5. How it fits into the larger system (if context provided)
