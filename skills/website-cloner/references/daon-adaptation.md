# 다온 환경 클론 실전 레시피

Windows + Python 3.12 + Chrome + Playwright + ego-browser 조합에서 실제로 쓰는 절차.
`SKILL.md`의 4단계를 이 환경에서 어떻게 수행하는지에 대한 부록이다.

---

## A. 도구 선택 결정표

| 상황 | 쓸 도구 | 이유 |
|---|---|---|
| 정적 HTML/CSS/폰트 목록 뽑기 | `probe_page.py` (requests) | 렌더 없이 빠르고, 원본 소스·CSS 변수 보존 |
| 렌더 후 외형·computed style | `capture_page.py` (Playwright, `channel="chrome"`) | JS 주입 사이트·Framer 대응 |
| 상태 조작(탭·아코디언·hover) | `ego-browser` 스킬 | 세션 유지, 실제 입력 이벤트 |
| 화면 판독 | `vision_analyze` | 픽셀 차이를 눈으로 확인 |
| 대량 에셋 | `download_assets.py` | Referer·포맷 판정·충돌 처리 |
| 프레임워크 소스가 필요 | `git clone` / `curl` | 브라우저 금지 |

⛔ 스켈레톤 브라우저(`browser_*`)는 대표님이 "스켈레톤으로"라고 말할 때만. 일반 브라우징은 ego-browser.

---

## B. Playwright 캡처 표준

`capture_page.py`가 자동으로 하는 일이지만, 수동 제어가 필요할 때의 정답 순서:

```python
browser = p.chromium.launch(channel="chrome", headless=True)   # 시스템 Chrome 사용(별도 설치 불필요)
page = browser.new_page(viewport={"width":1440,"height":900},
                        device_scale_factor=1,
                        reduced_motion="no-preference")
page.goto(url, wait_until="networkidle", timeout=90000)
page.evaluate("document.fonts.ready")          # 웹폰트 스왑 완료까지
page.wait_for_timeout(800)                     # 엔트런스 애니메이션 정착
```

### 풀페이지 캡처의 함정
- `full_page=True`만 쓰면 **스크롤 리빌 콘텐츠가 미노출 상태로 찍힌다.**
  1. 먼저 끝까지 스크롤해 lazy 미디어를 로드한다
  2. 맨 위로 돌아와 정착을 기다린다
  3. 그 다음 `full_page=True`
- `position:fixed` 헤더는 풀페이지 캡처에서 페이지 중간에 한 번만 그려진다 → 섹션별 캡처를 병행한다.
- 스크롤 애니메이션 사이트는 **뷰포트 단위 캡처**(`clip`)가 더 정확하다.

### 스크롤은 실제 입력으로
```python
page.mouse.wheel(0, 600)      # ✅ smooth scroll / scroll-snap 트리거됨
# page.evaluate("window.scrollTo(0, 600000)")  # ❌ 휠 기반 동작을 우회
```
`window.scrollTo`는 관찰용으로만 쓰고, **동작 판정에는 쓰지 않는다.**

---

## C. 계측 JS 스니펫 (computed style 선언값 읽기)

한 번의 렌더링에서 추론하지 말고 **선언된 값**을 읽는다.

```javascript
// 선택자에 대한 기하 + 타이포 + 레이아웃 선언값
const el = document.querySelector(sel);
const r = el.getBoundingClientRect();
const cs = getComputedStyle(el);
return {
  sel, text: el.textContent.trim().slice(0, 120), rect: {x:r.x,y:r.y,w:r.width,h:r.height},
  font: {family:cs.fontFamily, size:cs.fontSize, weight:cs.fontWeight,
         lineHeight:cs.lineHeight, letterSpacing:cs.letterSpacing},
  color: cs.color, background: cs.backgroundColor,
  padding: cs.padding, margin: cs.margin, gap: cs.gap,
  radius: cs.borderRadius, position: cs.position,
  transform: cs.transform, transition: cs.transition, zIndex: cs.zIndex,
};
```

### CSS 변수 전수 덤프
```javascript
// :root 및 실제 사용 중인 커스텀 프로퍼티
const out = {};
for (const sheet of document.styleSheets) {
  let rules; try { rules = sheet.cssRules } catch { continue }  // CORS 시트는 건너뜀
  for (const rule of rules) {
    if (rule.style && rule.selectorText && /:root|html|body/.test(rule.selectorText)) {
      for (const p of rule.style) if (p.startsWith("--")) out[p] = rule.style.getPropertyValue(p).trim();
    }
  }
}
```

### 뷰포트 스윕 (브레이크포인트 탐지)
같은 선택자의 `fontSize`/`display`/`gridTemplateColumns`를 폭 320→1920에서 40px 간격으로 측정하고 **값이 바뀌는 폭**을 기록한다. 그 지점이 실제 미디어쿼리 브레이크포인트다.

```javascript
const widths = []; for (let w=320; w<=1920; w+=20) widths.push(w);
// 뷰포트를 바꿔가며 동일 셀렉터의 computed 값을 배열로 수집 → 변화 지점 = 브레이크포인트
```
`capture_page.py --sweep "sel"` 옵션이 이 작업을 자동화한다.

---

## D. 상태 증거 수집 (trigger → visible result)

| 상태 | 유발 | 캡처할 것 |
|---|---|---|
| 호버 | `page.hover(sel)` | before/after 스크린샷 + transform/opacity 변화 |
| 포커스 | `page.keyboard.press("Tab")` | 아웃라인·링 두께·색 |
| 아코디언/탭 | 실제 클릭 | 열림 높이, 형제 요소 이동, 내용 |
| 캐러셀 | 다음/이전 클릭 | 경계(첫·마지막)에서 버튼 disabled 여부 |
| 모바일 메뉴 | 뷰포트 390 → 햄버거 | 오버레이·스크롤 락(`body overflow`) |
| 스크롤 리빌 | 휠 스크롤 | 초기/활성/정착 3상태 |
| 고정 씬 | 스크롤 거리 | pin 시작·해제 지점, 레이어 속도 |

**핀(pin) 씬은 sticky 여부만으로 판정하지 않는다.** 스크롤 거리와 해제 동작이 필요하다.

---

## E. 에셋 함정 (실측으로 확인된 것)

1. **CDN은 확장자를 속인다.** `photo.png` URL이 `image/avif`를 반환한다. → 매직바이트로 판정:
   - PNG `89 50 4E 47` / JPEG `FF D8 FF` / GIF `47 49 46` / WebP `RIFF....WEBP` / AVIF·HEIC `....ftypavif|heic` / SVG는 앞쪽 `<?xml` 또는 `<svg`
2. **`currentSrc`를 써라.** `src`는 브라우저가 고르기 전 후보다. 반응형이면 `srcset` 중 실제 선택본이 `currentSrc`.
3. **핫링크 차단**: `Referer` 헤더를 원본 페이지로 넣어야 200이 오는 CDN이 있다.
4. **폰트는 variable/static 구분**: `font-weight: 500` 정적 파일을 variable 패밀리로 선언하면 본문이 과하게 굵어진다. 페이지가 실제 로드한 face만 선언.
5. **인라인 SVG는 단독 파일로 못 쓴다**: `xmlns`와 `defs`(그라디언트·마스크·심볼)를 함께 내보내야 `<img src>`로 렌더된다. 로컬 `<img>` URL 렌더로 검증할 것.
6. **애니메이션 SVG는 조상 셀렉터·공유 심볼·키프레임에 의존**한다. 시퀀스를 "같이 펄스하는 몇 개"로 바꾸면 원본 동작이 사라진다. 한 사이클 전체를 보고 옮긴다.
7. **HTTP 200이 정상 이미지는 아니다.** 에러 페이지가 200으로 오기도 한다 → content-type + 디코딩 성공 여부 둘 다 확인.

---

## F. Framer / 모션 사이트 특이사항

- Framer는 **데스크탑/모바일 변형을 중복 렌더**하고 브레이크포인트 클래스로 하나를 숨긴다. 모든 노드를 복사하지 말고 **각 뷰포트에서 보이는 노드**의 레이아웃/computed만 읽는다.
- `data-framer-name`이 섹션 식별자다. 해시 클래스명은 컴포넌트 아키텍처가 아니다.
- 하이드레이션·엔트런스 애니메이션을 기다린다. 풀페이지 스크린샷만으로는 오프스크린 리빌 콘텐츠가 안 보인다.
- Framer의 부드러운 스크롤은 **Lenis**: `html.lenis` 클래스 또는 `window.lenis` 전역이 옵션을 노출한다. 그 옵션을 그대로 재현한다.
- CMS 그리드는 제공된 라우트를 방문해 **모든 필터를 조작**한다. 1상태 정적 리스트는 필터링/내비게이션을 재현하지 못한다.

### 모션 구동원 판별 → 구현 매핑
| 구동원 | 증거 | 구현 |
|---|---|---|
| 시간 | 주기·일시정지·방향·루핑 미디어 | CSS keyframes, video, 애니메이션 라이브러리 |
| 스크롤 리빌 | 진입 경계·시작/끝 위치·역재생 | IntersectionObserver + CSS |
| 연속 스크롤 | 진행도·핀 지속거리·레이어 속도 | scroll progress + CSS 변수 또는 라이브러리 |
| 클릭/호버 | 대체 콘텐츠·전환·해제/이동 | React state + CSS |
| 포인터/캔버스 | 반응·렌더러/미디어 정체 | 동일 렌더러 또는 근사임을 고지 |

양방향·모바일 모두 관찰한다. 측정 가능한 이징/지속시간을 맞춘다. 루프는 컴포넌트 생명주기로 정리한다.

---

## G. 프레임워크 선택 (다온 기준)

| 소스 성격 | 권장 스택 | 이유 |
|---|---|---|
| 정적 랜딩, JS 최소 | **정적 HTML + Tailwind(또는 순수 CSS)** | 빌드 불필요, 즉시 프리뷰, 다온 라온스튜디오와 동일 패턴 |
| 다페이지·CMS·필터·라우팅 | **Next.js(App Router) + Tailwind** | 라우트/데이터 분리, 원본 템플릿과 동일 |
| 강한 상태·컴포넌트 재사용 | React + Vite | HMR 속도 |
| Framer/모션 다수 | Next.js + Motion(Framer Motion) | 레이아웃·모션 라이브러리 일치 |

⚠️ 원본 템플릿은 **Next.js 16 + React 19 + shadcn/ui + Tailwind v4**다. 이 스택을 쓸 경우 `node_modules/next/dist/docs/`의 해당 버전 문서를 **먼저** 읽는다(훈련 데이터와 API가 다르다).

### 정적 HTML을 고를 때의 함정
- 라온스튜디오류 정적 서버는 **루트 기준**으로 파일을 서빙한다. `/screens/final/x.html` 같은 하위 경로로 열면 상대 `js/`가 404가 나 **전체가 목업처럼 보인다.** → 서빙 루트 확인 후 루트 URL로 검증.
- `python -m http.server`로 즉시 프리뷰: MIME이 정상이라 mp4/wav도 브라우저에서 재생된다.

---

## H. 검증 명령 (다온 3게이트)

```bash
# Gate 1 — 빌드/런타임
npm run build 2>&1 | tail -20
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
  --dump-dom http://127.0.0.1:3000/ > /tmp/dump.html 2>/dev/null
grep -cE 'NaN|undefined' /tmp/dump.html

# Gate 2 — 시각 판독 (스크린샷 생성 후 vision_analyze에 직접 투입)
python "$SK/capture_page.py" http://127.0.0.1:3000/ --out docs/design-references \
  --viewports 1440x900,768x1024,390x844 --fullpage

# Gate 3 — 비교 수치
python "$SK/compare_shots.py" docs/design-references/src_1440.png \
  docs/design-references/local_1440.png -o docs/design-references/diff_1440.png
```

`compare_shots.py`는 다음을 출력한다: 크기 불일치, 평균 절대 오차(0~255), 차이 픽셀 비율(임계 12/255), 차이 영역 바운딩 박스. **차이율 < 2%**면 근접, 그 이상이면 4단계 우선순위(지오메트리→누락→타이포→에셋→간격)로 되돌아간다.

⚠️ 픽셀 차이는 **안티앨리어싱·폰트 힌팅·서브픽셀** 때문에 0이 되지 않는다. 0을 목표로 하지 말고 **구조적 차이**(섹션 유무, 폭, 줄바꿈)를 먼저 없앤다.

---

## I. 보고 템플릿 (대표님 보고용)

```
■ 클론 완료: <사이트명>
- 스택: Next.js 16 / 정적 HTML
- 라우트: / (소스 /) · /about (소스 /about)
- 섹션: 9개 (히어로·로고월·기능3·가격·FAQ·CTA·푸터)
- 전환: 스크롤 리빌 4, hover 12, 탭 1, 캐러셀 1
- 에셋: 이미지 34/34 다운로드, 폰트 3, 영상 1 (모두 200 + 포맷 검증)
- 검증: 빌드 PASS, 타입체크 PASS, 콘솔에러 0, 404 0, 가로오버플로 0
- 비교: 1440px 차이율 1.4% / 390px 2.1% (diff 이미지 첨부)
- 남은 격차: ⚠️ 히어로 WebGL 셰이더는 정적 이미지로 근사(미구현)
```
