# 계획: 승인 요청 시 챗창 멈춤/타임아웃 문제 수정

**작성일:** 2026-08-07
**상태:** 대표님 승인 대기

---

## 1. 증상

대표님 보고: "승인 요청이 뜨면 → 응답대기시간 초과 표시 → 챗창이 안 돌아옴"

이번 세션에서 실제로 2회 재현됨 (버블 idx 11, 17에 `[응답 대기 시간 초과]` + `[실행 취소됨]` 흔적 확인,
도구 그룹 카드 다수가 중간 상태("3/5 완료" 등)에서 얼어붙음 = SSE 이벤트 수신 중단 증거).

---

## 2. 근본 원인 (코드 검증 완료)

### 원인 A — 일반 챗의 위험 명령 승인 흐름이 "죽은 길" (핵심)

| 단계 | 코드 위치 | 동작 |
|---|---|---|
| 1 | `hermes-agent/tools/approval.py:857-862` | gateway 승인 콜백(`notify_cb`) 조회 |
| 2 | `api/api/dynamic_jobs.py:271` | **콜백 등록은 다이나믹 하네스 잡만 함** — 일반 챗 스트리밍은 등록 안 함 |
| 3 | `tools/approval.py:964-980` | 콜백 없으면 폴백: `submit_pending` 후 에이전트에 `approval_required` **즉시 반환** (블록 안 함) |
| 4 | `static/modules/approval.js` 폴링 | `/api/approval/pending`에서 pending 발견 → **사용자에게 승인 카드 표시** |
| 5 | `api/routes/admin_routes.py:523-547` | 사용자가 "승인" 클릭 → `resolve_gateway_approval` → **대기 중인 스레드가 없으므로 아무 일도 안 일어남**. 명령은 실행되지 않음 |

결과: 에이전트는 이미 다른 길로 갔는데, 사용자는 승인 카드만 보고 기다리게 됨.
승인해도 명령이 실행되지 않는 죽은 상태 → "챗창이 안 돌아온다" 체감.

### 원인 B — 프론트엔드 idle 타임아웃이 너무 쉽게 "사망" 선언

`static/modules/chat.js:555-605` `_handleIdleTimeout`:

1. 30초 무이벤트 시 `/api/chat/stream/status` 조회
2. **조회 실패(네트워크 오류) 시 `active=false`로 간주** (line 564: `catch (_) { active = false; }`)
   → 백엔드가 살아있어도 사망 판정
3. 세션 복구 시도: 마지막 메시지가 assistant가 아니면(=턴 진행 중) 복구 실패
4. `[응답 대기 시간 초과]` 표시 후 `finishStream('idle_timeout')` → **SSE 영구 포기**
5. 재연결 시도 없음 — 백엔드 STREAMS가 아직 살아있는데도 (`chat_routes.py:69` 정상 서빙 가능)

### 원인 C — 백엔드 하트비트가 JS에 안 보임

`chat_routes.py:121-128`: 큐가 15초 비면 `: heartbeat` 코멘트 전송.
SSE 스펙상 `:` 시작 코멘트는 **EventSource가 어떤 이벤트로도 디스패치하지 않음**
→ 연결은 유지되지만 프론트 idle 타이머는 리셋되지 않음.
승인 대기/긴 작업 중 "보이는" 이벤트가 없으면 원인 B 경로로 빠짐.

### 원인 D — 새 메시지 전송이 진행 중 턴을 취소

`api/routes/chat_routes.py:167-170`: `handle_post_chat_start`가
`cancel_session_streams(session_id)` 호출 → 진행 중 에이전트 작업 강제 취소.
대표님이 "안 돌아온다"고 느끼고 새 메시지를 보내면 → `[실행 취소됨]` → 작업 손실.

---

## 3. 수정 계획

### Phase 1 — 일반 챗 승인 흐름 살리기 (원인 A) ★핵심

**백엔드 (`api/api/streaming.py`):**
1. 에이전트 실행 전 `register_gateway_notify(session_id, cb)` 등록, 종료 시 unregister
   (dynamic_jobs.py:256-271, 363-364 패턴 그대로 차용)
2. `cb(approval_data)`: 해당 세션의 활성 스트림 큐에
   `('cmd_approval', {command, description, pattern_key, session_id})` 이벤트 put

**프론트엔드 (`static/modules/chat.js` + `approval.js`):**
3. `sse.addEventListener('cmd_approval', ...)` 추가:
   - 인라인 승인 카드 표시 (승인 1회 / 세션 / 항상 / 거절)
   - 카드 표시 중 `setChatStatus('thinking', '승인 대기 중...')` + **idle 타이머 일시 정지** (`_approvalPending = true` 플래그)
4. 버튼 클릭 → `POST /api/approval/respond` (기존 엔드포인트 재사용, admin_routes.py:523)
   → `resolve_gateway_approval`가 블록된 에이전트 스레드 깨움 → 명령 실행 후 스트림 계속

**결과:** 승인 카드가 뜨면 에이전트가 실제로 기다리고, 승인 시 명령 실행 후 대화가 이어짐.
거절 시 "BLOCKED" 메시지가 에이전트에 전달되어 우회 경로 탐색.
(이미 `tools/approval.py:894-930`에 300초 블록 + 10초 단위 activity heartbeat 구현 존재)

### Phase 2 — 프론트엔드 타임아웃 견고화 (원인 B, C)

**`static/modules/chat.js` `_handleIdleTimeout` 수정:**
1. 상태 조회 실패 시 사망 판정 대신 **대기 연장** (최대 연장 횟수 내에서 재시도)
2. 복구 실패(턴 진행 중) 시 포기하지 말고 **같은 stream_id로 EventSource 재연결** 시도
   (백엔드가 STREAMS/_COMPLETED_STREAMS/_CANCELLED_STREAMS로 재연결 이미 지원)
   — 최대 3회, 실패 시에만 타임아웃 메시지
3. `cmd_approval`/`approval` 이벤트 수신 시 idle 타이머 정지 (승인 대기 중 타임아웃 원천 차단)

**백엔드 (`chat_routes.py`):**
4. 15초 heartbeat 코멘트 대신 `('heartbeat', {})` 실제 이벤트 전송 (JS에서 수신 가능)
   → 프론트가 수신 시 조용히 idle 타이머만 리셋 (UI 노이즈 없음)

### Phase 3 — 자동 취소 인지 (원인 D)

1. `handle_post_chat_start`에서 기존 스트림 취소 발생 시,
   새 스트림의 첫 이벤트로 `('notice', {text: '이전 진행 중 작업이 새 메시지로 취소되었습니다'})` 전송
2. 프론트: notice 이벤트 수신 시 토스트 표시
   (취소 자체는 현행 유지 — 큐잉 방식은 별개 대형 변경이라 이번 범위에서 제외)

---

## 4. 변경 파일 목록

| 파일 | 변경 내용 |
|---|---|
| `api/api/streaming.py` | gateway notify 콜백 등록/해제 + cmd_approval 이벤트 방출 (Phase 1) |
| `api/api/routes/chat_routes.py` | heartbeat를 실제 이벤트로 (Phase 2), 취소 notice (Phase 3) |
| `dist_new/static/modules/chat.js` | cmd_approval 핸들러, idle 타임아웃 재연결/재시도, 승인 대기 타이머 정지 (Phase 1, 2) |
| `dist_new/static/modules/approval.js` | cmd_approval 카드 렌더링 헬퍼 (Phase 1) |

> 소스 수정 후 대표님 확인 하에 재배포/재시작 필요 (dist_new가 실사용 경로).

---

## 5. 테스트 계획

1. **승인 흐름:** 일반 챗에서 위험 명령(인라인 python 스크립트 등) 실행 시도
   → 승인 카드 표시 확인 → "1회 승인" 클릭 → 명령 실행 + 응답 계속되는지 확인
   → "거절" 클릭 → 에이전트가 BLOCKED 인지 후 우회하는지 확인
2. **타임아웃 억제:** 승인 카드 표시 후 60초+ 대기 → 타임아웃 메시지 안 뜨는지 확인
3. **재연결:** SSE 스트림 중 네트워크 흔들림 simulation (긴 작업 중) → 재연결 후 이벤트 복귀 확인
4. **취소 인지:** 진행 중 새 메시지 전송 → 토스트 표시 확인

---

## 6. 범위 외 (참고)

- 빈 assistant 버블 (0자 메시지): 도구만 실행한 턴의 정상 부산물 — 도구 카드가 표시되므로 cosmetic, 이번 미수정
- 새 메시지 큐잉 (취소 대신 대기): 설계 결정 필요 — 추후 별도 논의
