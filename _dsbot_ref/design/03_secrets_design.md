# 시크릿 카드(Secret Card) UX — ds-bot → 다온 이식 상세 설계서

- 작성일: 2026-10-12
- 성격: **설계 문서 전용** (코드 수정 없음)
- 참조 원본: `_dsbot_ref/secrets.js`, `_dsbot_ref/constants.js`
- 이식 대상: `api/api/plugin_credentials.py`, `api/api/plugin_gateway.py`, `api/api/managers/model_manager.py`, `data/custom_providers.json`, `hermes-agent/tools/env_passthrough.py`, `api/api/agent_runner.py`, `api/api/streaming.py`, `api/api/routes/plugin_routes.py`, `static/modules/plugins.js`, `static/v2/js/app.js`
- 인용 규칙: 실제 파일에서 확인한 라인/구조만 인용한다. 확인되지 않은 값은 "미확인"으로 명시한다.

---

## 0. 요약 (TL;DR)

ds-bot의 시크릿 카드는 세 가지 아이디어로 요약된다.

1. **값은 봇/모델에게 오지 않는다.** 사용자가 카드에 입력한 값은 `secrets.json`(mode 600)에만 있고, 봇이 보는 것은 `[secret:<NAME>]` 플레이스홀더뿐이다.
2. **봇은 셸 환경변수로만 값을 쓴다.** `$DSH_SECRET_<NAME>` 형태로 실행 순간에만 주입된다.
3. **모든 출구(도구결과 저장·모델요청 전송)에서 값을 치환해 지운다.** (값 기반 redactor)

다온은 이미 (a) **패턴 기반 마스킹**(`hermes-agent/agent/redact.py`), (b) **세션 스코프 ContextVar 기반 자격증명 주입**(`tools/env_passthrough.py`), (c) **플러그인 자격증명 저장소 + pending 요청**(`plugin_credentials.py`, `plugin_gateway.py`)를 갖고 있다. **빠진 조각은 딱 하나 — "에이전트가 자기 채팅에서 값을 요청하는 카드"와 "등록된 값 자체를 값 기반으로 지우는 redactor"** 이다. 본 설계는 그 두 조각을 기존 인프라 위에 얹는 방법을 정의한다.

---

## 1. ds-bot `secrets.js` 전체 흐름 분석 (원본)

### 1.1 설계 철학 (파일 헤더, secrets.js:1-4)
> "A value lives only in `secrets.json` (mode 600) and in this process; Bots use it as `$DSH_SECRET_<NAME>` in their shell, **tool results lose it before they are saved**, and **every model request has it replaced by `[secret:<NAME>]` before it leaves**."

즉 값이 살아있는 곳은 **파일(600) + 프로세스 메모리** 두 곳뿐이고, 값이 빠져나가는 세 출구(도구결과 저장 / 모델요청 / 셸)는 각각 통제된다.

### 1.2 상수·이름 규칙 (secrets.js:11-28)
| 상수 | 값(확인분) | 의미 |
|---|---|---|
| `SECRET_NAME` | `secrets.js:11` — **복사본에서 `/^[A-Z...3}$/` 로 손상됨(미확인)** | 이름 검증. 코드·에러문구(287)로 보아 "대문자/숫자/밑줄, 문자로 시작" = 사실상 `/^[A-Z][A-Z0-9_]*$/` |
| `ENV_PREFIX` | `'DSH_SECRET_'` (12) | 환경변수 접두 |
| `MIN_VALUE` | `4` (16) | 최소 길이 |
| `WHOLE_VALUE` | `8` (17) | 이 미만 값은 "단독 등장"일 때만 치환 |
| `MAX_VALUE` | `16384` (18) | 최대 길이 |
| `MAX_REQUESTS` | `20` (19) | 봇별 요청 큐 상한 |
| `USED_SAVE_MS` | `5000` (20) | `lastUsedAt` 지연 저장(디바운스) |

- `envName(name) = 'DSH_SECRET_' + name` (22)
- `canUse(secret, botId)` = `scope==='all'` 또는 `scope` 배열에 `botId` 포함 (23-24) — **권한 판정의 단일 함수**
- `normalizeName(raw)` = trim → `$`/`env:`/`DSH_SECRET_` 접두 제거 → 공백·하이픈을 `_`로 → 대문자 (26-28)

### 1.3 저장 포맷 — `secrets.json`
- 경로: `join(home, 'secrets.json')` (82)
- 스키마(138, 84): `{ "version": 1, "secrets": [ entry, ... ] }`, entry =
  ```
  { name, value, purpose, scope:'all'|string[], requestedBy:string|null,
    createdAt, updatedAt, lastUsedAt? }
  ```
- 로드 시 검증: `SECRET_NAME.test(name)` + `typeof value==='string'` 아니면 skip (114-115); `scope`는 `'all'` 아니면 문자열 배열 필터 (119)
- **원자적 쓰기**: temp 파일(`<path>.<pid>.<uuid>.tmp`, mode 600)에 쓰고 `rename` (135-153); read 후 `chmod 600` (126)
- 파싱 에러는 파일 내용을 로그로 인용하지 않고 `error.code`/`error.name`만 남김 (97-98, 129) — **로그 유출 차단**

### 1.4 플레이스홀더 치환 — `redactor(entries)` / `redactDeep` (32-78)
- **값 기반**이다(패턴이 아니라 "등록된 값 목록" 기반). `entries`에서 길이 `< MIN_VALUE` 제외.
- 치환 라벨 = `` `[secret:${name}]` `` (36).
- 값이 **JSON 이스케이프 형태**로도 등장할 수 있어 `JSON.stringify(value).slice(1,-1)` 변형을 별 쌍으로 등록 (39-40).
- **긴 값 우선 정렬**(42) — 다른 값을 품은 값도 통째 치환.
- **짧은 값(<8)** 은 앞뒤가 `\p{L}\p{N}` 아닐 때만: lookbehind/lookahead로 "단독 등장" 한정 (48). → PIN/짧은 비밀번호가 일반 단어를 훼손하지 않게.
- 단일 패스 대체(49-52): 이미 쓴 라벨이 다시 매칭되지 않게.
- `redactDeep`: 문자열/배열/plain object 재귀, **변화 없으면 동일 참조 반환**(56-78) → 호출측이 "바뀌었는지" 싸게 판단.

### 1.5 값 주입 방식 — 셸 환경변수 (468-517)
- `resolveEnv(execution)`: bash 도구 실행 시 `canUse`인 시크릿을 `{ DSH_SECRET_NAME: value }`로 만들어 반환 (468-484). 커맨드 문자열에 변수명이 포함되면 `lastUsedAt` 갱신 + 디바운스 저장 (477-482, 155-164).
- `attachShellEnv`: `sub.shellEnv.register({ name:'ds-bot-secrets', variables:{NAME:{description}}, resolve: resolveEnv })` (488-504). **변수 "이름/설명"만 미리 선언하고 값은 실행 시점 resolve**.

### 1.6 권한/선택 & 카드 생명주기
- 요청 상태는 `state.secretRequests[botId] = request[]` (166). request =
  `{ id, callId?, sessionId, name, purpose(≤300), kind:'fill'|'allow', status:'pending', askedAt }` (396-405).
- `request_secret` 툴 (366-413):
  - 이미 `canUse`면 "이미 저장됨, `$DSH_SECRET_...`로 써라, 값은 못 본다" 반환 (383).
  - **그룹채팅 금지** — 자기 채팅에서만 (384-385).
  - `kind`: 기존 시크릿 있으면 `'allow'`(권한만 부여), 없으면 `'fill'`(값 입력) (402).
  - 카드 표시 후 `exec.concludeTurn()` (408) — **카드 표시가 턴을 끝낸다.**
- 엔드포인트 (258-340):
  | 엔드포인트 | 역할 |
  |---|---|
  | `secret-set` | 카드(requestId) 또는 Settings(name)에서 값 저장. 카드 경로는 scope=bot a또는 all, 기존+allow면 기존 scope에 bot 추가 |
  | `secret-allow` | 저장된 시크릿에 현재 bot만 scope 추가(kind=allow) |
  | `secret-cancel` | 취소 + 봇에 "다시 묻지 마라" 통지 (315-321) |
  | `secret-update` | scope만 변경 |
  | `secret-delete` | 삭제 |
- `reconcile(name, notes, except)` (206-219): 이름 변경 시 대기중 카드들을 **자동 해소(saved) 또는 kind 재판정(allow/fill)**.
- `secretView` (247-256): name/purpose/scope/requestedBy/createdAt/updatedAt/lastUsedAt **— value 필드 없음**. 목록 API도 값 미포함.
- 봇 삭제 시 `forgetBotSecrets` (348-362): scope에서 제거 + 카드 삭제.

### 1.7 숨김 규칙 — 3개 choke point
1. `tools/execute` 훅 (436-449): 도구 결과를 **저장·렌더 직전** `redactDeep`로 재구성 → 모델 content, 저장된 `tool/result`, 카드 표시 모두 redacted 값에서 파생.
2. `llm/stream` 훅 (457-466): 요청이 나가기 전 `messages`/`system` redact. `WeakSet`로 재귀 방지(456, 463).
3. 로그/에러: 값 인용 금지(97-98).

### 1.8 도구 표면
- `request_secret`(366), `list_secrets`(415, 값 미노출). `constants.js:44-49`의 `TEAM_TOOLS`에 두 도구가 등록됨.
- 최종 export(529): `secretsView / secretValue / secretEndpoint / forgetBotSecrets / loadSecrets / redactSecrets`.

---

## 2. 다온 현행 자격증명 처리 실태 (실제 구조·라인 인용)

### 2.1 저장소 — `api/api/plugin_credentials.py`
- 저장 위치: PyInstaller면 `%LOCALAPPDATA%/Daon/credentials.json`, 개발이면 `<repo>/data/credentials.json` (35-47).
- 파일 스키마(67, 111-114, 197-201):
  ```
  { "plugins": { "<plugin_name>": { "<KEY>": "<value>" } },
    "pending": [ { "plugin": "...", "key": "...", "session_id": "..." } ] }
  ```
- 공개 API(값 보호 원칙 명시, 90-91):
  - `get_credential(plugin, key)` — **내부 전용**, 값 반환 (94-99)
  - `set_credential` / `delete_credential` / `delete_plugin_credentials` (102-146)
  - `get_credential_status(...) -> Dict[str,bool]` — **설정 여부만** (149-164)
  - `is_plugin_authenticated` (167-172)
- Pending(값 미기록): `add_pending(plugin,key,session_id)` (180-203), `list_pending` (206-210), `resolve_pending`/`_remove_pending` (213-228), `clear_all_pending` (231-235).
- 원자적 쓰기: `path.with_suffix('.tmp')` 후 `replace` (70-78); Windows 포함 소유자 전용 권한 best-effort (79-84).
- **관찰**: 다온 저장소는 "plugin 단위 key→value"이고 **scope/요청자/purpose/lastUsedAt 메타데이터가 없다**. → ds-bot의 카드 상태머신을 이식하려면 메타데이터 확장이 필요.

### 2.2 프로바이더 키 저장 — `data/custom_providers.json` + `model_manager.py`
- 파일 구조(실파일): `{ "presets": { name: {base_url,label,models?} }, "providers": { name: {api_key, base_url, models, label} } }`.
  - 실측 예: `providers."opencode-go".api_key` = `"oc_sk_...iLSq"`, `providers."opencode-zen".api_key` = `"st_6f2...67c9"`, `providers."omniroute"/"minimax"/"openrouter".api_key` = `"***"` (custom_providers.json:161,209,247,278,305). **일부 값은 이미 축약/플레이스홀더 형태로 저장돼 있음(실파일 확인분).**
- 로드/세이브: `_load_custom_providers` (26-63), `_save_custom_providers` (65-83); 경로 `STATE_DIR/custom_providers.json` (17-23).
- **API 응답 마스킹**: `get_custom_providers()`가 `len>8`이면 `key[:4] + '•'*(len-8) + key[-4:]`, 아니면 전부 `•` (231-244).
- 키 보존 로직: 들어온 `api_key`가 비었거나 `•`/`*` 포함이면 기존 키 유지 (271-308).

### 2.3 라우트 & 웹UI (pending 흐름)
- `plugin_routes.py`:
  - `GET /api/plugins/credentials/pending` → `{ok, pending:[{plugin,key,session_id,created_at}]}` (169-179)
  - `GET /api/plugins/{name}/credentials` → `{ok,name,authenticated,secrets:[{name,description,set}]}` (182-201)
  - `POST /api/plugins/{name}/credentials` (body `{key,value}`) → 저장, 값 미반환 (204-235)
  - `POST .../credentials/remove` (238-257)
- 라우터 매핑: `routes/__init__.py:434-436, 480, 584, 643`.
- 웹UI: `static/modules/plugins.js` — pending을 **5초 폴링**(`_startPendingPolling`, 452-455; `pollPendingCredentials`, 464-483)해 카드에 "⏳ 요청 N" 배지(`_pluginState.pendingSeen`, 476-478). 모달 `openPluginCredModal`, 삭제 432-445.
- 대안 경로: 채팅 스트림은 **SSE(EventSource)** 기반 (`static/multi/stream.js:30`, `static/multi/pane.js:737`) → 카드 이벤트 push에 재사용 가능.

### 2.4 에이전트 도구 표면
- `hermes-agent/tools/plugin_manager_tool.py`: `plugin_set_secret(plugin, action, key, session_id)` — action = `status`/`request`/`remove` (409-475). **스키마에 value 없음**(role separation 명시 417-422). 등록은 709-725.
- `request`는 `pg.request_plugin_credential(...)` 호출(448-450) → pending 등록. 메시지에 "값을 채팅에 붙여넣게 하지 마라" 명시(456-459).

### 2.5 값 주입 경로 — 세션 스코프 ContextVar (다온의 최대 강점)
- `plugin_gateway.session_plugin_credentials(session_id) -> {var_name: value}` (632-657): 세션 활성 플러그인의 설정된 secrets만 수집. **`os.environ`에 넣지 않음**(635-639).
- `tools/env_passthrough.py`: `_credential_env_var` ContextVar (38-40), `set_plugin_credential_env`/`get_plugin_credential_env`/`clear_plugin_credential_env` (43-64). 샌드박스 env 빌더가 이 값을 읽어 **해당 세션 프로세스에만** 병합.
- 실행 바인딩: `agent_runner.py:195-204`, `streaming.py:1342-1351` — `set_plugin_credential_env(session_plugin_credentials(session_id))`.
- → **ds-bot의 "셸 환경변수 주입"은 다온에서 "세션 ContextVar → 샌드박스 env"로 이미 존재**한다.

### 2.6 마스킹 정책 (기존)
- `hermes-agent/agent/redact.py` `redact_sensitive_text(text)` (124-187):
  - **패턴 기반**: 알려진 키 접두(`sk-`, `ghp_`, `AIza`, `hf_`, … 21-57), ENV 할당(`_ENV_ASSIGN_RE` — 이름에 SECRET/… 포함 시 값 마스킹, 60-63·142-146), JSON 필드(`"apiKey": "…"` 66-70·148-152), Auth 헤더(73-76·154-158), JWT, 전화번호, DB conn string 등.
  - 짧은 토큰(<18)은 `***`, 긴 토큰은 앞6+뒤4 보존(117-121).
  - 적용 지점: `terminal_tool.py:1901-1902`, `send_message_tool.py:49`, `cron/scheduler.py:545-547`, 로깅은 `RedactingFormatter`(190-198; `hermes_logging.py:215,268`, `gateway/run.py:10826`).
  - **설정 스냅샷**(16-18): 런타임 env로 끌 수 없음.
- `api/api/sensitive_redaction.py`: demo-to-skill 파이프라인용. `redact_events`/`redact_text`/`build_trust_flags`, 플레이스홀더 `«REDACTED:{cls}»`(32).
- **중요 갭**: 다온 마스킹은 **패턴 기반**이라, 등록된 시크릿 값이 "알려진 접두/키명" 규칙에 안 걸리면(=임의 문자열, 사내 토큰, 짧은 PIN) 도구결과/프롬프트에서 그대로 노출된다. ds-bot은 **값 기반 redactor**로 이 갭을 닫는다. → 이식의 핵심.

---

## 3. 이식 설계

### 3.0 개념 매핑 (ds-bot → 다온)

| ds-bot | 다온 대응 | 비고 |
|---|---|---|
| Bot | **세션(session_id)** (부차적으로 에이전트/스킬) | 다온 요청 컨텍스트는 session_id |
| Team | 전체(global) | scope `'all'` |
| `secrets.json` | **`daon_secrets.json`** (신규) | `credentials.json` 계약 유지, 경로 규칙 재사용 |
| `secretRequests` state | 신규 store의 `requests` + pending REST | 기존 `/api/plugins/credentials/pending` 패턴 재사용 |
| `DSH_SECRET_<NAME>` | **`DAON_SECRET_<NAME>`** | 패턴 redactor의 ENV 규칙과 자연 정합(§4) |
| `shellEnv.register` resolve | `env_passthrough` ContextVar 주입 | 이미 존재 |
| `tools/execute` 훅 | 신규 **tool-result redaction 훅** | §3.5 |
| `llm/stream` 훅 | 신규 **outbound request redaction** | §3.5 |
| `request_secret`/`list_secrets` 툴 | **동명 툴 신규** | `plugin_manager_tool.py` 등록 패턴 재사용 |
| Settings→Secrets | **Settings 탭 + 채팅 인라인 카드** | §3.6 |

### 3.1 저장 포맷 (신규 `daon_secrets.json`)

`plugin_credentials.py`의 경로 규칙(`_resolve_credentials_dir`, 35-47)을 **공용 헬퍼로 추출**해 재사용하고, 별도 파일로 둔다(기존 plugin 자격증명 계약을 깨지 않기 위해).

```
{
  "version": 1,
  "secrets": [
    {
      "name": "GITHUB_TOKEN",          // normalizeName 후, /^[A-Z][A-Z0-9_]*$/
      "value": "<평문>",                // mode 600 / 소유자 전용
      "purpose": "GitHub 개인 액세스 토큰",
      "scope": "all" | ["<session_id>", ...],
      "requestedBy": "<session_id>|null",
      "createdAt": 1730000000000,
      "updatedAt": 1730000000000,
      "lastUsedAt": 1730000000000      // optional
    }
  ],
  "requests": {
    "<session_id>": [
      {
        "id": "<uuid>",
        "name": "GITHUB_TOKEN",
        "purpose": "...",
        "kind": "fill" | "allow",
        "status": "pending" | "saved" | "allowed" | "canceled",
        "sessionId": "<session_id>",
        "askedAt": 1730000000000,
        "settledAt": 1730000000002
      }
    ]
  }
}
```

- 원자적 쓰기(temp → `replace`) + `os.chmod(path, S_IRUSR|S_IWUSR)` best-effort: `plugin_credentials._save` (70-84)와 동일 계약.
- 로드 시 `name`/`value` 검증, scope 정규화: ds-bot readSecrets(108-132) 이식.

### 3.2 백엔드 저장/주입 API

신규 모듈 `api/api/daon_secrets.py`(가칭) + 라우트 `api/api/routes/secret_routes.py`(가칭). 기존 컨벤션(`plugin_routes.py`의 `handle_*` + `j/bad` 헬퍼) 준수.

| Method / Path | body | 반환(값 미포함) |
|---|---|---|
| `GET /api/secrets` | — | `[{name,purpose,scope,requestedBy,createdAt,updatedAt,lastUsedAt}]` |
| `GET /api/secrets/pending` | — | `[{id,name,purpose,kind,session_id,asked_at}]` |
| `POST /api/secrets/set` | `{requestId} \| {name, purpose?, scope?}`, `{value}` | `{ok,name,scope,...}` (value 미반환) |
| `POST /api/secrets/allow` | `{requestId}` | 저장된 시크릿에 현재 session scope 추가 |
| `POST /api/secrets/cancel` | `{requestId}` | `{ok}` |
| `POST /api/secrets/update` | `{name, scope}` | scope 변경 |
| `DELETE /api/secrets/{name}` | — | 삭제 (값 로그 금지) |

- 서버 함수 대응(ds-bot endpoints 258-340 이식): `secret_set / secret_allow / secret_cancel / secret_update / secret_delete`, 내부 `reconcile`/`settle`/`commit`.
- `set`는 반드시 **UI secure input 경로에서만** 값이 온다는 전제(기존 `handle_post_plugin_credentials_set` 204-208과 동일 원칙).
- pending은 기존과 동일하게 **값을 저장하지 않고** `{plugin|name, key, session_id}` 메타만 보관.

### 3.3 값 주입 — 셸 환경변수 (기존 배관 확장)

- 신규 함수 `daon_secret_env(session_id) -> {DAON_SECRET_<NAME>: value}` — `plugin_gateway.session_plugin_credentials`(632-657)와 동형. `canUse(secret, session_id)`(scope `all` 또는 session 포함)인 항목만.
- 실행 바인딩은 **기존 호출부에 병합**: `agent_runner.py:195-204` / `streaming.py:1342-1351`에서
  `set_plugin_credential_env({**session_plugin_credentials(sid), **daon_secret_env(sid)})`.
- 이름 충돌 방지: `DAON_SECRET_` 접두 vs 플러그인 manifest 키명(대개 `OPENAI_API_KEY`) — 충돌 시 명시적 우선순위 규칙 필요(§6).
- `lastUsedAt`: 샌드박스 command 문자열에 변수명 등장 시 갱신(있으면) — 해석 불가한 원격/도커 백엔드는 근사.

### 3.4 플레이스홀더 형식

- **권장: `[secret:NAME]`** (ds-bot과 동일). 이유: (a) 원본 문서/UX 그대로, (b) 다온 기존 `«REDACTED:CLS»`(sensitive_redaction.py:32)와 문자열 충돌 없음, (c) `agent/redact.py`의 어떤 패턴에도 매칭되지 않아 이중 치환 위험 0.
- 대안: `«secret:NAME»`(다온 표기 통일). 채택 시 §4 정합성 표의 "패턴 비충돌"만 재검증.

### 3.5 값 노출 차단 지점 (치환 위치) — chokes

값 기반 redactor(`redactor(entries)` + `redactDeep` 이식, secrets.js:32-78)를 신규 모듈 예: `api/api/value_redactor.py`로 두고, **다음 4개 출구에 반드시 건다.**

1. **도구 결과 → 저장·렌더 직전**: ds-bot `tools/execute`(436-449) 대응. 다온은 Hermes 동기 루프(`model_tools.handle_function_call` → messages)에서 결과가 직렬화되므로, 결과 반환 직후 redact 후 다시 messages에 넣는 훅/래퍼를 둔다. (구현 위치 후보: `hermes-agent/model_tools.py` 결과 직렬화 지점.)
2. **터미널/read_file 출력**: 이미 `terminal_tool.py:1901-1902`가 패턴 redaction 수행 → 여기에 **값 redactor를 추가로** 적용(순서: 패턴 → 값). `file_tools.py`의 read 결과도 동일 취급.
3. **모델 요청 outbound**: `run_agent.py`의 매 `client.chat.completions.create` 직전에 `messages`/`system` redact(ds-bot `llm/stream` 457-466 대응). Anthropic 경로(`anthropic_adapter.py`)도 동일.
4. **로그**: `RedactingFormatter`(redact.py:190-198)에 값 redactor를 결합. 단, 값 저장소는 프로세스 전역 싱글톤으로 로드돼 있어야 포매터가 조회 가능(§6 리스크).

추가:
5. **히스토리 재로드**: 저장된 transcript(`hermes_state.py`) 재조립 시에도 값이 남지 않도록 **저장 시점(#1)** 에 redact(다온은 저장 후 읽으므로 저장 시 차단이 근본책). `hermes_state.py:1108` 근처의 `***` 정리 로직과 별개로 취급.

### 3.6 시크릿 카드 UI (웹UI)

ds-bot의 두 표면(Settings→Secrets, 채팅 인라인 카드)을 모두 이식한다.

**(A) 채팅 인라인 카드** — 에이전트가 `request_secret` 호출 → 턴 종료 → 카드 렌더.
- 카드 내용: 이름, purpose, `kind`에 따라
  - `fill`: **secure input**(`type=password`, autocomplete off) + "이 값은 에이전트에게 보이지 않습니다" 문구 + [저장]/[취소].
  - `allow`: "이미 저장된 키를 이 세션에 허용?" + [허용]/[취소].
- 제출: `POST /api/secrets/set`(=`requestId`+`value`) → pending 해소 → 다음 턴에 봇에 note 도착(ds-bot `savedNote` 이식, 193-196: "값은 못 본다, 출력/echo 금지, 사용자에게 붙여넣기 요구 금지").
- 렌더 위치: `static/v2/js/app.js`의 메시지 렌더 계층(`renderSessionMessages` 517, `renderUserMessageContent` 1840)에 카드 컴포넌트 추가. 신규 모듈 `static/modules/secret_cards.js`(plugins.js 패턴 준수).
- pending 감지: **(권장) SSE 이벤트 push** — 채팅 스트림이 이미 SSE(`static/multi/stream.js:30`)이므로 `secret_request` 이벤트 추가. **(차선) 폴링** — `plugins.js:452-483`의 5초 폴링을 그대로 재사용.

**(B) Settings→Secrets 관리 탭**
- 표: name / purpose / scope(전체 또는 세션 목록) / lastUsedAt / [scope 변경] / [삭제].
- 신규 탭 등록: `app.js:217-225`의 `tabContextNames` 및 `activateTab`(227-287) 패턴, lazy load 훅(282-283)에 `loadSecrets()` 추가.
- 값은 **절대 표시하지 않음**(목록 API가 value 미포함). 필요 시 "저장됨"만.

**(C) 마스킹된 값 미리보기(선택)**: `custom_providers`가 쓰는 `key[:4] + '•'*… + key[-4:]`(model_manager.py:239-242) 방식을 값 확인용으로 재사용할지는 정책 결정(§4). 기본은 완전 비표시 권장.

### 3.7 에이전트 도구 표면
- 신규 `hermes-agent/tools/secrets_tool.py`(가칭), `plugin_manager_tool.py`의 register 패턴(709-725) 재사용:
  - `request_secret(name, purpose)` → pending 등록(타 턴 종료 신호 포함). **자기 세션에서만** 허용(ds-bot 384-385 대응).
  - `list_secrets()` → name/purpose/scope/"사용 가능 여부"만, **값 미포함**(ds-bot 415-431).
- `TEAM_TOOLS` 등가 목록(다온은 Hermes toolset)에 두 도구 추가.

### 3.8 이식 요약 다이어그램

```
 사용자 ──(웹UI 시크릿 카드, secure input)──▶ POST /api/secrets/set ──▶ daon_secrets.json (600)
                                                                          │
 에이전트 ──request_secret 툴──▶ pending ──▶ 카드 렌더 ──▶ (값은 서버만 저장)
                                                                          │
 샌드박스 실행: set_plugin_credential_env({... plugin, ... daon_secret_env(sid)}) ──▶ $DAON_SECRET_X
                                                                          │
 값 출구 4곳에서 redactor:  tool-result(저장·렌더) / terminal·file / outbound LLM / log
                            └──────────▶ 값 → [secret:NAME] ──────────┘
```

---

## 4. 다온 마스킹 정책과의 정합성

| 항목 | 기존 다온 | 신규 이식 | 충돌 여부 / 결론 |
|---|---|---|---|
| 방식 | **패턴 기반**(redact.py:21-57, 60-114) | **값 기반**(이식 redactor) | 충돌 없음. **상호 보완**. 순서: 패턴 → 값(둘 다 적용) |
| 플레이스홀더 | `***`(redact.py) / `«REDACTED:CLS»`(sensitive_redaction.py:32) | `[secret:NAME]` | 문자열 비충돌. `[secret:…]`가 기존 패턴에 안 걸림 → 이중 치환 없음 |
| 환경변수명 규칙 | `_ENV_ASSIGN_RE`가 이름에 SECRET/… 포함 시 값 마스킹(60-63,142-146) | `DAON_SECRET_<NAME>` | **자연 정합**: `DAON_SECRET_X=...`는 기존 ENV 규칙이 이미 2차 방어 |
| 값 주입 위치 | `os.environ` 금지, ContextVar 세션 스코프(env_passthrough.py:38-64) | 동일 ContextVar 재사용 | 충돌 없음. 오히려 **설계 일관** |
| 저장 권한 | 소유자 전용 best-effort(plugin_credentials.py:79-84) | 동일 계약 | 일관 |
| API 응답 마스킹 | `get_custom_providers`가 `•` 마스킹(231-244); plugin creds는 bool만 | 목록 API 값 미포함(bool/메타만) | 일관(더 엄격) |
| 로그 | `RedactingFormatter` 패턴 적용(190-198) | 값 redactor 결합 | **주의**: 포매터가 값 저장소를 참조해야 함(§6) |
| 짧은 값 | 패턴은 길이 무관 | 값 기반은 <8은 "단독 등장" 한정(secrets.js:48) | 정합(오탐 방지) |

**결론: 철학적으로 이미 정합.** ds-bot의 값 기반 redactor는 다온의 패턴 기반 마스킹과 경쟁하지 않고, 패턴이 못 잡는 "등록된 임의 값"을 메운다. 두 레이어를 순차 적용하는 것이 정답.

---

## 5. 단계별 구현 순서

**Phase 0 — 토대 (0 리스크)**
1. `plugin_credentials._resolve_credentials_dir` 경로 헬퍼 공용화.
2. 신규 `api/api/value_redactor.py`: `redactor(entries)`, `redactDeep`, `redact_text` (secrets.js:32-78 이식, 표준 라이브러리 `re`만 사용).
3. 신규 `api/api/daon_secrets.py`: store CRUD + pending + `normalizeName`/`canUse`/`secretView`. 값 로그 금지 규칙.

**Phase 1 — 가시화(읽기)**
4. 라우트 `GET /api/secrets`, `GET /api/secrets/pending`.
5. 설정 탭(b) 렌더(목록만, 값 비표시).

**Phase 2 — 요청/저장 흐름**
6. `request_secret`/`list_secrets` 툴 등록(자기 세션 한정, 타 턴 종료 신호).
7. `POST /api/secrets/set|allow|cancel|update`, `DELETE`, `reconcile/settle`.
8. 채팅 인라인 카드(a) + SSE `secret_request` 이벤트(또는 5초 폴링).
9. `savedNote` 통지(값 못 봄/echo 금지 문구).

**Phase 3 — 주입 & 차단 (보안 핵심)**
10. `daon_secret_env(session_id)` 구현 + 실행 바인딩 병합(agent_runner.py:195-204, streaming.py:1342-1351).
11. 값 redactor를 4개 출구에 연결: tool-result / terminal·file / outbound LLM / log.
12. `lastUsedAt` 갱신(가능한 백엔드).

**Phase 4 — 정리/평가**
13. 세션/플러그인 삭제 시 `forgetBotSecrets` 등가 정리.
14. 자동 테스트(§7) + `score_engine.py:369-384`의 "API 키 마스킹" 점검 항목에 신규 store도 포함.

---

## 6. 리스크 + 완화책

| # | 리스크 | 경로 | 완화책 |
|---|---|---|---|
| R1 | **저장 전 평문 유출** — 사용자가 키를 채팅에 붙여넣음 | 모델 컨텍스트/transcript | 카드 UX로 붙여넣기 유도 차단 + `savedNote` 문구("붙여넣지 마라") + 기존 패턴 redaction(알려진 접두) 2차 방어 |
| R2 | **패턴 미매칭 값 노출** — 임의 문자열 키 | tool-result/prompt | **값 기반 redactor**가 근본 완화(등록된 값은 접두 무관 제거) |
| R3 | **로그 노출** — 예외/에러에 값 인용 | 로그 파일 | 값 로그 금지 규칙(파싱 에러는 code/name만, secrets.js:97-98) + `RedactingFormatter`에 값 redactor 결합 |
| R4 | **포매터의 저장소 참조 문제** — 로깅은 프로세스 전역인데 값은 세션 스코프 | 로그 | 로그용 redactor는 **프로세스 전역 싱글톤 스냅샷**으로 별도 관리(세션 값과 분리). 세션 완료 시 갱신 |
| R5 | **세션 간 누출** | os.environ | **금지 유지**: ContextVar(env_passthrough.py:38-64)만 사용. 신규 값도 동일 |
| R6 | **값 이름 충돌** (`DAON_SECRET_X` vs 플러그인 `X`) | env 오염 | 접두 분리 + 병합 시 명시적 우선순위(플러그인 값 우선) + 충돌 로그(값 아님) |
| R7 | **Windows 권한** — chmod 무력 | 파일 ACL | plugin_credentials.py:79-84와 동일 best-effort, `%LOCALAPPDATA%` 위치 신뢰, 문서에 한계 명시 |
| R8 | **이중 치환/오탐** — 플레이스홀더 재매칭, 짧은 값이 일반어 훼손 | redactor | 단일 패스 + 라벨 Map(secrets.js:45-52), <8은 lookaround(48) |
| R9 | **custom_providers.json 평문 at-rest** | 파일 유출 | 본 설계 범위 외지만 병행 권고: 신규 store와 동일하게 600/최소권한, 필요 시 별도 후속 설계 |
| R10 | **턴 종료 의존** — 카드가 턴을 안 끝내면 스트리밍 꼬임 | UX | ds-bot `concludeTurn`(408) 이식: 카드 표시 = 턴 종료, note는 다음 턴 |

---

## 7. 검증 시나리오

**A. 값 비노출**
1. 카드에 값 입력 → 저장 API 응답 본문에 값 문자열이 **없음**(grep).
2. `GET /api/secrets` 응답에 value 필드 부재.
3. `list_secrets` 툴 반환에 값 부재.
4. 저장 후 transcript/히스토리 재로드 시 값 부재(`[secret:NAME]`만).

**B. 치환 정확성 (redactor 단위테스트, secrets.js 시맨틱)**
5. 긴 값 포함 문자열 → `[secret:NAME]`로 치환, JSON 이스케이프 형태도 치환.
6. 8자 미만 값은 단독 등장만 치환(앞뒤 영숫자 붙으면 미치환).
7. 다른 값을 품은 값 → 긴 쪽 우선.
8. 이미 쓴 라벨은 재치환 안 됨(단일 패스).

**C. 주입**
9. 세션 ON 시크릿 → 샌드박스에서 `$DAON_SECRET_X` 접근 성공.
10. 세션 OFF/미허용 → 해당 변수 부재(스코프 격리).
11. sibling 세션에서 값 부재(ContextVar 격리).

**D. pending/카드 상태머신**
12. `request_secret` → pending 등록 + 턴 종료 + 카드 렌더.
13. 저장 전 취소 → 봇에 "재요청 금지" note, pending 제거.
14. 기존 키 존재 시 `allow` 카드 → scope에 세션 추가.
15. 값 저장 상태에서 재요청 → "이미 저장됨, 변수명 안내" 반환(카드 미표시).
16. pending 상한(20) 트림.

**E. 출구 차단**
17. `terminal`에서 `echo $DAON_SECRET_X` → 출력이 패턴+값 redaction으로 `***`/`[secret:X]`.
18. 파일 read로 값 노출 시도 → redacted.
19. 모델로 나가는 messages에 값 부재(요청 로깅으로 확인).
20. 로그 파일에 값 부재.

**F. 정리**
21. 세션/플러그인 삭제 → scope 정리 + 카드 제거.
22. `score_engine` 마스킹 점검에 신규 store 반영.

---

## 8. 부록 — 미확인/주의 사항

- `secrets.js:11`의 `SECRET_NAME` 정규식은 복사본에서 `/^[A-Z...3}$/` 로 손상되어 **원문 미확인**. 에러 문구(287) 기반으로 `/^[A-Z][A-Z0-9_]*$/` 추정. 구현 시 원본 확인 필요.
- `agent/redact.py:60` `_SECRET_ENV_NAMES`도 손상된 형태(`r"(?:A...TH)"`)로 확인됨 — 정확한 토큰 집합은 구현 시 원문 확인 필요(단, "SECRET 포함 변수명"을 잡는 규칙이라는 점은 확인됨).
- Phase 3의 훅 삽입 지점(tool-result, outbound LLM)은 Hermes 동기 루프 내부라 실제 함수 경계 확인이 필요하다. 본 문서는 "결과 직렬화 직전 / 매 `create` 직전"이라는 **위치**를 확정하고, 정확한 함수는 구현 착수 시 소스 재확인 대상으로 남긴다.
- 다온에는 ds-bot의 "Bot/Team" 개념이 없으므로 scope를 **session_id 기반**으로 매핑했다. 에이전트 단위 scope가 필요하면 후속 확장.
