# Self-Update Build Root 붕괴 수리 시공 기록 (2026-09-12)

> **근본 원인**: Phase 3 모듈 분해 커밋에서 `resolveBuildRoot()` 이관이 누락되어,
> packaged 앱에서 build root 가 무검증으로 `<앱>/resources` 로 해석됐다.
> 그곳에 `_sync_build.py` / `daon-server.spec` 이 없어 `request_server_update(rebuild=true)`
> 가 **2026-09-07 이후 조용히 거부**되어 왔다.

---

## 1. 증상 (Symptoms)

### 문제 3 — 크래시 루프 1,998회 (표면 증상)
```
[ServerSupervisor] Main Python server exited (code=1) — Auto-restarting in 2s...
```
- 감독자가 추적하는 자식 프로세스는 죽었으나, **다른 `server.exe`(PID 7460)가 포트 9090을 점유**
- 새 스폰이 `EADDRINUSE` 로 즉사 → 2초 후 재스폰 → **무한 루프**
- 피해: `server.log` 122MB, `_MEI` 임시 폴더 11개 누적, CPU/디스크 churn

### 문제 4 — 자기 진화 파이프라인 정지 (근본 원인)
```
[SelfUpdate] rebuild refused — _sync_build.py missing in build root
[2026-09-07T23:55:20.521Z]   ← 이후 동일 메시지 반복
```

| 파일 | `<앱>/resources/` (감독자가 보는 곳) | 실제 소스 트리 |
|------|:---:|:---:|
| `_sync_build.py` | ❌ 없음 | ✅ 있음 |
| `daon-server.spec` | ❌ 없음 | ✅ 있음 |

---

## 2. 근본 원인 (Root Cause)

### 2.1 Phase 3 회귀 (Regression)
`git log -S "resolveBuildRoot" -- electron/main.js` 결과:

| 커밋 | 시각 | 동작 |
|------|------|------|
| `0089d5b` | — | `resolveBuildRoot()` **도입** (후보 순회 + `daon-server.spec` 검증) |
| `f4c1a73` | 2026-09-08 00:36 | "complete Phase 3 — dismantle monolithic main.js" — **함수 소실** |

`f4c1a73` 은 monolithic `main.js`(1,490줄)를 `electron/src/*` 모듈로 분해하면서
build root 해석 로직을 어느 모듈에도 이관하지 않았다. 대신 아래 한 줄이 남았다:

```js
// electron/main.js  (구버전)
const repoRoot = path.join(__dirname, '..');
```

### 2.2 경로 의미 붕괴 (Path Semantics Collapse)
개발 트리와 packaged 앱에서 `__dirname` 의 의미가 다르다.

| 실행 모드 | `__dirname` | `path.join(__dirname, '..')` |
|-----------|-------------|------------------------------|
| dev | `<repo>/electron` | `<repo>` ✅ |
| packaged | `<앱>/resources/app.asar/electron` | `<앱>/resources` ❌ (빌드 산출물 아님) |

동일 식이 **개발에서는 우연히 맞고 패키징에서는 틀린다**. 검증이 없으면 조용히 실패한다.

### 2.3 조용한 실패 (Silent Fail)
`restart_orchestrator` 는 재빌드 실패를 **비치명적(non-fatal)** 으로 처리한다.
따라서 자기 진화가 5일간 정지했음에도 사용자에게 표면화되지 않았다.
→ **실패는 조용해야 하는 것이 아니라, 시끄러워야 한다.**

### 2.4 파생 피해 (Derivative Damage)
1. **git 롤백 정지**: `gitRollback` 의 `cwd=repoRoot` 가 `<앱>/resources` → git 저장소 아님
2. **STATE_DIR 오염**: dev 후보 `<repoRoot>/data` = `<앱>/resources/data` 오염
3. **stale 미러**: `dist_new/electron/main.js`(1,489줄)가 옛 monolith 그대로

---

## 3. 시공 (Construction)

### A. 코드 계층 (Code)

**A-1/A-2 — [`electron/main.js`](../electron/main.js)**
- `resolveBuildRoot()` 복원: 후보 순회 + `daon-server.spec` 존재 검증
  ```js
  const candidates = [
    process.env.DAON_BUILD_ROOT,
    process.resourcesPath,      // packaged: extraResources 실제 위치
    path.join(__dirname, '..'), // dev: 레포 루트
  ].filter(Boolean);
  // 각 후보에서 daon-server.spec 존재를 확인한 뒤에만 반환
  ```
- `resolveRepoRoot()` 분리: `.git` 존재로 git 롤백용 루트를 따로 검증
- `selfUpdate` 에 **검증된 `buildRoot`** 주입 (무검증 `repoRoot` 직접 주입 금지)
- `gitRollback` 에 `if (!repoRoot)` 사전 가드 — packaged 빌드에서 롤백 비활성 명시

**A-3 — [`electron/self_update.js`](../electron/self_update.js)**
- `rebuildAndSwap()` 에 spec 이중 검증 추가 → 주입형 deps 로도 무검증 경로 방어
- `_sync_build.py` 부재 시 빌드 전 즉시 거부 (stale 미러가 exe 로 굳는 사고 차단)
- `RESOURCE_REFRESH_PAIRS` 를 레포 원본이 아닌 **`dist_new` 미러 기준**으로 정합화

### B. 패키징 계층 (Packaging — 자기완결)

**[`package.json`](../package.json)** + **[`electron-builder.yml`](../electron-builder.yml)** 의
`extraResources` 를 동일하게 확장 (두 설정 파일 정합 필수):

| 번들 대상 | 목적 |
|-----------|------|
| `_sync_build.py`, `daon-server.spec` | 재빌드 파이프라인 실행 |
| `server.py`, `tts_server.py` | spec `Analysis(['server.py'])` 진입점 |
| `api/api` → `api/api` | `_sync_build.py` DIR_PAIRS 리터럴 경로 |
| `api/agents` → `api/agents` | spec datas 리터럴 경로 |

> **소스 레이아웃 보존 이유**: 런타임 import 는 `resources/api`(= `api/api` 내용물)를
> `api` 패키지로 사용하지만(`server.py` 가 `RESOURCE_DIR/api` 를 `sys.path` 에 추가),
> **재빌드**는 `_sync_build.py` 와 spec 이 소스 트리 상대경로를 리터럴로 요구한다.
> 따라서 중첩 복사본(`api/api`, `api/agents`)을 함께 번들한다(동일 내용, 무해).

### C. 방어 계층 (Loop Defense)

**[`electron/src/ServerSupervisor.js`](../electron/src/ServerSupervisor.js)** — 세 겹 차단:

1. **재스폰 전 점유자 제거** — `_scheduleAutoRespawn()` 이 `killPortOwner(port)` 를
   `startPythonProcess(port)` 보다 **먼저** 호출 (1,998회 루프의 직접 원인 제거)
2. **즉사 판정 + 지수 백오프** — 스폰 후 `CRASH_WINDOW_MS`(15s) 내 종료 = '즉사'로 계상,
   `2s × 2^(streak-1)` (상한 `MAX_RESTART_BACKOFF_MS` 60s)
3. **서킷브레이커** — `MAX_CRASH_STREAK`(8) 도달 시 자동 재시작 **전면 중단**
   (`CIRCUIT BREAKER open` 로그), 수동 재시작만 허용

모든 재시작 경로(exit / error / spawn-catch)가 **단일 관문 `_scheduleAutoRespawn()`** 만 호출한다.

---

## 4. 회귀 방지 (Regression Guard)

[`tests/test_self_update_buildroot.js`](../tests/test_self_update_buildroot.js) — **G1~G8, 44 checks**

| 그룹 | 고정하는 계약 |
|------|---------------|
| G1 | main.js 배선 — `resolveBuildRoot()` + `resourcesPath` + spec 검증 + 무검증 `repoRoot` 금지 |
| G2 | spec 부재 시 **빌드 전 즉시** 거부 |
| G3 | `_sync_build.py` 부재 시 거부 (stale 미러 방지) |
| G4 | 느슨한 리소스 갱신이 `dist_new` 미러 기준 |
| G5 | restart_orchestrator STATE_DIR 규약 유지 |
| G6 | 재시작 루프 방어 — 점유자 제거 선행 + 서킷브레이커 + 무조건 2초 재시작 문구 제거 |
| G7 | 두 빌드 설정(`package.json` / `electron-builder.yml`)의 extraResources 완전 동일 |
| G8 | `_sync_build.py` / spec 이 요구하는 소스 트리 번들 계약 |

`tests/run_all_tests.py` 의 `TEST_SUITES` 에 등록되어 마스터 스위트에 포함된다.

```bash
python tests/run_all_tests.py     # 6 suites, 100% GREEN
node tests/test_self_update_buildroot.js   # SUMMARY checks=44 fail=0
```

---

## 5. 교훈 (Lessons)

1. **경로 해석은 검증 없이 신뢰하지 말 것** — dev 에서 맞는 식이 packaged 에서 틀린다.
   후보를 순회하고 **산출물 존재로 검증**한 뒤에만 채택한다.
2. **모듈 분해 시 부수 로직 이관을 명시적으로 점검할 것** — "dismantle" 리팩터는
   삭제된 심볼 목록(`git log -S`)을 대조해야 회귀를 잡는다.
3. **자기 완결 패키징** — 재빌드에 필요한 파일(spec, sync 스크립트, 진입점)을
   `extraResources` 로 번들해야 packaged 앱이 스스로를 갱신할 수 있다.
4. **비치명적 실패는 관측 가능해야 한다** — 조용한 거부는 5일의 정지를 낳았다.
5. **재시작 루프 방어는 3층** — 점유자 제거 + 백오프 + 서킷브레이커.
   한 겹이라도 빠지면 무한 루프가 재발한다.

---

## 6. 후속 작업 (Follow-ups)

- [ ] **앱 재패키징** — 새 `extraResources` 를 실제 설치본에 반영 (사용자 확인 필요)
- [ ] stale `dist_new/electron/main.js`(1,489줄 monolith) 미러 제거/재생성
- [ ] packaged 환경에서 `DAON_BUILD_ROOT` override 실측 검증
- [ ] 서킷브레이커 발동 시 트레이 알림/사용자 표면화 추가
