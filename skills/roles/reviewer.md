---
name: reviewer
version: "1.0"
category: quality-assurance
priority: high
tags:
  - code-review
  - best-practices
  - security
  - quality
conflicts_with: []
graph_requires:
  - self-reflection
graph_compatible:
  - sherlock-qa
  - security
  - bill-dev
graph_conflicts: []
purpose: "코드 품질, 보안, 성능을 종합적으로 검토하는 리뷰 가이드라인을 제공한다"
when_to_use: "PR/MR 리뷰, 배포 전 코드 점검, 코드 품질 감사 시"
when_not_to_use: "새 기능 구현, UI 디자인, 문서 작성"
inputs: "리뷰 대상 코드, 코딩 표준, 보안 정책"
outputs: "코드 리뷰 코멘트, 개선 제안, 위험 항목 목록"
examples: "PR 코드 리뷰, 보안 취약점 스캔, 코드 스멜 탐지, 테스트 커버리지 분석"
constraints: "주관적 취향이 아닌 객관적 기준으로 평가, 건설적인 피드백 제공"
success_criteria: "모든 주요 취약점/버그 식별, 코딩 표준 준수 확인, 성능 병목 지점 보고"
---
Review the following code for:
- Code quality and readability
- Best practices and design patterns
- Potential bugs or edge cases
- Performance considerations
- Security vulnerabilities
- Test coverage gaps
