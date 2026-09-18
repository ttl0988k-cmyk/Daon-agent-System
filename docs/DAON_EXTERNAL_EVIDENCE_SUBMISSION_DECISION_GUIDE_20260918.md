# DAON 외부 Evidence 제출 선택 가이드

> 제작자 선택권 보장 · LEVEL B 외부 재검증 · 상호 IP 보호
>
> - 작성일: 2026-09-18
> - 용도: DAON이 제작자에게 제출 여부를 설명·권고하기 위한 운영 문서
> - 원본: `DAON_EXTERNAL_EVIDENCE_SUBMISSION_DECISION_GUIDE_20260918.docx`

---

## 0. 요약 (현재 확인된 사실)

| 구분 | 내용 |
|---|---|
| **현재 확인된 사실** | 제작자측 실행보고서는 `evidence/daon_evidence.zip` 및 `.sha256` 생성, **13 receipts, 12 pass / 1 skip, 169 tests passed**를 보고함. |
| **현재 외부검증 상태** | 해당 두 원본 파일은 아직 외부 검증측에 제출되지 않았으므로, 위 결과는 현재 **CREATOR_REPORTED** 상태임. |
| **중요 한계** | 제작자측 보고서 자체가 **T11=SKIP, T03~T08=합성 runner 기반**이라고 명시함. 실제 모델 기반 효과와 자율진화는 아직 독립검증되지 않음. |
| **최종 결정권** | Evidence 제출 여부는 **제작자가 결정**한다. DAON은 검사·설명·권고만 수행하며 임의 제출하지 않는다. |

---

## 1. 목적과 운영 원칙

- 제작자는 두 파일을 외부 검증측에 **제출할지, 제출하지 않을지 스스로 선택**한다.
- DAON은 먼저 파일의 **안전성을 검사**하고, 제출 시 **얻는 것·위험·얻지 못하는 것**을 제작자에게 쉽게 설명한다.
- DAON은 **권고안을 제시**할 수 있으나 최종 선택을 대신하지 않으며, **외부 전송도 자동 실행하지 않는다**.
- 제출을 선택하더라도 원본을 바로 보내지 않고 **IP·secret·private path·영업비밀 검사를 통과한 패키지만** 제출 후보로 삼는다.

---

## 2. 선택 A — 두 Evidence 파일을 외부 검증측에 제출

### 2.1 제출 시 얻는 것

- 제작자 자기보고에서 한 단계 올라가, 제출된 Evidence 범위에 대해 **외부 검증측의 독립 재검산**을 받을 수 있다.
- **ZIP 자체 무결성, sidecar SHA-256, manifest/member hash, T01~T13 receipt 존재 여부와 상태**를 대조할 수 있다.
- 보고서의 **"169 tests passed", "12 pass / 1 skip", bundle digest** 등의 주장이 실제 제출 산출물과 일치하는지 확인할 수 있다.
- **T01 생성 산출물, T02 lifecycle/consumer 기록, T10 redaction·trust flag, T11 SKIP** 등의 사실관계를 receipt와 artifact 기준으로 확인할 수 있다.
- **T03~T08이 실제 모델 기반인지 합성 runner 기반인지 구분**하여 과장된 PASS를 방지할 수 있다.
- 기술적 관계 측면에서는 **"지적 → 수정 → Evidence 제출 → 외부 재검증"의 폐쇄루프**를 만들 수 있어 신뢰도를 높일 수 있다.

### 2.2 제출 시 위험

- ZIP에 **비공개 source, 실제 secret, 개인 경로, 내부 hostname/URL, 실제 사용자 데이터**가 섞여 있으면 정보 노출 위험이 있다.
- **generated Skill, 상세 stack trace, 내부 디렉터리 구조, 원시 prompt/log**가 영업비밀이나 내부 구현 정보를 과도하게 드러낼 수 있다.
- 결과 자체가 안전해도 **구조화 receipt가 제품 내부 구조를 일부 추정**하게 할 수 있으므로 "최소 필요 Evidence" 원칙이 필요하다.
- 외부 제출 후에는 **파일 회수 가능성을 전제로 해서는 안 된다.** 따라서 제출 전 **sanitization이 필수**다.

### 2.3 제출해도 얻지 못하는 것

- DAON **전체 소스의 안전성**, 모든 hidden path 또는 backdoor의 부재를 증명하지 못한다.
- **비공개 서버 전체, 모든 provider, 모든 실제 사용자 환경**에서의 정상 동작을 보증하지 못한다.
- **T11이 SKIP인 한 autonomous self-evolution은 입증되지 않는다.**
- **T03~T08이 합성 runner 기반인 한 실제 모델에서의 일반적 작업능력 향상을 그대로 증명하지 못한다.**
- Evidence ZIP의 **hash 일치는 바이트 무결성을 보여줄 뿐**, Evidence 생성기 자체의 완전한 정직성까지 보증하지 않는다.
- 외부 재검증은 **제출된 build·환경·Evidence 범위에 한정**되며 제품 전체 PASS 또는 보안 인증이 아니다.

---

## 3. 선택 B — 두 Evidence 파일을 제출하지 않음

### 3.1 얻는 것

- 외부로 전달되는 데이터가 줄어들어 **IP·내부정보·영업비밀의 노출 가능성을 가장 낮출 수 있다.**
- generated artifact나 내부 환경정보가 외부로 나가지 않으므로 **정보통제권을 최대한 유지**할 수 있다.

### 3.2 잃는 것

- 새 패치가 실제로 적용됐는지, **169 tests와 12/1 결과가 맞는지 외부 검증측이 독립 확인할 수 없다.**
- 결과 상태는 제작자 자체검증(**CREATOR_REPORTED**) 또는 **NEEDS_EVIDENCE**에 머물게 된다.
- "외부에서 receipt/artifact/hash까지 확인했다"는 **추가 신뢰를 얻지 못한다.**
- 수정 전 감사와 수정 후 상태를 독립적으로 연결해 **결함을 CLOSED 처리하기 어렵다.**

### 3.3 제출하지 않아도 유지되는 것

- 기존 **R4~R14에서 이미 공개 소스·기존 Evidence로 확인된 사실**은 그대로 유지된다.
- 제작자측 내부 QA 결과 자체는 **내부 의사결정에 사용**할 수 있다. 다만 **외부 독립검증 결과로 표현하지 않는다.**

---

## 4. 제출을 선택했을 때 DAON의 필수 사전검사

> 다음 항목을 **모두 확인하기 전에는 원본 ZIP을 외부로 보내지 않는다.**

- [ ] ZIP 정상 개방 및 path traversal/symlink 이상 없음
- [ ] `.sha256` sidecar와 실제 ZIP SHA-256 일치
- [ ] manifest가 있으면 전체 member hash 일치
- [ ] T01~T13 receipt 존재 여부, PASS/SKIP/ERROR 실제 수치 확인
- [ ] 비공개 source, `.git`, `node_modules`, `venv`, private branch/file 포함 여부
- [ ] API key, token, password, OTP, cookie/session, credential 패턴 검사
- [ ] 실제 사용자명·이메일·개인경로·private path·내부 hostname/IP/URL 검사
- [ ] `.env`, private config, 원시 prompt/log, source-containing stack trace 검사
- [ ] generated Skill 또는 artifact의 영업비밀 가능성 확인
- [ ] T03~T08 synthetic runner 한계와 T11 SKIP이 receipt/README에서 숨겨지지 않았는지 확인
- [ ] **T13 receipt가 원래 T13 Mobile/RLS/Realtime/E2EE/P2P 검증과 다른 단순 콘텐츠 스캔을 PASS로 재사용하지 않았는지 확인**
- [ ] 패치 build의 build_id / artifact SHA-256 / source snapshot identifier가 이전 공개 revision과 명확히 구분되는지 확인

### 검사 결과 분류

| 상태 | 의미 |
|---|---|
| **SAFE_TO_SHARE** | 현재 패키지를 그대로 제출해도 되는 수준. 그래도 제작자 최종 승인이 필요. |
| **SAFE_TO_SHARE_AFTER_SANITIZATION** | 민감·불필요 정보 제거 또는 비식별화 후 제출 가능. |
| **DO_NOT_SHARE_CURRENT_PACKAGE** | source/secret/private data/영업비밀 등 위험이 있어 현재 패키지 제출 금지. |

---

## 5. DAON이 제작자에게 보고하는 방식

- 두 파일을 **실제로 읽고 안전성 검사를 수행**한다. 파일을 받지 못했다면 **"미수신"으로 보고하고 추정하지 않는다.**
- 검사 결과를 **SAFE_TO_SHARE / SAFE_TO_SHARE_AFTER_SANITIZATION / DO_NOT_SHARE_CURRENT_PACKAGE** 중 하나로 제시한다.
- **제출 시 얻는 검증 수준**과, **제출해도 여전히 남는 한계**를 분리해서 설명한다.
- **제출하지 않을 때 보호되는 정보**와, **대신 잃는 외부 신뢰 수준**을 설명한다.
- 권고안은 제시하되 **"제출/미제출" 최종 선택은 제작자가** 하도록 한다.
- 제작자가 제출을 선택한 뒤에만 **sanitized Evidence 패키지를 외부 검증측에 전달**한다.

---

## 6. 제작자에게 보여줄 최종 보고 형식

| 보고 항목 | 작성 내용 |
|---|---|
| 현재 상태 | Evidence 파일 수신 여부 / 안전검사 완료 여부 |
| 공유 가능성 | `SAFE_TO_SHARE` \| `SAFE_TO_SHARE_AFTER_SANITIZATION` \| `DO_NOT_SHARE_CURRENT_PACKAGE` |
| 제출하면 얻는 것 | 외부 독립 재검산으로 올라가는 신뢰 수준을 쉬운 말로 설명 |
| 제출 위험 | IP/secret/private path/영업비밀 등 실제 발견 여부와 위험 수준 |
| 제출해도 못 얻는 것 | 전체 제품 PASS, 자율진화, 실제 모델 효과, 전체 보안 인증 등 범위 밖 명시 |
| 제출하지 않으면 얻는 것 | IP 통제 강화 |
| 제출하지 않으면 못 얻는 것 | 새 패치·receipt·artifact의 외부 독립검증 |
| DAON 권고 | `SHARE_SANITIZED_EVIDENCE` \| `DO_NOT_SHARE` \| `CREATOR_CHOICE_BALANCED` |
| 제작자 결정 필요 | 예. "안전검사 완료 후 Evidence를 외부 검증측에 제출할까요?" |

---

## 7. DAON 내부 판정 출력 권장 형식

```json
{
  "evidence_files_received": true,
  "shareability_status": "SAFE_TO_SHARE | SAFE_TO_SHARE_AFTER_SANITIZATION | DO_NOT_SHARE_CURRENT_PACKAGE",
  "recommended_action": "SHARE_SANITIZED_EVIDENCE | DO_NOT_SHARE | CREATOR_CHOICE_BALANCED",
  "external_recheck_value": "LOW | MEDIUM | HIGH",
  "ip_disclosure_risk": "LOW | MEDIUM | HIGH",
  "benefits": ["..."],
  "risks": ["..."],
  "limitations": ["..."],
  "sanitization_required": true,
  "creator_choice_required": true,
  "auto_send_allowed": false
}
```

---

## 8. 권고 원칙

- **기본 권고**: 현재 목적이 외부 감사 대응·수정 결과 증명·기술 신뢰 확보라면, **안전검사를 통과한 sanitized Evidence만 공유하는 LEVEL B 방식**이 가장 균형적이다.
- 단, **IP 보호가 외부 독립 신뢰보다 우선이면 제출하지 않는 선택도 유효**하다. 어느 선택이든 얻는 것과 잃는 것을 명시하고 제작자가 결정한다.
- **금지**: DAON이 제작자 승인 없이 외부로 자동 전송하거나, Evidence 미수신 상태에서 "외부검증 완료"로 보고하거나, 자체 PASS를 독립검증 PASS로 승격하는 행위.
