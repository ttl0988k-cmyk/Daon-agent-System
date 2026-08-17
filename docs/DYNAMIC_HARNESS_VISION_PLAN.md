# 계획: DAON 비전 — 자기 구성·자기 치유 다이나믹 하네스 완성

**작성일:** 2026-08-17
**상태:** ✅ 갭 B → C → A 전부 시공·검증 완료 (세부 기록은 8절) / ✅ 갭 D-1 시공·검증 완료 (세부 기록은 9절)
**세션 인계용 문서** — 이전 세션에서 비전 토론 + 코드 검증 완료, 새 세션에서 이 문서로 이어간다.

---

## 0. 이 문서가 만들어진 배경

세션이 길어져서 끊기는 문제가 반복됨. 아래는 이번 세션에서 코드 검증까지 마친
DAON 비전 토론의 결론과 남은 작업 목록이다. 새 세션은 이 문서만 읽으면 컨텍스트를
복원할 수 있어야 한다.

---

## 1. DAON의 목적 (대표님 비전)

**초보 바이브코더(대표님 본인 포함)를 위한 한글 코딩 도구.**
Claude Code / Codex / VSCode는 초보에게 "잘못 만지면 큰일 날 것 같은" 공포를 준다.
DAON은 겁나지 않는 도구를 목표로 한다.

### 최종 완성본 플로우 (대표님 원문 정리)

```
사용자 → 한 문장
  → 목표/의도 해석
  → "이 일을 하려면 무엇이 필요한가?"
  → Skill 선택 / MCP 선택 / Plugin 선택 / Agent 선택 / Model 선택 / Execution Environment 선택
  → Dynamic Harness 구성
  → 실행
  → 검증
  → 부족한 능력 발견 → 새 Skill/MCP/Agent 동적 투입
  → 재실행 → 검증 → 완료될 때까지 반복
  → 결과물
```

핵심은 **자기 구성**(필요한 능력을 골라 하네스를 JIT 구성)과
**자기 치유**(검증에서 부족함을 발견하면 능력을 동적 투입해 수렴할 때까지 반복).

---

## 2. 현재 상태 검증 결과 (이번 세션에서 코드 확인 완료)

### 2.1 이미 존재하는 것 ✅

| 구성 요소 | 위치 | 상태 |
|---|---|---|
| 일반 챗 → 하네스 브릿지 | `hermes-agent/tools/dynamic_harness_tool.py:216` (`execute_dynamic_harness`가 일반 에이전트 도구 registry에 등록, toolset="dynamic_harness", emoji="🎯") | 동작 |
| 브릿지의 채팅 실시간 스트리밍 | `dynamic_harness_tool.py:77` `get_current_thread_put()` 클로저 캡처 → `agent_log` SSE 이벤트 | 동작 |
| Virtual Office 동기화 | `dynamic_harness_tool.py:100` spawn/status 파일 브릿지 | 동작 |
| Claude Code-style 인터뷰 | `api/api/dynamic/clarifier.py` (질문 1~3개 × MAX_TURNS=3, ENOUGH 판정, POST /api/dynamic/answer/{run_id}) | 동작 |
| 인터뷰 기본 활성화 (HTTP 경로) | `api/api/dynamic_jobs.py:264` `clarification` 기본 True → `enriched_task` 구성(351행) → `runner.run()`(372행) | 동작 |
| CEO 플래너 | `api/api/dynamic/planner.py` (스킬 카탈로그 + 의미 검색 + 히스토리 기반 DAG 생성) | 동작 |
| 스킬 의미 검색 | `api/api/dynamic/skill_retriever.py` (keyword/minimax 임베딩 백엔드 Top-K) | 동작 |
| 모델 자동 선택 | `api/api/dynamic/model_selector.py` (비용/성공률/레이턴시 다중 스코어링) | 동작 |
| 실패 노드 재계획 | `api/api/dynamic/orchestrator.py:45` `_run_recovery_plan` | 동작 |
| 코드 리뷰 | `api/api/dynamic/orchestrator.py:183` `_run_code_reviewer` | 동작 |

### 2.2 인터뷰는 "두 겹"으로 존재함

| 경로 | 인터뷰 방식 |
|---|---|
| 챗 경로 | 일반 에이전트 자신이 인터뷰어 — 대화로 컨텍스트를 모으다 충분하면 enriched task로 도구 호출 (자유 형식, 암묵적) |
| 하네스 패널/HTTP 경로 | 구조화된 clarifier 인터뷰 (질문 1~3개 × 최대 3턴, ENOUGH 판정) |

---

## 3. 남은 갭 (검증 후 확정)

### 갭 A — 챗 경로 도구가 구조화된 clarifier를 거치지 않음 (작은 배선 작업) ✅ 시공 완료

- `dynamic_harness_tool.py:145`의 `runner.run(task=..., forced_skills=...)` 호출에
  run_id/clarification 파라미터가 없음.
- 단, **부품 누락이 아니라 배선/기본값 문제**다. 에이전트의 암묵적 인터뷰(대화 중
  스스로 물어보기)도 초보 친화적 설계로 정당화 가능 → 설계 선택의 문제.
- 시공 시: 도구 경로에 clarifier 옵션을 걸거나, 에이전트 시스템 프롬프트에
  "하네스 호출 전 확인해야 할 체크리스트"를 명시화.
- **우선순위 낮음. B·C가 먼저.**
- ✅ **시공 완료**: 도구 스키마에 `acceptance_criteria` 파라미터 + 사전 점검 체크리스트 추가,
  `_ensure_acceptance_criteria()`가 챗 경로에서도 수용 기준 섹션을 보장해 갭 C 검증 루프가 동작한다.
  `_probe/probe_gap_a.py`로 검증 (ALL GAP-A PROBES PASSED).

### 갭 B — CEO의 선택지에 MCP/Plugin/실행환경 축이 없음 ★시공 1순위 ✅ 시공 완료

현재 `HermesPlanner`가 고르는 것은 **Skill / Role(노드) / Model** 3축뿐.
MCP는 `api/api/streaming.py:1212` 부근에서 **런타임에 일괄 주입**되는 구조라,
플래너가 "이 노드에는 브라우저 MCP가 필요하다", "이 작업은 샌드박스 실행환경에서
돌려야 한다"고 **계획 단계에서 선택할 언어 자체가 없다.**

시공 내용:
1. 플랜 JSON 스키마에 노드별 `mcp_servers`, `plugins`, `environment` 필드 추가
2. CEO 프롬프트에 MCP 카탈로그 주입 (스킬 카탈로그 주입 패턴 차용 — `planner.py`의
   스킬 목록 주입 방식 참고)
3. 노드 컴파일(`compiler.py` AgentCompiler JIT) 시 선택된 MCP만 해당 노드에 바인딩
4. 실행환경 선택은 최소한 "로컬 워크스페이스 / 격리 임시 디렉터리" 2단계부터 시작

### 갭 C — 자기 치유 대루프 미완성 ★시공 2순위 ✅ 시공 완료

현재 수렴 장치는 두 개뿐:
- `_run_recovery_plan` (orchestrator.py:45) — **실패한 노드만** 재계획 (노드 수준)
- `_run_code_reviewer` (orchestrator.py:183) — 코드 품질 리뷰만

비전의 "검증 → **부족한 능력 발견** → 새 능력 동적 투입 → 재실행 → 완료까지 반복"에서
핵심인 **"사용자 의도(enriched_task) 대비 수용 기준 검증 → 결핍 능력을 이름 붙여
플래너로 피드백"** 구간이 비어 있다.

시공 내용:
1. 인터뷰 단계에서 **수용 기준(acceptance criteria)을 함께 추출**
   (clarifier가 이미 enriched_task를 만드니 자연스러운 확장 —
   `build_enriched_task` 출력에 수용 기준 섹션 추가)
2. 최종 병합 후 **검증 에이전트**가 수용 기준 대비 판정 (합격/불합격 + 결핍 능력 목록)
3. 불합격 시 "부족한 능력 = X"를 `skill_retriever`/플래너에 다시 넣어 재계획
   — **B가 시공돼 있어야 "새 MCP 투입"도 가능** (B → C 순서의 이유)
4. 무한 루프 방지: 최대 재시도 횟수(예: 2회) + 재시도마다 개선 증거 요구

### 갭 D — 재귀적 위임 (하위 에이전트가 다시 하위 에이전트를 호출) ✅ D-1 시공 완료

대표님 비전: CEO → Agent A → A가 자기 서브팀(A-1/A-2)을 다시 구성하는 트리형 조직.
권한 위임이 핵심 개념이며, 갭 B의 자원 축이 아래로 전달되고 갭 C 검증 게이트가
모든 깊이에서 프랙탈로 작동해야 한다. 통제 없이 스폰하면 "Agent 폭발"이 일어나므로
통치 계층(깊이/예산/사유 기록)이 필수다.

설계 원칙: **"위임은 도구 호출이다"** — 러너 코어를 고치지 않고, 노드가
`delegate_team` 도구를 호출하면 그 안에서 자식 하네스 실행이 동기 수행된다.
거부는 절대 노드를 죽이지 않고 "직접 처리하라"는 구조화 JSON을 반환한다(fail-open).

단계 분할:
- **D-1 (완료)**: 깊이 1 고정 + `delegate_team` 도구 + 가드(깊이/예산/사유) + 혈통/취소 연쇄
- **D-2 (향후)**: 깊이 > 1 허용, 자원에어 축(token/time 예산)의 실제 하위 배분
- **D-3 (향후)**: UI에서 위임 트리 관측성 (혈통 시각화)

---

## 4. 시공 순서 제안

**B → C → (A)**

- C의 "부족한 능력 동적 투입"은 그 능력 어휘(MCP/Plugin/환경)가 플래너의 선택지에
  먼저 존재해야 동작하므로 B가 선행해야 한다.
- B는 플래너 프롬프트 + 플랜 스키마 확장이 주 작업이라 비교적 가볍다.
- C는 검증 에이전트 + 루프 제어(무한 루프 방지 포함)가 필요한 큰 공사다.
- A는 설계 선택의 문제라 대표님 결정 후 언제든 끼울 수 있다.

---

## 5. 이번 세션에서 완료된 작업 (인계 컨텍스트)

새 세션에서 "이미 끝난 일"을 다시 하지 않도록 기록한다.

### 5.1 MiniMax 무응답 이중 결함 수정 (커밋 31ac335, push 완료)

- **원인**: 메인 MiniMax-M3 호출 → HTTP 400 "function parameters is empty (2013)"
  (빈 도구 스키마 전송) → 폴백 MiniMax-M2.7 → HTTP 401 "invalid api key (2049)"
  (폴백 키 해석이 낡은 내부 키로 떨어짐) → 무한 행잉.
- **수정 1** `hermes-agent/run_agent.py:696` `_normalize_tool_schemas_for_api(tools)` —
  chat_completions 전송 직전 빈 `parameters`를 `{"type":"object","properties":{}}`로
  정규화하는 최종 안전망 (~5197행에서 적용).
- **수정 2** `api/api/streaming.py:940` 부근 — `_fallback_resolved`에 api_key/base_url 주입.
  우선순위: model_manager UI 등록 키 → resolved 키 재사용 → runtime_provider 해석.
- **검증**: py_compile / AST 검사 / 기능 프로브(스키마 5종 케이스) 전부 통과.

### 5.2 재빌드 완료

- `_sync_build.py` (ok=8 fail=0) → PyInstaller → electron-builder → `.cmd` 재생성
  (electron-builder가 .cmd를 삭제하는 함정 재발, 백업에서 복원) → 포터블 zip 재생성.
- **중요**: `npx electron-builder` 실행 후 반드시
  `dist\win-unpacked\DAON Agent System.cmd` 존재 확인 → 없으면 재생성:
  ```bat
  @echo off
  rem DAON Agent System launcher
  set "ELECTRON_RUN_AS_NODE="
  start "" "%~dp0DAON Agent System.exe"
  ```

### 5.3 취소 버튼 조사 완료

- 프론트 `cancelActiveStream()`(chat.js:1709) → `handle_post_chat_cancel`(chat_routes.py:61)
  → `cancel_stream`(streaming.py:2053) 전 체인 정상.
- "취소가 안 먹힌" 증상의 실제 원인은 MiniMax 행잉 자체 (5.1에서 수정됨).

### 5.4 바로가기 검증 완료

- 바탕화면 + 시작 메뉴 DAON 바로가기 전부 `dist\win-unpacked\DAON Agent System.cmd` 대상, 정상.

---

## 6. 대표님 정책 (새 세션에서도 유지)

1. 설명은 한국어로.
2. **빌드 전 git push 먼저.**
3. 서버 무단 재시작 금지 (대표님이 직접 실행).
4. 증상 추측 금지 — 로그/코드로 근본 원인 확인 후 수정.
5. Pyrefly "Cannot find module" 오류는 기존 sys.path 구조 문제 — 런타임 무해, 무시.

---

## 7. 다음 세션 시작 체크리스트

- [x] 이 문서 3·4절(갭 B·C) 대표님 승인 확인
- [x] 갭 B 시공: `api/api/dynamic/planner.py` 플랜 스키마 + CEO 프롬프트에 MCP 카탈로그 편입
- [x] 갭 B 시공: `compiler.py` 노드별 MCP 바인딩
- [x] 갭 C 시공: clarifier 수용 기준 추출 → 검증 에이전트 → 재계획 루프
- [x] 갭 A: 챗 경로 수용 기준 배선 (시공 완료 — 8절 참조)
- [x] 갭 D-1: 재귀적 위임 통치 구조 시공 (시공 완료 — 9절 참조)
- [ ] MiniMax 수정 실사용 검증: 앱 실행 후 `%APPDATA%\daon-agent-system\server.log`에서
      `[webui-debug] fallback_resolved ... api_key=set` 확인, 400/401 소멸 확인
- [ ] 재빌드: git push 후 `_sync_build.py` + PyInstaller + electron-builder 실행,
      `dist\win-unpacked\DAON Agent System.cmd` 존재 확인 (5.2절 함정 주의)
- [ ] 갭 B/C/A 실사용 검증: 재빌드 후 앱에서 하네스 실행, 수용 기준 검증 로그 확인

---

## 8. 갭 B/C/A 시공 완료 기록 (2026-08-17)

### 갭 B (커밋 c6a3174)
1. `plan_validator.py`: 노드별 `mcp_servers`/`plugins`/`environment` 필드 스키마 검증
2. `planner.py`: CEO 프롬프트에 MCP 카탈로그 + 플러그인 카탈로그 + 실행환경(local/sandbox) 옵션 주입
3. `compiler.py`: 노드별 MCP 바인딩(`_inject_mcp_tools`) + 플러그인 해석(`_resolve_plugin_skills`)
4. `runner.py`: 노드별 MCP 툴 필터링 + 실행환경 적용
- 검증: `_probe/probe_gap_b.py` — ALL GAP-B PROBES PASSED

### 갭 C (커밋 c6a3174)
1. `clarifier.py`: 수용 기준 추출/부착/파싱 (`ensure_acceptance_criteria` 다단계 폴백:
   이미 부착 → precomputed → LLM 추출 → 일반 폴백)
2. `dynamic_jobs.py`: 인터뷰/타임아웃/미사용 전 경로에서 마커 섹션 보장
3. `orchestrator.py`: `_verify_acceptance()` 검증 에이전트 (병합 산출물 + 디스크 파일 증거 기반
   pass/fail 판정, 오류 시 fail-open으로 파이프라인 비차단)
4. `orchestrator.py`: `_run_acceptance_replan()` 결핍 능력 피드백 재계획 + 3중 루프 탈출
   (pass / 재시도 상한 `max_acceptance_retries: 2` / 개선 증거: 미충족 집합 감소 요구)
- 검증: `_probe/probe_gap_c.py` — ALL GAP-C PROBES PASSED

### 갭 A
1. `hermes-agent/tools/dynamic_harness_tool.py`: 스키마에 `acceptance_criteria` 파라미터 +
   도구 설명에 사전 점검 체크리스트(PRE-FLIGHT CHECKLIST) 명시
2. `_ensure_acceptance_criteria()`: 챗 경로에서도 수용 기준 섹션 보장 → 갭 C 검증 루프 활성화
   (실패 시 원본 task 반환, fail-open)
- 검증: `_probe/probe_gap_a.py` — ALL GAP-A PROBES PASSED

### 남은 일
- 재빌드 + 서버 재시작 후 실사용 검증 (MiniMax 수정 + 갭 B/C/A 엔드투엔드)

---

## 9. 갭 D-1 시공 완료 기록 (2026-08-17)

### 시공 내용
1. `api/api/dynamic/delegation.py` (신규): 통치 계층 코어
   - 스레드 로컬 위임 컨텍스트 (`set/get/clear_current_delegation`)
   - 스폰 예산 카운터 (`try_consume_spawn_budget` 원자적 소비, `reset_spawn_budget`)
   - 순수 가드 함수 `check_delegation_guard`: 컨텍스트 존재 / 깊이 여유 / spawn_reason 필수
2. `api/api/dynamic/limits.py`: `delegation` 예산 블록 추가
   (`max_depth: 1`, `max_children_per_spawn: 4`, `max_total_spawns: 6`, yaml 오버레이 지원)
3. `api/api/dynamic_jobs.py`: 혈통 레지스트리 (`register_lineage` / `get_descendants` BFS /
   `unregister_lineage_subtree`) + `cancel_job`이 서브트리 전체에 취소 연쇄 전파
   (자식 실행은 동기 실행이라 `_DYNAMIC_JOBS`에 없으므로 `_CANCELLED_JOBS`에 직접 표시 + interrupt)
4. `hermes-agent/tools/delegate_team_tool.py` (신규): `delegate_team` 도구
   - 스키마: task + spawn_reason 필수, acceptance_criteria/preferred_model/skills 선택
   - 흐름: 가드 판정 → 예산 차감 → 혈통 등록 → 자식 run_dir 생성 → 위임 지시문 주입
     (사유/서브팀 규모 상한/재위임 금지) → 수용 기준 부착(갭 C 프랙탈) →
     `HermesDynamicRunner().run(delegation_context=...)` 동기 수행 → tool_result
   - 거부/실패 시 전부 fail-open: "직접 처리하라" 구조화 JSON
5. `api/api/dynamic/orchestrator.py`: `run()`에 `delegation_context` 파라미터 추가,
   루트 실행은 자동 컨텍스트 구성 후 `mission_tracker["delegation"]`으로 노출,
   루트 실행 종료 시에만 `reset_spawn_budget` (finally)
6. `api/api/dynamic/runner.py`: 노드 워커 스레드에 위임 컨텍스트를 thread-local으로 주입
   (`_run_node_with_retries` 전후 set/clear — 도구 호출이 같은 스레드에서 실행되는 점 이용)
7. `api/api/dynamic/compiler.py`: 템플릿/레거시 두 경로 모두 `"delegation"` toolset 주입
   (Agent 폭발 방지는 도구의 가드가 담당)

### D-1의 알려진 한계 (D-2에서 해소)
- 깊이는 1로 고정 (자식 실행 안에서는 delegate_team 가드가 무조건 거부)
- `max_children_per_spawn`은 자식 플래너에 대한 지시문(텍스트) 수준에서만 강제
- 자식 실행은 orchestrator finally의 부수효과(bridge 메시지 등)를 그대로 수행 (표면적 문제)

### 검증
- 7개 파일 py_compile 통과
- `_probe/probe_gap_d.py` — ALL GAP-D PROBES PASSED
  (limits 블록 / 가드 시맨틱 / 예산 카운터+스레드 격리 / 혈통+취소 연쇄 /
   도구 거부·해피패스·실패 폴백 / 소스 배선 확인)
