# 앱 백지화(화이트스크린) 조사 중간 기록 (2026-08-21 23:36 KST 기준)

## 상태: 시공 완료 (2026-08-22 01:08 KST) — 감지+자동복구 추가, 재현 시 로그 증거 확보 가능

## ✅ C 시공 내역 (electron/main.js)
1. **렌더러 크래시 감지 추가** (STEP 4a-3, ~857줄):
   - `render-process-gone` → 로그 기록 + 자동 reload()
   - `did-fail-load` (메인프레임만) → 로그 기록 + 3초 후 loadURL 재시도
   - `unresponsive`/`responsive` → 로그 기록
   - `console-message` level>=2 → 렌더러 콘솔 에러를 daon-main.log로 포워딩 (사후 분석용)
2. **watchdog 재시작 후 mainWindow 자동 reload** (~549줄):
   - 서버 재시작 후 checkServerHealth 통과 시 mainWindow.webContents.reload()
   - 기존엔 서버가 살아나도 mainWindow는 죽은 페이지(백지) 그대로였음

## 구조적 결함 확정 (시공 전 상태)
- mainWindow에 render-process-gone / did-fail-load / unresponsive 핸들러 **전무** → 크래시 시 영구 백지, 로그 증거 없음
- watchdog(handleWatchdogFailure)이 서버를 재시작해도 mainWindow 미복구 → 서버 재시작 후 백지화

## 다음 단계 (재현 대기)
- 재현 시 daon-main.log에서 [RendererCrash]/[RendererFail]/[RendererConsole] 태그 검색
- 로그 증거가 확보되면 근본 원인(후보 A~D) 특정 가능

## ⚠️ 현상 재정의 (23:35 대표님 증언)
"앱이 하얗게 백지처럼 변했었거든" — **브라우저가 앱 창을 덮은 것이 아니라 메인 UI 렌더러 자체가 백지화됨**.
초기 진단(브라우저 덮어쓰기)은 잘못된 방향이었음. 대표님도 "브라우저 사용이 없었다면 브라우저가 덮은 건 아니겠네"로 동의.

## 대표님 초기 가설 → **기각됨**
에이전트가 자체 browser_* 도구 대신 Playwright를 직접 사용했다는 가설. 증거: 노드 세션 12개에서 브라우저 도구/Playwright 실제 호출 0건.

## 지금까지 검증 결과 (증거 기반)

### 1. 하네스 노드 세션에서 직접 Playwright 사용 증거: **없음**
- 2026-08-21 21:25~21:39 KST 세션 12개 전수 검색
- `sync_playwright` / `connect_over_cdp` / `chromium.launch` = **0건**
- `new_page`(2) / `playwright`(5~9) 매치는 전부 **주입된 메모리/규칙 텍스트**(시스템 프롬프트)에서 나온 것. 실제 호출 아님
- 주입 메모리에는 이미 올바른 규칙("반드시 browser_* 도구 사용, mcp_playwright_* 금지, new_page() 금지")이 포함됨

### 2. 노드들의 실제 도구 호출 (arguments 기준 추출)
| 세션 | 실제 호출 |
|------|-----------|
| 213257 (노드) | mcp_filesystem 3건 (디렉터리 생성/목록/PRD 쓰기) |
| 213351 | write_file, read_text_file, directory_tree |
| 213507 | read_multiple_files, list_directory |
| 213536_088fcb | read_file, write_file, read_multiple_files |
| 213536_6db258 | terminal x3, list_directory, write_file |
| 213536_c72716 | terminal x3, list_directory x2, read/write |
| 213712 (테스트 노드) | terminal x13, process x2, read_file x3 — **브라우저 도구 0건** |
| 212550/212814 (CEO/플래너) | "tool_calls" 마커 0건 (호출 형식 미확인 — 다음 단계) |

- 테스트 노드 terminal 명령: pip install flask/pytest, python app.py(포트 5000), curl 127.0.0.1:5000, pytest — 전부 CLI, 브라우저 없음
- **결론: 이번 하네스 실행에서 노드들은 브라우저 도구를 한 번도 안 씀. 대표님 가설(직접 Playwright)은 현재 증거와 불일치**

### 3. daon-main.log 확인 (23:36)
- 사고 시간대(12:21~14:32 UTC, pid=1336): 재시작/크래시 이벤트 없음. Watchdog/RestartOrch 정상 시작 기록만 있음
- 로그에 렌더러 크래시 핸들러(render-process-gone, did-fail-load, unresponsive) 기록 자체가 없음 → main.js에 크래시 감지/복구 로직 부재

## 백지화 후보 원인 (미확정 — 다음 단계에서 특정)
- A: 렌더러 프로세스 크래시 (JS 예외/메모리) — Electron이 흰 화면으로 남김. 감지·복구 로직 없음
- B: mainWindow 재로드/로드 실패 (서버 일시 중단 + will-navigate 가드 상호작용)
- C: 프론트 JS 예외로 DOM 초기화 (하네스 실행 중 상태 갱신 오류)
- D: 서버(9090) 응답 중단 → watchdog 재시작 동안 일시 백지

## 다음 세션 재개 지점
1. main.js에 렌더러 크래시 감지 추가: mainWindow.webContents의 render-process-gone / did-fail-load / unresponsive → 로그 + 자동 reload() (대표님 합의 후)
2. 재현 시 콘솔 증거 확보: 재현되면 F12 DevTools console 스크린샷, 또는 console 에러를 daon-main.log로 포워딩하는 코드 추가
3. server.log에서 사고 시간대(12:25~12:40 UTC = 21:25~21:40 KST) 서버 상태/재시작 기록 확인
4. watchdog(handleWatchdogFailure)이 서버를 죽였다 살릴 때 mainWindow가 백지가 되는지 코드 검토

## 참고: 기존 방어막 현황
- browser_routes._ensure_browser: new_page() 금지 (준수 중)
- browser_tool.py: BROWSER_CDP_URL 가드 (외부 agent-browser fallback 차단)
- Playwright MCP: --cdp-endpoint 제거됨 (자체 headless)
- **공백**: 전역 browser-window-created 가드는 OAuth 충돌로 제거됨 → CDP 경유 새 BrowserWindow 생성 시 방어막 없음 (main.js:144-149 주석)

## 사용 스크립트 (이 폴더)
- probe_sessions.ps1: 세션별 도구명 빈도 (스키마 포함)
- probe_structure.ps1: 세션 구조/role/키워드 카운트
- probe_calls.ps1: tool_calls 블록 + 8080/127.0.0.1 컨텍스트 추출
- probe_realcalls.ps1: 실제 호출(arguments 있는 것)만 추출 ← 핵심
- probe_terminal.ps1: terminal/process 호출 명령 추출
- probe_ceo.ps1 / probe_ceo2.ps1: CEO 세션 탐색 (미완)
