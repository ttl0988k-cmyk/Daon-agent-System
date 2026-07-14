# DAON Agent System

![Version](https://img.shields.io/badge/version-1.2.0-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Electron-lightgrey)

DAON Agent System은 Hermes Agent 기반 위에 구축된 **한국어 1등 멀티에이전트 데스크톱 IDE**입니다.
단일 사용자(대표님) 환경을 위해 최적화되었으며, 강력한 AI 에이전트 파이프라인과 다채로운 도구를 제공하여 생산성을 극대화합니다.

---

## 📥 다운로드 및 설치

[👉 최신 버전 다운로드 (Windows .exe)](https://github.com/ttl0988k-cmyk/Daon-agent-System/releases/latest)

다운로드 받은 `DAON Agent System Setup 1.0.0.exe` 파일을 실행하면 윈도우에 자동으로 설치되며 바로 사용할 수 있습니다.

---

## ✨ 핵심 기능 3대장 (Core Features)

### 1. 🧠 다이나믹 하네스 (Dynamic Harness JIT)
단순한 챗봇이 아닙니다. 자연어 미션을 입력하면 즉석에서 **DAG(Directed Acyclic Graph) 형태의 에이전트 파이프라인(Planner → Compiler → Runner → Merger)** 을 기획하고 조립하여 다수의 에이전트가 동시에 협업하는 환경을 구축합니다.

### 2. 📺 데모 투 스킬 (Demo-to-Skill)
에이전트에게 말로 설명하기 어려운 작업인가요? 직접 보여주세요! 시스템 내장 브라우저에서 사용자가 **시연(Demonstration)하는 과정을 자동으로 녹화하고 분석**하여, 언제든 재사용 가능한 완벽한 에이전트 스킬(`SKILL.md`)로 자동 변환해 줍니다.

### 3. 🌐 강력한 내장 브라우저 (Built-in Browser & Playwright)
시스템 내부에 브라우저가 통합되어 있습니다. 사용자와 에이전트가 **동일한 화면을 실시간으로 공유**하며, 에이전트가 마우스를 움직이고 클릭하는 모든 과정을 눈으로 직접 확인할 수 있습니다.

---

### 기타 주요 기능
*   **Persona 및 Skill 시스템**: 17개의 특화 스킬(bill, sherlock, prada 등)과 7개의 전문가 역할을 상황에 맞게 자동 주입
*   **확장 가능한 MCP 통합**: Filesystem, Memory, Daon-Design, Figma, PlayMCP-Gateway 등 6개의 MCP 서버와 유기적 연동
*   **Style Card 시스템**: 뛰어난 디자인의 웹페이지/앱에서 디자인 DNA를 추출해 믹싱하는 트렌디한 UI 컴포넌트 생성기

---

## 📸 스크린샷

### 메인 인터페이스 및 HTML 문서 미리보기
DAON Agent System의 분할 화면 기능으로, 좌측에서는 Monaco Editor 기반의 편집기를 사용하고 우측에서는 실시간 브라우저 미리보기(Playwright 연동)를 제공합니다. 시스템 아키텍처 문서 등 산출물을 바로 렌더링하여 확인할 수 있습니다.

![DAON Agent Interface - Editor](docs/images/screenshot_main1.png)

### 다이나믹 하네스 & 브라우저 뷰
![DAON Agent Interface - Browser](docs/images/screenshot_main2.png)

### 실시간 채팅 및 다중 스레드
![DAON Agent Interface - Chat](docs/images/screenshot_main3.png)

---

## 🏛 시스템 아키텍처

DAON Agent System은 안정성과 확장성을 위해 **4-Tier 아키텍처**로 구성되어 있습니다.

1.  **Presentation Tier**: Electron 기반의 데스크톱 UI, Monaco Editor 통합, 실시간 SSE 스트리밍
2.  **Orchestration Tier**: 작업 계획(Planner), 에이전트 빌드(AgentCompiler), 병렬 실행(ParallelRunner), 결과 병합(ResultMerger)
3.  **Execution Tier**: LLM 직접 호출, 62+개의 CLI 도구 실행, MCP 클라이언트 요청 처리
4.  **Persistence Tier**: SQLite 세션 관리, 메모리 영구 저장소, Skill Registry, 로컬 파일 시스템 제어

> 💡 상세한 시스템 아키텍처 다이어그램 및 엔드포인트 설명은 `docs/DAON_아키텍처_문서.html` 파일을 참고하세요.

---

## 💻 기술 스택

*   **Backend**: Python 3 (Custom HTTP Server / FastAPI 유사 구조)
*   **Frontend**: HTML5, Vanilla JavaScript, CSS (Tailwind 무의존성), Monaco Editor
*   **Desktop App**: Electron, PyInstaller (Bundling)
*   **AI & Tools**: LLM 연동, Playwright, Model Context Protocol (MCP)

---

## 🚀 시작하기

**1. 로컬 환경 실행**
```bash
# 백엔드 서버 시작
python server.py

# 프론트엔드 (Electron) 실행
npm run start
```

**2. 프로덕션 빌드 (Windows)**
```bash
# 백엔드 실행 파일 생성
npm run build:py

# Electron 앱 패키징 및 Setup.exe 생성
npm run build
```
배포된 설치 파일은 `dist/DAON Agent System Setup 1.0.0.exe` 경로에서 확인할 수 있습니다.

---

## 📄 라이선스

이 프로젝트는 [MIT 라이선스](LICENSE) 조건에 따라 배포됩니다.
