# Roo Code 채팅 UI → DAON 이식 설계서 (v2)

> 목표: Roo Code(Apache 2.0)의 채팅 UI를 DAON에 이식.
> **프론트는 Roo Code 동작/디자인 그대로 + 백엔드는 DAON(server.py + hermes-agent) 그대로.**

---

## 0. 설계 원칙 (v2 — 리뷰 반영)

1. **ClineMessage 변환기를 핵심으로 두지 않는다.**
   Roo 내부 메시지 구조는 upstream에서 바뀔 수 있으므로, 변환기를 중심에 두면
   Roo 업데이트마다 깨진다. 대신 **Store 중심** 아키텍처를 사용한다.

2. **Context 하나에 몰아넣지 않는다.** Store를 분리한다.

3. **SSE 원본 이벤트를 최대한 살린다.**
   `reasoning` → 즉시 `say(reasoning)` 로 뭉개지 않고,
   내부적으로는 원본 이벤트 의미를 유지하고 **UI 레이어에서만 병합**한다.

4. **Roo 컴포넌트는 최대한 수정하지 않는다.** (가장 중요)
   - ❌ `ChatRow.tsx` 수정
   - ⭕ `ChatRow.tsx` 그대로 사용 + Adapter에서 데이터만 맞춤
   - 그래야 Roo upstream 업데이트 시 `git merge`가 가능하다.
   - 교체는 **Vite alias**로만 수행 (Roo 원본 파일은 바이트 단위 동일 유지).

5. **Normalizer 레이어를 추가한다.**
   토큰 합치기, reasoning 합치기, tool 상태 변경, progress 갱신, done/cancel 처리를
   전부 Normalizer가 담당. UI는 `messages[]`만 렌더링.

---

## 1. 아키텍처 (v2)

```
DAON SSE (원본 이벤트: token / reasoning / tool / approval / done / cancel ...)
      │
      ▼
┌─────────────────────────────────────────────┐
│ DaonBridge                                   │
│  - EventSource 래퍼 (재연결, heartbeat)      │
│  - REST 클라이언트 (send/cancel/approval)    │
└──────────────────┬──────────────────────────┘
                   │ 원본 이벤트 그대로 발행
                   ▼
┌─────────────────────────────────────────────┐
│ Event Bus (typed emitter)                    │
│  - SSE 이벤트명을 그대로 유지                 │
│  - reasoning_delta, tool_start, tool_end 등  │
│    세분화 이벤트도 여기서 정의 가능           │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ Event Normalizer ★ 핵심                      │
│  - 토큰 합치기 (token 누적)                  │
│  - reasoning 합치기                          │
│  - tool 상태 전이 (start→running→done)       │
│  - job progress 갱신                         │
│  - done/cancel/error 종료 처리               │
│  - 세션별 스트림 상태 관리                    │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ Stores (zustand)                             │
│  ├── chatStore     : messages[], streaming   │
│  ├── toolStore     : tool 실행 상태 map      │
│  ├── approvalStore : pending 승인 목록        │
│  └── sessionStore  : 세션 목록, 활성 세션     │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ View-Model Adapter (유일한 Roo 타입 인식 지점)│
│  - Store 데이터 → Roo가 기대하는 props 형태   │
│    (ClineMessage[] 등) 로 변환               │
│  - Roo 내부 타입 변경 시 여기만 수정          │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│ Roo UI 컴포넌트 (무수정)                      │
│  ChatView / ChatRow / ChatTextArea /         │
│  TaskHeader / ToolUseBlock / MarkdownBlock   │
│  - useExtensionState() → 우리 shim           │
│  - vscode.postMessage() → 우리 shim          │
│  (Vite alias로 교체, 원본 파일 미수정)        │
└─────────────────────────────────────────────┘
```

### 1.1 Vite alias 교체 목록 (Roo 원본 무수정 보장)

| Roo 원본 모듈 | DAON shim | 역할 |
|---|---|---|
| `@src/utils/vscode` | `src/daon/shims/vscode.ts` | `postMessage()` → DaonBridge REST 호출 |
| `@src/context/ExtensionStateContext` | `src/daon/shims/ExtensionStateContext.tsx` | `useExtensionState()` → Store 구독 |
| `@src/i18n/TranslationContext` | `src/daon/shims/i18n.tsx` | 다국어 (한국어 기본) |

> Roo 컴포넌트 파일(`components/**`)은 **한 글자도 수정하지 않는다.**
> 모든 DAON 전용 코드는 `src/daon/` 아래에만 존재.

---

## 2. DAON 백엔드 SSE 프로토콜 (기존 분석 유지)

| 이벤트 | 페이로드 | Normalizer 처리 |
|---|---|---|
| `token` | `{text}` | 마지막 text 메시지에 누적 |
| `reasoning` | `{text}` | reasoning 블록에 누적 (접기/펼치기 가능) |
| `tool` | `{name, event, preview, args}` | toolStore 상태 전이 |
| `terminal_output` | `{tool, text}` | 해당 tool의 출력 버퍼에 누적 |
| `job` | `{type, tool, status, duration, tools}` | progress 갱신 |
| `file_edit` / `file_edit_done` | `{name, args}` / `{name, path, content}` | 파일 편집 블록 |
| `diff_preview` | `{preview_id, path, old, new_full}` | diff 블록 |
| `approval` | `{preview_id, type, ...}` | approvalStore 추가 + ask 메시지 |
| `media_result` | `{...}` | 미디어 블록 |
| `heartbeat` | `{}` | idle 타이머 리셋 (메시지 변환 없음) |
| `model_info` / `model_fallback` | `{requested, ...}` | 메타데이터 |
| `compressed` | `{message}` | info 메시지 |
| `speak` | `{text}` | TTS 사이드 이펙트 (메시지 변환 없음) |
| `done` | `{session, usage, job_error}` | 스트림 종료, usage 기록 |
| `cancel` | `{message}` | 스트림 종료 (취소 마크) |
| `error` / `apperror` | `{message, type}` | error 메시지 |
| `notice` | `{...}` | info 메시지 |

**엔드포인트:**
- `POST /api/chat/send` → `{stream_id, session_id}`
- `GET /api/chat/stream?stream_id=` → SSE
- `GET/POST /api/chat/cancel?stream_id=` → 취소
- `POST /api/approval/respond` / `/api/approval/approve` / `/api/approval/reject`
- `GET /api/approval/pending?session_id=`
- `GET /api/sessions`, `POST /api/session/new`, `GET /api/session?session_id=`

---

## 3. 디렉토리 구조

```
webview/
├── package.json
├── vite.config.ts          ← alias 정의 (Roo 모듈 → daon shim)
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── daon/               ★ DAON 전용 코드 (여기만 수정)
    │   ├── bridge/
    │   │   ├── DaonBridge.ts       (EventSource + REST)
    │   │   └── api.ts              (fetch 래퍼)
    │   ├── bus/
    │   │   └── EventBus.ts         (typed emitter)
    │   ├── normalizer/
    │   │   ├── EventNormalizer.ts  (이벤트 → 상태 리듀서)
    │   │   └── reducers.ts
    │   ├── stores/
    │   │   ├── chatStore.ts
    │   │   ├── toolStore.ts
    │   │   ├── approvalStore.ts
    │   │   └── sessionStore.ts
    │   ├── adapter/
    │   │   └── viewModel.ts        (Store → Roo props 변환, 유일한 Roo 타입 인식 지점)
    │   └── shims/
    │       ├── vscode.ts
    │       ├── ExtensionStateContext.tsx
    │       └── i18n.tsx
    └── roo/                ← Roo 원본 복사 (무수정 유지, upstream merge 대상)
        ├── components/chat/...
        ├── components/common/...
        ├── components/ui/...
        ├── utils/...
        ├── hooks/...
        └── lib/...
```

---

## 4. 구현 단계

### Phase 1: 스캐폴드 + DAON 레이어
- [ ] `webview/` Vite+React+TS 프로젝트 생성
- [ ] `src/daon/bridge` 구현 (EventSource + REST)
- [ ] `src/daon/bus` 구현 (typed EventBus)
- [ ] `src/daon/normalizer` 구현 (이벤트 리듀서)
- [ ] `src/daon/stores` 구현 (zustand 4개 store)
- [ ] `src/daon/shims` 구현 (vscode, ExtensionStateContext, i18n)

### Phase 2: Roo 컴포넌트 복사 + alias
- [ ] Roo `webview-ui/src` → `webview/src/roo/` 복사 (무수정)
- [ ] vite.config.ts alias 설정
- [ ] workspace 패키지(@roo-code/types, @roo/*) 로컬 복사 또는 alias
- [ ] 빌드 시도, 누락 의존성 해결

### Phase 3: View-Model Adapter + 동작 완성
- [ ] `adapter/viewModel.ts` (Store → ClineMessage[])
- [ ] Abort/Retry/Continue, 자동 스크롤, Thinking 접기/펼치기
- [ ] 승인 UI, 세션 전환/히스토리

### Phase 4: 통합
- [ ] server.py 정적 서빙 연동 (`webview/dist/`)
- [ ] 기존 index.html 전환
- [ ] Electron 빌드 연동

---

## 5. 빌드 도구

- Node v24.13.1 / npm 11.8.0 확인됨 (pnpm 없음)
- Roo는 pnpm workspace 사용 → `corepack enable pnpm` 또는
  workspace 패키지를 `webview/vendor/`로 복사 + tsconfig paths로 대체
- zustand 추가 (Store 관리, Roo 미사용 의존성)

---

## 6. 라이선스

Roo Code는 **Apache 2.0**.
- 상업적 사용/수정/재배포 가능, 저작권 고지 + 라이선스 사본 필요
- `webview/ROO-LICENSE` 포함
- `src/roo/`는 원본 유지 → Apache 2.0 고지 유지
- `src/daon/`는 DAON 코드
