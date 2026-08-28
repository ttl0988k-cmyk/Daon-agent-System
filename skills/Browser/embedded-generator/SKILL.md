# embedded-generator — 임베드 생성기 직접 열기

## 언제 사용하나

- upsampler.co처럼 **생성기/플레이어가 iframe으로 임베드된 사이트**에서
  화면이 잘 안 보일 때 (iframe이 페이지 레이아웃에 묻혀 있는 경우)
- 대표 예: `upsampler.co` → `wan-ai-wan2-1.hf.space` iframe 임베드

## 핵심 규칙

1. **iframe URL을 직접 열면 해결된다.** iframe 자체는 정상 렌더링되므로,
   그 URL을 브라우저에서 직접 열면 전체 화면으로 사용할 수 있다.
2. **자동으로 무조건 새 탭을 열지 마라.** 광고/분석 iframe까지 열면 탭 폭탄이 된다.
   반드시 아래 절차로 "큰 iframe"만 골라서 판단한다.

## 절차

1. `browser_navigate` 결과(또는 `browser_snapshot` 결과)의 **`iframes` 필드**를 확인한다.
   - 백엔드가 400x300 이상의 큰 iframe만 골라 `{src, width, height}` 목록으로 반환한다.
   - `hint` 필드가 있으면 이 사이트는 임베드 구조라는 신호다.
2. `iframes`가 비어 있으면 → 일반 페이지이므로 아무것도 하지 않는다.
3. `iframes`가 있고 사용자가 생성기/플레이어를 보고자 하는 상황이면:
   - **가장 큰 iframe(면적 최대)의 `src`**를 `browser_navigate`로 직접 연다.
   - 예: `browser_navigate(url="https://wan-ai-wan2-1.hf.space")`
4. 사용자에게 "생성기를 전체 화면으로 직접 열었습니다"라고 알린다.

## 주의

- iframe src가 로그인/리다이렉트를 요구할 수 있다 — 그 경우에도 직접 열면
  같은 세션(쿠키)을 공유하므로 대부분 바로 동작한다.
- `iframes` 필드는 navigate/open/snapshot 응답에 항상 포함된다 (큰 iframe이 있을 때만).
