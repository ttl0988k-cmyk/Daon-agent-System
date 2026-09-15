# DAON 산출물 & 빌드 지도 (ARTIFACTS_AND_BUILD)

> **한 줄 요약:** 소스는 **한 곳**(저장소 루트)에서 관리하고, 빌드 파이프라인
> (`_sync_build.py → PyInstaller → electron-builder`)이 만든 **산출물**만
> `dist/ → release_friend/ → (설치본 / 포터블)` 로 흘러간다.
> 실행 중인 앱은 항상 **설치본(하이픈 폴더)** 또는 **포터블** 두 곳 중 하나다.

이 문서는 "server.exe·electron·포터블·바로가기가 여기저기 흩어져 보이는" 문제를
없애기 위한 **단일 지도**다. 각 위치가 무엇이고, 어디가 정본이며, 무엇이 파생물인지
한눈에 정리한다.

---

## 1. 폴더명 혼선 제거 (가장 중요 · 흩어짐의 실제 원인)

과거에 빌드 산출물이 흩어진 것처럼 보인 근본 원인은 **설치 폴더명 불일치**였다.

| 구분 | 값 | 쓰이는 곳 |
|------|----|-----------|
| electron-builder `productName` | `DAON Agent System` (공백) | 바로가기 이름, exe 이름, 프로세스 이름 |
| **실제 설치 폴더명** | `daon-agent-system` (**하이픈/소문자**) | `%LOCALAPPDATA%\Programs\daon-agent-system` |

- `scripts/after-pack.js` 는 예전에 **공백 경로**로만 자동동기화 → **조용히 실패**.
- `scripts/sync_to_installed.ps1` 는 **공백 경로**만 보고 "설치본 없음" → `exit 1`.
- `install-portable.ps1` 는 **공백 경로**로 "또 다른" 설치본을 생성.

→ 이제 단일 정의 모듈 [`scripts/lib/daon_paths.ps1`](../scripts/lib/daon_paths.ps1) 이
두 후보를 **우선순위대로** 탐색한다(하이픈 → 공백). 위 3개 스크립트와
[`scripts/after-pack.js`](../scripts/after-pack.js) 모두 이 규칙으로 정렬되었다.

```
Resolve-DaonInstalledDir      # 존재하는 실제 설치본 폴더 (하이픈 우선)
Resolve-DaonDeployTargets     # 배포 대상 resources 폴더들 (설치본 + 포터블)
Get-DaonMainExe -Root <dir>   # <dir>\DAON Agent System.exe
```

---

## 2. 실행(Runtime) 계층 — "실제로 돌아가는" 3개 위치

| 이름 | 경로 | 성격 | 갱신 방법 |
|------|------|------|-----------|
| **설치본** | `%LOCALAPPDATA%\Programs\daon-agent-system` | NSIS 설치본 (바로가기 대상, 정상 실행) | `scripts\sync_to_installed.ps1` |
| **포터블** | `C:\daon\DAON-Portable` | robocopy 포터블 사본 | `install-portable.ps1` / 배포 시 |
| 소스 실행(개발) | 저장소 루트에서 `npm start` | Electron 개발 모드 | 소스 수정 즉시 반영 |

### 실행 체인 (바로가기가 무엇을 가리키는가)

```
바로가기(.lnk)  →  DAON Agent System.exe      (Electron 셸, ~204 MB)
                        │  ServerSupervisor.findServerExe()
                        ▼
                   resources\server.exe        (PyInstaller 백엔드 번들, ~188 MB)
                        │  런타임에 daon_runtime\_MEIxxxx\ 로 자기추출
                        ▼
                   Python 백엔드 실행 (hermes-agent / api 번들 포함)
```

- **바로가기 대상**(실측): `C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\DAON Agent System.exe`
  - 시작 메뉴: `...\Start Menu\Programs\DAON Agent System.lnk`
  - 시작프로그램: `...\Start Menu\Programs\Startup\DAON Agent System.lnk` (인자 `--remote-debugging-port=9222`)
  - 바탕화면 `.lnk` 는 현재 **없음**(시작 메뉴/시작프로그램만).
- **Python 버그픽스가 사는 곳**은 Electron 셸 exe 가 아니라 **백엔드**(`resources\server.exe`)와
  **느슨한 리소스**(`resources\hermes-agent`, `resources\api`)다. 따라서 셸 exe 크기는 그대로여도 정상.

### 런타임 로드 우선순위 (중요)

```
sys.path  ←  resources\hermes-agent      (느슨한 사본)   ← 우선
           ←  _MEIPASS\hermes-agent      (server.exe 번들) ← 차선
```

→ **느슨한 리소스가 번들보다 우선**한다. 그래서 픽스 배포 시
`server.exe`(번들) **와** 느슨한 리소스 **둘 다** 갱신해야 한다. (아래 §5 참조)

---

## 3. 빌드 파이프라인 — "어디서 무엇이 만들어지는가"

```
[소스 · 유일한 정본]
  hermes-agent/ , api/api/ , static/ , skills/ , server.py , config.yaml , index.html
        │
        │ ① python _sync_build.py                       (미러 구성)
        ▼
[dist_new/]  ← PyInstaller 의 입력 트리 (dist_new/api/api, dist_new/hermes-agent …)
        │
        │ ② python -m PyInstaller daon-server.spec --noconfirm
        ▼
[dist/server.exe]  ← 백엔드 onefile 번들 (dist_new/* 를 _MEIPASS 로 내장)
        │
        │ ③ powershell scripts\build_friend_release.ps1 (sanitize → electron-builder → restore)
        ▼
[release_friend/]  ← 지인 배포용: "DAON Agent System Setup 1.0.0.exe" + win-unpacked/
```

각 단계 산출물의 성격:

| 경로 | 무엇 | 비고 |
|------|------|------|
| `dist_new/` | PyInstaller 입력 미러 | 파생물 (gitignore) — 직접 수정 금지, `_sync_build.py` 로만 갱신 |
| `dist/server.exe` | 백엔드 번들 | 파생물 — 픽스는 항상 여기까지 재빌드해야 반영 |
| `dist/*.exe`, `latest.yml`, `builder-debug.yml` | electron-builder 로컬 출력 | 파생물 |
| `release/` | 포터블 zip + 설치 스크립트 | 파생물 (zip/exe 는 gitignore) |
| `release_friend/` | 지인 배포 Setup.exe + win-unpacked | 파생물 (gitignore) |

---

## 4. 외부(저장소 밖) 위치

저장소 밖에 존재하는 경로는 [`docs/external/README.md`](external/README.md) 참고.

---

## 5. 버그픽스를 배포본에 반영하는 절차 (정본 순서)

> 과거에는 이 순서를 건너뛰거나 경로가 어긋나 "일부만 옛 버전"이 남는 사고가 있었다.

```powershell
cd "c:\daon\Daon agent System"

# 1) 소스 수정 (예: hermes-agent\..., api\api\...)

# 2) 소스 → dist_new 미러
python _sync_build.py

# 3) 백엔드 재빌드 (dist\server.exe 갱신)
python -m PyInstaller daon-server.spec --noconfirm

# 4) 지인 배포용 재빌드 (release_friend 갱신, originals 자동 복원)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_friend_release.ps1

# 5) 설치본(하이픈 폴더) 동기화 + 재시작
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sync_to_installed.ps1 -Open
```

`build_friend_release.ps1` 의 `afterPack`([`scripts/after-pack.js`](../scripts/after-pack.js))가
**설치본 + 포터블**의 `resources\app.asar` 와 `resources\server.exe` 를 자동 복사한다.
(이 자동동기화가 예전엔 공백 폴더를 봐서 조용히 실패했다 — §1 참조, 현재 수정 완료.)

---

## 6. 검증(verification) 진입점

| 파일 | 검증 내용 |
|------|-----------|
| [`scripts/verify_browser_screenshot_bridge.py`](../scripts/verify_browser_screenshot_bridge.py) | 브라우저 스크린샷 브리지 (base64→path 파일 저장) 6/6 |
| [`scripts/verify_crlf_read_basis.py`](../scripts/verify_crlf_read_basis.py) | patch 개행 read/write 기준 회귀 6/6 |
| [`scripts/verify_patch_newline_bug.py`](../scripts/verify_patch_newline_bug.py) | patch 거짓 실패 재현 |
| [`scripts/verify_restart_loop_fix.ps1`](../scripts/verify_restart_loop_fix.ps1) | 재시작 루프 방지 |
| [`scripts/verify_memory_gate.py`](../scripts/verify_memory_gate.py) | 메모리 게이트 |

핵심 해시(픽스 반영 기준):
- `hermes-agent/tools/file_operations.py` = `7f2ae3a2…` (개행 비대칭 픽스)
- `api/api/browser_bridge.py` = `1181345a…` (스크린샷 픽스)
- `resources/server.exe` = **188,489,675 bytes**

---

## 7. "흩어짐" 정리 요약 (Before → After)

| 항목 | Before | After |
|------|--------|-------|
| 설치 폴더명 | 스크립트마다 공백/하이픈 혼용 | `scripts/lib/daon_paths.ps1` **단일 정의** |
| after-pack 자동동기화 | 공백 경로 → 조용히 실패, app.asar만 | 하이픈+공백 탐색, app.asar **+ server.exe** |
| sync_to_installed | 공백 경로만, 없으면 즉시 중단 | 실제 설치본 자동 탐색 |
| install-portable | 공백 경로로 별도 설치본 생성 | 기존 설치본 재사용 / 표준 하이픈 경로 |
| 임시 산출물 | `daon_runtime`, `_verify_*`, `nul` 잔존 | 정리 완료 |
| 문서 | 없음(머릿속에만) | **이 문서** |
