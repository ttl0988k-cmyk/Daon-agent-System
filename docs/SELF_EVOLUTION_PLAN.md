# 계획: 갭 E — 자기 적용(Ouroboros)과 자율 개발 생태계

**작성일:** 2026-08-17 (야간 세션) / **갱신:** 2026-08-19 (E 본선 완결 + E-L1 심장 연결 + E-L2 Builder Agent 역할화 + E-L3 격리(E-4a worktree 동반) + E-L4 편입 거버넌스 시공 완료 — E-Master Architecture 폐루프 완성 + 커밋 예산(리스크 1 후반) 시공 완료)
**상태:** ✅ **Gap E — Self-Application / Ouroboros 승인 (대표님, 2026-08-18)** / E-Master Architecture를 상위 설계로 기록 / 기존 E-0a부터 순차 시공
**선행 문서:** [DYNAMIC_HARNESS_VISION_PLAN.md](DYNAMIC_HARNESS_VISION_PLAN.md) (갭 A·B·C·D 전부 시공 완료)
**조사 출처:**
- get-bb/bb (README + docs 5종) — 자기 빌드의 비전
- openai/symphony (SPEC.md 2314줄 + WORKFLOW.md + elixir README) — 자율 실행의 뼈대
- codecrafters-io/build-your-own-x (README 전문, 별 54만) — 재창조를 통한 이해의 철학

---

## 0. 배경 — "마지막 퍼즐"의 정체

bb는 "The agent IDE that builds itself" — 스스로를 빌드하는 에이전트 IDE다.
DAON은 갭 A(수용 기준) → B(플러그인) → C(검증·재계획) → D(재귀 위임+관측성)가
모두 시공되어 부품은 완비 상태. 남은 방향은 새 능력이 아니라
**DAON 하네스가 DAON 코드베이스 자체를 작업 대상으로 돌리는 자기 적용(self-hosting/dogfooding)**이다.

대표님 확인: "에이전트가 필요한 도구를 만드는 게 가능하지? MCP라든지 플러그인이라든지"
→ 코드베이스 사실 확인 결과: **대부분 이미 가능**. 세부 격차는 2절.

---

## 0A. E-Master Architecture — Self-Expanding Execution Loop (대표님 제안, 2026-08-18 승인)

### 정의

> **"스스로 필요한 개발 도구를 만들고 → 그 도구를 이용해 작업하고 →
> 그 작업에서 얻은 것을 다시 시스템에 편입하는 자율 개발 생태계"**

세 저장소가 하나의 선으로 연결된다:

| 저장소 | 제공하는 것 | 한 마디 |
|---|---|---|
| bb | **비전** | 스스로를 빌드하는 에이전트 IDE — 자기 적용 |
| Symphony | **메커니즘** | 이슈 → 격리 워크스페이스 → 무인 실행 → 화해 — 자율 실행의 뼈대 |
| Build Your Own X | **철학+커리큘럼** | 파인만 "내가 만들 수 없는 것은 이해하지 못한 것이다" — 재창조를 통한 이해 |

bb가 "뭐가 될 것인가", Symphony가 "어떻게 스스로 돌릴 것인가",
BYOX가 "어떻게 이해하고 성장할 것인가"를 답한다.

### 아키텍처 주도권 (대표님 지침, 2026-08-18)

세 외부 프로젝트는 DAON의 상위 시스템이 아니며, 기능으로 들여놓는 것도 아니다.
**DAON Dynamic Harness가 상위 오케스트레이터**이고, 외부 프로젝트는 오직
DAON의 각 층에 이식할 패턴/교훈/지식 소스다.

| 외부 프로젝트 | 잘못된 표현 | 정확한 표현 |
|---|---|---|
| Symphony | Symphony를 DAON에 넣는다 | DAON Dynamic Harness가 상위 오케스트레이터이고, Symphony에서 검증된 **장주기 작업 오케스트레이션 패턴**(폴링/격리/화해/워크플로 계약)을 **실행 계층에 이식**한다 |
| bb | bb를 DAON에 넣는다 | bb의 **worktree 격리·self-hosting·bootstrap 설계 교훈**을 DAON의 **자기 적용 계층에 이식**한다 |
| Build Your Own X | BYOX를 기능으로 넣는다 | Builder Agent가 필요한 능력을 구축할 때 사용하는 **설계 지식/패턴 소스**로 활용한다 |

> **검증 결론 (2026-08-18, 코드 대조):** 처음에는 "Symphony + bb + BYOX를 DAON에 붙이면 어떨까?"라고
> 생각했지만, 실제로는 상황이 반대다. **재료의 대부분은 이미 DAON 안에 있었고**, 외부 프로젝트들은
> 빠진 연결선(E-L1~L4)과 설계 패턴을 알려주는 역할에 가깝다. 따라서 갭 E의 다음 단계는
> 새로운 시스템을 만드는 것이 아니라 **연결선을 닫는 작업**이다.

### 핵심 통찰 — 부족한 건 판단 능력이 아니라 행동의 연결

갭 C의 `missing_caps`는 이미 존재한다(`_run_acceptance_replan()`, api/api/dynamic/orchestrator.py).
즉 DAON은 **"현재 능력으로 이 작업을 해결할 수 없다"를 이미 판단할 수 있다.**
그러므로 부족한 건 판단 능력 자체가 아니라 **다음 행동으로의 연결**이다.

### 결정 사슬 (대표님 원안, 2026-08-18)

```
missing_caps
     │
     ▼
"능력 부족"
     │
     ├── 기존 Skill 검색
     │
     ├── 다른 Agent 배정
     │
     └── 그래도 불가능
              │
              ▼
        ★ Builder 호출
              │
              ▼
      Skill / Plugin / MCP 제작
              │
              ▼
          Sandbox
              │
              ▼
          Probe/Test
              │
              ▼
          Approval
              │
              ▼
           Promote
              │
              ▼
       Skill Registry
              │
              ▼
        원래 작업 재개
```

### 불변 순서 — 자기파괴 루프 방지

**Builder가 만든 능력을 곧바로 자기 자신에게 적용하면 안 된다.** 반드시:

```
생성 → 격리 → 검증 → 승인 → 편입 → 사용
```

이 순서를 거쳐야 한다. 그래야 Ouroboros가 **자기증폭 루프**가 되지,
**자기파괴 루프**가 되지 않는다.

### 기록과 시공의 분리 (대표님 지침)

- Master Architecture 기록과 실제 시공은 **분리**한다.
- 당장의 실행 순서: **Master Architecture 기록 → 승인 상태 저장 → 기존 E-0부터 순차 시공**.
- 기존 갭 E 시공 순서(E-0a → E-0c → E-1 → E-2 → E-3 → E-4)는 건드리지 않는다.
- 시공 중 L1~L4는 **독립적으로 검증 가능한 갭(E-L1~E-L4)**으로 쪼개 진행해,
  나중에 "어느 순간부터 DAON이 자기 능력을 만들어내기 시작했는지" 정확히 추적할 수 있게 한다.

### 루프 구조 (대표님 원안)

```
                 ┌─────────────────────┐
                 │       목표/문제       │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │    Symphony Layer   │
                 │  작업 분해 / 배정 /   │
                 │  격리 / 실행 / 검증    │
                 └──────────┬──────────┘
                            ↓
             ┌──────────────┴──────────────┐
             ↓                             ↓
     ┌───────────────┐             ┌───────────────┐
     │  Coding Agent │             │  Builder Agent │
     │   실제 구현    │             │ 필요한 도구 제작 │
     └───────┬───────┘             └───────┬───────┘
             │                             │
             └──────────────┬──────────────┘
                            ↓
                 ┌─────────────────────┐
                 │   Build Your Own X  │
                 │ "필요하면 직접 만든다" │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │       bb 계열       │
                 │  IDE / Agent / Tool │
                 │     자기 확장        │
                 └──────────┬──────────┘
                            ↓
                    새로운 능력/Skill
                            │
                            └──────→ 다시 위로
```

※ 주도권 조항에 따라: "Symphony Layer" 상자는 DAON Dynamic Harness 실행 계층(Symphony 패턴 이식),
"bb 계열" 상자는 DAON 자기 적용 계층(bb 교훈 이식), "Build Your Own X"는 Builder Agent의
설계 지식 소스를 뜻한다. 그림은 대표님 원안 그대로 기록용으로 보존한다.

### 루프 박스 ↔ DAON 기존 부품 대조 (코드 검증 완료, 2026-08-18)

루프의 약 70%는 이미 DAON 안에 존재한다.

| 루프의 상자 | DAON의 기존 부품 | 상태 |
|---|---|---|
| 목표/문제 | 사용자 입력 / TODO 패널 / 하네스 콘솔 | ✅ 존재 |
| Symphony Layer (분해/배정/격리/실행/검증) | `api/api/dynamic/planner.py`(분해) + `api/api/dynamic/runner.py`(배정·실행) + 리뷰어·수용 기준(검증) | ✅ 대부분 — **격리만 부재** |
| Coding Agent | hermes-agent 노드 실행 | ✅ 존재 |
| Builder Agent (도구 제작) | `plugin_create`(hermes-agent/tools/plugin_manager_tool.py) + `skill_manage`(hermes-agent/tools/skill_manager_tool.py) | ⚠️ 도구는 있으나 **독립 역할로 배정되는 경로 없음** |
| Build Your Own X ("필요하면 만든다") | 갭 C의 `missing_caps` — `_run_acceptance_replan()`(api/api/dynamic/orchestrator.py)이 결핍 능력을 이미 검출 | ⚠️ 검출은 하나 "제작"으로 안 이어짐 |
| bb 계열 자기 확장 | `_sync_plugin_tool_env()` + `_refresh_hermes_plugins()` 핫 리로드 (api/api/plugin_gateway.py) | ✅ 재시작 없이 반영 |
| 새 능력 → 다시 위로 | `skill_registry`(api/api/skill_registry.py)의 `register_new_auto_skill()` + `promote_skill()` 라이프사이클 + 플래너 카탈로그 자동 노출 | ✅ 닫혀 있음 |

### 루프를 닫는 4개의 연결선 (E-L1–E-L4, 독립 검증 가능 갭)

각 연결선은 **독립적으로 검증 가능한 갭(E-L1~E-L4)**으로 쪼개 시공한다.
E-L1이 심장이고, E-L2~E-L4가 붙으면 폐루프가 된다.

| # | 연결선 | 내용 | 난이도 |
|---|---|---|---|
| **E-L1** | "결핍 → 제작" 결정 분기 | 갭 C가 `missing_caps`를 검출하면 지금은 재계획만 함. 여기에 위 결정 사슬(기존 Skill 검색 → 다른 Agent 배정 → 그래도 불가능 → Builder 호출)을 연결. **루프의 심장** | 중간 |
| **E-L2** | Builder Agent 1급 역할화 | `delegate_team`으로 "도구 제작 전문 서브팀"을 스폰하는 전용 경로 (스킬/플러그인/MCP 중 무엇을 만들지 판정 포함) | 중간 |
| **E-L3** | 격리 | Symphony 안전 불변식(SPEC 9.5: 에이전트는 워크스페이스 안에서만 / 경로는 루트 안에만 / 키 sanitize+해시) + git worktree (E-4) | 중간~어려움 |
| **E-L4** | 편입 거버넌스 | 제작된 도구가 draft → 프로브 검증 → promote 순서로만 정식 편입 (기존 `promote_skill` 재활용). 불변 순서(생성→격리→검증→승인→편입→사용)를 강제 | 쉬움 |

이 네 선을 그으면 "스스로 도구를 만들고 → 그 도구로 작업하고 → 결과를 다시 편입하는"
폐루프가 완성된다. 외부 입력 없이도 돌아가는 구조.

### Symphony 뼈대 이식안 (2026-08-18 의논, 대표님 최종 결정 대기)

위치 설정: **DAON Dynamic Harness가 상위 오케스트레이터**이고, Symphony의 장주기 작업
오케스트레이션 패턴을 실행 계층에 이식한다 (0A절 주도권 조항).
추천: **Symphony의 뼈대만 이식하고, 장기는 DAON 것을 그대로 쓴다.**

- Symphony의 본질 = ① 폴링+디스패치 스케줄러 ② 이슈당 격리 워크스페이스
  ③ 화해(reconciliation) 루프 ④ 레포 소유 WORKFLOW.md 계약
- DAON은 자체 런타임·재귀 위임·승인 게이트·수용 기준이라는 장기가 있음 → Codex를 따라 할 이유 없음
- 추천 경로:
  1. 작업 소스 = DAON 내부 TODO 패널 우선 (`panelTodos` in index.html), GitHub Issues 어댑터는 나중에
  2. 서버 내 얇은 오케스트레이터 루프 (폴 틱 + 화해 + 백오프 재시도) — 기존 `dynamic_jobs` 백그라운드 스레드 인프라 활용
  3. 승인 게이트 유지 (DAON 정체성 = 겁나지 않는 초보 도구), 나중에 "자동 모드" 토글 추가
  4. 격리 = git worktree + Symphony 안전 불변식 3조항
  5. `DAON_WORKFLOW.md` 레포 소유 계약 (YAML front matter + 프롬프트 템플릿, 버전 관리 + 핫 리로드)
  6. 장기: `DAON_SPEC.md` — "스펙이 제품" 철학 (에이전트가 스펙을 읽고 DAON을 재현/개선)

### Build Your Own X 3층 구조 (DAON 적용)

1. **철학 층**: DAON이 자기 하위 시스템(잡 레지스트리/위임 가드/스킬 허브)의 스펙을 직접 쓰고,
   에이전트가 그 스펙을 읽고 재구현해 기존 구현과 대조·검증 — 갭 E 자기 적용의 완성형
   ("코드를 고치는 것"을 넘어 "자기를 이해하는 것")
2. **제품 층**: 초보자를 위한 "다온이와 함께 나만의 X 만들기" guided 경험 —
   에이전트가 대신 짜주는 게 아니라 같이 만들어서 이해시킴. BYOX는 CC0라 자유 활용 가능.
   bb/Symphony가 못 하는 초보자 시장을 DAON이 차지
3. **벤치마크 층**: BYOX 카테고리(정규식 엔진/Git/데이터베이스...)를 DAON 자율능력 시험 미션으로 활용 —
   Symphony 뼈대 위에서 무인 실행 → 프로브 검증

첫 시범 제안: **"다온이와 나만의 Git 만들기"** (BYOX Git 카테고리 + 기존 `static/modules/git.js` 패널 활용)

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

> 이 절의 E-0/E-1~E-4는 E-Master Architecture(0A절)의 하위 시공 단위다.
> 대표님 지침(2026-08-18)에 따라 **아래 시공 순서는 변경 없이 유지**하며,
> E-L1~E-L4 연결선은 독립 갭으로 쪼개 본선 이후 순차 시공한다 (4.2절).

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

- worktree 격리: 자기 수정 전용 브랜치+worktree에서 실험 후 병합 (E-2의 격리 강화판, L3의 기반)
- 에이전트용 CLI 표면: `daon` CLI로 하네스 잡을 스크립트에서 띄울 수 있게 (bb의 1급 표면 철학)
- SQLite 영속화: 잡/스레드 상태를 재시작 후에도 복원

---

## 4. 시공 순서와 검증

### 4.1 갭 E 본선

| 순서 | 항목 | 검증 방법 | 상태 |
|---|---|---|---|
| 1 | E-0a `mcp_manage` 도구 | 프로브: 등록→연결→도구 목록 조회 모의 | ✅ 완료 (2026-08-19, `_probe/probe_gap_e0a.py` 전 액션 통과) |
| 2 | E-0c `plugin_create` 도구 템플릿 | 프로브: 스캐폴드 생성→import 성공 확인 | ✅ 완료 (2026-08-19, `_probe/probe_gap_e0c.py` 35개 체크 통과) |
| 3 | E-1 대상 결속 + `daon-self-knowledge` 스킬 | 스킬 카탈로그 노출 확인 | ✅ 완료 (2026-08-19, `_probe/probe_gap_e1.py` 40개 체크 통과) |
| 4 | E-2 안전 통치 | `probe_gap_e.py` (체크포인트→승인→프로브→복귀 전 구간 모의) | ✅ 완료 (2026-08-19, `_probe/probe_gap_e.py` 89개 체크 통과) |
| 5 | E-3 부트스트랩 | 일렉트론 측 재시작 오케스트레이션 + 헬스체크 (실기기 검증 필요) | ✅ 완료 (2026-08-19, `_probe/probe_gap_e3.py` 54체크 + `_probe/probe_gap_e3.js` 80체크 통과. 실기기 검증은 빌드 후 별도 수행) |
| 6 | E-4 장기 항목 | 별도 계획 분리 | ✅ 완료 (2026-08-19, [`docs/E4_LONG_TERM_ROADMAP.md`](E4_LONG_TERM_ROADMAP.md)로 분리 — E-4a worktree 격리 / E-4b daon CLI / E-4c SQLite 영속화, 조건 기반 시공) |

**E-0a 시공 기록 (2026-08-19):**
- 신규 [`hermes-agent/tools/mcp_manager_tool.py`](../hermes-agent/tools/mcp_manager_tool.py) — `mcp_manage` 단일 도구, action 기반 7개 액션(list/add/add_preset/remove/connect/disconnect/tools)
- 기존 MCP 매니저 싱글톤(`api.mcp_client.get_mcp_manager()`)을 지연 임포트로 재호출 → UI 패널과 상태 일관
- 시크릿 정책: `auth_token`은 결과에서 마스킹(`_sanitize`) — 에이전트가 토큰 값을 되받아 로그에 남기지 않음
- `discover_builtin_tools()`가 `tools/*.py`를 자동 스캔하므로 파일 생성만으로 등록됨 (check_fn으로 DAON 런타임 여부 판정)
- 프로브 `_probe/probe_gap_e0a.py`: 전 액션 라우팅 + 입력 검증 + sanitize + 스키마 무결성 + registry 등록 확인 — 전부 통과

**E-0c 시공 기록 (2026-08-19):**
- [`plugin_create`](../hermes-agent/tools/plugin_manager_tool.py)에 `tool_template`/`tool_description` 옵션 추가 — 지정 시 `__init__.py`(`register(ctx)` + `ctx.register_tool` 최소 구현: schema/handler/check_fn 플레이스홀더)를 스캐폴드하고 plugin.yaml `tools:` 목록에 선언
- 격차의 본질: 기존 스캐폴드는 `__init__.py`를 만들지 않아 생성 플러그인이 도구를 노출할 수 없었음. `PluginManager._load_directory_module`가 `__init__.py`를 임포트하고 `_load_plugin`이 `register(ctx)`를 호출하는 계약에 맞춤
- `tool_template` 지정 + `skill_name` 미지정 시 도구 전용 플러그인(SKILL.md 없음)으로 스캐폴드 — 기존 스킬 전용 경로는 무변경
- 툴셋은 `plugin-{name}`으로 네임스페이스화해 충돌 방지
- 프로브 `_probe/probe_gap_e0c.py`: 도구 전용/병행/레거시 스캐폴드 + 생성 `__init__.py` 실제 임포트 후 `register(ctx)` 계약 검증 + 입력 검증 + 스키마/registry 배선 — 35개 체크 전부 통과

**E-1 시공 기록 (2026-08-19):**
- 신규 [`skills/System/daon-self-knowledge/`](../skills/System/daon-self-knowledge/SKILL.md) — DAON 자체 지식을 스킬 1장으로 주입: 레포 구조, 빌드 파이프라인 순서(push → `_sync_build.py` → PyInstaller `daon-server.spec` → electron-builder → `.cmd` 확인 → zip), electron-builder `.cmd` 삭제 함정 + 재생성 bat, 프로브 실행법, 운영 정책, 자기 수정 불변식
- `SkillRegistry._scan()`이 curated `skills/`를 `rglob("SKILL.md")`로 자동 발견하므로 등록 코드 불필요 — 라이프사이클은 `approved`, CEO 카탈로그의 Curated 섹션에 노출됨
- [`workspace.py`](../api/api/workspace.py) 워크스페이스 프리셋 추가: `get_workspace_presets()`가 'DAON Repo' 프리셋 노출(server.py/server.exe 마커 확인 시에만), `ensure_workspace_presets()`가 읽기 시점에 멱등 주입(사용자가 삭제해도 복원, 같은 경로 대소문자 무시 중복 방지, 원본 목록 미수정), `load_workspaces()`가 `_load_workspaces_base()`를 래핑
- 설계 결정: 프리셋은 저장 파일(workspaces.json)을 오염시키지 않고 읽기 시점 주입 — 프로필 간 일관되고 시스템 관리 항목으로서 항상 존재
- 프로브 `_probe/probe_gap_e1.py`: 스킬 파일 파싱 + 카탈로그 노출(get_skill/get_catalog_text/load_skills) + 프리셋 멱등 주입/원본 불변/실제 load_workspaces — 40개 체크 전부 통과

**E-2 시공 기록 (2026-08-19):**
- 신규 [`api/api/dynamic/self_modify.py`](../api/api/dynamic/self_modify.py) — `SelfModifyPipeline` 상태 머신(8상태: init→checkpointed→awaiting_approval→applied→verified→committed, 실패 시 reverted/rejected). 자기 수정 실행이 반드시 통과해야 할 순서 강제: git 체크포인트(자동 커밋) → 승인 게이트 → 수정 적용 → 프로브 회귀검사 → 통과 시 커밋 확정 / 실패 시 체크포인트 자동 복귀(`git reset --hard` + `clean -fd`)
- 설계 결정: 모든 외부 효과(git 실행, 프로브 실행, 수정 적용)를 주입 가능한 러너/콜백으로 분리 — 서버는 실제 구현을, 프로브는 페이크를 배선. `_accepts_cwd()`가 러너 시그니처를 감지해 cwd 전달 여부를 자동 판정
- 기존 통치 자산 재사용: 승인 게이트(`api.approval`의 set_pending/has_pending/get_history 계약, `approval=None`이면 자동 승인으로 dev/프로브용), 위임 가드(`check_delegation_guard` — delegation_ctx 제공 시 깊이/spawn_reason 검사로 자기 수정도 갭 D 통치 하에 둠)
- 실패 경로 전부 자동 복귀: 승인 거부/타임아웃, apply_fn 예외, 프로브 회귀 실패 시 체크포인트로 롤백 후 터미널 상태 기록. 순서 위반(단계 건너뛰기, 이중 체크포인트, 완료 후 재실행)은 `SelfModifyError`로 차단
- 프로브 `_probe/probe_gap_e.py`: 모듈 표면 + 해피패스(git 호출 순서 rev-parse→add→commit→add→commit 검증) + 승인 게이트(승인/거부/타임아웃) + 실패 경로(apply 예외/프로브 실패/git 실패 4종/롤백 실패) + 위임 가드 + 순서 강제 + history 원장 — 89개 체크 전부 통과

**E-3 시공 기록 (2026-08-19):**
- 신규 [`api/api/dynamic/restart_request.py`](../api/api/dynamic/restart_request.py) — 서버 측(피감시자) 와이어 프로토콜. 서버는 절대 자기 재시작을 수행하지 않고 `STATE_DIR/restart-request.json` 요청 파일만 원자 기록(tmp + `os.replace`). 진행 중 잡 가드(리스크 3): `running`/`clarifying`/`awaiting_approval` 상태 잡이 있으면 `RestartRequestError`로 거부. payload = type/version/reason/checkpoint_ref/requested_at/server_pid
- 신규 [`electron/restart_orchestrator.js`](../electron/restart_orchestrator.js) — 일렉트론 측(감시자) 오케스트레이션. 요청 파일 폴링(5초) → 서버 kill → 재기동 → 헬스체크 → 실패 시 체크포인트 git 롤백(`reset --hard` + `clean -fd`) → 재-kill → 재기동 → 재헬스체크(최대 2회 시도). 손상/비객체 요청 파일은 삭제 후 무시(차단 방지). 모든 외부 효과(fs/kill/spawn/health/git/sleep) 주입 가능 설계
- [`electron/main.js`](../electron/main.js) 배선: 오케스트레이터 생성·시작(STEP 3e), `selfModifyRestartActive` 플래그로 기존 exit 핸들러 자동재시작과 충돌 방지(오케스트레이터가 respawn 소유), kill 시 워치독 보류 구간 설정, 사이클 완료 후 워치독 카운트 리셋 + UI reload, before-quit에서 `stop()`
- 설계 결정: 감시자/피감시자 분리 — 재시작 실행 권한은 감시자(일렉트론 메인)만 보유. 서버가 죽어도 감시자는 살아있으므로 롤백 후 회복 가능. STATE_DIR 이중 후보 스캔(dev: 레포/data, packaged: %LOCALAPPDATA%/DAON Agent System/data)
- 프로브: `_probe/probe_gap_e3.py`(54체크: 요청 모듈 표면/잡 가드/원자 기록/소비/손상 처리 + node 서브프로세스 실행 + main.js 배선 정적 확인) + `_probe/probe_gap_e3.js`(80체크: 해피패스/롤백 회복/롤백 실패/kill 실패/손상 파일/busy 가드/디렉터리 스캔/라이프사이클 등 17시나리오, 페이크 deps) 전부 통과. 프로브 첫 실행에서 오케스트레이터의 배열 payload 미거부 결함 발견 → `Array.isArray` 가드 추가 후 통과(프로브가 실제 결함을 잡은 사례)

**E-4 분리 기록 (2026-08-19):**
- 3.4절의 장기 항목 3종을 [`docs/E4_LONG_TERM_ROADMAP.md`](E4_LONG_TERM_ROADMAP.md)로 분리: **E-4a** worktree 격리(E-L3 동반 시공, `SelfModifyPipeline` 주입점에 cwd 교체로 격리 워크트리 적용), **E-4b** `daon` CLI 표면(E-L2 이후, 기존 HTTP API의 얇은 래퍼), **E-4c** SQLite 영속 상태(E-3 실기기 검증 완료 후, `STATE_DIR/daon_state.db` 최소 스키마)
- 분리 원칙: 고정 일정이 아닌 **조건 기반 시공** — 각 항목의 시공 트리거(선행 갭 완료)를 명시하고, 시공 시 본선과 동일 규율(프로브 → 통과 → commit+push → 문서 갱신) 적용
- 이로써 갭 E 본선(E-0a → E-4) 6개 항목 전부 완결. 다음 단계는 4.2절 E-L1~E-L4 독립 갭 순차 시공

**E-L1 시공 기록 (2026-08-19):**
- 신규 [`api/api/dynamic/capability_resolver.py`](../api/api/dynamic/capability_resolver.py) — 0A절 결정 사슬을 주입 가능한 순서 체인으로 구현. `CapabilityResolver.resolve(missing_caps)`가 능력별로 **스킬 검색 → 에이전트 배정 → Builder 요청** 순서를 강제하고 단축 평가(첫 해결 단계가 승리). 결과는 `resolved_by_skill` / `resolved_by_agent` / `needs_builder` 3종(와이어 안정 문자열). 단계 예외는 "해결 불가"로 처리해 체인 계속(fail-safe — 스킬 조회 실패가 작업을 차단하지 않음). `enable_builder=False`는 순수 라우터 모드(최대 깊이 위임 자식 실행용)
- E-L1은 **분기(라우팅+판정)만** 구현 — Builder 단계는 제작 요청(`builder_request`: capability/source/status=pending)을 기록할 뿐이며, 실제 제작은 E-L2가 소비. 이것이 "DAON이 자기 능력을 만들기 시작하는" 정확한 분기점
- [`orchestrator.py`](../api/api/dynamic/orchestrator.py) 배선: `_run_acceptance_replan()`에서 `missing_caps`가 비어있지 않으면 `_resolve_missing_capabilities()`가 결정 사슬을 실행해 능력별 판정을 재계획 프롬프트에 주입(스킬 해결=해당 스킬 사용 지시, 에이전트 배정=해당 역할 노드 배치, 제작 요청=우회 방안 반영 지시)하고, `merged_plan`에 `capability_resolutions`/`builder_queue`를 노출. 리졸버는 지연 구성(`self.capability_resolver = None` → 기본 생성)으로 기존 무인자 생성과 호환, 리졸버 예외 시 기존 caps_line 경로로 우아한 폴백(재계획은 절대 차단되지 않음)
- 설계 결정: 분기만 구현(Builder 실체는 E-L2) / 순서 강제+단축 평가 / 전 단계 주입 가능(프로브 검증) / 단계 예외=해결 불가(fail-safe) / 리졸버 전체 실패=기존 경로 폴백
- 프로브: `_probe/probe_gap_el1.py` 67체크 전부 통과 — 상수/순서 값, 순서 강제+단축 평가(스킬 해결 시 에이전트/Builder 미호출), 단계 예외 fail-safe, resolve() 정규화(None/str/빈 cap 스킵/비-iterable 거부), builder_queue 수집, 기본 단계 구현(페이크 레지스트리 토큰 매칭/역할 매칭/Builder 요청 형태), 오케스트레이터 배선(프롬프트 주입/merged_plan 노출/리졸러 예외 폴백/빈 caps 경로), 정적 배선 확인

**E-L2 시공 기록 (2026-08-19):**
- 신규 [`api/api/dynamic/builder_agent.py`](../api/api/dynamic/builder_agent.py) — E-L1이 남긴 `builder_queue`를 소비하는 Builder 역할 모듈. 파이프라인: ① 제작 대상 분류(`classify_build_target`: mcp/plugin 키워드 휴리스틱, 기본=스킬(가장 가벼운 산출물), classifier 주입 가능) ② 승인 게이트(`default_builder_gate`: **리스크 5 기본 강제 — approver 미등록 시 스폰 불가**, approver 예외=거부 fail-safe) ③ Builder 미션 구성(`build_builder_task`: draft 전용·자체 promote 금지(E-L4 관할)·워크스페이스 경로 한정·프로브 필수·산출물 경로+사용법 포함, 수용 기준 4개) ④ 스폰(`default_builder_spawner`: `delegate_team` 래핑 — 갭 D 위임 가드/예산/혈통을 그대로 통과, 절대 raise 안 함)
- `dispatch_builder_requests(builder_queue, ...)`가 큐를 입력 순서대로 소비해 디스패치 레코드(`capability`/`build_target`/`status`: spawned|denied|error /`child_run_id`/`final_output`/`spawn_reason`)를 반환. 게이트/스포너 시그니처 유연성(`_accepts_approver`/`_accepts_model` inspect 헬퍼), 전 단계 주입 가능, 절대 raise 안 함(실패=error 레코드, 큐는 계속)
- [`orchestrator.py`](../api/api/dynamic/orchestrator.py) 배선: `_run_acceptance_replan()`에서 `builder_queue`가 비어있으면 `_dispatch_builder_queue()`가 승인 게이트(`self.builder_approver`) 통과 건만 스폰하고, 결과를 `merged_plan["builder_dispatches"]`로 노출. `builder_approver`/`builder_spawner`는 지연 구성(기본 None = 게이트 거부 — 리스크 5 안전 기본값). 디스패치 요약 로그 방출, 재계획 경로는 절대 차단되지 않음
- 설계 결정: 승인 게이트 기본 거부(자동 모드는 명시적 opt-in) / 제작 대상은 결정적 휴리스틱(기본=스킬) / 미션에 draft 제약 명시(E-L4 편입 거버넌스와 관할 분리) / delegate_team 래핑으로 갭 D 가드 재사용 / 스폰된 서브팀은 중첩 Dynamic Harness 실행이라 갭 C 수용 기준 검증 프랙탈 상속
- 프로브: `_probe/probe_gap_el2.py` 92체크 전부 통과 — 상수 값, 제작 대상 분류(키워드/classifier 주입/무효·예외 폴백), 미션 구성(draft/promote 금지 문구/수용 기준 4개), 승인 게이트(미등록 거부/허용/거부/예외 fail-safe), dispatch 큐 소비(spawned/denied/error/게이트 예외/스포너 예외/비-dict 반환/빈 cap 스킵/순서 보존/시그니처 호환/로그 방출/기본 게이트 거부), 오케스트레이터 배선(builder_queue→디스패치→merged_plan 노출/approver 미등록 시 스포너 미호출 확인/스킬 해결 시 키 없음), 정적 배선 확인

**E-L3 시공 기록 (2026-08-19, E-4a 동반):**
- 신규 [`api/api/dynamic/isolation.py`](../api/api/dynamic/isolation.py) — Symphony SPEC 9.5 안전 불변식 3조항을 함수로 구현 + git worktree 격리. ① Invariant 1(`validate_cwd_is_workspace`: 실행 전 cwd == workspace_path resolve 동등 검증) ② Invariant 2(`is_path_inside_root`/`validate_path_inside_root`: 양쪽 절대경로 정규화 후 prefix 디렉터리 검증, 루트 밖 경로 거부, 절대 raise 안 하는 bool 판정 + raise하는 validate 쌍) ③ Invariant 3(`sanitize_workspace_key`: `[A-Za-z0-9._-]`만 허용·나머지 `_` 교체·교체 발생 시 sha256 64비트 hex 접미사 부착으로 충돌 저항)
- `WorktreeIsolation` 클래스 — git worktree 생명주기: `create()`(브랜치 `self-modify/<sanitize된 run_id>`, 관리 디렉터리 `STATE_DIR/worktrees` 안에 생성 후 Invariant 2 재검증) → `merge_back()`(검증 통과 시 `git merge --no-edit`, 실패 시 cherry-pick 폴백, 완료 후 worktree 제거+브랜치 `-d`) / `discard()`(실패·거부 시 force remove+브랜치 `-D`, 절대 raise 안 함·멱등). 전 구간 주입 가능 `git_runner`로 프로브는 페이크 사용
- `run_isolated_self_modify()` — E-4a 통합 실행기. `SelfModifyPipeline`의 **기존 `cwd` 파라미터에 격리 워크트리 경로를 주입**(인터페이스 변경 없음), apply_fn이 1인자면 워크트리 경로를 주입(`_apply_accepts_path` inspect 판정)해 격리 워크트리 안에 파일을 쓰게 함. 파이프라인 결과 ok → merge_back, 아니면 discard. 결과는 dict(`ok`/`isolated`/`merge`/`worktree_path`/`branch`), 절대 raise 안 함
- 설계 결정: SPEC 9.5 불변식은 순수 함수(프로브 직접 검증) / worktree는 레포 루트 밖 관리 디렉터리(`STATE_DIR/worktrees`, 폴백 `.daon_state/worktrees`) / 병합 실패 시 cherry-pick 폴백 후 양쪽 실패만 raise / discard는 어떤 경로에서도 잔여 워크트리·브랜치를 남기지 않음 / SelfModifyPipeline은 현재 프로브에서만 사용되므로 orchestrator 배선 없이 독립 모듈로 시공(E-4b/E-4c와 동일 규율)
- 프로브: `_probe/probe_gap_el3.py` 81체크 전부 통과 — 키 sanitize(결정적/충돌 저항/빈 값), 경로 포함 불변식(중첩/루트/형제/`..`/절대경로/None), cwd==workspace 검증, WorktreeIsolation 생명주기(페이크 git 러너: create/merge/cherry-pick 폴백/discard/멱등/이중 create 가드/실패 주입), run_isolated_self_modify 통합(성공 시 Invariant 1 재검증·프로브 실패/apply 예외/승인 거부 시 discard·worktree 실패 시 비격리 에러), 정적 표면. 첫 실행에서 전부 통과

**E-L4 시공 기록 (2026-08-19):**
- 신규 [`api/api/dynamic/incorporation.py`](../api/api/dynamic/incorporation.py) — 불변 순서(생성→격리→검증→승인→편입→사용)의 편입 구간을 강제하는 거버넌스 파이프라인. 4단계: ① 진입 게이트(`check_entry_gate`: draft만 진입, approved/rejected/incorporated 재편입 거부, 이름 누락 거부) ② 검증(`verify_artifact`: probe_paths 누락/빈 목록은 거부 — 거버넌스는 프로브 검증을 필수로 요구, 첫 실패에서 중단, 러너 예외=fail-safe 거부) ③ 승인(`approve_artifact`: **리스크 5 기본 강제 — approver 미등록 시 거부**, approver 예외=거부) ④ 편입(`default_skill_promoter`: 기존 `SkillRegistry.promote_skill` 재활용, 게으른 임포트)
- `run_incorporation(artifact, probe_runner, approver, promoter)`은 `INCORPORATION_ORDER`(entry→verify→approve→incorporate)를 엄격히 순서 실행하고, 단일 게이트 실패 시 즉시 중단해 **이후 단계 콜러블은 절대 호출되지 않는다**(검증 실패→approver/promoter 미호출, 승인 거부→promoter 미호출). 결과는 dict(`ok`/`status`: incorporated|rejected|error /`name`/`stages`/`reason`), 절대 raise 안 함
- **순서 강제 증거**: `result["stages"]`는 항상 `INCORPORATION_ORDER`의 **접두사** — 어느 단계에서 파이프라인이 멈췄는지를 증명하는 감사 추적. 프로브는 각 실패 경로에서 후속 콜러블 미호출을 CallRecorder 페이크로 검증
- 설계 결정: 전 단계 주입 가능(probe_runner/approver/promoter) / 절대 raise 안 함 / stages=감사 추적 / promote_skill 재활용(새 승격 경로 발명 금지) / 수동 UI promote 경로(admin_routes)는 인간 승인 표면이라 의도적으로 미변경 / orchestrator 배선 없음(E-L3와 동일 근거 — 프로덕션 배선은 E-4b/E-4c 관할)
- 프로브: `_probe/probe_gap_el4.py` 76체크 전부 통과 — 상수+순서 정합성(거버넌스 단계는 불변 순서의 부분 수열), artifact 접근자(이름/lifecycle 추출·정규화), 진입 게이트(draft 허용/재편입 거부/이름 없음), 검증 단계(프로브 없음 거부/단일·다중 통과/첫 실패 중단/예외 fail-safe/bool·단일 문자열 호환), 승인 단계(미등록 거부/튜플 호환/예외 fail-safe), run_incorporation 순서 강제(해피패스/검증 실패→승인·편입 미호출/승인 거부→편입 미호출/미등록 거부/진입 거부→전부 미호출/promoter 실패·예외→error/stages 접두사 성질), default_skill_promoter 안전성, E-L2 핸드오프 정합성(미션의 E-L4 관할 선언+draft 수용 기준). 첫 실행에서 전부 통과

**커밋 예산 시공 기록 (2026-08-19) — 리스크 1 후반 보강:**
- 라온(서브 에이전트)이 코드 감사에서 발견: 리스크 1의 전반부(위임 가드 재사용)는 `_guard_check()`로 시공됐으나, 후반부 "자기 수정 전용 예산(1회 실행당 최대 N커밋) 별도 상수"는 미시공 상태였음(의도적 생략 아님)
- [`api/api/dynamic/self_modify.py`](../api/api/dynamic/self_modify.py) 수정 — `MAX_COMMITS_PER_RUN = 4` 상수 + `try_consume_commit_budget`/`count_commits`/`reset_commit_budget`(원자 카운터, delegation.py 스폰 예산과 동일 규율). `SelfModifyPipeline`은 `_stage_checkpoint`/`_stage_finalize`에서 각 1슬롯을 소비하고, 상한 도달 시 `SelfModifyError`로 해당 커밋 단계를 fail-safe 차단. `commit_budget_key=None` 기본 = 예산 해제(하위 호환) — 프로덕션 배선(E-4b/E-4c)이 공유 키(예: root_run_id)를 주입하면 한 세션의 반복 시도가 예산을 공유해 무한 루프가 차단됨
- 프로브: `_probe/probe_gap_e.py` Group 6 추가(25체크) — 단위(소비/상한 거부/리셋/빈 키·0·음수 fail-safe), 기본 상한 해피패스(정확히 2슬롯 소비), cap 1이면 finalize 차단(verified에서 정지, finalize git 호출 없음), 공유 예산 루프 차단(2차 시도 checkpoint에서 차단·git 호출 0건), 키 없음 하위 호환. 총 114체크 통과, E-L3 프로브 81체크 회귀 없음

### 4.2 E-Master Architecture 연결선 (독립 검증 가능 갭)

E 본선(4.1) 이후 순차 시공. 각 항목은 독립 갭이며, 갭 완료 시마다
py_compile/프로브 통과 → git commit+push → 이 문서 상태 갱신.
"어느 순간부터 DAON이 자기 능력을 만들어내기 시작했는지" 정확히 추적하기 위함.

| 순서 | 갭 | 내용 | 검증 방법 |
|---|---|---|---|
| E-L1 ✅ | 결핍→제작 분기 (심장) | 0A절 결정 사슬을 `_run_acceptance_replan()`에 연결 | 프로브: `missing_caps` 검출 시 Builder 배정 분기 모의 — `capability_resolver.py` + orchestrator 배선, 프로브 67체크 통과 |
| E-L2 ✅ | Builder Agent 역할화 | `delegate_team`으로 도구 제작 서브팀 스폰 | 프로브: Builder 서브팀 스폰 + 제작 산출물 인도 모의 — `builder_agent.py` + orchestrator 배선, 프로브 92체크 통과 |
| E-L3 ✅ | 격리 | 워크스페이스 안전 불변식 + git worktree | 프로브: 경로 불변식 위반 차단 확인 — `isolation.py`(SPEC 9.5 3조항 + WorktreeIsolation + run_isolated_self_modify, E-4a 동반), 프로브 81체크 통과 |
| E-L4 ✅ | 편입 거버넌스 | 생성→격리→검증→승인→편입→사용 강제 | 프로브: draft→프로브→promote 순서 강제 확인 — `incorporation.py`(거버넌스 4단계 파이프라인, promote_skill 재활용), 프로브 76체크 통과 |

**폐루프 완성 (2026-08-19):** E-L1~E-L4 4개 항목 전부 ✅ — E-Master Architecture의 불변 순서
생성(E-L1 결핍 감지 → E-L2 Builder 제작) → 격리(E-L3 worktree) → 검증·승인·편입(E-L4 거버넌스) → 사용(기존 SkillRegistry 카탈로그 노출)이
전부 연결되었다. "어느 순간부터 DAON이 자기 능력을 만들어내기 시작했는지" 정확히 추적 가능하며,
편입은 오직 E-L4가 강제하는 순서(draft → 프로브 검증 → 승인 → promote)를 통해서만 이루어진다.

**앱 실사용 검증 (2026-08-20) — 폐루프 실증 완료 ✅:**
프로브(모의)가 아닌 실제 앱 서버(포트 9090)에서 `POST /api/dynamic/run`으로 작업을 투입해 실관찰했다. 오류 0건.
- 시나리오 B (기본 동적 하네스, run_id=2556ff371cb9491c, 201.9초): 갭 C 수용 기준 자동 추출(4기준) → 2노드 DAG 계획 → 컴파일 → 실행 → 검증 → 병합 → Verifier 수용 기준 통과 → CodeReviewer 보고서. `hello.html` 실제 생성 검증(264B 유효 HTML5). DynamicModelSelector 역할 기반 모델 재선정 실관찰(계획=qwen3.6-flash, 실행=qwen3.8-max)
- 시나리오 A (E-L1→E-L2 하프루프, run_id=06731dc58b074f3a, 1230초): 수행 불가 능력(전화 걸기) + 수용 기준 3개 투입 → Verifier fail(미충족 2개) → Resolver "결핍 능력 판정: 스킬 0건, 에이전트 0건, 제작 요청 4건"(한국어 능력명 전부 Builder 큐 낙하) → 재계획 루프(env_recon/telephony_attempt/doc_writer 우회 시도) → Builder "제작 요청 디스패치: 스폰 0건, 게이트 거부 4건"(리스크 5 기본 거부 실동작) → 2차 검증 fail 시 "개선 증거 없음" 탈출 조건 작동(무한 루프 방지) → CodeReviewer 완료
- 영속 기록 검증: metadata.json의 plan에 `first_run_plan`/`acceptance_replan`/`capability_resolutions`(4건 needs_builder)/`builder_queue`(4건 pending)/`builder_dispatches`(4건 denied, 제작 대상 자동 분류 skill×3+mcp×1) 전부 기록 확인
- 실증 산출물: `_tmp_demo_b/`(시나리오 B), `_tmp_demo_a/`(시나리오 A) — 미추적 유지

---

## 5. 리스크

1. **무한 자기 수정 루프** — 자기 적용 실행에도 갭 D의 위임 가드(스폰 예산/깊이 제한)를 그대로 적용하고,
   자기 수정 전용 예산(1회 실행당 최대 N커밋)을 별도 상수로 추가
   ✅ 시공 완료(2026-08-19): 전반부=`_guard_check()` 위임 가드 재사용, 후반부=`MAX_COMMITS_PER_RUN=4` 커밋 예산(커밋 예산 시공 기록 참조)
2. **빌드 산출물 오염** — 자기 수정 대상에서 `dist/`, `release/`는 제외 경로로 명시 (path_security 재사용)
3. **재시작 중 데이터 손실** — E-3 실행 전 진행 중 잡 없음 확인(잡 레지스트리 비어 있을 때만 재시작 허용)
4. **bb 라이선스/코드 직접 차용 금지** — bb는 참고(교훈)만 하고 코드 복사는 하지 않음. DAON은 자체 아키텍처 유지
5. **폐루프 폭주 (E-Master Architecture 고유)** — 루프가 닫히면 외부 입력 없이도 돌아갈 수 있으므로,
   E-L1 분기에 "Builder 배정은 승인 게이트 통과 시에만" 조건을 기본값으로 강제. 자동 모드는 별도 토글
6. **Symphony SPEC 직접 구현 유혹** — Symphony는 Codex app-server 프로토콜에 종속된 설계.
   DAON은 뼈대(폴링/격리/화해/워크플로 계약)만 참고하고 에이전트 실행은 자체 런타임 유지

---

## 6. 인계 메모

- 조사 원문(미추적 파일, git add 금지 — 확인 후 삭제 가능):
  - bb: 워크스페이스 루트 `_tmp_bb_*.md` 6종
  - Symphony: `_tmp_symphony_readme.md`, `_tmp_symphony_spec.md`(2314줄), `_tmp_symphony_workflow.md`, `_tmp_symphony_elixir_readme.md`
  - Build Your Own X: `_tmp_byox_readme.md`
- E-Master Architecture(0A절)는 대표님 제안·승인(2026-08-18), 갭 E 자체도 같은 날 승인 → E-0a부터 순차 시공 시작.
- 아키텍처 주도권(대표님 지침): DAON Dynamic Harness = 상위 오케스트레이터. Symphony = 실행 계층에 이식할 패턴, bb = 자기 적용 계층에 이식할 교훈, BYOX = Builder의 설계 지식 소스. 외부 프로젝트를 상위로 넣지 않음.
- Symphony 뼈대 이식안의 세부 결정(작업 소스/승인 수준)은 대표님 최종 판단 대기.
- 앱 실사용 검증(연기분)이 E-0a 시공보다 우선일 수 있음.
- 정책 유지: 한국어 설명 / 빌드 전 push / 서버 무단 재시작 금지 / cp949 로그 규칙 / 최종 응답은 attempt_completion
