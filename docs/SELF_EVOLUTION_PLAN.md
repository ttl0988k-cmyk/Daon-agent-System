# 계획: 갭 E — 자기 적용(Ouroboros)과 bb 교훈 이식

**작성일:** 2026-08-17 (야간 세션)
**상태:** 📋 계획 초안 (시공 전) — 대표님 승인 대기
**선행 문서:** [DYNAMIC_HARNESS_VISION_PLAN.md](DYNAMIC_HARNESS_VISION_PLAN.md) (갭 A·B·C·D 전부 시공 완료)
**조사 출처:** get-bb/bb 저장소 (README + docs 5종: system-overview / VISION / repository-overview / platform-support / worktrees)

---

## 0. 배경 — "마지막 퍼즐"의 정체

bb는 "The agent IDE that builds itself" — 스스로를 빌드하는 에이전트 IDE다.
DAON은 갭 A(수용 기준) → B(플러그인) → C(검증·재계획) → D(재귀 위임+관측성)가
모두 시공되어 부품은 완비 상태. 남은 방향은 새 능력이 아니라
**DAON 하네스가 DAON 코드베이스 자체를 작업 대상으로 돌리는 자기 적용(self-hosting/dogfooding)**이다.

대표님 확인: "에이전트가 필요한 도구를 만드는 게 가능하지? MCP라든지 플러그인이라든지"
→ 코드베이스 사실 확인 결과: **대부분 이미 가능**. 세부 격차는 2절.

---

## 1. bb에서 배울 점 (DAON에 이식할 교훈)

| bb의 설계 | DAON 현 상태 | 이식 가치 |
|---|---|---|
| 관리형 git worktree 격리 (스레드마다 새 브랜치+복사본, 아카이브 시 자동 정리) | 워크스페이스 전환(수동) | ★★★ 자기 적용 시 안전 격리의 핵심 수단 |
| CLI/SDK를 에이전트용 1급 표면으로 노출 | 웹 UI만 존재 | ★★ 에이전트가 에이전트를 부르는 통로 |
| SQLite 영속 상태 | 인메모리 잡 레지스트리+파일 | ★★ 재시작 후에도 스레드 복원 |
| 계약 패키지(server-contract / host-daemon-contract)로 경계 강제 | 모놀리스 단일 서버 | ★ 장기 리팩터링 후보 |
| server / host-daemon 분리 (감시자와 피감시자 분리) | 일렉트론이 서버 감시 | ★★★ 부트스트랩(자기 수정 후 재시작) 문제의 해법 원형 |

bb의 약점(참고): Windows 네이티브 미지원(WSL2만), 외부 에이전트 CLI 의존(자체 런타임 없음),
수용 기준/리뷰어 같은 품질 게이트 없음. DAON은 이 셋에서 이미 우위.

---

## 2. 스킬 / MCP / 플러그인 현 상태와 격차 (사실 확인 완료)

### 2.1 이미 되는 것 ✅

| 경로 | 도구/위치 | 비고 |
|---|---|---|
| 스킬 생성·수정 | [`skill_manage`](../hermes-agent/tools/skill_manager_tool.py) (create/edit/add_file/replace) | 에이전트 직접 호출 가능 |
| 플러그인 제작 | [`plugin_create`](../hermes-agent/tools/plugin_manager_tool.py) — 스캐폴드+즉시 등록 | plugin.yaml + SKILL.md |
| 플러그인 수입 | `plugin_import` (git URL/로컬) → [`import_plugin()`](../api/api/plugin_gateway.py) | |
| 플러그인→도구 노출 | [`_sync_plugin_tool_env()`](../api/api/plugin_gateway.py) + 핫 리디스커버 `_refresh_hermes_plugins()` | 재시작 불필요 |
| 플러그인→MCP 노출 | [`_sync_plugin_mcp_servers()`](../api/api/plugin_gateway.py) | manifest mcp 항목 |
| MCP 서버 관리 API | [`/api/mcp/servers/add`](../api/api/routes/mcp_routes.py) 외 add-preset/connect/remove/tools/call | HTTP 경로만 |

### 2.2 격차 (수정 방안)

| # | 격차 | 수정 방안 | 난이도 |
|---|---|---|---|
| E-0a | MCP 직접 등록이 에이전트 도구로 미노출 (플러그인 우회로만 존재) | `hermes-agent/tools/mcp_manager_tool.py` 신규 — `mcp_manage` 도구(add/remove/connect/list)가 기존 MCP 매니저 API 재호출. 스키마는 `plugin_create` 패턴 참고 | 쉬움 |
| E-0b | 네이티브 도구(tools/*.py의 `registry.register`)는 파일 작성 후 반영에 서버 재시작 필요 | 무리하게 핫로드하지 않음. 대신 ① 문서화(플러그인 경로를 공식 우회로로 안내) ② 갭 E 부트스트랩 파이프라인(3.3절)이 재시작을 자동화하므로 사실상 해결됨 | 문서화+간접 해결 |
| E-0c | 플러그인 제작 시 도구 코드(Python) 스캐폴드는 SKILL.md 중심 — 실제 `registry.register` 도구 코드 템플릿 부재 | `plugin_create`에 `tool_template` 옵션 추가: 최소 도구 코드(register+handler+check_fn)를 생성해 플러그인 디렉토리에 기록 | 중간 |

---

## 3. 갭 E 시공 계획 — 자기 적용(Ouroboros)

### 3.1 E-1 대상 결속 (DAON을 작업 대상으로)

- 하네스 워크스페이스를 DAON 레포(`c:/daon/Daon agent System`)로 고정하는 프리셋 추가
- DAON 자체 지식을 스킬 1장으로 주입: `_sync_build.py` 미러 규칙, `daon-server.spec` 번들 구조,
  electron-builder `.cmd` 삭제 함정(docs 5.2절), `_probe/*` 실행법, 정책(cp949 로그, 비ASCII 금지 등)
- 스킬 이름(가칭): `daon-self-knowledge` — curated 디렉토리에 수동 작성 후 `skill_manage`로 유지보수

### 3.2 E-2 안전 통치 (자기 수정 가드레일)

자기 수정 실행은 반드시 이 순서로만 통과:

```
git 체크포인트(자동 커밋) → 승인 게이트(기존 approval) → 수정 적용
  → 프로브 회귀검사(_probe/probe_gap_a~d.py 패턴 재사용, 신규 probe_gap_e.py)
  → 통과 시 커밋 확정 / 실패 시 git revert 자동 복귀
```

- 재사용 자산: 승인 게이트(`tools/approval.py`), 혈통/가드(`api/api/dynamic/delegation.py`),
  프로브 패턴(`_probe/probe_gap_*.py`)
- 신규: `probe_gap_e.py` — 자기 수정 파이프라인의 각 단계 모의 검증

### 3.3 E-3 부트스트랩 (자기 수정 후 반영) — 유일한 진짜 난제

문제: DAON이 서버 코드를 고치면 재시작해야 반영된다.
bb의 해법: server/host-daemon 분리(감시자와 피감시자 분리).

DAON 적용안: 기존 일렉트론 메인 프로세스가 서버를 감시하는 구조를 살려,
**"자기 수정 커밋 확정 → 승인 → 서버 프로세스 재시작 → 헬스체크+프로브 통과 확인 → 실패 시 롤백"**
파이프라인을 일렉트론 메인 측 오케스트레이션으로 구현. 서버 자신은 재시작을 요청만 하고
자기 재시작을 스스로 수행하지 않는다(감시자/피감시자 분리 원칙).

### 3.4 E-4 (선택·장기) bb 교훈의 본격 이식

- worktree 격리: 자기 수정 전용 브랜치+worktree에서 실험 후 병합 (E-2의 격리 강화판)
- 에이전트용 CLI 표면: `daon` CLI로 하네스 잡을 스크립트에서 띄울 수 있게 (bb의 1급 표면 철학)
- SQLite 영속화: 잡/스레드 상태를 재시작 후에도 복원

---

## 4. 시공 순서와 검증

| 순서 | 항목 | 검증 방법 |
|---|---|---|
| 1 | E-0a `mcp_manage` 도구 | 프로브: 등록→연결→도구 목록 조회 모의 |
| 2 | E-0c `plugin_create` 도구 템플릿 | 프로브: 스캐폴드 생성→import 성공 확인 |
| 3 | E-1 대상 결속 + `daon-self-knowledge` 스킬 | 스킬 카탈로그 노출 확인 |
| 4 | E-2 안전 통치 | `probe_gap_e.py` (체크포인트→승인→프로브→복귀 전 구간 모의) |
| 5 | E-3 부트스트랩 | 일렉트론 측 재시작 오케스트레이션 + 헬스체크 (실기기 검증 필요) |
| 6 | E-4 장기 항목 | 별도 계획 분리 |

각 단계 완료 시: py_compile/프로브 통과 → git commit+push → 이 문서 상태 갱신.

---

## 5. 리스크

1. **무한 자기 수정 루프** — 자기 적용 실행에도 갭 D의 위임 가드(스폰 예산/깊이 제한)를 그대로 적용하고,
   자기 수정 전용 예산(1회 실행당 최대 N커밋)을 별도 상수로 추가
2. **빌드 산출물 오염** — 자기 수정 대상에서 `dist/`, `release/`는 제외 경로로 명시 (path_security 재사용)
3. **재시작 중 데이터 손실** — E-3 실행 전 진행 중 잡 없음 확인(잡 레지스트리 비어 있을 때만 재시작 허용)
4. **bb 라이선스/코드 직접 차용 금지** — bb는 참고(교훈)만 하고 코드 복사는 하지 않음. DAON은 자체 아키텍처 유지

---

## 6. 인계 메모

- bb 조사 원문: 워크스페이스 루트 `_tmp_bb_*.md` 6종 (미추적 파일, git add 금지 — 확인 후 삭제 가능)
- 이 플랜은 대표님 승인 후 E-0a부터 순서대로 시공. 앱 실사용 검증(오늘 연기분)이 우선일 수 있음.
- 정책 유지: 한국어 설명 / 빌드 전 push / 서버 무단 재시작 금지 / cp949 로그 규칙 / 최종 응답은 attempt_completion
