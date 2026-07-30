## DAON Agent System v1.0.1

v1.0.0 이후 수정·개선 사항입니다. **v1.0.0 릴리스 실행 파일에는 아래 수정이 빠져 있었으니, 반드시 이 버전으로 설치하세요.**

### 설치
- `DAON Agent System Setup 1.0.0.exe` 다운로드 후 실행 (내부 버전 v1.0.1)
- Windows 10/11 x64 전용

### 수정 사항

**미디어 생성 (Wan2.7 이미지/비디오)**
- Wan2.7 최신 DashScope 엔드포인트로 교체 — 동기 `multimodal-generation` 우선, 비동기 `image-generation` 폴백 (`53f343c`)
- 이미지 size 형식 정규화 (`1024x1024` → `1024*1024`), HTTP 400 즉시 실패 분기 (`3624c38`)

**스트리밍 안정성**
- 미디어 생성 중 전용 `heartbeat` SSE 이벤트로 keep-alive — 긴 생성 작업 중 연결 끊김 방지 (`3f1b57c`)
- 서버 SSE heartbeat 주기 30초 → 15초 단축

**다이나믹 하네스**
- 취소 버튼이 실제로 백엔드 실행을 멈추도록 수정 — 실행 중인 AIAgent 즉시 interrupt, 답변 대기 즉시 해제, 1초 단위 취소 폴링, cancelled 상태 반영 (`e647f58`)

**문서**
- DAON 기억 시스템 보고서 추가 (`edc1b5e`)

### 주요 기능 (v1.0.0 동일)
- 멀티 에이전트 채팅 (역할 기반 에이전트)
- 도구 시스템 (터미널, 파일, 브라우저, 코드 실행, 이미지 생성, 웹 검색 등)
- 스킬 시스템 (마크다운 기반 에이전트 스킬)
- 다중 LLM 프로바이더 (OpenAI, Anthropic, OpenRouter, xAI, DeepSeek, Ollama 등)
- 서버 우선 아키텍처 (무한 로딩 방지) + 워치독 (서버 자동 감시/복구)
