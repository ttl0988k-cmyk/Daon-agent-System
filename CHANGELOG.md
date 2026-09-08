# Changelog

이 프로젝트의 주요 변경 사항은 이 파일에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/)를 따르며, 버전은 [Semantic Versioning](https://semver.org/)을 참고합니다.

---

## [Unreleased]

### Added (2026-09-08) — 대규모 리팩토링 (Phase 0~6)

- **Phase 6 — 회귀 테스트 스위트** (`78f29b2`): 통합 마스터 러너 [`tests/run_all_tests.py`](tests/run_all_tests.py) 추가, dev-mode TTS 프로세스 메모리 오버헤드 최적화
- **Phase 5 — 영속성** (`0c36482`): SQLite 기반 [`HarnessJobStore`](api/api/dynamic/job_store.py)로 작업·계보(lineage) 영속화, 크래시된 작업 자동 복구, `_ENV_LOCK` 파일 경합 제거
- **Phase 4 — 프론트엔드** (`f9fa58b`): 모놀리식 `panels.js`를 `static/modules/panels/` 8개 서브패널로 분리, 유령 코드 4,152줄 제거, 리스너 누수(M2, M3) 패치
- **Phase 3 — Electron** (`f4c1a73`): 모놀리식 `main.js`를 전용 모듈로 분해 (ServerSupervisor, TabManager, WindowManager, TrayManager, IpcHandlers, HeaderNormalizer, TempCleaner, Logger)
- **Phase 2 — 라우팅** (`48b5ba3`): 210줄 if/elif 체인을 O(1) 해시 디스패처로 전환, 중복 라우트 제거, server.py 직접 라우트 통합 (H2)
- **Phase 1 — 스트리밍** (`eb2d212`): `StreamEmitter` 추출, 도구→`streaming_tools.py`·프롬프트→`streaming_prompts.py` 모듈화, silent except 수정 (H1, M1)
- **Phase 0 — 퀵윈** (`52eb99e`): `_probe` 삭제, 4개 파일 개행 오염 수정, `.gitattributes` 추가, main.js isAlive/wmic 패치, server.py 경로 탐색 차단
- **라우트 통합** (`4984516`): 전체 라우트 통합, 엄격한 prefix 경계, 동적 UA, 타깃 포트 종료
- **Planner SPOF 제거** (`68f8d81`): 플래너 5회 실패 시 폴백 plan.md 자동 생성 recovery 로직 적용

### Added (2026-09-07)

- **EvolutionLedger** (`f0accbc`): 자가 수리 메모리 & 재시작 후 세션 연속성 구현
- 스트리밍 self-evolution 프롬프트 주입 누락 except 블록 복원 (`f91f531`)

### Fixed (2026-09-02 ~ 2026-09-07) — 브라우저·안정성

- CDP 탭 닫기 멈춤 해결, 좀비 자동열기 루프 제거, 스레드 안전 결과 디스패처 (`2fe95dd`)
- 내부 브라우저 유령 탭 크래시 방지 — navigate() 죽은 webContents 가드 (`fbbd1a8`)
- 내장 브라우저 크롬 신원 복귀 + 링크 튕김(-3) 방지 (`6f1a40f`)
- 워치독 TCP listening 체크 강화, 브라우저 status/grid 라우트 패스트패스 (`2b64b50`)
- TabManager 네이티브 WebContentsView 컨테이너 경계 클리핑 (`c1ee152`)
- 세션 전환 freeze 방지 — lock timeout 자동 해제 (`5853949`)
- 멀티탭 그리드 모드 렌더링, 새 탭 즉시 생성, 흰 화면 방지 (`da32b46`, `3b704a1`, `f53e9dc`)
- 좀비 server.exe 기동 시 종료 + 긴급 재시작 (`d43c259`, `9b94a79`)
- spawn EBUSY 예외 처리 (`7d581bb`)

### Added (2026-09-04)

- **멀티 에이전트 브라우저 미니 뷰 그리드 + 세션 격리** (`ba1175e`)
- **Browser-Use 스타일** 가시성 필터, Set-of-Marks 오버레이, 배치 액션 엔진 네이티브 이식 (`40a41f0`)

### Fixed (2026-09-01 ~ 2026-09-03) — 비용·기억

- 백그라운드 기억작업 크레딧 유출 차단 — 미선택 유료 프로바이더 폴백 금지 + 429 영구실패 판정 (`a4a89e2`)
- 기억큐 앵커 정렬 is_custom 플래그 정확화 (`622b6a0`)
- 세션 저장 비동기화 — `/api/session/new` 24.8초 → 447ms (`5827c7b`)
- OpenCode Go 자동 모델 감지 — Cloudflare 1010 UA 차단 회피 (`bb24b09`)

### Added (2026-09-06)

- OmO 이노베이션 채택 (Hashline, Skill-Embedded MCP, Hyperplan) & 다이나믹 하네스 플래너 개편 (`fe718ac`)

---

## [1.0.1] — 2026-08-29

자세한 내용은 [RELEASE_NOTES_v1.0.1.md](RELEASE_NOTES_v1.0.1.md) 참조.

### 요약

- **미디어 생성**: Wan2.7 최신 DashScope 엔드포인트 교체 (동기 `multimodal-generation` 우선, 비동기 폴백), size 형식 정규화, HTTP 400 즉시 실패 분기
- **스트리밍 안정성**: 스트리밍 도중 연결 끊김 방지
- **음성 안내**: 메인서버 TTS 폴백
- **에디터**: 실시간 표시
- **기타**: beginner 레이아웃, OpenRouter 차단 처리

---

## [1.0.0] — 초기 릴리스

- Electron + Python 멀티 에이전트 IDE 첫 배포
- NSIS 인스톨러 + 포터블 배포
