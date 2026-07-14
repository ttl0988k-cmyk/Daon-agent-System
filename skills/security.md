---
name: security
version: "1.0"
category: security
priority: critical
tags:
  - security
  - vulnerability
  - port-safety
  - input-validation
conflicts_with: []
graph_requires: []
graph_compatible:
  - sherlock-qa
  - bill-dev
  - self-reflection
graph_conflicts: []
purpose: "코드 및 시스템의 보안 취약점을 식별하고 차단한다"
when_to_use: "코드 보안 감사, 입력 검증 검토, 인증/인가 체계 점검, 포트 안전성 확인 시"
when_not_to_use: "UI/UX 디자인, 문서 작성, 일반 기능 구현"
inputs: "대상 코드베이스, 환경 설정 파일(.env, config.yaml)"
outputs: "보안 취약점 목록, 위험도 평가, 수정 권고사항"
examples: "SQL Injection 탐지, XSS 취약점 식별, 하드코딩된 API 키 발견, 포트 안전성 검증"
constraints: "활성 서버 프로세스 kill 금지, 작업 디렉토리 외부 파일 접근 금지"
success_criteria: "OWASP Top 10 취약점 없음, 민감 정보 노출 없음, 포트 보호 규칙 준수"
---

# Security - 보안 검증 스킬

> 이 스킬은 보안 검증 에이전트에게 주입되어 코드 및 시스템의 보안 취약점을 식별합니다.

## 코드 보안 점검

### 입력 검증
- 모든 사용자 입력을 검증하고 이스케이프 처리한다.
- SQL Injection 방지: 파라미터화된 쿼리 사용.
- XSS 방지: HTML 이스케이프, Content-Security-Policy 헤더.
- Path Traversal 방지: 경로 정규화 후 화이트리스트 체크.

### 인증/인가
- 하드코딩된 비밀번호, API 키, 토큰 탐지.
- 민감 정보가 로그에 출력되지 않는지 확인.
- `.env` 파일이 `.gitignore`에 포함되어 있는지 확인.

### 리소스 관리
- 파일 핸들, DB 커넥션, 네트워크 소켓이 적절히 닫히는지 확인.
- `with` 문 또는 `try-finally` 패턴 사용 확인.
- 메모리 누수 가능성 탐지 (무한 성장하는 리스트/딕셔너리).

## 환경 안전성

### 포트 안전 규칙 (필수)
- 활성 백엔드 프로세스를 **절대** kill/terminate/restart 하지 않는다.
- 보호 대상 포트: 9090, 8787, 8765, 8766, 8000, 8080.
- `taskkill`, `kill`, `pkill`, `Stop-Process` 등의 명령으로 서버 프로세스를 종료하는 행위는 **즉시 거부**한다.

### 파일 시스템 안전
- 작업 디렉토리 외부의 파일을 수정/삭제하지 않는다.
- 시스템 파일, 설정 파일에 대한 무단 접근을 금지한다.
- `rm -rf /`, `del /s /q C:\` 같은 위험 명령 차단.

## 의존성 보안
- 알려진 취약점이 있는 라이브러리 버전 식별.
- 불필요한 의존성 최소화.
- CDN 사용 시 SRI (Subresource Integrity) 해시 권장.
