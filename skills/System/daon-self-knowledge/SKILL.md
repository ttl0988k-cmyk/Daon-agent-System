# daon-self-knowledge

DAON Agent System이 자기 자신을 작업 대상으로 다룰 때 필요한 지식 (갭 E 대상 결속).
이 레포의 코드를 수정하거나 빌드/프로브를 실행하기 전에 반드시 이 스킬을 읽는다.

## 1. 레포 구조

- `server.py` — 웹 서버 진입점 (HTTP API + 정적 UI 서빙)
- `api/api/` — 서버 구현 (`routes/` 라우트, `dynamic/` 다이나믹 하네스 엔진)
- `hermes-agent/` — 에이전트 엔진 (`tools/` 도구, `hermes_cli/` CLI/플러그인, `plugins/` 번들 플러그인)
- `static/` — 프론트엔드 (`index.html`, `modules/*.js`, `styles.css`)
- `electron/` — 데스크톱 셸 (`main.js`가 서버 프로세스를 감시 — 감시자/피감시자 분리)
- `skills/` — curated 스킬 (`Category/skill-name/SKILL.md` + `skill.yaml`)
- `_probe/` — 회귀 프로브 (`probe_gap_*.py`)
- `docs/` — `SELF_EVOLUTION_PLAN.md`(갭 E 시공 상태), `DYNAMIC_HARNESS_VISION_PLAN.md`(정책/빌드 이력)

## 2. 빌드 파이프라인 (순서 불변)

1. **git push 먼저** (정책: 빌드 전 push. push 없이 빌드하지 않음)
2. `python _sync_build.py` — `dist_new/` 미러 재구성
   - DIR_PAIRS: `static`, `api/api`, `data`, `hermes-agent`, `skills` → `dist_new/`
   - FILE_PAIRS: `config.yaml`, `index.html`, `.env` → `dist_new/`
   - '삭제된' 추적 파일만 복원한다. **수정된 파일은 절대 건드리지 않음**
     (무조건 git restore로 미커밋 패치가 유실된 사고 이력 있음)
3. `python -m PyInstaller daon-server.spec --noconfirm` — `dist_new` → server.exe
   - spec은 번들 안에서 `api/api/*.py`를 `api/*.py`로 평탄화한다
     (dev: `api/api/dynamic_hermes.py` → 번들: `api/dynamic_hermes.py`)
     — 번들 경로 계산 시 이 평탄화를 고려해야 한다
4. `npx electron-builder` — `dist_new/server.exe`를 `dist/win-unpacked`로 패키징
   - afterPack 훅(`scripts/after-pack.js`)이 `.cmd` 런처를 자동 재생성한다 (아래 3절)
5. **`dist\win-unpacked\DAON Agent System.cmd` 존재 확인** — 훅이 보장하지만
   빌드 후 존재를 눈으로 확인한다 (없으면 3절 수동 절차로 재생성)
6. `release/_build_zip.ps1` — 포터블 zip 생성

## 3. electron-builder .cmd 삭제 함정 (항구 대책 시공 완료)

`npx electron-builder` 실행 후 `dist\win-unpacked\DAON Agent System.cmd`가
삭제되는 함정이 반복적으로 발생했다. 바탕화면/시작 메뉴 바로가기 전부
`.cmd`를 대상으로 하므로 없으면 앱 실행이 깨진다.

**항구 대책 (2026-08-19 시공)**: `scripts/after-pack.js` afterPack 훅.
`package.json`의 `build.afterPack`에 등록되어 electron-builder가 앱 디렉터리
(win-unpacked) 구성 직후, NSIS 설치본 빌드 직전에 실행된다. 훅이 `.cmd`를
재생성하므로 win-unpacked은 물론 설치본에도 포함된다. win32에서만 동작하며
`productFilename` 기반으로 경로를 계산한다.

훅이 쓰는 내용(수동 재생성 시에도 동일):

```bat
@echo off
rem DAON Agent System launcher
set "ELECTRON_RUN_AS_NODE="
start "" "%~dp0DAON Agent System.exe"
```

수동 재생성(훅 미동작 시 비상 절차): 위 내용을
`dist\win-unpacked\DAON Agent System.cmd`로 저장한다.

## 4. 프로브 실행법

- 레포 루트에서 실행: `python _probe\probe_gap_X.py`
- 프로브는 페이크 매니저/페이크 게이트웨이로 외부 의존을 모의하므로
  서버 실행 없이 동작한다
- `ALL ... PROBES PASSED` 출력 + exit code 0이면 통과
- 갭 관련 코드를 수정한 뒤에는 해당 프로브를 반드시 재실행한다 (회귀 검사)
- 신규 갭 시공 시에는 `_probe/probe_gap_<갭>.py`를 함께 추가한다

## 5. 운영 정책 (대표님 지시 — 모든 세션에서 유지)

1. 설명은 한국어로
2. 빌드 전 git push 먼저
3. 서버 무단 재시작 금지 (대표님이 직접 실행)
4. 증상 추측 금지 — 로그/코드로 근본 원인 확인 후 수정
5. Pyrefly "Cannot find module" 오류는 기존 sys.path 구조 문제 — 런타임 무해, 무시
6. Python 로그/와이어 형식에 비ASCII 문장부호·이모지 금지 (cp949 콘솔).
   이모지는 프론트엔드 렌더링/registry 메타데이터에서만 사용
7. `_tmp_*` 파일은 미추적 유지 (git add 금지)

## 6. 자기 수정 불변식 (갭 E)

- 불변 순서: **생성 → 격리 → 검증 → 승인 → 편입 → 사용** (순서 변경 금지)
- 에이전트는 자기 재시작을 스스로 수행하지 않고 요청만 한다
  (감시자/피감시자 분리 — electron main이 서버를 감시)
- 자기 수정 실행에도 위임 가드(스폰 예산/깊이 제한) 적용
- `dist/`, `release/`는 자기 수정 대상에서 제외 (빌드 산출물 오염 방지)
