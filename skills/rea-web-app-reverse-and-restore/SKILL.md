---
name: rea-web-app-reverse-and-restore
description: "REA(rea-agents) MCP/CLI + 격리 headless Chrome으로 웹앱(three.js/WebGL 등)의 소스·GLSL 셰이더·텍스처를 전량 추출하고, 로컬에 동일 디렉토리 구조로 복원한 뒤 sha256·렌더로 바이트 단위 검증하는 파이프라인. 2026-10-13 three.js webgl_shaders_ocean 실증(14파일 2.5MB, 11모듈 sha256 100% 일치). ★모방 목적이면 §5-C로 — 완전 복제가 아니라 기능/디자인만 떠서 SPEC.md(티어 A/B/C 판정·기능 인벤토리·NON-GOALS·ACCEPTANCE)를 쓰고 다른 사이트를 재창조한다(2026-10-19 Notion 202줄 명세 실증). ⛔이때 라온은 tainted라 구현 금지 — SPEC.md까지만 쓰고 4경로(빌·작업장·다이나믹 하네스·코덱스/클로드 워커) 중 하나로 단독 인계, 검증은 셜록. 트리거 — 웹사이트 소스 뜯기, REA 웹 리버싱, three.js 복원, 셰이더 추출, 웹앱 클론, 번들 해체, 사이트 기능 모방, 디자인 참고해 새로 만들기, 기능 명세서 뽑기, 클린룸 재창조, SPEC.md 작성."
metadata:
  version: "1"
---

# REA 웹앱 리버싱 → 완전 복원 파이프라인

REA는 **JS를 실행하지 않는 수동 관찰 도구**다. 브라우저를 띄워 페이지를 로드하고, 정적으로 소스/에셋/네트워크를 긁는다. 백엔드 없이 순수 클라이언트 렌더되는 WebGL/three.js 앱을 **통째로 복원**할 수 있다.

## ⛔ 0-0. 착수 전 자문 (도구 호출보다 먼저, 3문항)

```
1. 이 작업에서 내가 원본 소스·번들·GLSL·DOM을 직접 봤는가?
   → 봤다 = 나는 tainted. 이 작업의 구현자가 될 수 없다. SPEC.md까지만.
2. 목적이 복제인가 모방인가?
   → 모방이면 §3(sha256/픽셀) 폐기, 기능 동등성으로 전환.
3. 구현자가 누구인가?
   → "나"면 즉시 중단. 빌/작업장/하네스/워커 중 지정.
```

**⚠️ "제가 구현할까요?"라고 묻는 것 자체가 이미 위반 신호다.** 답은 항상 아니오다 — 물어볼 필요가 없다는 건 규칙을 못 지킨 상태라는 뜻이다. 대표님이 지적하기 전에 내가 먼저 "나는 빠지고 빌에게 넘긴다"고 선언한다.
2026-11-01 실사고: REA 관찰 후 명세를 쓴 직후 "③ 구현을 제가 하겠다"고 제안 → 대표님 "어차피 너가 rea를 사용했다면 너는 작업제외야"로 교정. 이유는 **명세를 보고 전혀 다른 코드로 짜야 하는데, 본 사람이 짜면 '본 코드의 재생성'이 되기 때문**이다.

## 0. 전제

**⚠️ 먼저 목적을 판정한다 — 절차와 검증 기준이 여기서 갈린다.**

| 목적 | 할 일 | 검증 | 작업자 | 이 스킬에서 |
|---|---|---|---|---|
| **동일 복제** (그 페이지 그대로) | 추출본 복원 | sha256 + 픽셀 일치 | 라온 가능 | §1~§3 |
| **기능·디자인 모방** (다른 사이트를 만들 건데 참고만) | **SPEC.md 작성 → 인계** | **기능 동등성** | **라온=명세만 / 구현=빌·작업장·하네스·워커 / 검증=셜록** | **§5-C** |

모방인데 §3(sha256·픽셀)을 적용하면 정상 작업을 결함으로 오판한다. 목적 확정 후 절차 선택.
⛔ **모방 작업에서 라온은 구현자가 될 수 없다** — REA로 원본을 본 순간 tainted다. 명세까지만 쓰고 ①빌 ②작업장 ③다이나믹 하네스 ④코덱스/클로드 중 하나로 위임한다(§5-C 인계 경로). 구현 단계는 `frontend/website-cloner`(복제 절차)도 **아니다** — 그건 원본과 똑같이 만드는 도구라 모방 목적과 반대다.

- REA 설치: `npm install --global rea-agents` (rea@6.0.0). MCP 등록은 `rea --mcp`.
- **앱 9090 포트를 절대 침범하지 말 것** — 전용 headless Chrome을 **별도 포트**(9222/9333/9335)에 띄운다.
- CLI는 `kebab-case`, MCP 도구는 `snake_case`. 일부는 MCP 전용.

## 1. 파이프라인 (실증 순서)

**⚠️ CLI는 endpoint/targetId/입출력 경로가 전부 positional이다. `--endpoint` / `--out` 같은 플래그는 없다(실패한다).**

```bash
# 0) 전용 headless Chrome 기동 — 페이지를 먼저 띄우고 시작한다 (REA는 수동 관찰이라 스스로 열지 못함)
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new \
  --remote-debugging-port=9333 --remote-allow-origins=* \
  --user-data-dir="C:\daon\_rea_chrome" --no-first-run --no-default-browser-check \
  --enable-unsafe-swiftshader --use-angle=swiftshader --window-size=1280,720 \
  "https://threejs.org/examples/webgl_shaders_ocean.html"
# 백그라운드로 띄우고, 타깃 id를 확보한다 (type=='page' 인 것)
curl -s http://127.0.0.1:9333/json/list

E=http://127.0.0.1:9333 ; T=<targetId from /json/list>

# 1) 타깃 목록 (endpoint positional)
rea list-browser-targets $E --format json

# 2) 페이지 정적 관찰 → 캡처 JSON 저장 (⚠️ --include-script-sources 는 opt-in, 없으면 소스 안 옴)
rea inspect-web-page $E $T --include-script-sources --format json > C:/daon/_rea_cap.json

# 3) 캡처 JSON에서 스크립트 추출 (⚠️ 입력=<캡처 JSON>, 출력=<존재하지 않는 디렉토리> 둘 다 positional)
rea export-web-scripts C:/daon/_rea_cap.json C:/daon/_rea_scripts

# 4) 번들 정적 분석 (소스맵 시도)
rea analyze-web-bundle $E $T --fetch-source-maps --format json > C:/daon/_rea_bundle.json

# 5) 렌더 스크린샷 (base64는 normalized_result.artifact.data_base64)
rea capture-web-screenshot $E $T --format json > C:/daon/_rea_shot.json
```

**MCP 경유 시 차이** (`call_mcp_tool`, server=rea):
```python
list_browser_targets {"cdp_endpoint": "http://127.0.0.1:9333"}
inspect_web_page     {"cdp_endpoint": E, "target_id": T}   # cdp_endpoint/target_id 둘 다 필수
```
- MCP는 `cdp_endpoint`가 **필수** — 빠지면 `Invalid input: expected string, received undefined`.
- MCP `inspect_web_page`는 opt-in 없이는 `script_sources`를 `not_approved`로 제외한다(`completeness.excluded`에서 확인).
- **관찰은 REA attach 시점부터만 유효** — 이전 네트워크/콘솔은 `prior_activity_available: false`로 못 본다. 캡처는 attach 직후 수행할 것.

**출력이 최대 28만 자** → MCP로 그대로 받으면 컨텍스트가 터진다. **CLI + `> file.json` 리다이렉트 + 파이썬 요약**을 기본으로 쓴다(`scripts/rea_summarize.py`).

산출물 트리(export-web-scripts):
```
_rea_scripts/
├─ manifest.json
└─ files/
   ├─ isolated/source-<hash>.js            # 인라인 스크립트
   └─ origins/<origin-hash>/<path>.js      # 서버에서 받은 모듈 원문
```

## 2. 로컬 복원 (재구성)

추출한 파일들을 **원본과 동일한 디렉토리 구조**로 재배치한다. URL 경로가 디렉토리 구조가 된다.

```
_rea_rebuilt/
├─ build/three.core.js
├─ build/three.module.js
├─ examples/jsm/objects/Water.js
├─ examples/jsm/objects/Sky.js
├─ examples/jsm/postprocessing/UnrealBloomPass.js
├─ examples/jsm/controls/OrbitControls.js
├─ examples/jsm/shaders/*.js
├─ examples/textures/waternormals.jpg    # ⚠️ 텍스처(.jpg/.png)도 export-web-scripts가 회수함
├─ examples/main.css
└─ examples/webgl_shaders_ocean.html     # 진입점
```

로컬 서빙: `python -m http.server 18765` (⚠️ 8099 금지 — 아래 함정 ①).

## 3. 검증 (필수)

```bash
# sha256 바이트 일치 — 추출본 vs 복원본
for rel in build/three.core.js examples/jsm/objects/Water.js ...; do
  diff <(sha256sum "$SRC/$rel"|cut -d' ' -f1) <(sha256sum "$RB/$rel"|cut -d' ' -f1) && echo "MATCH $rel"
done

# 픽셀 해상도 일치 — PIL (파일 크기는 달라도 무방, 압축률 차이)
python -c "from PIL import Image; print(Image.open('a.png').size, Image.open('b.png').size)"
```

**렌더 판정 정답 (WebGL 스크린샷 함정 회피):**
- `browser_vision` 스크린샷은 **WebGL을 미합성**해 검게 나온다 → 실패로 오독 금지.
- 판정은 ① `browser_console` 에러 0건 ② `canvas.getContext('webgl2')` + `gl.getError()===0` ③ title/GUI 요소(예: lil-gui의 Sky/Water/Bloom 패널) 노출.
- `gl.readPixels` 중심 픽셀이 0이어도 정상 — WebGL 기본 `preserveDrawingBuffer:false`면 버퍼가 클리어된 뒤다. 렌더 확인은 `canvas.toDataURL()`(preserveDrawingBuffer:true 필요) 또는 REA `capture-web-screenshot`로.

## 3-B. 다온 내부 브라우저로 검증 (챗창 카드)

복원본을 **다온 챗창 내장 브라우저(8088 서비스)** 로 띄워 육안 확인한다. 에이전트 도구와 **완전히 같은 길**이다.

```bash
curl -s -X POST http://127.0.0.1:9090/api/browser/navigate \
  -H "Content-Type: application/json" \
  -d '{"url":"http://127.0.0.1:18765/examples/webgl_shaders_ocean.html"}'
```

경로: `POST /api/browser/navigate` → `browser_service(8088)` 위임(`call_browser_api`) → SSE `tool` 이벤트 → 프론트 `app.js onToolCall` → `liveBrowser.mount(viewerSlot)`.

```bash
curl -s http://127.0.0.1:8088/health
# {"browser_running":true,"current_url":"http://127.0.0.1:18765/...","viewers_count":1,...}
```

- `viewers_count: 1` = 대표님 챗창에 카드가 붙은 것.
- 카드에 뜨는 화면은 **threejs.org가 아니라 로컬 복원본(18765)** 이다 → 이게 곧 "바이트 동일"의 육안 증거.
- 뷰어가 안 붙을 때: ① 챗에서 턴 한 번 돌리기(SSE `tool` 유발) ② 새로고침(자동복구 `restoreOnLoad`) ③ 🌐 FAB 클릭(`manualOpen`).
- ⚠️ 9090 `/api/browser/status`의 `status:"disconnected"`는 이 경로와 무관(레거시 9222 워커 기준). **8088 `/health`의 `viewers_count`가 진실.**

## 4. 함정 (실측 기록)

| 함정 | 대응 |
|---|---|
| **8099 = 다온 인증 게이트(DaonAuthGate) 전용** | 로컬 테스트 서버로 8099 쓰지 말 것. Windows SO_REUSEADDR로 중복 바인딩이 "성공"해도 실제 서빙은 게이트로 감 → 복원본이 "다온·잠금" 페이지로 출력됨. 18765 등 자유 포트 사용 |
| Chrome 재사용 함정 | ⚠️ **진단 신호: 페이지 title이 엉뚱하면(예: "다온 · 잠금") 옛 프로세스가 그 포트를 계속 점유 중이고 새 Chrome은 조용히 바인딩 실패한 것.** `netstat -ano \| grep :9333` → LISTENING PID 확인 → `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" \| Where CommandLine -like '*_rea_chrome*' \| Stop-Process -Force` 로 정리 후 **새 포트 + 새 프로필**로 재기동 |
| 잠금화면이 나옴 | ① 8099를 침범한 경우(SO_REUSEADDR 함정) ② `office.raonagent.stream`은 대표님 모바일 즐겨찾기 "다온 잠금" **그 자체** — 설계상 항상 잠금을 반환한다(정상). 복원 테스트와 무관 |
| 스크린샷 파일 크기가 다름 | 정상 — PNG 압축률 차이. **판정은 크기가 아니라 viewport 해상도 + sha256(코드) + 예외 0건** |
| 애니메이션 사이트의 픽셀 불일치 | 정상 — `performance.now()` 기반 매 프레임 갱신이라 원본/복원본은 **같은 장면의 다른 시점 프레임**. 8×8 다운샘플 평균차로 구조 유사도만 확인 |
| CDP `Target.createTarget` | 앱 내장 브라우저에선 `Not supported` — 새 탭 생성 대신 **별도 Chrome을 직접 띄운다** |
| `--include-script-sources` 누락 | 소스가 안 옴(opt-in). REA는 기본적으로 스크립트 본문을 안 준다 |
| `export-web-scripts` 출력 디렉토리 존재 | 이미 존재하면 실패 — 빈 디렉토리를 미리 만들고 그 **부모**를 넘김 |
| 소스맵 unavailable | 사이트가 미선언이면 복원 불가 → 단 원문이 비난독화면 지장 없음 |
| 배포본이 수정된 라이브러리 | 예: three.js가 `renderer.setEffects`/`THREE.Timer`/`Sky cloudCoverage` 커스텀 → npm 표준판으로는 동일 효과 재현 불가. 복원한 GLSL/코드를 직접 이식해야 |

## 5. 판정 규칙

- "웹사이트 소스 뜯을 수 있어?" → **네.** 단 REA는 **정적 관찰**(실행 안 함). 런타임 호출순서는 `observe-web-execution`, 이벤트 리스너는 `inspect-web-event-listeners`.
- 백엔드 유무: inspect-web-page의 endpoints가 WebGL 확장 조회뿐이면(`WEBGL_multi_draw`, `EXT_texture_filter_anisotropic` 등) **순수 클라이언트 렌더**.
- 커스텀 GLSL 셰이더는 난독화와 무관하게 **평문 그대로** 추출된다 — WebGL이 GLSL을 런타임 컴파일하므로 JS 안에 문자열로 반드시 존재한다.
- **three.js/WebGL은 DOM에 장면이 없다** — `<canvas>` 하나뿐이라 DOM 긁기로는 아무것도 안 나온다. 씬 그래프는 `observe-javascript-runtime`으로 런타임 덤프(살아있는 `THREE.Scene` 순회), 에셋은 네트워크 캡처(`.glb`/`.hdr`/텍스처 URL)로 얻는다.
- 서버가 있는 앱: **프론트엔드는 완전 복원 / 백엔드는 API 계약까지만** (엔드포인트·요청·응답 형태는 네트워크 캡처로 역추적, 서버 내부 로직은 불가).

### 5-B. "이 기능도 REA로 뽑히나?" 판정 — 실측 사례 (노션 협업, 2026-10-19)

번들 227파일 20.1MB를 정적 분석해 **뽑히는 것 / 안 뽑히는 것**을 실측했다.
절차: `tools/probe_collab_reach.py` (카테고리별 심볼 카운트 + API/WS 엔드포인트 추출), `tools/probe_crdt_and_sync.py` (심볼 실체 확인).

**✅ 뽑힌다 — 클라이언트에 존재하는 모든 것**

| 대상 | 실측 |
|---|---|
| 댓글 UI·데이터 모델 | `discussion` 957, `comments` 188, `resolved` 356, `context` 1513, `attachments` 57, `reactions` 37 |
| 권한 체계 | 역할 이름 전부 노출 — `editor`/`commenter`/`viewer`/`guest`/`admin`/`owner`/`member`, `can_edit`, `can_comment`, `read_only`, `full_access`, `public_permission` |
| 커서·프레즌스 | `presence` 106, `cursor` 511 |
| 서버 **인터페이스** | `syncRecordValues`, `saveTransaction`, `submitTransaction`, `loadPageChunk`, `/api/v3/` |
| 아키텍처 힌트 | `isCrdtEnabled`, `useCrdt`, `crdt_format_version`, `CrdtOperationAlreadyAppliedError`, `legacyNonCrdt`(105) → **노션은 자체 CRDT로 마이그레이션 중** |

**❌ 안 뽑힌다 — 클라이언트에 존재하지 않는 것**

| 대상 | 이유 |
|---|---|
| **CRDT 병합 알고리즘** | 노션은 Yjs가 아니라 **자체 구현**(`Yjs` 단 1건, `Automerge` 0건). minified + 서버측 처리라 복원 불가 |
| **권한 검증 로직** | 클라이언트는 UI만. 실제 판정은 서버 |
| **실시간 전송 인프라** | 협업 WS 주소조차 번들에 없음. 추출된 `wss://` 3건은 전부 **서드파티 STT**(x.ai, fireworks, assemblyai) |
| DB·메시지 브로커·프레즌스 서버 | 애초에 클라이언트 밖 |

**⚠️ 결정적 구분: "REA로 못 뽑는다" ≠ "못 만든다"**

협업 기능은 REA가 애초에 필요 없는 영역이다. 공개 자원으로 구현한다:

| 필요 요소 | 공개 대안 (라이선스) |
|---|---|
| 동시편집 병합 | **Yjs** (MIT), **Automerge** (MIT) — 노션도 결국 CRDT를 쓴다는 사실이 방향 검증 |
| 실시간 전송 | WebSocket 직접 / Supabase Realtime / Liveblocks / PartyKit |
| 댓글·권한 | 자체 서버이므로 직접 설계 (REA 대상 없음) |

**판정 규칙**: 대표님이 "이것도 뽑을 수 있나?" 물으면 →
① **클라이언트에서 실행/렌더되는가** → 뽑힌다 (심볼 카운트로 증명)
② **서버에서 판정/저장/중계되는가** → 인터페이스까지만
③ **표준 공개 라이브러리로 해결되는 문제인가** → REA 불필요, 공개판 사용이 더 빠르고 안전

숫자로 답할 것 — "됩니다/안됩니다"만 말하면 오진한다. 카운트 0이면 "없다"가 아니라 "다른 이름일 수 있다"로 취급하고 `\w*<심볼>\w*` 로 형태를 뽑아 확인한다(예: `crdt` → `legacyNonCrdt`/`crdt_data`/`isCrdtEnabled` 로 정체 확인).

### 5-C. 모방용 기능 명세서(SPEC.md) — "완전 복제 말고, 기능/디자인만 떠서 다른 사이트 만들기"

> ## ⛔⛔ 라온은 이 작업의 구현자가 될 수 없다 (tainted)
>
> **REA로 원본 소스를 본 순간 라온은 오염(tainted)됐다.** SPEC.md까지만 쓰고 **손을 뗀다.**
>
> - ❌ 라온이 직접 `index.html`/`app.js`/컴포넌트를 작성 → 무의식적으로 원본 코드 구조·변수명·함수 분해를 따라간다. **명세를 보고 "전혀 다른 코드"로 짜야 하는데, 본 사람이 짜면 재생성에 가까워진다 = 클린룸 위반.**
> - ✅ 라온 = 관찰·명세 작성·인계까지만
> - ✅ 구현 = **아래 4경로 중 하나** — ①`빌`(`delegate_to_agent`) ②작업장(`open_workshop`+`delegate_to_agent` 분업) ③`execute_dynamic_harness` ④`worker-codex`/`worker-claude`(`delegate_to_worker`) — 전부 **SPEC.md 단독 수령**
> - ✅ 검증 = `셜록`(`delegate_to_agent`) — 구현자와 다른 사람
>
> **프로필 기본값("라온은 만능 수석 엔지니어, 단독 작업 주도")은 이 경우 적용되지 않는다.** 클린룸은 역할 분리가 본질이고, 혼자 다 하면 그 자체로 실패다.
> 위반 신호: "내가 명세 썼으니 내가 빨리 짜면 되겠다" — 이 생각이 들면 이미 규칙 위반이다.

§5-B는 "뽑히나?"까지만 답한다. **모방 목적이면 그 다음에 SPEC.md를 써야 하고, 여기서 검증 기준이 뒤집힌다.**

| 목적 | 산출물 | 검증 기준 | **작업자** | 쓰는 절차 |
|---|---|---|---|---|
| 동일 복제 | 추출본 그대로 복원 | sha256 바이트 일치 + 픽셀 일치 (§3) | 라온 가능 | §1~§3 |
| **기능·디자인 모방** | **SPEC.md** | **기능 동등성** — 바이트/픽셀 일치는 목표가 **아님** | **라온=명세만 / 구현=빌·작업장·하네스·워커 / 검증=셜록** | **§5-C** |

⛔ §3의 sha256·픽셀 검증을 모방 작업에 그대로 쓰면 **잘 만든 걸 결함으로 오판**한다(폰트를 내 것으로 바꾼 게 정상인데 "3.96% 어긋남"으로 FAIL 처리하는 식). 목적을 먼저 확정하고 절차를 고른다.

**실증**: `D:\daon\_cleanroom\notion-rebirth\SPEC.md` (2026-10-19, Notion 7.30.0 정적관찰 → 202줄 명세 → 클린룸 재구현)

**빈 양식**: 이 스킬의 `assets/SPEC.template.md` — 복사해서 `<프로젝트>-rebirth/SPEC.md`로 채운다.

#### 산출물 위치
`D:\daon\_cleanroom\<프로젝트>-rebirth\SPEC.md` (+ `research/` 관찰 원본, `tools/` 프로브 스크립트)

#### SPEC.md 필수 7섹션 (노션 실증 포맷 그대로)

| § | 섹션 | 내용 | 없으면 생기는 일 |
|---|---|---|---|
| 0 | **티어 판정** | A=퍼블릭/오픈소스(그냥 사용+출처) / B=아이디어·데이터모델·UX패턴(자유 재구현) / C=고유 창작표현: 로고·워드마크·마케팅카피·브랜드 색/폰트조합(⛔ **사용 금지**, 자체 디자인 대체) | 표절과 재사용 경계가 무너짐 |
| 1 | **관찰 증거** | 실측 어휘 — 번들 문자열 심볼 카운트, 엔드포인트. "추정"이 아니라 숫자 | 구현자가 근거 없이 지어냄 |
| 2 | **기능 인벤토리** | 모듈별(M1~M10) 기능 목록. 각 항목에 구현 필요/불필요 판정 | 범위가 무한정 늘어남 |
| 3 | **데이터 모델** | 엔티티·필드·관계를 **아이디어 수준**으로 (원본 코드 아님) | 구현자마다 스키마가 달라 통합 불가 |
| 4 | **아키텍처** | 스택·모듈 경계 (라온 결정) | 구현자 재량으로 갈라짐 |
| 5 | **NON-GOALS** | 베끼지 **않을** 것 명시 | 원본 흉내내다 C티어 침범 |
| 6 | **ACCEPTANCE** | 동등성 검증 항목 (셜록/QA 인계용) | "다 만들었다"와 "동작한다" 구분 불가 |

#### 방화벽 선언 (문서 첫머리에 반드시 박는다)

```markdown
> **작성**: 라온(관찰자, tainted) · **근거**: REA 정적 관찰 + CDP 직접 관찰
> **방화벽**: 이 문서는 구현자에게 전달되는 유일한 입력이다. 원본 코드·에셋·정확한 색상값은 포함하지 않는다.
> **검증 기준**: 기능 동등성 (동작 일치). 코드·바이트 동일성은 목표가 아니다.
```

#### 절차

```bash
# 0) 목적 확정 — 복제인가 모방인가? 모방이면 §3(sha256/픽셀) 폐기, 기능 동등성으로 전환
# 1) REA 정적 관찰 (§1과 동일)
rea inspect-web-page $E $T --include-script-sources --format json > _rea_cap.json
# 2) 뽑히는 범위 판정 (§5-B) — 클라이언트에 있는 것/없는 것
python tools/probe_collab_reach.py    # 카테고리별 심볼 카운트
# 3) 기능 인벤토리 → SPEC.md §2  (증거 숫자와 함께)
# 4) 데이터 모델 추론 → §3   ⚠️ 코드 복사 금지, 아이디어/스키마만
# 5) NON-GOALS 확정 → §5     ⚠️ C티어(브랜드 표현)는 전부 여기로
# 6) SPEC.md를 구현자에게 단독 입력으로 인계 (원본 소스는 넘기지 않는다)
#    ★ 라온은 여기서 손을 뗀다. 구현 착수 금지.
```

#### 인계 경로 — 4가지 중 하나를 고른다 (라온은 직접 구현 금지)

**먼저 "무엇을 주고 무엇을 주지 않는가"를 확정한다.**

| 대상 | 라온(관찰자) | 구현자(워커/에이전트) | 이유 |
|---|---|---|---|
| **SPEC.md** (기능) | 작성 | ✅ **받는다** | 이것만으로 구현해야 클린룸 성립 |
| **DESIGN.md** (다른 사이트에서 뽑은 디자인) | 작성 | ✅ **받는다** | 새 껍데기의 재료. 원본과 달라지는 근거 |
| **클로너 산출물** (`index.html`/`styles.css`/`app.js`) | ⚠️ **참고 OK** | ❌ **받지 않는다** | 라온이 **동작 관찰용**으로 쓰는 건 유용 — 버튼 클릭 시 상태 전이, 탭 전환 시 사라지고 나타나는 요소, 폼 검증 흐름, 스크롤 고정 구간을 **눈으로** 확인해 SPEC.md `BEHAVIOR`에 글로 옮긴다. 단 워커가 코드를 받으면 구조·클래스명·C티어(로고·브랜드색·카피)를 그대로 물려받아 "다른 코드"가 아니라 **복제본 재편집**이 된다 |
| 원본 소스·번들 (`research/`) | ⚠️ 참고 OK | ❌ 금지 | 동일 원리 |
| 스크린샷·녹화 | 참고 OK | ⚠️ 참고만, 입력 아님 | 픽셀을 맞추려 든다 |

> **핵심**: 클로너 산출물은 **라온의 관찰 보조 자료**다. REA가 정적 소스만 보여주는 반면 클로너는 렌더된 DOM과 실제 동작을 보여주므로, **인터랙션 상세를 뽑는 데 REA보다 낫다.** 다만 그 관찰 결과는 반드시 **산문(SPEC.md `BEHAVIOR`)으로 번역**해서 넘긴다. 코드를 넘기지 않는다.

> **노션 재구현 예시**: 노션에 클로너를 돌려 **버튼·탭 동작을 관찰**하고 → 그걸 SPEC.md `BEHAVIOR`에 글로 적는다. 워커에게는 `SPEC.md`(노션 기능) + `DESIGN.md`(B 디자인) **두 장만** 준다. 클로너 코드는 넘기지 않는다.

**① 다른 에이전트 (단일 구현 — 기본값)**
```
delegate_to_agent(agent_name='bill', task='''
D:\\daon\\_cleanroom\\<프로젝트>-rebirth\\SPEC.md 를 읽고 구현해라.
- 이 문서가 유일한 입력이다. 원본 사이트 소스·번들은 열람 금지.
- SPEC에 없는 기능 추가 금지.
- 원본과 다른 코드로 처음부터 작성한다 (구조·변수명을 원본에서 가져오지 않는다).
''')
```

**② 작업장 (여러 에이전트 분업 — 규모 클 때)**
```
open_workshop()                                    # 작업장(멀티뷰) 먼저 연다
delegate_to_agent(agent_name='bill',  task='SPEC.md 기반 백엔드/API 구현')
delegate_to_agent(agent_name='prada', task='DESIGN.md 기반 UI 구현')
delegate_to_agent(agent_name='tony',  task='SPEC.md §2 기능 인벤토리 검토·범위 확정')
```

**③ 다이나믹 하네스 (여러 도메인이 얽힌 대규모 재창조)**
```
execute_dynamic_harness(
  task='SPEC.md(기능) + DESIGN.md(디자인)로 <앱명>을 새로 구현. 클린룸: 원본 소스 접근 금지.',
  skills=['rea-web-app-reverse-and-restore'],   # 명세 절차만 참조
  acceptance_criteria=['SPEC §6 ACCEPTANCE 전항목 통과', 'C티어(원본 로고·카피·브랜드색) 0건']
)
```

**④ 외부 CLI 워커 (Claude Code / Codex)**
```
delegate_to_worker(worker_name='worker-codex',  prompt='<①과 동일 내용>')
delegate_to_worker(worker_name='worker-claude', prompt='<①과 동일 내용>')
```

**검증 — 셜록 (구현자와 반드시 다른 사람)**
```
delegate_to_agent(agent_name='sherlock', task='''
SPEC.md §6 ACCEPTANCE 항목으로 구현물을 판정해라.
기능 동등성만 본다. 원본과 픽셀이 다른 것은 결함이 아니다.
''')
```

| 규모 | 고르는 경로 |
|---|---|
| 단일 화면·한 모듈 | ① 빌 |
| 프론트+백엔드 분업 필요 | ② 작업장 |
| 여러 도메인·대규모 | ③ 다이나믹 하네스 |
| 외부 모델 판단이 필요 | ④ 워커 |

⚠️ **원본 소스를 구현자에게 같이 넘기면 클린룸이 깨진다.** SPEC.md(+DESIGN.md)만 넘긴다.
⚠️ **라온이 구현을 대신 하면 §5-C 상단 금지 조항 위반이다.** 어떤 경로든 실패하면 **같은 경로에 재지시**한다. 라온이 대타로 짜면 "명세 보고 재생성"이 되어 클린룸 목적 자체가 무효화된다.

## 6. 동봉 스크립트 (scripts/)

| 스크립트 | 용도 |
|---|---|
| `rea_summarize.py` | 방대한 Evidence JSON을 구조 요약 + 스크린샷 PNG 복원. 모드: `bundle` / `shot` / `capture` |
| `console_probe.py` | CDP로 canvas·WebGL2·JS 예외를 직접 진단(렌더 성공 판정). `python console_probe.py <port>` |
| `verify_hashes.py` | manifest.json 기반 sha256 대조 — "복제했다"의 객관적 증거 |
| `serve_and_open.sh` | 빈 포트 확인 → 로컬 서빙 → 다온 내부 브라우저로 띄우기 |

## 7. 산출물 규칙
- 추출 소스: `C:\daon\_rea_scripts\`
- 복원 웹앱: `C:\daon\_rea_rebuilt\`
- 렌더 PNG: `C:\daon\output\ocean_render.png`(원본) / `ocean_rebuilt.png`(복원)
- 런타임 관찰·스크립트: `observe-javascript-runtime` / `inspect-web-event-listeners` (아직 미실증)

## 8. ⭐이 스킬을 수정했을 때 — 배포 게이트 (필수)

⛔ **"SKILL.md 고쳤다"로 끝내지 말 것.** 정본은 `~/.hermes/profiles/raon/skills/**` 한 곳뿐이고, 다른 런타임(Claude·Codex·DAON 앱·dist_new)은 **각자 사본을 읽는다.** 배포 없이 완료 보고하면 그 런타임에서는 구버전이 계속 돈다.
(2026-11-01 실사고: 이 스킬을 신설하고 "완료" 보고했으나 실제로는 1/7곳에만 존재 → 전 런타임 구버전/미존재.)

### 배포 대상 7곳 (디렉터리 통째로 — `assets/`, `scripts/` 포함)

```bash
SRC=~/.hermes/profiles/raon/skills/software-development/rea-web-app-reverse-and-restore
for base in \
  ~/.hermes/skills \
  ~/.claude/skills \
  ~/.codex/skills \
  "C:/daon/Daon agent System/skills" \
  "$LOCALAPPDATA/Programs/daon-agent-system/resources/skills" \
  "$LOCALAPPDATA/Programs/daon-agent-system/resources/dist_new/skills" ; do
  rm -rf "$base/rea-web-app-reverse-and-restore"
  cp -r "$SRC" "$base/"
done
```

### 완료 판정 (눈으로 세지 말고 카운트로)

```bash
# 7곳 전부 같은 바이트수여야 통과. 하나라도 다르면 그 런타임은 구버전.
for p in <7개 경로>; do echo "$(wc -c < "$p/SKILL.md")  $p"; done
```

- ✅ 통과 기준: 7줄의 바이트수가 **전부 동일**, `find <사본> -type f | wc -l`도 동일
- ⛔ `.bak*` 파일과 `scripts/__pycache__`는 세지 말 것 (개수 검증 오염)

### ⚠️ 함정 2개

1. **앱이 3P skills-plugin 캐시를 재생성하면 롤백된다** — Claude-3p 앱은 `local-agent-mode-sessions\skills-plugin\...\skills\<name>\SKILL.md`만 읽는다. 앱 실행 후 사본 크기가 줄었으면 캐시가 덮어쓴 것 → 같은 곳에 한 번 더 배포.
2. **`skills-index.json` 스테일** — `skill_view`로는 로드되는데 `/api/skills` 목록에 안 뜨면 인덱스가 낡은 것(파일시스템 스킬 수 > 인덱스 entries). 같은 위치의 **다른 스킬도 같이** 누락됐다면 내 파일 문제가 아니므로 `rebuild_index(force=True)`로 재빌드 → 서버가 mtime 변화를 감지해 자동 재로드.
