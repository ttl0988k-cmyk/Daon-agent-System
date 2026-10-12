# DAON Agent System

> Electron + Python 기반 **멀티 에이전트 데스크톱 환경** — 로컬 백엔드 서버(9090), 웹 UI(v2), 다중 AI 에이전트, 브라우저 직접제어를 결합한 개발·자동화 플랫폼

> 🧩 **크롬 확장(브라우저 에이전트)은 별도 레포** → [ttl0988k-cmyk/daon-browser-agent](https://github.com/ttl0988k-cmyk/daon-browser-agent)
> (소스는 이 레포의 [`extensions/daon-browser-agent/`](extensions/daon-browser-agent)에도 유지)

---

## ✨ 주요 기능

- **멀티 에이전트 채팅** — 역할 기반 에이전트(빌/셜록/토니/프라다 등)와 대화형 개발, 위임·릴레이 협업
- **다이나믹 하네스** — 멀티 에이전트 DAG 오케스트레이션, 작업·계보 SQLite 영속화(`HarnessJobStore`), 크래시 자동 복구
- **브라우저 뷰어 (8088)** — 채팅창에 고정되는 영구 도크 카드
  - 🕹️ **직접 제어 takeover** — 대표님이 마우스로 직접 클릭/스크롤/입력 가능
  - ⌨️ **한글 입력** — 카드 하단 입력창 + `Input.insertText` 우회 (헤드리스 IME 한계 해결)
  - ⏪ **탐색 버튼** — 뒤로 / 앞으로 / 새로고침
  - 🔄 **크래시 자동복구** — 페이지 크래시 시 마지막 URL로 자동 복귀
  - 💤 **idle 절전** — 미사용 시 WS만 끊고 브라우저 유지, 조작 시 자동 재연결
- **도구 시스템** — 터미널, 파일, 브라우저, 코드 실행, 이미지/영상 생성, 웹 검색, 웹훅, 메모리, 세션 검색, 스킬, cron 등
- **스킬 시스템** — [`skills/`](skills/) 카테고리별 마크다운 에이전트 스킬 + SkillRankermaya
- **다중 LLM 프로바이더** — OpenAI / Anthropic / OpenRouter / xAI / DeepSeek / MiniMax / Ollama / LM Studio 등 (`data/custom_providers.json`에서 동적 관리, opencode-go 라우팅)
- **서버 슈퍼바이저 + Self-Update** — 감시·재시작 오케스트레이션, 카나리 검증 후 스왑, 실패 시 롤백
- **MCP 서버 통합** — Model Context Protocol 서버 연동
- **Cron 스케줄러** — 예약 작업 실행
- **TTS 서버** — 별도 포트 음성 합성 서버

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│ Electron Main Process (electron/main.js — 진입점)        │
│  ┌──────────┐ ┌────────────┐ ┌───────────────────────┐  │
│  │ Splash   │ │ Main Window│ │ electron/src/ 모듈군   │  │
│  └──────────┘ └────────────┘ │ ServerSupervisor      │  │
│         │ spawn              │ TabManager / Tray 등   │  │
│  ┌──────────────────────────┐└───────────────────────┘  │
│  │ server.exe (port 9090)   │                            │
│  │ Python HTTP (server.py)  │                            │
│  │ ├─ routes/ 라우트 모듈군 │                            │
│  │ ├─ streaming.py (SSE)    │                            │
│  │ ├─ dynamic/ 하네스+DAG   │                            │
│  │ └─ Static Web UI (static/v2)                            │
│  └──────────────────────────┘                            │
│         │ spawn (optional)                               │
│  ┌──────────────────────────┐                            │
│  │ browser_service (port 8088) — 헤드리스 Chromium + CDP │  │
│  └──────────────────────────┘                            │
└─────────────────────────────────────────────────────────┘
```

| 포트 | 역할 |
|------|------|
| `9090` | 메인 서버 (채팅 API/SSE + v2 UI 서빙) |
| `8088` | 브라우저 서비스 (CDP 제어 + WS 스트림 + 스크린샷) |

> ⚠️ **개발 시 3곳 동기화 필수** — `소스(static/, browser_service/)` ↔ `설치본 resources/` ↔ `설치본 resources/dist_new/` (재기동 시 dist_new에서 덮어씀). 프론트 수정 후엔 `app.js` import 버전 꼬리표(`?v=YYYYMMDD_이름`) 범프.

---

## 📁 프로젝트 구조

```
Daon-agent-System/
├── electron/               # Electron 메인 프로세스
│   ├── main.js             #   진입점 (모듈 조립, 서버 시작)
│   ├── self_update.js      #   자가 업데이트 (카나리 검증+롤백)
│   └── src/                #   ServerSupervisor, TabManager 등
├── server.py               # Python 백엔드 서버 (port 9090)
├── api/api/                # API 모듈 (routes/, streaming.py, dynamic/)
├── browser_service/        # 브라우저 서비스 (port 8088, CDP)
├── static/v2/              # 웹 UI v2 (chat + browser viewer dock)
├── hermes-agent/           # Hermes 에이전트 프레임워크
├── skills/                 # 에이전트 스킬 (카테고리별)
├── extensions/daon-browser-agent/  # 크롬 확장 소스 (별도 레포로도 배포)
├── index.html              # 웹 UI 진입점
├── config.yaml             # 서버 설정
├── daon-server.spec        # PyInstaller 스펙 (server.exe 빌드용)
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
> 직접 Electron을 실행할 때는 먼저 `set ELECTRON_RUN_AS_NODE=`로 해제하세요.

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

---

## ⚙️ 설정

### [`config.yaml`](config.yaml)

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `server.host` | 바인딩 호스트 | `127.0.0.1` |
| `server.port` | 서버 포트 | `9090` |
| `limits.max_file_bytes` | 최대 파일 읽기 크기 | `200000` |
| `limits.max_upload_bytes` | 최대 업로드 크기 | `20971520` (20MB) |

### LLM 프로바이더

OpenAI, OpenAI Codex, Anthropic, OpenRouter, xAI, ZhipuAI, Kimi, DeepSeek, MiniMax, Ollama, LM Studio 등 — `data/custom_providers.json`에서 동적 관리.

---

## 📄 라이선스

MIT
