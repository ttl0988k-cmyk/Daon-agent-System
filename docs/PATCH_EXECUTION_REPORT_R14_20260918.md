# DAON R14 감사 대응 — 패치 실행 보고서

- 작성일: 2026-09-18
- 근거 문서:
  - `DAON_PUBLIC_SCOPE_FINAL_VERIFICATION_REPORT_20260918_R3.docx` (외부 감사 R4~R14)
  - `DAON_VERIFICATION_SELECTION_MUTUAL_IP_PROTECTION_SPEC_V1_3_20260918.txt` (재검증 명세 v1.3)
- 결정서: [`docs/PATCH_PLAN_AUDIT_R14_20260918.md`](PATCH_PLAN_AUDIT_R14_20260918.md)
- 목적: 결정서에 따라 **실제로 수행한 패치 전체**를 코드 근거·테스트·실측 결과와 함께 기록한다.

---

## 0. 한 줄 요약

감사가 지목한 R14 `FAIL - FINAL_GENERATION_CONTRACT_FAILURE`의 직접 원인을 **코드로 봉합**하고,
T01~T13 receipt 발행기와 효과 실험 하네스를 붙여 **재검증 가능 상태**로 만들었다.
실측 결과 **13 receipt — pass 12 / skip 1 / fail 0**, 결정성 ZIP digest `d440a477…` (2회 바이트 동일),
전체 소유 테스트 **169 passed**.

| 항목 | 값 |
|---|---|
| 단계 | 8단계 (P0 5건 → P1-2 → 계측 → 효과실험 → 실측 → 빌드) |
| 신규 모듈 | 4개 (`sensitive_redaction`, `evidence_receipt`, `effect_experiment`, `run_evidence_suite`) |
| 신규 테스트 파일 | 7개 |
| 테스트 | **169 passed** |
| Evidence ZIP | `evidence/daon_evidence.zip` + `.sha256` sidecar |
| 결정성 digest | `d440a4775f6d186769146abe774475aba80621f06a5db71931b7c70836ae06aa` |
| 빌드 산출물 | `dist/DAON Agent System Setup 1.0.0.exe` (360,105,895 bytes ≈ 343.4 MB) |
| 설치기 SHA-256 | `ED29D14A33E8A62E…` (전체: `ED29D14A33E8A62E`로 시작, 2026-09-18 23:58:32 생성) |
| 백엔드 `server.exe` | 211,170,086 bytes, SHA-256 `F7C8B7E65466A87E…` (2026-09-18 23:56:30 생성) |
| 빌드 파이프라인 | 3단계: `_sync_build.py` → `pyinstaller daon-server.spec` → `npm run build` |

---

## 1. [1단계] P0-1 — Demo-to-Skill 계약 하드닝 ★R14 FAIL 직접 원인

### 문제 (소스 확정)

- [`analyze()`](../api/api/demo_to_skill.py:1035)는 `"frontmatter"` 키 **존재만** 검사한다.
- [`write()`](../api/api/demo_to_skill.py:1566)는 `body`가 `str`이라고 **무조건 가정**한다.
- 따라서 `body`가 `dict`/`list`/`None`이면 **`TypeError` 확정**.

### 패치 (3중 방어)

| 계층 | 함수 | 역할 |
|---|---|---|
| 정규화 | [`_coerce_body_to_text()`](../api/api/demo_to_skill.py:1491) | body 5종 타입(dict/list/None/str/int)을 안전하게 텍스트로 강제 변환 |
| 봉투 | [`_normalize_skill_data()`](../api/api/demo_to_skill.py:1522) | `{"frontmatter": dict, "body": str, "_meta": {...}}` 정규화 봉투 생성 |
| writer | [`SkillWriter.write()`](../api/api/demo_to_skill.py:1566) | body 타입에 **독립적**으로 동작하도록 수정 |

### 테스트

- [`api/tests/test_demo_to_skill_contract.py`](../api/tests/test_demo_to_skill_contract.py:1) — **20건 통과**

### 정직 기록

A1은 **실재하는 잠재 결함**이다. 제품 진입점 [`analyze_text_workflow()`](../api/api/demo_to_skill.py:1887)에서는
`analyze()`가 실패 시 [`_fallback_skill()`](../api/api/demo_to_skill.py:1314)을 반환하므로 우연히 회피되나,
**무결성은 없다**. 수정은 하되 "실제 제품에서 실패하는 버그"라고는 단정하지 않는다.

---

## 2. [1.5단계] P0-5 — 라이브 사이트 허위 문구 정정

### 문제

라이브 공개 사이트에 구현에 없는 주장이 노출되어 **법적 리스크**가 있었다.

- `E2EE`, `P2P 암호화`, `자기진화 시스템`, `0.1ms 보장`, `1.4 GB/s`, `daon.vault`/`daon.crypto` import

### 패치

- 소스 4개 파일 정정
- **빌드 산출물** [`../portfolio/Test/daon-download/index.html`](../portfolio/Test/daon-download/index.html:2130) 직접 정정
  - `1.4 GB/s` → `Local loopback`
  - `자기진화 시스템` → `Skill 기반 보강 구조`

> ⚠️ **중요**: `daon-download/index.html`은 `stitch-preview/`에서 재생성되는 **빌드 산출물**이다.
> 소스만 고치면 라이브에 반영되지 않으므로 산출물도 직접 수정했다.

### 검증

- T13 라이브 사이트 금지 문구 스캔 — **잔여 0건**

---

## 3. [2단계] P0-4 — 민감정보 단일 관문 (Redaction)

### 패치

- [`api/api/sensitive_redaction.py`](../api/api/sensitive_redaction.py:1) 신규 — **단일 choke-point**
- T10 trust flags 배선:
  `DOM_CAPTURED / LOCAL_BRIDGE / MODEL_PROMPT / PERSISTED_SKILL / LOGGED / EXTERNAL_PROVIDER_SENT`

### 테스트

- [`api/tests/test_sensitive_redaction.py`](../api/tests/test_sensitive_redaction.py:1) — **19건 통과**

---

## 4. [3단계] P0-3 — REJECTED lifecycle 강제

### 문제

[`load_skills()`](../api/api/skill_registry.py:705)에 lifecycle 필터가 **전무**하여
거부된 Skill이 정상 경로에서 실행될 수 있었다.

### 패치

- `include_rejected` 게이트 추가 (기본 `False`)
- 필터 적용 지점:
  - [`get_catalog_text()`](../api/api/skill_registry.py:658)
  - [`load_skills()`](../api/api/skill_registry.py:705)
  - [`load_skills_for_reviewer()`](../api/api/skill_registry.py:746)
  - [`get_skill()`](../api/api/skill_registry.py:777)

### 테스트

- [`api/tests/test_skill_lifecycle_gate.py`](../api/tests/test_skill_lifecycle_gate.py:1) — **15건 통과**

---

## 5. [4단계] P0-2 — Approval verification_level (A~E 등급)

### 문제

[`promote_skill()`](../api/api/skill_registry.py:828)에 expected-output/verifier 호출이 없어
**"APPROVED ≠ VERIFIED"** 불변식이 위반되었다.

### 패치

- 등급 정의:

  | 등급 | 이름 | 의미 |
  |---|---|---|
  | A | `LIFECYCLE_ONLY` | lifecycle 전이만 |
  | B | `STATIC_VALIDATION` | 정적 검증 |
  | C | `RUNTIME_SELF_TEST` | 런타임 자체 시험 |
  | D | `EXPECTED_OUTPUT_VALIDATION` | 기대 출력 검증 |
  | E | `INDEPENDENT_VERIFICATION` | 독립 검증 |

- [`promote_skill()`](../api/api/skill_registry.py:828)에 `verification_level` 파라미터 + manifest 기록

### 테스트

- [`api/tests/test_approval_verification_level.py`](../api/tests/test_approval_verification_level.py:1) — **20건 통과**

---

## 6. [5단계] P1-2 — CDP / IPC / Auth 경계 강화

### 문제 3건

1. CDP `remote-allow-origins`에 `*` → 넓은 권한
2. IPC installer path 검증 없음
3. [`check_auth()`](../api/api/auth.py:130)가 **어디서도 호출되지 않음** (배선 0건) + HOST 기본값 `0.0.0.0`

### 패치

| 대상 | 파일 | 변경 |
|---|---|---|
| CDP origins | [`electron/main.js`](../electron/main.js:29) | `CDP_ALLOWED_ORIGINS`에서 `*` 제거, **포트 9222 유지** |
| IPC | [`electron/src/IpcHandlers.js`](../electron/src/IpcHandlers.js:29) | `validateInstallerPath()` fail-closed 검증 |
| Auth | [`api/api/auth.py`](../api/api/auth.py:1) | 미들웨어 **신규 배선** |
| 바인딩 | [`api/api/config.py`](../api/api/config.py:1) | HOST 기본값 loopback |

> ⚠️ **초판 계획의 "CDP 기본 비활성"은 제품을 파손**하므로 "origins 축소"로 교체했다.
> CDP 9222는 내부 브라우저 에이전트의 핵심 드라이버이므로 반드시 유지한다.

### 테스트

- [`api/tests/test_p1_2_boundary_hardening.py`](../api/tests/test_p1_2_boundary_hardening.py:1) — **24건 통과**

---

## 7. [6단계] T01~T13 Receipt 발행기

### 패치

- [`api/api/evidence_receipt.py`](../api/api/evidence_receipt.py:1) 신규
  - `BuildFingerprint` — `product_version` / `build_id` / `artifact SHA-256`
  - `Receipt` — test_id / status / evidence / notes / fingerprint
  - `EvidenceReceiptEmitter` — 수집 + LEVEL B Evidence ZIP 발행
  - per-member SHA-256 + bundle digest sidecar

### 테스트

- [`api/tests/test_evidence_receipt.py`](../api/tests/test_evidence_receipt.py:1) — **30건 통과**

---

## 8. [7단계] P2-1 — 효과 실험 (T03~T08)

### 패치

- [`api/api/effect_experiment.py`](../api/api/effect_experiment.py:1) 신규 — stdlib-only, AB/BA 교차, fail-closed verdict

  | 테스트 | 메서드 | 내용 |
  |---|---|---|
  | T03 | [`run_t03()`](../api/api/effect_experiment.py:314) | baseline/approved 동일조건·반복·순서 통제 |
  | T04 | [`run_t04()`](../api/api/effect_experiment.py:342) | unseen 일반화 |
  | T05 | [`run_t05()`](../api/api/effect_experiment.py:369) | regression |
  | T06 | [`run_t06()`](../api/api/effect_experiment.py:397) | restart 지속성 |
  | T07 | [`run_t07()`](../api/api/effect_experiment.py:416) | rejected 차단 |
  | T08 | [`run_t08()`](../api/api/effect_experiment.py:431) | verification level 기록 |

### 테스트

- [`api/tests/test_effect_experiment.py`](../api/tests/test_effect_experiment.py:1) — **41건 통과**

---

## 9. [8단계] 실측 실행 + 결정성 결함 수정

### 패치

- [`scripts/run_evidence_suite.py`](../scripts/run_evidence_suite.py:1) 신규 — 실제 파이프라인 구동 runner
- [`_extract_origins_value()`](../scripts/run_evidence_suite.py:344) — T12 오탐 수정
  (기존 `_extract_origins()`가 `remote-allow-origins` 이후 200자 윈도우를 잘라 **주석 속 `*`를 오탐**)

### 결정성 결함 3건 (모두 실측으로 발견)

`--deterministic`이 바이트 동일 ZIP을 내지 못하던 진짜 원인:

| # | 결함 | 원인 | 수정 |
|---|---|---|---|
| 1 | receipt `fingerprint.captured_at` 미고정 | [`BuildFingerprint.capture()`](../api/api/evidence_receipt.py:100)가 wall-clock을 박고, 이 값이 모든 receipt에 임베드됨 | [`write_bundle()`](../api/api/evidence_receipt.py:257)에서 고정 |
| 2 | temp workdir 랜덤 경로 | `tempfile.TemporaryDirectory(prefix="daon_evidence_")`의 랜덤 접미사가 T01 evidence의 `skill_path`에 박힘 | [`main()`](../scripts/run_evidence_suite.py:359)에서 `--deterministic` 시 고정 경로 사용 |
| 3 | manifest `fingerprint.captured_at` 미고정 | manifest가 자체 fingerprint 사본을 임베드 | `manifest_fingerprint` 별도 고정 |

### 회귀 테스트

- [`test_deterministic_pins_fingerprint_captured_at`](../api/tests/test_evidence_receipt.py:250) 추가
  - 기존 결정성 테스트들은 fingerprint를 `captured_at="FIXED"`로 **미리 얼려둬서** 이 버그를 못 잡았다.
  - 신규 테스트는 **서로 다른** captured_at을 넣고 바이트 동일 + sentinel 고정을 검증한다.

### 실측 결과

| 항목 | 값 |
|---|---|
| receipt 수 | 13 (T01~T13) |
| pass / skip / fail / error | **12 / 1 / 0 / 0** |
| Status | **COMPLETE** |
| 산출물 | `evidence/daon_evidence.zip` + `evidence/daon_evidence.zip.sha256` |
| 결정성 digest | `d440a4775f6d186769146abe774475aba80621f06a5db71931b7c70836ae06aa` (2회 바이트 동일) |

---

## 10. 변경 파일 요약

| 파일 | 상태 | 내용 |
|---|---|---|
| [`api/api/demo_to_skill.py`](../api/api/demo_to_skill.py:1) | 수정 | P0-1 계약 하드닝 |
| [`api/api/sensitive_redaction.py`](../api/api/sensitive_redaction.py:1) | 신규 | P0-4 |
| [`api/api/skill_registry.py`](../api/api/skill_registry.py:1) | 수정 | P0-3 + P0-2 |
| [`api/api/auth.py`](../api/api/auth.py:1) | 수정 | P1-2 배선 |
| [`api/api/config.py`](../api/api/config.py:1) | 수정 | HOST loopback |
| [`api/api/evidence_receipt.py`](../api/api/evidence_receipt.py:1) | 신규 | 6단계 |
| [`api/api/effect_experiment.py`](../api/api/effect_experiment.py:1) | 신규 | 7단계 |
| [`electron/main.js`](../electron/main.js:29) | 수정 | CDP origins |
| [`electron/src/IpcHandlers.js`](../electron/src/IpcHandlers.js:29) | 수정 | IPC fail-closed |
| [`scripts/run_evidence_suite.py`](../scripts/run_evidence_suite.py:1) | 신규 | 8단계 runner |
| [`../portfolio/Test/daon-download/index.html`](../portfolio/Test/daon-download/index.html:2130) | 수정 | P0-5 문구 |
| 테스트 7개 파일 | 신규 | 169건 |
| [`docs/PATCH_PLAN_AUDIT_R14_20260918.md`](PATCH_PLAN_AUDIT_R14_20260918.md:1) | 수정 | §6 실측 반영 |

---

## 11. 정직 기록 (감사 대응상 필수)

- **T11(비공개 자율 경로) = SKIP** — 공개 범위에서 입증 불가.
  "실패"가 아니라 **"미입증"**이며, 최종 판정에서 `Coverage=PARTIAL`로 명시한다.
- **T03~T08 효과 실험은 합성 runner 기반** — 실제 모델 대상 재현은 별도 과제로 남긴다.
- **P0-1은 잠재 결함 수정** — 제품 경로에서는 우연히 회피되나 무결성은 없다.
- **P0-5는 빌드 산출물까지 직접 수정** — 소스만 고치면 라이브에 반영되지 않는다.

---

## 12. 검증 방법

```bash
# 전체 소유 테스트 (169 passed)
python -m pytest \
  api/tests/test_demo_to_skill_contract.py \
  api/tests/test_sensitive_redaction.py \
  api/tests/test_skill_lifecycle_gate.py \
  api/tests/test_approval_verification_level.py \
  api/tests/test_p1_2_boundary_hardening.py \
  api/tests/test_evidence_receipt.py \
  api/tests/test_effect_experiment.py -q

# 실측 + Evidence ZIP (13 receipts, pass 12 / skip 1 / fail 0)
python scripts/run_evidence_suite.py --deterministic

# 결정성 확인 (2회 실행 후 digest 비교)
python scripts/run_evidence_suite.py --deterministic
# → digest d440a4775f6d186769146abe774475aba80621f06a5db71931b7c70836ae06aa
```

---

## 12-A. 빌드 (3단계 파이프라인)

### 왜 3단계인가 (중요)

`npm run build`는 **electron-builder만** 실행한다. 백엔드 `server.exe`는 PyInstaller onefile 번들이며
electron-builder가 **생성하지 않는다** — `package.json`의 `extraResources`에서 `"from": "dist/server.exe"`로
**입력**으로 소비된다. 따라서 `server.exe`를 먼저 빌드하지 않으면 **stale 바이너리가 그대로 복사**된다.

> 이전 시도에서 `npm run build`만 실행하고 "빌드 성공"이라 보고했으나, `dist/server.exe`가
> 새벽 빌드(12:29) 그대로여서 **오보**였다. 아래 3단계를 순서대로 실행해야 한다.

### 실행 순서

```powershell
# [0] 소스 → dist_new/ 스테이징 (spec의 datas가 dist_new/를 참조)
python _sync_build.py

# [1] 백엔드 빌드 — 우리 Python 패치가 들어가는 server.exe (onefile, slim)
pyinstaller daon-server.spec --noconfirm --clean

# [2] 프론트 + 설치기 — 새 server.exe를 extraResources로 포함
npm run build
```

### 이번 빌드 실측 (2026-09-18)

| 산출물 | 크기 (bytes) | 생성 시각 | SHA-256 (앞 16) |
|---|---|---|---|
| `dist/server.exe` | 211,170,086 | 23:56:30 | `F7C8B7E65466A87E` |
| `dist/win-unpacked/resources/server.exe` | 211,170,086 | 23:56:30 | `F7C8B7E65466A87E` ✅ |
| `dist/DAON Agent System Setup 1.0.0.exe` | 360,105,895 | 23:58:32 | `ED29D14A33E8A62E` |
| `dist/win-unpacked/resources/app.asar` | 135,384 | 23:57:08 | `02A69CD2767DBD44` |
| `C:\daon\DAON-Portable\resources\server.exe` | 211,170,086 | 23:56:30 | `F7C8B7E65466A87E` ✅ |
| `%LOCALAPPDATA%\Programs\daon-agent-system\resources\server.exe` | 211,170,086 | 23:56:30 | `F7C8B7E65466A87E` ✅ |

- `afterPack`의 4개 sync(app.asar + server.exe → Portable/설치본) **모두 성공** (EBUSY 없음).
- **검증 포인트**: `server.exe`의 SHA-256이 `dist`·`win-unpacked`·Portable·설치본 **4곳 모두 동일** →
  패치된 백엔드가 실제 배포 트리에 반영됐음을 해시로 입증.
- 이전 stale 값(server.exe 12:29 / installer 11:43)과 **완전히 상이** → 이번 빌드가 실제로 재생성됨.

---

## 13. 최종 판정 기준 (명세 v1.3 §15 준수)

최종 판정은 **self-report가 아니라 artifact/hash/독립 verifier 기준**으로 한다.

- Evidence ZIP의 **per-member SHA-256** + **bundle digest sidecar**로 검증 가능
- `Coverage=PARTIAL`, `Closure=NEEDS_EVIDENCE` (T11 미입증 + 효과 실험 합성 runner 한계)
- "DAON 전체 FAIL"이 아니라 **"고정 revision + 로컬 모델 2종에서 재현된 단일 계약경계 실패"**로 한정
