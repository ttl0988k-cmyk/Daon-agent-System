---
name: auto-documenter
version: "1.0"
category: documentation
priority: high
tags:
  - docs
  - readme
  - wiki
  - analysis
conflicts_with: []
graph_requires: []
graph_compatible:
  - self-reflection
  - bill-dev
graph_conflicts: []
purpose: "코드베이스 분석과 고품질 문서 자동 생성을 보장한다"
when_to_use: "README, API 문서, 아키텍처 문서, 코드 주석 자동 생성이 필요할 때"
when_not_to_use: "코드 작성, UI 디자인, 보안 감사만 필요할 때"
inputs: "대상 프로젝트 디렉토리, 문서 유형(readme/api/architecture)"
outputs: "생성된 문서 파일들, 분석 결과 JSON 리포트"
examples: "README.md 생성, API 레퍼런스 문서화, Mermaid 아키텍처 다이어그램"
constraints: "실제 코드와 일치하는 내용만 기술, 추측 금지, 간결성 원칙"
success_criteria: "모든 public 함수/클래스 docstring 포함, 진입점부터 체계적 분석, 한글/영문 일관성"
---

# Auto-Documenter — 코드베이스 분석 & 문서 자동 생성 스킬

> 이 스킬은 문서화 에이전트에게 주입되어 코드베이스 분석과 고품질 문서 생성을 보장합니다.

## 문서 유형별 가이드라인

### README.md
- 프로젝트 이름, 한 줄 설명 (간결하고 강력하게)
- 주요 기능 목록 (불렛 포인트, 5~8개)
- 기술 스택 (언어, 프레임워크, 주요 라이브러리)
- 빠른 시작 (설치 → 실행까지 3단계 이내)
- 프로젝트 구조 (핵심 디렉토리 트리와 설명)
- 환경 변수 / 설정 방법
- 라이선스 정보

### API 문서
- 엔드포인트별 Method, Path, Description, Parameters, Response 예시
- 인증 방식 명시
- 에러 코드 및 응답 포맷

### 아키텍처 문서 (ARCHITECTURE.md)
- 시스템 개요 다이어그램 (Mermaid 또는 ASCII)
- 데이터 흐름 설명
- 모듈 간 의존성 관계
- 주요 디자인 결정 사항 (ADR)

### 코드 주석 / Docstring
- 모든 public 함수/클래스에 docstring 작성
- Args, Returns, Raises 명시
- 복잡한 로직에는 인라인 주석으로 설명

## 분석 원칙

1. **전체 탐색 후 세부 분석**: 먼저 디렉토리 구조와 `import` 관계를 파악한 후 세부 파일 분석
2. **진입점 우선**: `server.py`, `main()`, `app.py` 등 실행 진입점부터 분석
3. **의존성 그래프**: `import` 문을 추적하여 모듈 간 의존 관계 매핑
4. **설정 파일 분석**: `config.yaml`, `.env`, `package.json` 등 설정에서 아키텍처 힌트 추출
5. **라우트 맵핑**: 웹 애플리케이션의 경우 모든 라우트를 열거하고 그룹화

## 문서 품질 원칙

- **간결성**: 불필요한 설명 제거, 핵심만 전달
- **정확성**: 실제 코드와 일치하는 내용만 기술 (추측 금지)
- **계층화**: 개요 → 상세 구조 → API 레퍼런스 순으로 깊이 있는 구조
- **코드 예시**: 모든 API/함수 설명에는 실제 사용 예시 포함
- **한글 우선**: 한글 사용자를 위한 프로젝트는 한글로 작성, 영문 프로젝트는 영문으로

## 출력 포맷

문서 생성 결과는 아래 JSON 구조로 반환한다:

```json
{
  "files_analyzed": 42,
  "docs_generated": [
    {
      "path": "README.md",
      "type": "readme",
      "summary": "프로젝트 개요 문서",
      "size_bytes": 3420
    }
  ],
  "warnings": ["분석하지 못한 파일: legacy/old_code.py"],
  "suggestions": ["test/ 디렉토리에 테스트 문서 추가 권장"]
}
```

## 문서 생성 프로세스

1. 프로젝트 루트 탐색 → 디렉토리 구조 파악
2. 설정 파일 분석 → 기술 스택, 의존성 확인
3. 진입점 분석 → main(), server 시작 로직 파악
4. 라우트/API 엔드포인트 수집 → API 문서 초안
5. 핵심 모듈 분석 → 아키텍처 문서 초안
6. 문서 파일 생성 → 지정된 출력 디렉토리에 저장
7. 결과 요약 리포트 반환
