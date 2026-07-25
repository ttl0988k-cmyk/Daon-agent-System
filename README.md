# DAON Agent System

> Electron + Python 기반 **멀티 에이전트 IDE** — 로컬 백엔드 서버, 웹 UI, 다중 AI 에이전트를 결합한 데스크톱 개발 환경

---

## ✨ 주요 기능

- **멀티 에이전트 채팅** — 역할 기반 에이전트들과의 대화형 개발
- **도구 시스템** — 터미널, 파일, 브라우저, 코드 실행, 이미지 생성, 웹 검색, 웹훅, 메모리, 위임(delegation) 등
- **스킬 시스템** — [`skills/`](skills/) 디렉토리의 마크다운 기반 에이전트 스킬
- **다중 LLM 프로바이더** — OpenAI, Anthropic, OpenRouter, xAI, DeepSeek, Ollama, LM Studio 등
- **탭형 웹뷰** — [`TabManager`](electron/main.js)를 통한 다중 웹 콘텐츠 뷰
- **TTS 서버** — 별도 포트에서 실행되는 음성 합성 서버
- **Cron 스케줄러** — 예약 작업 실행
- **MCP 서버 통합** — Model Context Protocol 서버 연동
- **서버 우선 아키텍처** — HTTP 포트를 즉시 바인딩하고 무거운 초기화는 백그라운드에서 처리하여 무한 로딩 방지
- **워치독** — 서버 프로세스 자동 감시 및 복구
- **Single Instance Lock** — 중복 실행 방지

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                  Electron Main Process                  │
│                    (electron/main.js)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  Splash Win  │  │  Main Window │  │  TabManager   │ │
│  └──────────────┘  └──────────────┘  └───────────────┘ │
│         │                                               │
│         ▼  spawn                                        │
│  ┌──────────────────────────────────────────────────┐  │
│  │            server.exe  (port 9090)               │  │
│  │         Python HTTP Server (server.py)           │  │
│  │  ┌────────────────────────────────────────────┐  │  │
│  │  │  API Routes (api/)                         │  │  │
│  │  │  Hermes Agent Runtime (hermes-agent/)      │  │  │
│  │  │  Static Web UI (static/, index.html)       │  │  │
│  │  └────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────┘  │
│         │  spawn (optional)                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │         TTS Server  (tts_server.py)              │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 서버 우선 아키텍처 (Server-First)

[`server.py`](server.py)는 HTTP 서버를 **즉시 바인딩**한 후, 프로필 초기화·Whisper 웜업 등 무거운 작업을 백그라운드 스레드에서 수행합니다. 이를 통해 Electron의 헬스 체크가 즉시 응답받을 수 있어 무한 로딩을 방지합니다.

---

## 📁 프로젝트 구조

```
Daon-agent-System/
├── electron/               # Electron 메인 프로세스
│   ├── main.js             #   메인 진입점 (서버 시작, 워치독, 탭 관리)
│   ├── preload.js          #   프리로드 스크립트
│   └── splash.html         #   스플래시 화면
├── server.py               # Python 백엔드 서버 (HTTP, port 9090)
├── tts_server.py           # TTS 전용 서버
├── api/                    # API 모듈 (실제 모듈: api/api/)
│   └── api/
│       ├── routes/         #   HTTP 라우트
│       └── ...
├── hermes-agent/           # Hermes 에이전트 프레임워크
│   ├── run_agent.py        #   에이전트 실행 루프
│   ├── agent/              #   프롬프트 빌더 등
│   └── ...
├── skills/                 # 에이전트 스킬 (마크다운)
├── static/                 # 웹 UI 정적 파일
│   ├── styles.css
│   ├── modules/
│   └── ...
├── index.html              # 웹 UI 진입점
├── config.yaml             # 서버 설정
├── .env                    # 환경 변수 (API 키 등)
├── daon-server.spec        # PyInstaller 스펙
├── installer.nsh           # NSIS 인스톨러 스크립트
└── package.json            # Electron 빌드 설정
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

[`skills/`](skills/) 디렉토리에 마크다운 기반 에이전트 스킬이 포함되어 있습니다:

| 스킬 | 설명 |
|------|------|
| `auto-documenter` | 자동 문서화 |
| `bill-dev` | 개발 비용 산정 |
| `brutalist-ui` | 브루탈리스트 UI 디자인 |
| `contract-validator` | 계약 검증 |
| `creative-director` | 크리에이티브 디렉션 |
| `html-anything` | HTML 생성 |
| `minimalist-ui` | 미니멀리스트 UI |
| `premium-ui` | 프리미엄 UI |
| `redesign-audit` | 리디자인 감사 |
| `security` | 보안 분석 |
| `self-reflection` | 자기 성찰 |
| `sherlock-qa` | QA 탐정 |
| `taste` / `taste-design` | 디자인 취향 |
| `notification-relay` | 알림 릴레이 |
| `full-output` | 전체 출력 |

---

## 🛠️ 기술 스택

| 기술 | 용도 |
|------|------|
| **Electron 31** | 데스크톱 셸 |
| **electron-builder 24** | 패키징 / 인스톨러 |
| **Python** | 백엔드 서버 |
| **PyInstaller** | server.exe 빌드 |
| **Hermes Agent** | 에이전트 프레임워크 |
| **Monaco Editor** | 코드 에디터 (웹 UI) |
| **ThreadingHTTPServer** | HTTP 서버 |

---

## ⚠️ 참고 사항

- **`data/` 디렉토리** — 사용자별 설정·토큰을 포함하므로 `.gitignore`로 제외됩니다.
- **`nul` 파일** — Windows 예약 이름으로 일반 삭제가 불가합니다. `.gitignore`에 포함되어 있습니다.
- **ELECTRON_RUN_AS_NODE** — VSCode 터미널에서 Electron 실행 시 반드시 `set ELECTRON_RUN_AS_NODE=`로 해제하세요.

---

## 📄 라이선스

ISC
