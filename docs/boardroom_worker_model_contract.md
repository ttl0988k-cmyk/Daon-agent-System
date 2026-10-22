# 회의실 워커 모델 격리 — 설계 계약 (Design Contract)

> **문서 종류**: 구현 계약(Contract). 빌·셜록·프라다가 **이 문서만 보고** 각각의 작업을 독립 수행할 수 있어야 한다.
> **작성**: 토니(기획) · **일자**: 2026-10-18 · **대상 코드**: `api/api/managers/herdr_manager.py`, `api/api/routes/boardroom_routes.py`
> **성격**: 코드 수정 금지. 본 문서는 명세이며, 구현은 작업장에서 진행한다.

---

## 0. 문제 정의 (왜 이 계약이 존재하는가)

현재 슬롯 07·08의 "모델 지정"은 **3단 파이프라인이 각각 다른 시스템에서 끊겨** 있다.

| 단 | 현재 동작 |故障 |
|---|---|---|
| ① 선택 | 회의실 UI가 슬롯별 모델 저장 → `boardroom_selection.json` | 정상 |
| ② 반영 | `HerdrManager.set_worker_model()` → provider 역조회 → **`_apply_codex_model_config()`** | ⚠️ `~/.codex/config.toml`을 **직접 덮어씀** + `setx` 호출 |
| ③ 복원 | selection JSON lazy 로드 | 정상 (단, ②가 전역 파일을 오염시켰으므로 "상주"가 "오염"으로 구현됨) |

진짜病因은 **②의 위치**다. 개인 앱 설정을 쓰기 대상(mutate)으로 삼았기 때문에, 모델이 "상주"되는 것과 대표님 Codex 앱이 "망가지기"는 같은 동작이었다.

또한 슬롯 08은 **LiteLLM(:4000) 경유**라 Anthropic `tool_use` ↔ OpenAI `tool_calls` 변환 계층이 존재하고, 실측으로 `400 No tool output found for tool call`이 터진다. harness-worker 쪽은 이미 이 문제를 네이티브 직결로 해결해 두었다. **해결책을 복제하지 말고, 이미 통과한 것을 이식한다.**

---

## 1. Provider 3종 세트 계약

### 1.1 규칙

- 슬롯 설정에 **모델명 단독을 허용하지 않는다.** 항상 `(provider, model)` 2튜플로 저장·전달한다.
- 값의 출처는 **이 문서 표 1-1**이 유일한 진실(single source of truth)이다. `worker.py::PROVIDERS`의 복사본을 만들지 않는다.
- `wire_api` 는 Codex 전용. `anthropic_base_url` 이 **빈 값이면 그 provider는 슬롯 08 후보에서 제외**한다(Contract 1.3).

### 1.1 표 — 실제 값 (`worker.py::PROVIDERS` L127-227 / `data/custom_providers.json` 실측)

| name | base_url (OpenAI 호환) | wire_api (07) | anthropic_base_url (08) | env_key | codex_model | claude_model | 필수 헤더 | 슬롯08 허용 |
|---|---|---|---|---|---|---|---|---|
| `opencode-go` | `https://opencode.ai/zen/go/v1` | `responses` | `https://opencode.ai/zen/go` | `DAON_HARNESS_OPENCODE_GO_KEY` | `muse-spark-1.3-contributor` | `deepseek-v4.1-flash` | `x-opencode-session: daon-harness-worker` | ✅ **기본값** |
| `openrouter` | `https://openrouter.ai/api/v1` | `responses` | *(없음)* | `DAON_HARNESS_OPENROUTER_KEY` (+ 하위호환 `OPENROUTER_API_KEY`) | `z-ai/glm-5.3-flash` | `deepseek/deepseek-v4.1-flash` | *(없음)* | ⛔ LiteLLM 경유 |
| `minimax` | `https://api.minimax.io/v1` | `responses` | *(없음)* | `DAON_HARNESS_MINIMAX_KEY` | `MiniMax-M3` | *(없음 → codex_model 폴백)* | *(없음)* | ⛔ LiteLLM 경유 |

> **인증 방식 주의 (실측)**: `/v1/messages`(Anthropic)는 `x-api-key`, `/v1/responses`(OpenAI)는 `Authorization: Bearer`. Claude Code는 `ANTHROPIC_API_KEY`를 `x-api-key`로 보낸다. **`ANTHROPIC_AUTH_TOKEN`은 Bearer로 보내 401을 유발하므로 반드시 `pop` 한다.**
> `openrouter.context_window = 1048576`(스펙 1,310,720이 아니라 **실제 서빙 한계**). 하드코딩 `128000` 금지 — `herdr_manager.py:1020`의 현행 값이 이 버그의 살아있는 증거다.

### 1.2 슬롯별 허용 provider 집합

```
슬롯 07 (worker-codex) : { opencode-go, openrouter, minimax }        (chatgpt/OAuth 제외)
슬롯 08 (worker-claude) : { opencode-go }                            (anthropic_base_url 존재 조건)
```

`chatgpt`(OAuth 구독)와 `omniroute`는 **회의실 슬롯 후보에서 제외**한다. 근거:
- `chatgpt` — `claude_supported: False`. 게다가 임시 `CODEX_HOME`에 `auth.json`을 복사해야 하는데, 회의실은 "대표님 구독 자격증명을 워커가 쓰게" 하는 설계 판단이 아직 대표님 승인 없음.
- `omniroute` — `claude_supported: False`, 업스트림 410/500 변동. 주석에 이미 "Codex 전용"으로 명시.

### 1.3 Contract 규칙

- **C-1.2** 슬롯 08에 `anthropic_base_url` 이 없는 provider가 지정되면 **거부**(400)하지 **않고** `LiteLLM 금지` 오류로 명시적 실패한다. 자동 폴백 금지.
- **C-1.3** 헤더는 provider 스펙이 **선언적으로 제공**한다. 코드에 `if prov_key in ("opencode-go","opencode-zen")` 식 하드코딩 금지.

### 1.4 ⚠️ 계약 위반 사항 1건 — 빌 확인 필수

`data/custom_providers.json`의 `providers.opencode-go.models` **8종 목록에 `muse-spark-1.3-contributor` 가 없다.**

```
현재 목록: deepseek-v4.1-flash, deepseek-v4-flash, deepseek-v4-pro, glm-5.3-flash,
          gpt-6-luna, gpt-5.6-luna, qwen3.8-max, mimo-v2.6-flash
```

현행 `set_worker_model()`은 `custom_providers.json` 모델 목록에서만 provider를 역조회하므로, **대표님이 기본값 muse를 고르면 `prov_key=None`으로 떨어져 ChatGPT OAuth 경로(모델만 config.toml에 쓰는 구 분기)로 새어간다.** 이것은 2장 폴백 규칙(R-2.1)의 정면 위반이므로 구현 착수 전에 해결해야 한다.

**선택지 2개 (빌 판단, 셜록 검증)**
- **A안(권장)**: 폴백 모델을 **JSON 카탈로그에 의존하지 않는 계층**(§2 폴백 테이블 직접 참조)에서 결정한다. 카탈로그가 갱신되지 않아도 기본값이 깨지지 않는다.
- **B안**: `muse-spark-1.3-contributor` 를 `custom_providers.json` 에 명시 등록한다. 단, 이때 `claude_model` 이 `deepseek-v4.1-flash` 로 **분리 고정**되어야 한다(뮤즈가 Claude로 새면 안 됨).

---

## 2. 슬롯 ↔ 워커 ↔ 모델 매핑

### 2.1 매핑표

| 슬롯 | 라벨 | 워커 키 | kind | 기본 provider | **지정 시** | **미지정(기본) 시** |
|---|---|---|---|---|---|---|
| `07` | 코덱스 워커 | `worker-codex` | `codex` | `opencode-go` | `(provider, model)` 그대로 | `muse-spark-1.3-contributor` (opencode-go) |
| `08` | 클로드 워커 | `worker-claude` | `claude` | `opencode-go` | `(provider, model)` 그대로 | `deepseek-v4.1-flash` (opencode-go) |

### 2.2 "UI가 값을 비웠을 때 서버가 무엇으로 채우는가" — 정확한 절차

```
1. POST /api/boardroom/selection {slots:[...], models:{"07":"", "08":""}}
2. normalize_slot_models()  ← 빈 문자열은 **현재 코드에서 버려진다**(키 자체가 사라짐)
3. save_selection() → _SELECTION["models"] = {}  ← 07/08 항목 자체가 사라짐
4. 재기동 후 load → models={}
5. dispatch 시 slot_model = _resolve_slot_model({}, "07", "worker-codex") = ""
6. set_worker_model("worker-codex", "") ← 빈 문자자로 호출됨
```

**이 경로는 계약 위반이다.** "비운 것"과 "아무것도 안 한 것"이 구분되지 않으며, 결과적으로 ②의 오염 동작(전역 config 덮어쓰기)이 기본값 경로로 실행된다.

**계약 동작(구현해야 할 것)**

```
- 저장 계층: 빈 값도 "의도된 기본"으로 **유지**한다.
  models["07"] = ""  ← 키를 살려두고 빈 문자열로 저장 (버리지 않는다)
- 폴백 계층: 빈 값/미지정 → §2.1 기본 컬럼으로 해석
  07 → ("opencode-go", "muse-spark-1.3-contributor")
  08 → ("opencode-go", "deepseek-v4.1-flash")
- UI 계층: 빈 셀은 "기본값(모델명)" 라벨을 표시한다.
           사용자가 의도적으로 비웠다면 그 표시가 곧 정답이다.
```

**C-2.2** 저장 형식에서 **모델명을 키로 쓰지 않는다.** 지금 `models = {"07": "deepseek-v4.1-flash"}`처럼 provider가 추론에 의존하는 구조는 §1.4 버그의 근원이다. 저장 포맷은 §3에서 정의한다.

### 2.3 현행 대비 제거 대상 (빌 작업 목록)

`herdr_manager.py`에서 **아래를 삭제하거나 우회**해야 한다. 삭제 근거는 모두 §5 불변 조건 위반이다.

| 위치 | 현행 | 처리 |
|---|---|---|
| L972-1037 `_apply_codex_model_config()` | `~/.codex/config.toml` read+write | **삭제**. 대체: `worker.write_codex_home()` 결과를 슬롯 전용 CODEX_HOME 에 쓰고 `CODEX_HOME` env 로 주입 |
| L1002 | `subprocess.run(["setx", ...])` | **삭제** |
| L149-172 `_detect_current_codex_model()` | `~/.codex/config.toml` 파싱 | **읽기 전용 유지 가능**(표시 목적). 단 "실사용 모델"이 아니라 **"개인 설정 표시"** 라벨로 명명 변경 |
| L175-176 `_detect_current_claude_model()` | 문자열 하드코딩 | 슬롯 상태(`_SELECTION`) 기반으로 변경 |
| L178-201 `ensure_litellm_gateway()` | LiteLLM 기동 | **슬롯 08 경로에서 호출 금지**(개체 자체를 남겨도 되나 호출자 0개가 되어야 함) |
| L456-459, L923-931 | `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` | **네이티브 `anthropic_base_url` 로 교체** |
| L1020 | `model_context_window = 128000` 하드코딩 | provider 스펙 `context_window`(openrouter=1048576) 참조 |

---

## 3. selection JSON 스키마

### 3.1 현행 구조

```json
{ "slots": ["02","07"], "models": {"07": "deepseek-v4.1-flash"}, "saved": true }
```

파일 경로: `api.config.STATE_DIR / "boardroom_selection.json"` → 폴백 `%LOCALAPPDATA%\DAON Agent System\data\` → 폴백 `<프로젝트루트>/data/`.

### 3.2 확장안 — **기존 키 하위호환 유지**

```jsonc
{
  "slots": ["02", "07", "08"],           // 기존. 불변
  "models": {                            // 기존. 불변 — 읽기 경로 그대로 동작해야 한다
    "07": "muse-spark-1.3-contributor",  // 기존 형식(모델명 단독). LEGACY 읽기 지원
    "08": "deepseek-v4.1-flash"
  },
  "saved": true,                         // 기존. 불변

  "worker_models": {                     // ★ 신규. 슬롯 07/08 전용
    "07": {
      "provider": "opencode-go",
      "model": "muse-spark-1.3-contributor",
      "explicit": true,                  // false = UI에서 비웠음 → §2.1 기본값
      "updated_at": 1760000000.0
    },
    "08": {
      "provider": "opencode-go",
      "model": "deepseek-v4.1-flash",
      "explicit": false,
      "updated_at": 1760000000.0
    }
  },

  "worker_status": {                     // ★ 신규. 조용한 실패 금지(§4)의 영속 기록
    "07": { "state": "ready",     "resolved_model": "muse-spark-1.3-contributor", "provider": "opencode-go", "error": null, "checked_at": 1760000000.0 },
    "08": { "state": "running",   "resolved_model": "deepseek-v4.1-flash",        "provider": "opencode-go", "error": null, "checked_at": 1760000000.0 }
  }
}
```

### 3.3 마이그레이션 규칙

- **M-3.1** 읽기 시 `worker_models` 가 없으면 `models["07"|"08"]` 로부터 **합성**한다. 이때 `explicit=true`(사용자가 명시적으로 골랐다고 간주). provider 는 `resolve_provider()` 로 추론하되, §1.4 때문에 **추론 실패 시 명시 오류**를 낸다(무음 폴백 금지).
- **M-3.2** `models` 의 슬롯 07/08 항목은 쓰기 시점에 `worker_models` 로 승격하고 **삭제하지 않는다**(구버전 프론트가仍在 읽을 수 있음).
- **M-3.3** 빈 문자열은 `explicit:false` 로 표현한다. §2.2에서 키를 버리지 않는 것에 주의 — 버리는건 `normalize_slot_models()` **dict 반환 단계**에서 이미 제거된다. 이 함수를 **조건부 수정**(07/08 슬롯에 한해 빈 값 보존)해야 한다.

---

## 4. 폴백·오류 규칙 (조용한 실패 금지)

> **원칙: 어떤 실패든 슬롯 상태(`worker_status`)에 `state` + `error`로 기록하고, UI에 뱃지로 노출한다. `return {ok:true}` 인 채로 아무 일도 안 일어나는 상태는 금지.**

### 4.1 상태 머신

| state | 의미 | UI |
|---|---|---|
| `ready` | 지시 모델로 기동 성공, 응답 확인 | 🟢 `적용됨` |
| `running` | 프로세스 기동됨, 첫 응답 미확인 | 🟡 `대기` |
| `degraded` | 폴백 모델로 동작 중 (지시 모델 아님) | 🟠 `폴백됨: <실사용>` |
| `failed` | 기동 실패 또는 요청 오류 | 🔴 `실패: <error>` |
| `blocked` | 승인·트러스트 화면 대기 | 🟣 `승인 대기` |

### 4.2 규칙표

| 상황 | 동작 | state | 금지 |
|---|---|---|---|
| **지정 모델이 provider 카탈로그에 없음** | provider 스펙 표(§1.1)의 기본 model로 폴백 + `error="model_not_in_catalog:<id>"` 기록 | `degraded` | ❌ `prov_key=None`으로 흘러 OAuth 경로에 진입. ❌ 조용히 다른 provider 선택 |
| **provider에 API 키가 없음** | 슬롯 08이면 → `anthropic_base_url` 있는 다른 provider가 있으면 그곳의 기본 모델로 폴백, 없다면 **기동 중단** + `error="no_api_key:<provider>"` | `degraded` / `failed` | ❌ 키를 `setx` 로 전역 등록. ❌ 빈 키로 기동 후 401을 슬롯 밖에서 삼킴 |
| **LiteLLM 전용 provider가 슬롯 08에 지정됨** | 기동 **중단** + `error="claude_requires_anthropic_native:<provider>"` | `failed` | ❌ `ensure_litellm_gateway()` 호출. ❌ 자동 폴백 |
| **워커 기동 실패(프로세스 exit)** | 기존 워커 유지(이전 프로세스 파괴 금지), 재시도 1회, 실패 시 `error="spawn_failed rc=<n>"` | `failed` | ❌ pane 상태를 클리어한 채 빈 채팅으로 남김 |
| **첫 응답 타임아웃** | `state=blocked`(UI 승인 대기 가능) — 400이 아니라면 | `blocked` | ❌ 무한 대기 |
| **tool_use 400 (`No tool output found`)** | 슬롯 08에서 **1회 자동 재기동 후 재현** → 여전히 400이면 `error="tool_use_400"` + `failed` | `failed` | ❌ 원인을 UI에 숨김. ❌ LiteLLM로 되돌림 |
| **선택값 JSON 손상** | 손상 사실을 `error`로 기록, 빈 선택으로 시작(워크플로우 차단 금지) | `ready`(기본값) + 경고 로그 | ❌ 조용히 기본값으로 덮어쓰고 사용자에게 숨김 |

### 4.3 폴백의 방향성

폴백은 **항상 사용자가 지정한 provider 계열 안에서** 일어난다(모델 → 기본 모델). **provider 자체가 바뀌면 안 된다.** 프로바이더 간 이동은 폴백이 아니라 **오류**다. 서로 다른 과금·품질·개인정보 경계이므로 조용히 바뀌면 안 된다.

---

## 5. 불변 조건 (Invariants)

> 이 7개는 **테스트가 아니라 계약**이다. 하나라도 깨지면 구현은 실패로 판정한다.

| ID | 불변 조건 | 위반 시 대표님에게 발생하는 피해 |
|---|---|---|
| **I-1** | `~/.codex/config.toml` 은 작업 전후 **byte-identical**(sha256 동일). 회의실 코드 경로는 이 파일에 **쓰기 IO를 하지 않는다**. | 대표님 개인 Codex 앱이 회의실에서 고른 모델로 바뀌고, 되돌리려면 대표님이 직접 손댈 것 |
| **I-2** | 어느 경로로도 **`setx` 를 호출하지 않는다.** env 키 주입은 **자식 프로세스 env dict** 에만 존재한다. | API 키가 사용자 레지스트리에 평생 남음(오염·유출) |
| **I-3** | 회의실 워커는 **harness-worker 플러그인 설정과 독립**이다. 공유하는 것은 §1.1 표(스펙 상수)와 코드 **복제본이 아니라 공용 모듈 import** 뿐. | 회의실을 만지다 플러그인 워커가 깨지거나 그 반대 |
| **I-4** | 슬롯 07은 전용 `CODEX_HOME` 아래에서만 동작한다. `CODEX_HOME` env 가 자식 프로세스에 **반드시 주입**된다. | 전역 `config.toml` 이 이겨서 "지정한 모델로 안 바뀐다"(현행 증상) |
| **I-5** | 슬롯 08은 **Anthropic 네이티브 엔드포인트**에만 붙는다. `ANTHROPIC_BASE_URL` 값이 `127.0.0.1:4000` 인 상태로 기동되면 **게이트 ④ 즉시 실패**. | tool 왕복 400 → 워커가 도중 사망(현행 증상) |
| **I-6** | `ANTHROPIC_AUTH_TOKEN` 은 슬롯 08 환경에서 **반드시 제거**된다(→ `pop`). | Bearer로 전송되어 401 |
| **I-7** | 서버 재기동 후 슬롯 07/08의 provider·model 이 **저장값 그대로 복원**된다. 복원 실패 시 기본값이 아니라 **오류로 기록**된다. | "저장했는데 다음에 보니까 바뀐 것 같기도 하고 아닌 것 같기도" — 최악의 UX |

### 5.1 격리 경로 규약

```
WORKER_ROOT      = C:\daon\_worker_runs                    (worker.py L88, 현행과 동일)
CODEX_HOME_ROOT  = WORKER_ROOT\_codex_homes                (worker.py L91)
  └─ <slot>_<provider>\                                    ← 슬롯별 격리(공용 아님)
       ├─ config.toml    ← worker.write_codex_home() 산출
       └─ auth.json      ← ~/.codex/auth.json 의 **읽기 전용 복사**(chatgpt 슬롯 한정)
```

- **I-8** 슬롯 07·08의 `CODEX_HOME` 은 **슬롯별로 다른 디렉터리**다. 두 슬롯이 같은 홈을 공유하면 서로의 모델이 뒤집어쓴다.
- `~/.codex` 는 **읽기만** 허용하며, 읽기는 §4.2 chatgpt 슬롯 승인 시 `auth.json` 복사뿐이다.

---

## 6. 검증 게이트 4종 — 수치 판정 기준

> 셜록 담당. **4개 모두 PASS 여야 작업 완료로 인정한다.** 3/4 PASS는 실패다.

### 게이트 ① — 지정 모델 = 실제 프로세스/요청 payload 일치

| 항목 | 판정 |
|---|---|
| 방법 | 슬롯 07 → 지정 모델로 기동 → 기동된 codex 프로세스의 **커맨드라인 + 주입된 `CODEX_HOME/config.toml`** 스캔. 슬롯 08 → Anthropic 요청 캡처에서 `model` 필드 + `base_url` 확인. |
| PASS | **2/2 슬롯**, 지정 문자열이 **완전일치**(부분일치·치환·기본값 역참조 0건) |
| FAIL | 1개라도 불일치. 특히 "지정한 게 무시되고 기본값으로 돈다"가 1건이면 FAIL |
| 증거 | 프로세스 커맨드라인 덤프 + config.toml 전문 + 캡처된 요청 JSON 3종 |

### 게이트 ② — 재기동 후 복원률

| 항목 | 판정 |
|---|---|
| 방법 | 슬롯 07/08에 각각 **서로 다른** provider+model 지정 → 저장 → 서버 프로세스 완전 종료 → 재기동 → `GET /api/boardroom/selection` 및 실제 워커 기동 확인 |
| PASS | 복원률 **100% (2/2 슬롯)**. 기본값으로 내려가는 경우 0건 |
| FAIL | 1개라도 폴백·기본값 강등 |
| 추가 | 복원 직후 `worker_status[slot].state` 가 `ready`/`running` 이고 `explicit=true` 로 보존 |
| 증거 | 저장 전/후 JSON 덤프 2종 + 기동 후 상태 스냅샷 |

### 게이트 ③ — 개인 설정 무결성 (★ 킬러 게이트)

| 항목 | 판정 |
|---|---|
| 방법 | 작업 **직전** `~/.codex/config.toml` sha256 · mtime · 크기 기록 → 전체 시나리오(07 기동·스왑·08 기동·재기동 3회) 수행 → **직후** 재측정 |
| PASS ③-a | sha256 **동일** (mtime 변경만은 허용하되 **바이트 변경은 0건**) |
| PASS ③-b | `reg query HKCU\Environment` 에 `*_API_KEY` **0건**. 신규 키가 하나라도 남으면 FAIL |
| PASS ③-c | `~/.codex/` 내 **신규 파일 생성 0건**, **기존 파일 mtime 변경 0건** |
| PASS ③-d | `setx` 호출이 코드에 **잔존하지 않음**(grep 결과 0건) |
| 증거 | 전/후 sha256 출력, `reg query` 전문, `dir ~/.codex` 전후 diff |

### 게이트 ④ — Claude tool_use 왕복 안정성

| 항목 | 판정 |
|---|---|
| 방법 | 슬롯 08을 파일 읽기·쓰기·검색 등 **도구 20회 이상** 연속 호출하는 시나리오로 구동 |
| PASS | `No tool output found for tool call` **0건**. 그 외 4xx 도 **0건** |
| FAIL | 400 발생 1건, 또는 기동 실패 후 성공으로 **재시도 은폐**된 경우 |
| 부가 | `ANTHROPIC_BASE_URL` 이 `127.0.0.1:4000` 이 **아님**을 기동 시점에 캡처로 증명 |
| 증거 | 20회 이상 호출 로그 + 요청/응답 캡처 3건 이상 + 상태 전이 기록 |

---

## 7. 작업 분담

| 담당 | 산출물 | 완료 판정 |
|---|---|---|
| **빌** | ① `herdr_manager.py` — `_apply_codex_model_config()` 제거, 슬롯 전용 CODEX_HOME 경로, §1.1 표 기반 provider 해석, §2.2 폴백 계층, §4.2 상태 기록. ② `boardroom_routes.py` — `worker_models`/`worker_status` 스키마 + 마이그레이션(M-3.1~3.3). ③ §1.4 A안/B안 결정 및 반영. | 게이트 ①② 자체 구현, ③④는 셜록에 인계 |
| **셜록** | 게이트 4종 검증 스크립트 (재시동 자동화 + sha256 전후 비교 + reg query + 20회 tool_use 루프). 게이트는 **빌의 시나리오를 그대로 재현**해야 한다. | 4/4 PASS 리포트 |
| **프라다** | 슬롯 카드 상태 뱃지 5종(§4.1 `ready/running/degraded/failed/blocked`) + 폴백 시 실제 사용 모델 노출 + I-1 "개인 설정 미변경" 안심 배지 | 대표님이 1초에 "진짜 바뀐 건가"를 판단 |

**의존 순서**: 빌 §1.4 결정 → 빌 ① ② → 셜록 게이트 → 프라다 뱃지(상태 필드가 확정된 뒤).

---

## 8. 빌 구현 시 반드시 지켜야 할 계약 — Top 3

> 나머지 세부사항은 위 본문을 읽으면 되지만, **이 3개는 하나라도 무시하면 I-1 불변 조건이 깨집니다.**

### ⛔ 1. `~/.codex/config.toml` 을 **쓰기 대상에서 제거**하라

- 금지: `_apply_codex_model_config()` 계열의 `read_text → write_text`. `set_worker_model()`이 슬롯 경로에서 **어떤 형태로도 이 파일을 변경해서는 안 된다.**
- 대체: 슬롯 전용 `CODEX_HOME` 디렉터리에 config.toml 을 쓰고, 자식 프로세스 `env["CODEX_HOME"]` 로 주입. (I-4)
- 읽기는 허용 — 단 **표시 목적**이며 "실사용 모델"이라는 라벨을 금지한다.

### ⛔ 2. 슬롯 08은 LiteLLM 경로를 **완전히 제거**하라

- 금지: `ensure_litellm_gateway()` 호출, `ANTHROPIC_BASE_URL=http://127.0.0.1:4000`, `claude-*` alias 인자, `ANTHROPIC_AUTH_TOKEN` 주입.
- 대체: provider 스펙의 `anthropic_base_url` + `ANTHROPIC_API_KEY`(→ `x-api-key`) + `ANTHROPIC_CUSTOM_HEADERS`(→ `x-opencode-session`) + `ANTHROPIC_MODEL`. `--model` 은 **실모델명**(alias 아님). (I-5, I-6)
- 잔여 alias(`claude_alias`) 필드는 LiteLLM 사문화와 함께 **삭제 대상**이다.

### ⛔ 3. 모든 실패를 슬롯 상태에 **기록**하라

- 금지: `ok:true` 반환 후 실제로는 기본 모델로 도는 경우, 빈 폴백, 조용한 catch 후 무응답.
- 필수: 실패 지점마다 `worker_status[slot] = {state, resolved_model, provider, error, checked_at}` 갱신 → UI 뱃지 → (가능하면) 로그.
- 판정선: **"사용자가 지정한 모델로 안 돌았는데 화면은 정상이라고 말하는 상태"** 는 어떤 경우에도 금지.

---

> **문서 끝.** 구현 중 본문과 코드가 충돌하면 **본문이 우선**한다. 본문이 틀렸다면 문서를 먼저 고친다 — 계약을 코드 뒤에 두지 않는다.