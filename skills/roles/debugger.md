---
name: debugger
version: "1.0"
category: debugging
priority: high
tags:
  - debugging
  - troubleshooting
  - bug-fix
  - root-cause
conflicts_with: []
graph_requires: []
graph_compatible:
  - bill-dev
  - self-reflection
graph_conflicts: []
purpose: "체계적인 디버깅과 버그 수정을 위한 가이드라인을 제공한다"
when_to_use: "에러/버그 진단, 스택 트레이스 분석, 런타임 문제 해결 시"
when_not_to_use: "새 기능 구현, 문서 작성, UI 디자인"
inputs: "에러 메시지, 스택 트레이스, 문제 재현 단계, 관련 코드"
outputs: "근본 원인 분석, 수정 코드, 예방 권고사항"
examples: "TypeError 디버깅, import 오류 해결, 무한 루프 탐지"
constraints: "추측 기반 수정 금지, 활성 서버 프로세스 kill 금지"
success_criteria: "근본 원인 식별, 검증된 수정안 제시, 동일 유형 재발 방지책 포함"
---
I'm encountering the following issue:

**Problem:** [Describe the bug/error]
**Expected behavior:** [What should happen]
**Actual behavior:** [What's actually happening]
**Error messages:** [Any error output]

**Code:**
[Paste relevant code]

**Context:**
[Environment, dependencies, recent changes]

Please help me:
1. Identify the root cause
2. Suggest a fix
3. Explain why this is happening
4. Recommend how to prevent similar issues
