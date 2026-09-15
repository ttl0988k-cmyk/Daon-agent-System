# 저장소 밖(외부) 경로 지도

저장소(`c:\daon\Daon agent System`) **바깥**에 존재하지만 이 프로젝트와 관련된
경로들을 모아 둔 곳. "왜 여기저기 흩어져 보이는가"의 절반은 이 외부 경로 때문이다.

---

## 배포본(실행) — 실제로 앱이 도는 곳

| 경로 | 정체 | 갱신 방법 |
|------|------|-----------|
| `%LOCALAPPDATA%\Programs\daon-agent-system` | **설치본** (바로가기 대상, 정상 실행 경로) | `scripts\sync_to_installed.ps1` |
| `C:\daon\DAON-Portable` | **포터블** 사본 (robocopy 배포) | `install-portable.ps1` / 배포 |

> ⚠ 설치본 폴더명은 **하이픈** `daon-agent-system` 이다 (공백 아님).
> 자세한 규칙은 [`../ARTIFACTS_AND_BUILD.md`](../ARTIFACTS_AND_BUILD.md) §1 참조.

## 사용자 데이터 (앱 상태)

| 경로 | 정체 |
|------|------|
| `%LOCALAPPDATA%\DAON Agent System\data` | 실제 사용자 상태(STATE_DIR) — 설치 경로 삭제와 무관 |
| `~\.hermes\` | profile-level 설정(auth.json, config.yaml 등) |

## 바로가기

| 위치 | 대상 |
|------|------|
| `...\Start Menu\Programs\DAON Agent System.lnk` | `...\Programs\daon-agent-system\DAON Agent System.exe` |
| `...\Start Menu\Programs\Startup\DAON Agent System.lnk` | 동일 + 인자 `--remote-debugging-port=9222` |
| 바탕화면 `.lnk` | (현재 없음) |

## 형제 프로젝트 (이 저장소 아님)

`C:\daon\` 아래에는 이 프로젝트 외에도 여러 형제 폴더가 있다
(`hermes-for-web`, `deploy`, `mobile`, `blender-portable`, `remotion` 등).
DAON Agent System 의 빌드/배포와는 무관하므로 혼동하지 않는다.

---

## 로컬 스크래치(저장소 내부, 정리 대상)

아래는 gitignore 대상이며 언제든 삭제 가능한 파생물이다.

- `dist/`, `dist_new/`, `release/`, `release_friend/`, `build/` — 빌드 산출물
- `daon_runtime/` — PyInstaller onefile 추출 임시 폴더 (재생성됨)
- `_friend_build_backup_*/` — `build_friend_release.ps1` 임시 백업 (스크립트가 자동 삭제)
