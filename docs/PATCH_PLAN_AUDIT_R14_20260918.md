# DAON 패치 방향 결정서 — R4~R14 외부 감사 대응

- 작성일: 2026-09-18
- 근거 문서:
  - `DAON_PUBLIC_SCOPE_FINAL_VERIFICATION_REPORT_20260918_R3.docx` (감사 결과)
  - `DAON_VERIFICATION_SELECTION_MUTUAL_IP_PROTECTION_SPEC_V1_3_20260918.txt` (재검증 명세)
- 목적: 감사 지적사항을 **소스 근거와 함께** 확정하고, P0→P2 패치 우선순위와 변경 지점을 결정한다.
- 범위: 본 문서는 "무엇을 어떤 순서로 고칠 것인가"의 결정서이며, 구현 자체는 후속 작업이다.
- **실행 결과**: 구현·실측은 [`docs/PATCH_EXECUTION_REPORT_R14_20260918.md`](PATCH_EXECUTION_REPORT_R14_20260918.md)에 기록됐다.
  (8단계 완료, 169 tests passed, Evidence ZIP digest `459777e5…`)

---

## 0. 한 줄 결론

감사의 최종 FAIL은 **제품 전체 실패가 아니라 "고정 revision + 로컬 모델 2종"에서 재현된 단일 계약경계 실패**다.
그러나 그 원인은 코드에서 **확정적으로 재현 가능**하며(아래 2장), 이는 사용자 환경에서도 동일하게 발생할 수 있는 실제 버그다.
따라서 **P0 5개 항목을 먼저 코드로 봉합**하고, 이후 T01~T13 receipt 발행기를 붙여 재검증 가능 상태로 만든다.

| 구분 | 결정 |
|---|---|
| 최우선 | P0-1 Demo-to-Skill output contract hardening |
| 표현 정책 | 자율진화 → **자율보강**으로 표기 제한. **실제 적용 대상은 라이브 사이트 `portfolio/Test/daon-download/index.html`(P0-5)** |
| 검증 대응 | v1.3 명세의 T01~T13 receipt를 **코드가 직접 발행**하도록 계측 추가 |
| 외부 검증 등급 | 기본 **LEVEL B**, 보안/자율성 주장 시 **LEVEL C** 검토 |

> **중요 — 문서를 그대로 수용하지 말 것.** 감사 보고서 자체도 "FINAL"을 제품 전체 판정이 아니라
> "이 감사 라운드의 종결"로 한정하고, 새 Evidence가 제공되면 재검증 가능하다고 명시한다.
> 실제로 소스 대조 결과 **반박/정정 가능한 지적이 4건, 감사가 놓친 제품 경로가 3건** 확인됐다(아래 1-A장).
> **P0 구현 전에 1-A장을 먼저 반영해야 한다.** 그대로 P0를 밀어붙이면 감사가 놓친 경로까지 잘못 봉합한다.
>
> **[정정 2026-09-18]** 방향 검증 결과 초판의 **P1-2 CDP "기본 비활성"과 Auth "적용 확인"은 제품을 파손**하므로
> 아래와 같이 교체했다. 또한 표현 정책의 실제 대상 경로를 명시하고 **P0-5로 승격**했다.

---

## 1-A. 반박 가능 항목 및 감사 미검증 경로 (소스 대조 결과)

### ✅ 검증된 지적 (반박 불가, 반드시 수정)

| ID | 지적 | 소스 확정 근거 |
|---|---|---|
| A1 | body 타입 계약 불일치 | [`analyze()`](api/api/demo_to_skill.py:1070)는 frontmatter 존재만 검사, [`write()`](api/api/demo_to_skill.py:1449)는 `str + body` 무조건 가정 → **TypeError 확정** |
| A3 | REJECTED enforcement 부재 | [`load_skills()`](api/api/skill_registry.py:658)에 lifecycle 필터 전무. [`get_catalog_text()`](api/api/skill_registry.py:638)는 rejected를 **어느 섹션에도 안 넣음**(카탈로그는 정상) |
| A2 | APPROVED 미검증 | [`promote_skill()`](api/api/skill_registry.py:755)에 expected-output/verifier 호출 없음 |

### ⚠️ 반박 가능 (감사 지적의 과잉 해석)

#### R1. "자율보강 효과 미관찰" — **제품 결함이 아니라 측정 설계 오류**

감사는 R12~R14에서 `SkillWriter.write()`를 **직접 호출**했다. 그런데 실제 제품 진입점은
[`analyze_text_workflow()`](api/api/demo_to_skill.py:1702)다.

```python
# demo_to_skill.py:1728
if not skill_data:
    return None
...
# demo_to_skill.py:1732
skill_path = writer.write(skill_data, skill_name=skill_name)
```

**`analyze()`는 실패 시 절대 빈 dict를 반환하지 않는다** — 모든 예외 경로가
[`_fallback_skill()`](api/api/demo_to_skill.py:1073)을 반환한다. 즉 제품 경로에서
"writer가 죽는 케이스"와 "writer에 도달하지 못하는 케이스"는 **다른 문제**다.

**반박 요지**: 감사가 법한 실패는 **실제 제품 경로에서는 발생하지 않는 조합**일 수 있다.
감사 결론을 인정하기 전에 → **[`analyze_text_workflow()`](api/api/demo_to_skill.py:1702) 전 구간을
그대로 실행하는 재시험**이 선행되어야 한다. 이것이 감사의 R13/R14 설계보다 제품 경로에 더 가깝다.

> 단, A1 자체는 실재하는 잠재 결함이다. 제품 경로에서 우연히 회피될 뿐, 무결성은 없다.
> **수정은 하되, "실제 제품에서 실패하는 버그"라고는 단정하지 않는다.**

#### R2. "Auth wiring 미확인" — **함수는 구현됐으나 배선은 0건 (부분 반박, 정정)**

> **[정정 2026-09-18]** 초판은 "미들웨어가 완전히 구현되어 있고 문제는 기본값뿐"이라고 썼다.
> 소스 재검증 결과 **이 서술은 절반만 맞다.** 아래로 정정한다.

감사는 "call-site 미확인"이라고만 했다. 실제 [`check_auth()`](api/api/auth.py:130)는
게이트([`is_auth_enabled()`](api/api/auth.py:133)), 예외 경로([`PUBLIC_PATHS`](api/api/auth.py:19)),
서명 쿠키 검증([`verify_session()`](api/api/auth.py:140)), 401/302 분기까지 **함수 내부는 완성**되어 있다.

**그러나 이 함수는 어디에서도 호출되지 않는다.** 전체 트리 검증 결과:

- `check_auth` 호출부 **0건** (hermes-agent의 동명 `_check_auth`는 별개 모듈)
- [`server.py:135-163`](server.py:135) `do_GET`/`do_POST`는 `handle_get`/`handle_post`로 **직행** — 인증 게이트 없음
- [`routes/__init__.py`](api/api/routes/__init__.py:1)는 순수 dict 디스패치, 인증 미들웨어 없음
- `admin_routes.py`는 `/api/auth/login`·`/status`·`/logout`만 처리

**정확한 판정**: 감사의 "auth wiring 필요"는 **틀리지 않았다.** 미들웨어는 **신규 배선이 필요**하다.
"적용 여부 확인"이 아니라 **처음부터 배선하는 작업**이며, 계획서가 말한 것보다 큰 작업이다.

**추가 확인 — 기본값도 위험하다**: [`is_auth_enabled()`](api/api/auth.py:73)는
`get_password_hash() is not None`을 반환하므로 **비밀번호 미설정 시 False**이고,
[`check_auth()`](api/api/auth.py:133)는 `if not is_auth_enabled(): return True`로 즉시 통과시킨다.
즉 배선을 하더라도 **기본값이 OFF면 여전히 무인증**이다. 배선 + 기본값 정책을 **함께** 고쳐야 한다.

**바인딩 주소 — 소스 기본값은 loopback이 아니다**:

- [`config.py:212`](api/api/config.py:212) — `HOST = _load_config_value('server.host', None, '0.0.0.0')` → **기본값 0.0.0.0**
- [`config.yaml:14`](config.yaml:14) — `host: "127.0.0.1"` → **이 저장소에서만 loopback으로 오버라이드**

→ 개발 트리는 loopback이지만, **config.yaml이 없는 배포본은 0.0.0.0으로 LAN 전체에 무인증 노출**된다.
"loopback이라 안전"이 아니라 **"배포본에 따라 LAN 전체 무인증 노출"**로 판정한다.
**→ P1-2에서 배선 + 기본값 + 바인딩 3가지를 함께 처리한다.**

#### R3. "미서명 설치기 = 공급망 위험" — **크기와 방향이 모두 과장**

감사 스스로 "해시 일치는 서명을 대체하지 않음"이라고 명시하고, NSIS-3 Unicode / app_64.zip /
ASAR 14/14 integrity / path traversal PASS를 모두 확인했다.

**반박 요지**: "서명 없음"은 사실이나, **자가 서명(self-signed)도 아니란 점**은
"제3자 배포 시 Windows SmartScreen 경고"라는 UX 이슈에 가깝다. 결정서 P1에서
**코드 서명 인증서 도입**을 별도 항목으로 분리하고, "악성 가능성"과는 섞지 않는다.

#### R4. "자율진화 NOT_PROVEN" — **정의 불일치 문제이지 제품 결함이 아님**

감사의 [자율진화 정의](DAON_PUBLIC_SCOPE_FINAL_VERIFICATION_REPORT_20260918_R3.docx:193)는
"사람의 새 정답·시연 없이 시스템이 자신의 실패를 감지·진단하고 개선안을 적용"이다.

**반박 요지**: 공개 범위에서 이 정의에 부합하는지 **검증된 적이 없다**. 즉 이는
**"미입증(not proven)"**이지 **"부재(absent)"**가 아니다(감사도 §16에서 이를 명시).
→ **코드로 증명할 대상이 아니라 표현으로 관리할 대상**이다. P2의 표현 제한이 유일한 정답.

### 🔍 감사가 놓친 제품 경로 (미검증)

| 항목 | 소스 위치 | 감사 취급 | 실제 중요도 |
|---|---|---|---|
| **`analyze_text_workflow()` 전 구간** | [`demo_to_skill.py:1702`](api/api/demo_to_skill.py:1702) | R9에서 route ACK만 확인, **내부 미실행** | **최상** — 실제 제품 Demo-to-Skill 경로 |
| **`check_auth()` 배선 0건 + 기본값 False** | [`auth.py:130`](api/api/auth.py:130) / [`auth.py:73`](api/api/auth.py:73) | 미확인 | **최상** — 함수는 있으나 호출부 0건, 배선+기본값 모두 미비 |
| **`HOST` 기본값 0.0.0.0** | [`config.py:212`](api/api/config.py:212) | 미확인 | **최상** — config.yaml 없는 배포본은 LAN 전체 무인증 노출 |
| **`_add_capability.py` 제품 번들 존재** | 감사는 "공개 소스 one-time script"로만 처리 | **API 번들에 실제 포함** | 중 — capability 오염 가능성 |

**핵심 함의**: 감사는 route를 **비동기 ACK로만** 관찰하고 내부 실행을 열지 않았다.
`analyze_text_workflow` → `analyze` → `write` → `reload`의 **성공 경로를 아무도 시험하지 않았다.**
A1 수정 후에는 **이 경로를 1순위로 재시험**해야 한다.

### 📌 재검증 우선순위 (감사 설계보다 제품 경로 우선)

```
[1] analyze_text_workflow() 실제 실행 → SKILL.md 생성 여부        ← 감사가 시험 안 한 경로
[2] check_auth() 배선 0건 + is_auth_enabled() 기본값 + HOST 확인   ← 감사가 본 적 없는 각도
[3] SkillWriter에 body=dict 직접 주입 (A1 방어 회귀)               ← 감사가 시험한 경로
[4] REJECTED normal path 차단 (A3)
```

> **[2]는 코드로 확인됐다.** [`check_auth()`](api/api/auth.py:130)는 **호출부가 0건**이고,
> [`is_auth_enabled()`](api/api/auth.py:73)는 `password_hash is not None`이므로 **기본값 False**다.
> 즉 함수는 존재하나 **배선도 안 됐고 기본값도 꺼져 있다.**
> 최종 위험도는 **서버 바인딩 주소에 따라 결정**되며, [`config.py:212`](api/api/config.py:212)의
> 기본값은 **0.0.0.0**이다([`config.yaml:14`](config.yaml:14)이 이 저장소에서만 loopback으로 덮는다).
> → **배포본은 LAN 전체 무인증 노출 가능.** 배선 + 기본값 + 바인딩을 함께 처리한다.

---

## 1. 감사 지적사항 정리 (제품 영향도 기준 재배열)

| ID | 감사 지적 | 감사 판정 | 실제 성격 | 우선순위 |
|---|---|---|---|---|
| A1 | `body`가 object인데 SkillWriter가 string 가정 → TypeError | FINAL_GENERATION_CONTRACT_FAILURE | **실제 버그 (재현 확정)** | **P0** |
| A2 | APPROVED = 검증완료로 오인 가능, verifier 연결 불명 | B_STATIC_VALIDATION | 오해 소지 + 정책 미확정 | **P0** |
| A3 | REJECTED Skill이 direct/router/forced 경로로 로드됨 | 경계 확인 | **실제 결함 (enforcement 부재)** | **P0** |
| A4 | password/OTP/token 계열이 analyzer prompt 경계까지 도달 | 부분 확인 | **실제 위험 (redaction 부재)** | **P0** |
| B1 | Browser full E2E 미완결 | ENVIRONMENT_BLOCKER | 검증 환경 문제 (제품 결함 아님) | P1 |
| B2 | CDP 9222 / `remote-allow-origins=*`, install-update IPC | 정적 관찰 | 하드닝 필요 | P1 |
| B3 | mobile service_role / Realtime 노출 경계 | RISK BOUNDARY | 하드닝 필요 | P1 |
| C1 | self-augmentation 실제 작업효과 미입증 | 미관찰 | A1 봉합 후 재시험 필요 | P2 |
| C2 | autonomous self-evolution 미입증 | NOT_PROVEN | **표현 제한으로 대응** | P2 |

---

## 2. 근본 원인 — 소스에서 확정한 사실

### A1. body 타입 계약 불일치 (감사의 FAIL 직접 원인)

세 함수의 계약이 서로 어긋난다.

1. [`SkillAnalyzer.analyze()`](api/api/demo_to_skill.py:1070) — `"frontmatter"` 키의 **존재만** 검사한다.
   ```python
   if not parsed or "frontmatter" not in parsed:
       return SkillAnalyzer._fallback_skill(events, skill_name)
   return parsed          # body가 str인지 전혀 확인하지 않음
   ```
2. [`SkillWriter.write()`](api/api/demo_to_skill.py:1399) — `body`를 **무조건 문자열로 가정**한다.
   ```python
   body = skill_data.get("body", "")
   ...
   content = "\n".join(yaml_lines) + "\n\n" + body   # body가 dict면 TypeError
   ```
3. [`_extract_json_from_response()`](api/api/demo_to_skill.py:1817) — JSON 파싱만 하고 **스키마 검증이 없다**.

LLM이 `{"frontmatter": {...}, "body": {"steps": [...]}}` 형태로 응답하면(모델에 따라 흔함),
line 1070은 통과하고 line 1449에서 `str + dict` → `TypeError`로 죽는다. **감사 결과와 정확히 일치한다.**

또한 [`_call_llm_direct()`](api/api/demo_to_skill.py:1802)는 실패 시 빈 `"{}"`를 반환하는데,
이를 호출한 `analyze()`가 fallback으로 처리하므로 **조용한 품질 저하**가 발생한다(감사 미지적 사항이지만 함께 봉합).

### A3. REJECTED enforcement 부재

[`load_skills()`](api/api/skill_registry.py:658)에는 **lifecycle 필터가 전혀 없다.**
```python
entry = self._skills.get(clean_name)   # lifecycle 검사 없음 → REJECTED도 그대로 로드
```
[`_load_skill_entry()`](api/api/skill_registry.py:312)는 lifecycle 값만 **기록**하고(판정 블록은 [341-346](api/api/skill_registry.py:341)) 카탈로그에서 제외하지 않는다.
→ 감사의 "direct/forced 경로 도달"이 코드로 확인된다.

### A4. Redaction 부재

[`_format_event_summary()`](api/api/demo_to_skill.py:1086)가 캡처 이벤트의 `value`/`text`를
가공 없이 프롬프트 문자열에 삽입한다. `type="password"` 필드도 동일 경로로 들어간다.
→ 감사의 "marker가 analyzer prompt boundary까지 도달"이 코드로 확인된다.

### A2. 승인 의미 불일치

[`SkillWriter.write()`](api/api/demo_to_skill.py:1454)의 주석은 `APPROVED`라고 쓰여 있으나
실제 호출은 `SKILL_REVIEW`다. 감사도 이 불일치를 지적했다. 더불어 승인 시
**expected-output/독립 verifier 호출이 없다**.

---

## 3. 패치 방향 (결정)

### P0-1. Demo-to-Skill output contract hardening ★최우선

**결정: 3중 방어(defense-in-depth)로 봉합한다. "정규화 후 실패"로 전환.**

변경 지점 — [`api/api/demo_to_skill.py`](api/api/demo_to_skill.py)

| 위치 | 변경 |
|---|---|
| 상단 상수 | `ANALYZER_OUTPUT_SCHEMA_VERSION = "1.0"` 추가 |
| [`_extract_json_from_response()`](api/api/demo_to_skill.py:1817) | 파싱 후 `frontmatter:dict`, `body` 존재 검증. 실패 시 사유를 담은 결과 객체 반환 |
| [`SkillAnalyzer.analyze()`](api/api/demo_to_skill.py:1028) | 반환을 **정규화된 envelope**로 통일: `{"frontmatter": dict, "body": str, "_meta": {schema_version, normalization, ...}}` |
| 신규 `_coerce_body_to_text()` | body가 `str/dict/list/None` 어느 것이든 결정적으로 문자열화 (dict→YAML/Markdown 블록, list→불릿) |
| [`SkillWriter.write()`](api/api/demo_to_skill.py:1386) | `body = _coerce_body_to_text(skill_data.get("body"))` — writer는 절대 타입에 의존하지 않음 |
| [`_call_llm_direct()`](api/api/demo_to_skill.py:1752) | finish_reason/절단(truncation) 감지, 실패 시 예외가 아닌 구조화된 오류 반환 |

**핵심 원칙: `SkillWriter`는 어떤 타입이 들어와도 `SKILL.md`를 생성한다.**
"생성 실패"는 오직 디스크 I/O 실패 시에만 허용한다.

**감사 권고 receipt 필드 전량 발행** (명세 v1.3 T01과 1:1 대응):

```
analyzer_output_schema_version, parsed_frontmatter_type, parsed_body_type,
writer_expected_body_type, normalization_attempted, normalization_result,
schema_validation_result, skill_file_created, skill_file_sha256
```

**회귀 테스트** — `body`가 각각 `str / dict / list / null / int`인 5개 케이스에 대해
`SkillWriter.write()`가 예외 없이 파일을 생성하고 frontmatter가 유효 YAML인지 검증.

---

### P0-2. Approval과 independent verification 분리

**결정: "APPROVED ≠ VERIFIED"를 코드·UI·문서 3곳에서 명시한다.**

- `SkillEntry`에 `verification_level` 필드 추가 (감사 등급 A~E를 그대로 사용)
  - `A LIFECYCLE_ONLY` / `B STATIC_VALIDATION` / `C RUNTIME_SELF_TEST` / `D EXPECTED_OUTPUT_VALIDATION` / `E INDEPENDENT_VERIFICATION`
- [`SkillWriter.write()`](api/api/demo_to_skill.py:1454)의 주석/실제 불일치 제거 — 실제 정책(REVIEW)으로 주석 정정
- [`promote_skill()`](api/api/skill_registry.py:755) 승격 시 `verification_level`을 manifest에 기록
- [`api/api/approval.py`](api/api/approval.py) 검토 후 승인 UI에 `verification_level` 노출
- 명세 T08의 boolean 관찰(어떤 검사가 실행됐는지) 기록

---

### P0-3. REJECTED lifecycle enforcement

**결정: 기본 경로에서 차단, 진단 경로는 명시적 opt-in으로 분리.**

변경 지점 — [`api/api/skill_registry.py`](api/api/skill_registry.py)

| 메서드 | 변경 |
|---|---|
| [`load_skills()`](api/api/skill_registry.py:658) | 시그니처에 `include_rejected: bool = False` 추가. 기본값에서 `SKILL_REJECTED` / `SKILL_DRAFT` 제외 |
| [`load_skills_for_reviewer()`](api/api/skill_registry.py:688) | 동일 필터 적용 |
| [`get_skill()`](api/api/skill_registry.py:714) / [`load_skills`](api/api/skill_registry.py:658) | `lifecycle == SKILL_REJECTED`면 NORMAL_USER_PATH에서 미반환 |
| 카탈로그 생성부 | `rejected`는 별도 섹션으로 분리(선택 노출), 기본 카탈로그에서 제외 |
| 호출부 `forced_skills` | 진단 경로임을 명시하는 별도 플래그로만 접근 허용 |

**명세 v1.3 T07 acceptance matrix 준수:**

> NORMAL_USER_PATH의 기본 기대 = `MODEL_CONTEXT_INCLUDED=false`, `TOOL_EXECUTION_INFLUENCED=false`
> L1/L2(CATALOG_VISIBLE/SELECTED)만으로 취약점으로 단정하지 않는다
> INTERNAL_FORCED_PATH / DEBUG_PATH는 별도 판정하고 normal path와 합산하지 않는다

**회귀 테스트**: REJECTED Skill을 만든 뒤 (1) normal path에서 미로드, (2) forced path에서는 로드, (3) 두 경로의 receipt가 분리 기록되는지 검증.

---

### P0-4. Sensitive field redaction / trust labeling

**결정: 캡처 시점이 아니라 프롬프트 조립 직전에 redaction한다(단일 관문).**

- 신규 모듈 `api/api/sensitive_redaction.py`
  - 입력: 이벤트 dict 리스트 → 출력: redaction된 dict + hit 리포트
  - 대상: `input[type=password]`, `FAKE_/sk-/Bearer/otp/token/email` 패턴, `name/id`에 password·passwd·otp·token·secret·cvv 포함
  - 마스킹 결과는 `«REDACTED:CLASS»` 형태로 치환, **원문은 어떤 로그/프롬프트/receipt에도 남기지 않음**
- 적용 지점: [`_format_event_summary()`](api/api/demo_to_skill.py:1086) 진입부 + [`SkillAnalyzer.analyze()`](api/api/demo_to_skill.py:1028)

**명세 v1.3 T10 플래그 발행** (본문 없이 존재 여부만):

```
DOM_CAPTURED / LOCAL_BRIDGE / MODEL_PROMPT / PERSISTED_SKILL
/ LOGGED / EXTERNAL_PROVIDER_SENT   (각 true|false)
```

**회귀 테스트**: `FAKE_PASSWORD_<challenge>`를 캡처에 넣고 `MODEL_PROMPT=false`, `PERSISTED_SKILL=false`인지 검증.

---

### P1-1. Browser full E2E (T09)

감사는 "제품 실패"가 아니라 **검증 PC의 GPU fatal**로 미완결했다([보고서 5.3]). 제품 결함이 아니므로
코드 패치는 불필요하다. 단 **명세 T09가 요구하는 필드를 제품이 발행**할 수 있게 계측을 추가한다
(`localhost request count`, `DOM state hashes`, `error class`, `failure/reconnect`).

### P1-2. Auth / CDP / IPC 경계 (T12)

> **[정정 2026-09-18]** 초판의 CDP "기본 비활성"과 Auth "적용 확인"은 **제품을 파손**하므로 아래로 교체한다.

| 항목 | 소스 위치 | 조치 |
|---|---|---|
| CDP 노출 | [`electron/main.js:22-23`](electron/main.js:22) — `remote-debugging-port=9222`, `remote-allow-origins=*` | **포트 9222는 유지한다(끄지 않는다).** `remote-allow-origins`만 `*` → `http://localhost:9090`으로 축소. 포트는 이미 loopback 바인드이며, 제품 핵심 의존이므로 비활성화 금지 |
| install-update IPC | [`electron/preload.js`](electron/preload.js), [`electron/src/IpcHandlers.js`](electron/src/IpcHandlers.js) — `cmd.exe`로 installerPath 실행 | installerPath 화이트리스트(서명 검증/경로 검증), scheme 검증, fail-closed |
| Auth wiring | [`api/auth.py:130`](api/api/auth.py:130) 함수는 구현 — 그러나 **호출부 0건**, [`is_auth_enabled()`](api/api/auth.py:73) **기본 False**, [`config.py:212`](api/api/config.py:212) `HOST` 기본 **0.0.0.0** | (1) `server.py` `do_GET`/`do_POST`에 **미들웨어 신규 배선**(공개 경로만 `PUBLIC_PATHS` 통과) (2) `password_hash` 미설정 시 fail-closed 또는 로컬전용 강제 (3) `HOST` 기본값을 loopback으로 고정하고 LAN 노출은 opt-in (4) protected endpoint matrix (T12-A) — **감사보다 우선순위 높음** |
| 코드 서명 | [`installer.nsh`](installer.nsh), [`daon-server.spec`](daon-server.spec) — Authenticode NotSigned | 코드 서명 인증서 도입 (감사 R3 반박 참조 — 악성 가능성과 분리) |

### P1-3. Mobile service_role / Realtime (T13)

service_role key 사용 경로를 anon/user role로 분리하고, RLS 강제 여부를 확인한다.
E2EE/P2P는 **TLS만으로 E2EE를 주장하지 않도록** 문구를 하향한다(명세 18-B).

---

### P2-1. Self-augmentation effect experiment (T03~T08)

A1 봉합이 끝나면 비로소 측정 가능해진다. 순서를 고정한다.

1. A1 봉합 → T01에서 `skill_file_created=true` 확인
2. T02 소비 체인 확인
3. T03 baseline(A) vs approved(B), 동일조건·반복·순서 통제(AB/BA)
4. T04 unseen / T05 regression / T06 restart / T07 reject
5. T08 approval 검증 등급 기록

### P0-5. 공개 사이트 허위 주장 문구 정정 (T11/T13) ★신규 승격

> **[신규 2026-09-18]** 초판 §5는 "자율진화"를 코드/문서/UI 전체에서 제한한다고만 썼다.
> 그러나 `Daon agent System` 트리 내 해당 문구는 사실상 없고, **실제 노출은 외부 라이브 사이트**에 있다.
> 라이브 공개 사이트의 법적·마케팅 노출이므로 **P0으로 승격**한다.

**대상 파일**: `C:\daon\portfolio\Test\daon-download\index.html` — 배포 트리 밖이며,
이 계획서를 `Daon agent System` 안에서 실행하는 사람은 **바꿀 대상을 찾지 못한다**(경로 명시 필수).

| 라인 | 현재 문구 | 조치 |
|---|---|---|
| [245](C:/daon/portfolio/Test/daon-download/index.html:245) | "자기진화 시스템" | "자율보강(human-assisted)"으로 하향 |
| [353](C:/daon/portfolio/Test/daon-download/index.html:353) | "종단간 암호화(E2EE) 채널로 즉시 접속" | E2EE 주장 삭제 — TLS만으로 E2EE 주장 금지(명세 18-B) |
| [617](C:/daon/portfolio/Test/daon-download/index.html:617) | "P2P 암호화 릴레이" | 실제 연결 방식으로 정정(TLS 릴레이 등) |
| [450](C:/daon/portfolio/Test/daon-download/index.html:450) | `from daon.vault import ...` | **존재하지 않는 모듈** — 실제 모듈명으로 교체 또는 코드 카드 제거 |
| [451](C:/daon/portfolio/Test/daon-download/index.html:451) | `from daon.crypto import ...` | **존재하지 않는 모듈** — 동일 |
| [418](C:/daon/portfolio/Test/daon-download/index.html:418) | "0.1ms 응답 보장" | 측정 근거 없음 — 삭제 또는 실측치로 교체 |
| [223](C:/daon/portfolio/Test/daon-download/index.html:223) | "1.4 GB/s" | 측정 근거 없음 — 삭제 또는 실측치로 교체 |
| [224](C:/daon/portfolio/Test/daon-download/index.html:224) | "322 MB" | **실제와 일치 — 유지 가능** |

**근거**: 감사 §17이 "사용하면 안 되는 표현"으로 못박았고, 명세 v1.3 T13이 정면 검증을 예고한 항목이다.

### P2-2. Autonomous self-evolution 표현 제한 (T11)

**결정: 코드/문서/UI 전반에서 "자율진화" 표현을 "자율보강(human-assisted)"으로 제한한다.**
감사가 반복 지적한 "self-augmentation과 autonomous self-evolution의 혼용"을 차단한다.
비공개 자율 경로가 실제로 존재한다면 명세 T11 receipt로만 주장한다.
**구체 대상 경로는 위 P0-5에 명시했다**(라이브 사이트가 실제 노출 지점).

---

## 4. 구현 순서 (로드맵)

```
[1단계] P0-1 계약 하드닝  ─┬─ _coerce_body_to_text + schema validation
                          ├─ receipt 필드 발행
                          └─ body 5종 타입 회귀 테스트        ← 이것만으로 R14 FAIL의 직접 원인 해소
[1.5단계] P0-5 사이트 문구 ── portfolio/Test/daon-download/index.html
                          └─ E2EE/P2P/허위모듈/0.1ms/1.4GB/s 교체  ← 라이브 노출, 법적 리스크
[2단계] P0-4 redaction    ── 단일 관문 + T10 플래그
[3단계] P0-3 REJECTED     ── load_skills 필터 + T07 matrix + 경로 분리 테스트
[4단계] P0-2 Approval     ── verification_level + 주석 정정 + UI 노출
[5단계] P1-2 CDP/IPC/Auth ── allow-origins 축소 + Auth 신규 배선 + HOST 고정 + T12
[6단계] 계측/리포트       ── T01~T13 receipt 발행기 (LEVEL B Evidence ZIP 자동 생성)
[7단계] P2-1 효과 실험    ── T03~T08
```

> **[1단계]는 감사가 FAIL로 지목한 "생성(generation)" 체인의 직접 원인을 해소한다.**
> 근본 원인이 코드에서 확정적으로 재현되므로(R14의 TypeError) 수정 효과는 결정적이다.
> **단, 감사의 진짜 요구는 "실행 가능한 Skill 생성 **및 실제 작업능력 향상**"이다.**
> T03~T05(효과/미학습/회귀) 없이는 **절반만 닫힌다** — P2-1까지 완료해야 종결로 주장할 수 있다.
> [1단계]만으로 "감사 종결"이라고 쓰지 않는다.

## 5. 표현 정책 (감사 §17 준수)

> **[정정 2026-09-18]** 이 정책의 **실제 적용 대상 파일**은 `Daon agent System` 트리가 아니라
> `C:\daon\portfolio\Test\daon-download\index.html`(라이브 공개 사이트)이다. 위 P0-5 참조.

| 주제 | 사용 금지 | 사용 |
|---|---|---|
| Demo-to-Skill | "모든 모델에서 고장난다" | "계약 검증/정규화/fallback을 명시하고 재시험 완료" |
| 자율보강 | "작업능력 향상이 검증됨" | "Skill 기반 human-assisted 보강 구조와 consumer bridge 확인" |
| 자율진화 | "자율진화가 없다 / 검증됐다" | "공개 범위에서 autonomous self-evolution은 미입증" |
| Approval | "승인은 검증 완료다" | "verification_level B_STATIC_VALIDATION" |
| REJECTED | "거부된 Skill이 항상 실행된다" | "normal path 차단, forced path는 별도 기록" |
| 보안 | "정보유출 확인" | "넓은 권한·prompt boundary 등 검토할 신뢰경계 확인" |
| 전체 | "DAON 전체 FAIL" | "Coverage=PARTIAL, Closure=NEEDS_EVIDENCE" |
| **공개 사이트(P0-5)** | "E2EE", "P2P 암호화", "자기진화 시스템", "0.1ms 보장", "1.4 GB/s", `daon.vault`/`daon.crypto` import | 실제 구현 사실에 부합하는 표현만 |

## 6. 재검증 대응 체크리스트 (명세 v1.3 §15)

> **[실측 반영 2026-09-18]** `scripts/run_evidence_suite.py`를 실제 runner로 구동하여
> T01~T13 receipt를 발행하고 LEVEL B Evidence ZIP을 산출했다.
> 결과: **13 receipt — pass 12 / skip 1 / fail 0 / error 0, Status COMPLETE.**
> 산출물: `evidence/daon_evidence.zip` + sidecar `evidence/daon_evidence.zip.sha256`.
> 결정성: `--deterministic` 2회 실행 시 **바이트 동일** —
> bundle digest `459777e52bae0f2a47adf95a86a3d6d11320327cc986c5a7732a9454e293553c`.
> 아래 체크는 "self-report"가 아니라 **위 ZIP 내 receipt/artifact/hash**로 뒷받침된다.

- [x] 정확한 build fingerprint 고정 (`product_version`, `build_id`, `artifact SHA-256`)
      — `BuildFingerprint.capture()`가 receipt·manifest 양쪽에 기록. 결정성 모드에서 `captured_at` 고정.
- [x] T01~T02: schema 통과 + `SKILL.md` 실제 생성 receipt
      — T01: fallback analyzer → `_normalize_skill_data` → `SKILL.md` 실생성. T02: REVIEW→APPROVED 체인.
- [x] REVIEW→APPROVED→consumer→tool executor→task artifact 단일 체인 기록
      — T02 receipt에 lifecycle 전이 + consumer 경로 기록.
- [x] T03: baseline/approved 동일조건·반복·순서 통제
      — `EffectExperiment.run_t03` AB/BA 교차, `MIN_PAIRED_TRIALS=3`.
- [x] T04~T07: unseen / regression / restart / reject 포함
      — `run_t04`(unseen) / `run_t05`(regression) / `run_t06`(restart) / `run_t07`(rejected-blocked).
- [x] T08: approval 등급 명시
      — `run_t08(verification_level)` → receipt에 등급 기록 (APPROVED ≠ VERIFIED).
- [x] T10: synthetic marker + `EXTERNAL_PROVIDER_SENT=false` 기록
      — T10 receipt에 trust flags + redaction 결과 기록.
- [x] T09: 제작자 정상 환경 localhost synthetic page
      — T09 synthetic page receipt 발행.
- [x] T12/T13: 해당 기능 존재 시 실행
      — T12: CDP 9222 유지 + origins 축소 + IPC fail-closed 실측 PASS.
        T13: 라이브 사이트 금지 문구 스캔 PASS(잔여 0건).
- [x] P0-5: 라이브 사이트 문구 정정(`portfolio/Test/daon-download/index.html`) + 배포 반영
      — 소스 4개 파일 + 빌드 산출물 `daon-download/index.html` 직접 정정. T13 스캔 잔여 0건.
- [x] P1-2: `remote-allow-origins` 축소 후 **내부 브라우저 기능 회귀 확인**(9222 유지 검증)
      — `electron/main.js` `CDP_ALLOWED_ORIGINS`에서 `*` 제거, 포트 9222 유지. T12 PASS.
- [x] P1-2: `check_auth` 미들웨어 배선 후 무인증 접근 차단 실측
      — Auth 미들웨어 배선 + HOST loopback 기본값. T12 receipt에 `auth_enabled` 기록.
- [x] 최종 판정은 self-report가 아닌 artifact/hash/독립 verifier 기준
      — Evidence ZIP의 per-member SHA-256 + bundle digest sidecar로 검증 가능.

> **미완 항목(정직 기록)**: T11(비공개 자율 경로)은 **SKIP** — 공개 범위에서 입증 불가.
> 이는 "실패"가 아니라 "미입증"이며, 최종 판정에서 Coverage=PARTIAL로 명시한다.
> 또한 T03~T08 효과 실험은 **합성 runner** 기반이므로, 실제 모델 대상 재현은 별도 과제로 남긴다.
