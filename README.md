# DAON Agent System

> Electron + Python 기반 **멀티 에이전트 IDE** — 로컬 백엔드 서버, 웹 UI, 다중 AI 에이전트를 결합한 데스크톱 개발 환경

---

## ✨ 주요 기능

- **멀티 에이전트 채팅** — 역할 기반 에이전트들과의 대화형 개발
- **다이나믹 하네스** — 멀티 에이전트 DAG 오케스트레이션 (플래너 폴백 체인 내장)
- **영구 작업 저장소** — SQLite 기반 [`HarnessJobStore`](api/api/dynamic/job_store.py)로 작업·계보(lineage) 영속화, 크래시 자동 복구
- **도구 시스템** — 터미널, 파일, 브라우저, 코드 실행, 이미지/영상 생성, 웹 검색, 웹훅, 메모리, 위임(delegation) 등
- **스킬 시스템** — [`skills/`](skills/) 디렉토리의 카테고리별 마크다운 에이전트 스킬
- **다중 LLM 프로바이더** — OpenAI, Anthropic, OpenRouter, xAI, DeepSeek, Ollama, LM Studio 등
- **탭형 웹뷰** — [`TabManager`](electron/src/TabManager.js)를 통한 다중 웹 콘텐츠 뷰
- **TTS 서버** — 별도 포트에서 실행되는 음성 합성 서버
- **Cron 스케줄러** — 예약 작업 실행
- **MCP 서버 통합** — Model Context Protocol 서버 연동
- **회귀 테스트 스위트** — [`tests/run_all_tests.py`](tests/run_all_tests.py) 통합 마스터 러너
- **서버 우선 아키텍처** — HTTP 포트를 즉시 바인딩하고 무거운 초기화는 백그라운드에서 처리하여 무한 로딩 방지
- **서버 슈퍼바이저** — 서버 프로세스 감시, 재시작 오케스트레이션, 실패 시 롤백
- **Self-Update** — 자가 업데이트 오케스트레이션 ([`self_update.js`](electron/self_update.js))
- **Single Instance Lock** — 중복 실행 방지

---

## 🏗️ 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│                  Electron Main Process                        │
│              (electron/main.js — 진입점만 유지)                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────────┐ │
│  │ Splash Win  │ │ Main Window │ │ electron/src/ 모듈 8종   │ │
│  └─────────────┘ └─────────────┘ │  ServerSupervisor       │ │
│         │                        │  TabManager             │ │
│         ▼  spawn                 │  WindowManager          │ │
│  ┌─────────────────────────────┐ │  TrayManager            │ │
│  │  server.exe  (port 9090)    │ │  IpcHandlers            │ │
│  │  Python HTTP (server.py)    │ │  HeaderNormalizer       │ │
│  │  ┌────────────────────────┐ │ │  TempCleaner / Logger   │ │
│  │  │ routes/ — 29개 라우트   │ │ └─────────────────────────┘ │
│  │  │ 모듈 (O(1) 디스패처)    │ │                             │
│  │  ├────────────────────────┤ │                             │
│  │  │ streaming.py           │ │                             │
│  │  │ + StreamEmitter        │ │                             │
│  │  │ + streaming_tools.py   │ │                             │
│  │  │ + streaming_prompts.py │ │                             │
│  │  ├────────────────────────┤ │                             │
│  │  │ dynamic/               │ │                             │
│  │  │ + job_store.py (SQLite)│ │                             │
│  │  ├────────────────────────┤ │                             │
│  │  │ Static Web UI          │ │                             │
│  │  │ (static/, index.html)  │ │                             │
│  │  └────────────────────────┘ │                             │
│  └─────────────────────────────┘                             │
│         │  spawn (optional)                                  │
│  ┌─────────────────────────────┐                             │
│  │  TTS Server (tts_server.py) │                             │
│  └─────────────────────────────┘                             │
└──────────────────────────────────────────────────────────────┘
```

### 서버 우선 아키텍처 (Server-First)

[`server.py`](server.py)는 HTTP 서버를 **즉시 바인딩**한 후, 프로필 초기화·Whisper 웜업 등 무거운 작업을 백그라운드 스레드에서 수행합니다. 이를 통해 Electron의 헬스 체크가 즉시 응답받을 수 있어 무한 로딩을 방지합니다.

### 리팩토링 아키텍처 (2026-09)

| 계층 | 구조 |
|------|------|
| **Electron** | `main.js`는 진입점 역할만 하고, 실제 기능은 `electron/src/` 모듈 8종으로 분리 |
| **라우팅** | 210줄 if/elif 체인 → `routes/` 29개 모듈 + O(1) 해시 디스패처 |
| **스트리밍** | `streaming.py`에서 `StreamEmitter`·`streaming_tools.py`·`streaming_prompts.py` 추출 |
| **프론트엔드** | `static/modules/panels.js` → `static/modules/panels/` 8개 서브패널 모듈 |
| **영속성** | 작업·계보를 SQLite(`HarnessJobStore`)에 저장, 크래시 시 자동 복구 |

---

## 📁 프로젝트 구조

```
Daon-agent-System/
├── electron/                 # Electron 메인 프로세스
│   ├── main.js               #   진입점 (모듈 조립, 서버 시작)
│   ├── preload.js            #   프리로드 스크립트
│   ├── restart_orchestrator.js # 재시작 오케스트레이션 (재빌드/롤백)
│   ├── self_update.js        #   자가 업데이트
│   ├── splash.html           #   스플래시 화면
│   └── src/                  #   기능 모듈 8종
│       ├── ServerSupervisor.js #   서버 감시/복구
│       ├── TabManager.js       #   탭형 웹뷰 관리
│       ├── WindowManager.js    #   창 관리
│       ├── TrayManager.js      #   트레이 관리
│       ├── IpcHandlers.js      #   IPC 라우팅
│       ├── HeaderNormalizer.js #   헤더 정규화
│       ├── TempCleaner.js      #   임시 파일 정리
│       └── Logger.js           #   로깅
├── server.py                 # Python 백엔드 서버 (HTTP, port 9090)
├── tts_server.py             # TTS 전용 서버
├── api/                      # API 모듈 (실제 모듈: api/api/)
│   └── api/
│       ├── routes/           #   HTTP 라우트 29개 모듈 (O(1) 디스패처)
│       │   ├── chat_routes.py
│       │   ├── dynamic_routes.py   # 다이나믹 하네스
│       │   ├── mcp_routes.py
│       │   ├── mobile_routes.py    # 모바일 작업실
│       │   └── ...
│       ├── streaming.py      #   스트리밍 코어 + StreamEmitter
│       ├── streaming_tools.py#   스트리밍 도구 이벤트
│       ├── streaming_prompts.py# 스트리밍 프롬프트 처리
│       ├── dynamic/          #   다이나믹 하네스 (플래너/오케스트레이터/DAG)
│       │   └── job_store.py  #     SQLite HarnessJobStore (영속화+복구)
│       └── ...
├── hermes-agent/             # Hermes 에이전트 프레임워크
│   ├── run_agent.py          #   에이전트 실행 루프
│   ├── agent/                #   프롬프트 빌더 등
│   └── ...
├── skills/                   # 에이전트 스킬 (카테고리별 폴더)
├── static/                   # 웹 UI 정적 파일
│   ├── styles.css
│   ├── modules/
│   │   ├── chat.js / core.js / editor.js / ...
│   │   └── panels/           #   패널 서브모듈 8종
│   │       ├── cron_panel.js       #   크론 스케줄러
│   │       ├── demo_panel.js       #   시연→스킬
│   │       ├── memory_panel.js     #   메모리
│   │       ├── profiles_panel.js   #   프로필
│   │       ├── settings_panel.js   #   설정
│   │       ├── skills_panel.js     #   스킬 허브
│   │       ├── todo_panel.js       #   작업 목록
│   │       └── workspace_panel.js  #   워크스페이스
│   └── ...
├── tests/                    # 회귀 테스트 스위트
│   ├── run_all_tests.py      #   통합 마스터 러너
│   ├── test_phase2_routes.py
│   ├── test_phase3_electron.js
│   ├── test_phase4_frontend.js
│   └── test_phase5_persistence.py
├── index.html                # 웹 UI 진입점
├── config.yaml               # 서버 설정
├── .env                      # 환경 변수 (API 키 등)
├── daon-server.spec          # PyInstaller 스펙
├── installer.nsh             # NSIS 인스톨러 스크립트
└── package.json              # Electron 빌드 설정
```

---

## 🚀 시작하기

### 사전 요구 사항

- **Node.js** (Electron 실행용)
- **Python 3.10+** (서버 실행용)
- **Windows 10/11** (현재 빌드 대상)

### 1. 의존성 설치

```bash
npm install
```

### 2. 환경 변수 설정

[`.env`](.env) 파일에 API 키 등을 설정합니다.

### 3. 개발 모드로 실행

```bash
npm start
```

> ⚠️ **VSCode 터미널 주의**: VSCode는 `ELECTRON_RUN_AS_NODE=1`을 주입하므로,
> 터미널에서 직접 Electron을 실행할 때는 먼저 `set ELECTRON_RUN_AS_NODE=`로 해제해야 합니다.

### 4. 서버 단독 실행 (개발용)

```bash
python server.py --no-browser --port 9090
```

---

## 📦 빌드

### server.exe 빌드 (PyInstaller)

```bash
python -m PyInstaller daon-server.spec --noconfirm
```

→ `dist/server.exe` 생성

### Electron 빌드

```bash
# Portable (디렉토리)
set ELECTRON_RUN_AS_NODE= && npx electron-builder --win --dir

# NSIS 인스톨러
set ELECTRON_RUN_AS_NODE= && npx electron-builder --win nsis
```

→ `dist/win-unpacked/` (portable), `dist/DAON Agent System Setup 1.0.0.exe` (installer)

---

## 🧪 테스트

```bash
python tests/run_all_tests.py
```

Phase 2(라우팅)·3(Electron)·4(프론트엔드)·5(영속성) 회귀 테스트를 한 번에 실행합니다.

---

## ⚙️ 설정

### [`config.yaml`](config.yaml)

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `server.host` | 바인딩 호스트 | `127.0.0.1` |
| `server.port` | 서버 포트 | `9090` |
| `model.default` | 기본 모델 | `""` (동적) |
| `limits.max_file_bytes` | 최대 파일 읽기 크기 | `200000` |
| `limits.max_upload_bytes` | 최대 업로드 크기 | `20971520` (20MB) |
| `toolsets.default` | 기본 도구 세트 | browser, file, terminal, code_execution 등 |

### 기본 도구 세트

`browser`, `clarify`, `code_execution`, `cronjob`, `delegation`, `file`, `image_gen`, `memory`, `session_search`, `skills`, `terminal`, `todo`, `web`, `webhook`

### LLM 프로바이더

OpenAI, OpenAI Codex, Anthropic, OpenRouter, xAI, ZhipuAI, Kimi, DeepSeek, Nous, MiniMax, NVIDIA NIM, Meta Llama, HuggingFace, Alibaba, Ollama, LM Studio

> 모델 목록은 `data/custom_providers.json`에서 동적으로 관리됩니다.

---

## 🎯 스킬

[`skills/`](skills/) 디렉토리에 카테고리별 폴더 구조로 에이전트 스킬이 정리되어 있습니다:

| 카테고리 | 용도 |
|------|------|
| `AI` | AI 모델·API 연동 |
| `Automation` | 자동화 워크플로우 |
| `Browser` | 브라우저 제어·크롤링 |
| `Business` | 비즈니스 문서·분석 |
| `Coding` | 개발·코드 리뷰 |
| `Content` | 콘텐츠 제작 |
| `Creative` | 디자인·크리에이티브 (brutalist-ui, premium-ui, taste 등) |
| `Data` | 데이터 처리 |
| `Design` | 디자인 시스템 |
| `System` | 시스템 운영·자기 성찰 (self-reflection, security 등) |
| `Archive` | 보관된 스킬 |

---

## 🛠️ 기술 스택

| 기술 | 용도 |
|------|------|
| **Electron 31** | 데스크톱 셸 |
| **electron-builder 24** | 패키징 / 인스톨러 |
| **Python** | 백엔드 서버 |
| **PyInstaller** | server.exe 빌드 |
| **SQLite** | 다이나믹 하네스 작업 영속화 (HarnessJobStore) |
| **Hermes Agent** | 에이전트 프레임워크 |
| **Monaco Editor** | 코드 에디터 (웹 UI) |
| **ThreadingHTTPServer** | HTTP 서버 (O(1) 라우트 디스패처) |

---

## ⚠️ 참고 사항

- **`data/` 디렉토리** — 사용자별 설정·토큰을 포함하므로 `.gitignore`로 제외됩니다.
- **`nul` 파일** — Windows 예약 이름으로 일반 삭제가 불가합니다. `.gitignore`에 포함되어 있습니다.
- **ELECTRON_RUN_AS_NODE** — VSCode 터미널에서 Electron 실행 시 반드시 `set ELECTRON_RUN_AS_NODE=`로 해제하세요.

---

## 📄 라이선스

MIT
