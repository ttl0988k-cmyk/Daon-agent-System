---
name: ui-ux-pro
version: "1.0"
category: design
priority: high
tags:
  - design
  - ui
  - ux
  - accessibility
  - typography
  - color
  - layout
  - animation
  - responsive
  - frontend
conflicts_with: []
graph_requires: []
graph_compatible:
  - premium-ui
  - minimalist-ui
  - brutalist-ui
  - taste-design
  - taste
  - redesign-audit
  - html-anything
  - creative-director
  - self-reflection
  - full-output
graph_conflicts: []
purpose: "UI/UX 설계·구현·리뷰를 위한 종합 디자인 인텔리전스. 우선순위 기반 체크리스트, 스타일/컬러/타이포그래피/레이아웃/애니메이션 원칙, 스택별 구현 가이드를 제공하여 일관성 있고 접근성 높은 고품질 UI를 만들도록 이끈다."
when_to_use: "새 페이지/컴포넌트 설계, UI 리팩토링, 컬러/타이포그래피/간격/레이아웃 시스템 선택, UX/접근성/일관성 리뷰, 내비게이션/애니메이션/반응형 구현, 데이터 시각화 설계"
when_not_to_use: "순수 백엔드 로직, API/DB 설계, 비시각적 성능 작업, 인프라/DevOps, 시각적 변화가 없는 스크립트 작업"
inputs: "사용자 요구사항, 제품 유형(SaaS/이커머스/포트폴리오/대시보드/엔터테인먼트/도구), 타겟 사용자, 스타일 키워드, 기술 스택"
outputs: "우선순위 기반 디자인 결정, 접근성/UX 체크리스트 통과 코드, 스택별 모범 패턴 적용 결과물"
examples: "SaaS 대시보드 설계, 이커머스 상품 페이지, 포트폴리오 랜딩, 모바일 앱 UI, 데이터 시각화 차트, 다크모드 테마"
constraints: "접근성(대조비 4.5:1, 키보드 내비게이션)은 타협 불가. 스택을 가정하지 말고 프로젝트에서 감지. 0결과 시 지어내지 말고 기본 원칙으로 폴백."
success_criteria: "WCAG AA 접근성 통과, 모바일-데스크톱 반응형, 일관된 디자인 토큰, 의미 있는 애니메이션, CLS < 0.1, 터치 타겟 44px 이상"
---

# UI/UX Pro — 종합 디자인 인텔리전스

> UI 구조, 시각 디자인, 인터랙션 패턴, UX 품질 관리를 위한 우선순위 기반 스킬.
> 모든 디자인 결정은 아래 우선순위 테이블을 따라 **접근성 → 인터랙션 → 성능 → 스타일 → 레이아웃 → 타이포/컬러 → 애니메이션 → 폼 → 내비게이션 → 차트** 순으로 검토한다.

---

## 1. 우선순위 테이블 (Priority 1→10)

| 우선순위 | 카테고리 | 영향도 | 핵심 체크 (Must Have) | 안티패턴 (Avoid) |
|----------|----------|--------|----------------------|-----------------|
| 1 | **접근성** | CRITICAL | 대조비 4.5:1, Alt 텍스트, 키보드 내비, Aria-label | 포커스 링 제거, 라벨 없는 아이콘 버튼 |
| 2 | **터치 & 인터랙션** | CRITICAL | 최소 44×44px, 8px+ 간격, 로딩 피드백 | 호버만 의존, 0ms 즉시 상태 변경 |
| 3 | **성능** | HIGH | WebP/AVIF, 지연 로딩, 공간 예약 (CLS < 0.1) | 레이아웃 스래싱, 누적 레이아웃 시프트 |
| 4 | **스타일 선택** | HIGH | 제품 유형 매칭, 일관성, SVG 아이콘 (이모지 금지) | 플랫+스큐어모픽 혼용, 이모지를 아이콘으로 |
| 5 | **레이아웃 & 반응형** | HIGH | 모바일-퍼스트 브레이크포인트, 뷰포트 메타, 가로 스크롤 금지 | 가로 스크롤, 고정 px 컨테이너, 줌 비활성화 |
| 6 | **타이포그래피 & 컬러** | MEDIUM | 기본 16px, 줄높이 1.5, 시맨틱 컬러 토큰 | 12px 미만 본문, 회색 위 회색, 컴포넌트에 raw hex |
| 7 | **애니메이션** | MEDIUM | 150~300ms, 움직임이 의미 전달, 공간 연속성 | 장식 전용 애니메이션, width/height 애니메이션, reduced-motion 무시 |
| 8 | **폼 & 피드백** | MEDIUM | 보이는 라벨, 필드 옆 에러, 헬퍼 텍스트, 점진적 공개 | 플레이스홀더만 라벨, 상단에만 에러, 처음부터 과다 입력 |
| 9 | **내비게이션** | HIGH | 예측 가능한 뒤로가기, 하단 내비 ≤5개, 딥링크 | 과적 내비, 깨진 뒤로가기, 딥링크 없음 |
| 10 | **차트 & 데이터** | LOW | 범례, 툴팁, 접근성 컬러 | 색상만으로 의미 전달 |

---

## 2. 워크플로우

### Step 1: 요구사항 분석

사용자 요청에서 추출:
- **제품 유형**: SaaS, 이커머스, 포트폴리오, 대시보드, 엔터테인먼트, 도구, 생산성, 하이브리드
- **타겟 & 맥락**: 연령대, 사용 맥락 (이동 중, 여가, 업무)
- **스타일 키워드**: 미니멀, 다크모드, 콘텐츠-퍼스트, 몰입형, 플레이풀, 비브란트 등
- **스택 감지**: `package.json` (react/next/vue/svelte/angular), `pubspec.yaml` (Flutter), `*.xcodeproj` (SwiftUI), `composer.json` (Laravel). 감지 불가 시 사용자에게 확인. **절대 스택을 가정하지 말 것.**

### Step 2: 디자인 시스템 결정

제품 유형 + 키워드로 종합 추천:
- **스타일**: 제품 유형에 맞는 스타일 카테고리 선택
- **컬러 팔레트**: 프라이머리/세컨더리/뉴트럴/시맨틱 (success/warning/error/info)
- **타이포그래피**: 헤딩 + 본문 폰트 페어링, 크기 스케일
- **간격 스케일**: 4px 기반 (4, 8, 12, 16, 24, 32, 48, 64, 96)
- **이펙트**: 그림자, 보더, 블러, 그라데이션 수준
- **안티패턴**: 피해야 할 패턴 명시

### Step 3: 디자인 다이얼 (선택)

| 다이얼 | 낮음 (1-3) | 중간 (4-7) | 높음 (8-10) |
|--------|-----------|-----------|------------|
| **Variance** | 중앙정렬/미니멀 | 균형/모던 | 대담/비대칭 (브루탈리즘, 벤토 그리드) |
| **Motion** | 미묘한 마이크로 인터랙션 | 표준 스크롤/스테거 | 복잡한 코레오그래피 (pin, Flip, SplitText) |
| **Density** | 여유 (24-96px 간격) | 표준 (16-64px) | 밀집/대시보드 (8-32px 간격) |

### Step 4: 구현 & 검증

우선순위 테이블 1→10 순서로 체크하며 구현. 각 카테고리별 상세 규칙은 아래 섹션 참조.

---

## 3. 접근성 (Priority 1) — CRITICAL

### 색상 대조비
- 본문 텍스트: **4.5:1** 이상 (WCAG AA)
- 큰 텍스트 (18px+ bold / 24px+): **3:1** 이상
- UI 컴포넌트 (보더, 아이콘): **3:1** 이상
- 다크모드: 배경이 어두울수록 텍스트 밝기 확보, 회색 텍스트 주의

### 키보드 내비게이션
- 모든 인터랙티브 요소에 `:focus-visible` 스타일
- 포커스 링 제거 금지 (`outline: none` 단독 사용 금지)
- 논리적 탭 순서 (DOM 순서 = 시각 순서)
- 모달/다이얼로그: 포커스 트랩 + ESC 닫기

### 스크린 리더
- 이미지: 의미 있는 `alt`, 장식용은 `alt=""`
- 아이콘 버튼: `aria-label` 필수
- 폼: `<label for="">` 또는 `aria-labelledby`
- 라이브 리전: `aria-live="polite"` (알림), `"assertive"` (에러)

### 안티패턴
- ❌ `outline: none` without replacement
- ❌ 라벨 없는 아이콘 전용 버튼
- ❌ 색상만으로 정보 전달 (차트, 상태 표시)
- ❌ `user-scalable=no`

---

## 4. 터치 & 인터랙션 (Priority 2) — CRITICAL

- 터치 타겟: **최소 44×44px** (Apple HIG), **48×48dp** (Material)
- 인접 터치 요소 간격: **8px 이상**
- 호버 전용 인터랙션 금지 (모바일 대응)
- 상태 변경 시 피드백: 로딩 스피너, 스켈레톤, 프로그레스 바
- 버튼 프레스: `:active` 상태 또는 스케일 변환
- 롱프레스/스와이프: 대체 수단 제공

---

## 5. 성능 (Priority 3) — HIGH

- 이미지: **WebP/AVIF** 포맷, `loading="lazy"`, `width`/`height` 속성으로 CLS 방지
- 폰트: `font-display: swap`, 서브셋, preload
- 코드: 라우트 기반 코드 스플리팅, 동적 import
- **CLS < 0.1**: 동적 콘텐츠에 공간 예약
- 리스트: 100개 이상 시 **가상화** (react-window, vue-virtual-scroller)
- 이벤트: debounce/throttle (스크롤, 리사이즈, 입력)
- 메인 스레드: 50ms 이상 작업 시 청크 분할

---

## 6. 스타일 선택 (Priority 4) — HIGH

### 제품 유형 → 스타일 매핑

| 제품 유형 | 추천 스타일 | 피할 스타일 |
|-----------|------------|------------|
| SaaS / B2B | 미니멀, 클린, 뉴트럴 | 브루탈리즘, 네온 |
| 이커머스 | 콘텐츠-퍼스트, 카드 기반 | 과도한 애니메이션 |
| 포트폴리오 | 대담, 비대칭, 몰입형 | 제네릭 템플릿 |
| 대시보드 | 밀집, 데이터 중심, 다크 | 여유로운 마케팅 스타일 |
| 엔터테인먼트 | 비브란트, 플레이풀, 다크 | 미니멀/무채색 |
| 핀테크 | 신뢰, 보수적, 높은 대조비 | 실험적 레이아웃 |
| 헬스케어 | 차분, 접근성 우선, 큰 터치 | 복잡한 내비게이션 |

### 규칙
- 하나의 프로젝트에서 **하나의 스타일 시스템** 유지
- 아이콘: **SVG** 사용 (이모지 ❌, 아이콘 폰트 지양)
- 일관성: 같은 패턴은 같은 컴포넌트로 재사용

---

## 7. 레이아웃 & 반응형 (Priority 5) — HIGH

### 모바일-퍼스트 브레이크포인트

```css
/* Base: 모바일 (0-639px) */
/* sm: 640px */
/* md: 768px */
/* lg: 1024px */
/* xl: 1280px */
/* 2xl: 1536px */
```

### 규칙
- `<meta name="viewport" content="width=device-width, initial-scale=1">` 필수
- 컨테이너: `max-width` + `margin-inline: auto` (고정 px 너비 금지)
- 가로 스크롤: `overflow-x: hidden`으로 숨기지 말고 **원인 제거**
- 그리드: `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))`
- Safe area: `env(safe-area-inset-*)` (노치/홈바)

---

## 8. 타이포그래피 & 컬러 (Priority 6) — MEDIUM

### 타이포그래피 스케일

| 용도 | 크기 | 줄높이 | 무게 |
|------|------|--------|------|
| Display | 48-72px | 1.1 | 700-800 |
| H1 | 36-48px | 1.2 | 700 |
| H2 | 28-36px | 1.25 | 600-700 |
| H3 | 22-28px | 1.3 | 600 |
| Body | 16px | 1.5-1.6 | 400 |
| Small | 14px | 1.4 | 400 |
| Caption | 12px | 1.3 | 400-500 |

### 규칙
- 본문 **최소 16px** (모바일)
- 줄 길이: **45-75자** (max-width: 65ch)
- 폰트 페어링: 헤딩(디스플레이) + 본문(산세리프) 최대 2개
- 가변 폰트 우선 (variable font)

### 컬러 시스템

```css
:root {
  /* 시맨틱 토큰 — raw hex를 컴포넌트에 직접 쓰지 말 것 */
  --color-primary: hsl(220 90% 56%);
  --color-primary-hover: hsl(220 90% 48%);
  --color-surface: hsl(0 0% 100%);
  --color-surface-raised: hsl(0 0% 98%);
  --color-text: hsl(220 15% 15%);
  --color-text-muted: hsl(220 10% 45%);
  --color-border: hsl(220 15% 90%);
  --color-success: hsl(145 65% 42%);
  --color-warning: hsl(38 92% 50%);
  --color-error: hsl(0 72% 51%);
  --color-info: hsl(210 80% 55%);
}

[data-theme="dark"] {
  --color-surface: hsl(220 15% 10%);
  --color-surface-raised: hsl(220 15% 14%);
  --color-text: hsl(220 10% 92%);
  --color-text-muted: hsl(220 10% 60%);
  --color-border: hsl(220 15% 22%);
}
```

### 안티패턴
- ❌ 12px 미만 본문 텍스트
- ❌ 회색 배경 위 회색 텍스트 (대조비 부족)
- ❌ 컴포넌트에 `#3b82f6` 같은 raw hex 직접 사용
- ❌ 다크모드에서 단순히 `filter: invert()`

---

## 9. 애니메이션 (Priority 7) — MEDIUM

### 기본 원칙
- 지속시간: **150~300ms** (마이크로), 300~500ms (전환)
- 이징: `cubic-bezier(0.4, 0, 0.2, 1)` (표준), `cubic-bezier(0, 0, 0.2, 1)` (감속)
- **움직임이 의미를 전달**해야 함 (장식 전용 ❌)
- 공간 연속성: 요소가 나타난 위치로 사라짐
- Exit는 Enter보다 **빠르게** (200ms enter → 150ms exit)

### 성능
- `transform`, `opacity`만 애니메이션 (레이어 승격)
- `width`, `height`, `top`, `left` 애니메이션 ❌
- `will-change`는 최소한으로, 애니메이션 종료 후 제거
- `prefers-reduced-motion` 미디어 쿼리 필수

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

### GSAP / 스프링 물리
- 스프링: `stiffness: 300, damping: 30` (표준), `stiffness: 170, damping: 26` (부드러운)
- 스태거: `stagger: 0.05~0.1` (리스트 등장)
- 스크롤 트리거: `start: "top 80%"`, `toggleActions: "play none none reverse"`

---

## 10. 폼 & 피드백 (Priority 8) — MEDIUM

- **보이는 라벨** (플레이스홀더만 라벨로 사용 ❌)
- 에러: **필드 바로 아래**, 빨간색 + 아이콘 + 텍스트 (색상만 ❌)
- 헬퍼 텍스트: 필드 아래 회색 작은 텍스트
- 점진적 공개: 처음부터 모든 필드 노출 ❌, 단계별
- 인라인 검증: `blur` 시 검증, 제출 시 전체 검증
- 제출 버튼: 로딩 상태 + 비활성화 (이중 제출 방지)
- 성공/실패: 토스트 또는 인라인 피드백

---

## 11. 내비게이션 (Priority 9) — HIGH

- 뒤로가기: **예측 가능**하게 동작 (브라우저 히스토리 존중)
- 하단 내비 (모바일): **최대 5개** 항목
- 딥링크: 모든 주요 화면에 URL 매핑
- 현재 위치 표시: `aria-current="page"`, 활성 스타일
- 브레드크럼: 3단계 이상 깊이에서 사용
- 햄버거 메뉴: 모바일에서만, 데스크톱에서는 보임

---

## 12. 차트 & 데이터 시각화 (Priority 10) — LOW

- **범례** 필수 (색상만으로 구분 ❌)
- **툴팁**: 호버/포커스 시 상세 값 표시
- 접근성 컬러: 색맹 안전 팔레트 (ColorBrewer, Okabe-Ito)
- 축 라벨 + 단위 명시
- 대안 텍스트: `aria-label`에 데이터 요약
- 실시간: `aria-live="off"` (스크린 리더 폭주 방지)

### 차트 유형 선택

| 데이터 | 추천 차트 |
|--------|----------|
| 시간 경과 추세 | 라인 차트 |
| 카테고리 비교 | 바 차트 (가로: 라벨 길 때) |
| 비율/구성 | 도넛 (≤5 카테고리), 스택 바 |
| 분포 | 히스토그램, 박스 플롯 |
| 상관관계 | 스캐터 플롯 |
| 지리 | 콜로플레스 맵 |
| 실시간 | 스트리밍 라인 + 스파크라인 |

---

## 13. 스택별 가이드

### React / Next.js
- 서버 컴포넌트 우선, 클라이언트 컴포넌트는 인터랙션 필요 시만
- `next/image`로 자동 최적화 (WebP, lazy, blur placeholder)
- `next/font`로 폰트 최적화 (self-host, zero CLS)
- Suspense + Streaming으로 점진적 렌더링
- `React.memo`, `useMemo`는 측정 후 적용 (조기 최적화 ❌)
- 상태: 서버 상태는 TanStack Query, 클라이언트 상태는 Zustand/Jotai

### Vue / Nuxt
- `<script setup>` + Composition API
- `useFetch` / `useAsyncData`로 서버 상태
- `<NuxtImg>` / `<NuxtPicture>` 이미지 최적화
- `@nuxt/fonts` 자동 폰트 최적화
- `<Transition>` / `<TransitionGroup>` 내장 애니메이션

### Tailwind CSS
- 유틸리티 우선, 컴포넌트 추출은 반복 3회 이상 시
- `@apply` 남용 금지 (유틸리티 우선 원칙 유지)
- 다크모드: `dark:` 변형 + `class` 전략
- 반응형: `sm: md: lg: xl: 2xl:` 모바일-퍼스트
- 디자인 토큰: `tailwind.config.js` `theme.extend`에 정의

### Flutter
- `Theme.of(context)`로 일관된 테마
- `MediaQuery` + `LayoutBuilder` 반응형
- `Semantics` 위젯으로 접근성
- `AnimatedContainer`, `Hero`, `AnimatedSwitcher` 내장 애니메이션
- 터치: `InkWell` / `GestureDetector` + `MaterialStateProperty`

### SwiftUI
- `.accessibilityLabel()`, `.accessibilityHint()` 필수
- `@ScaledMetric`으로 Dynamic Type 지원
- `.frame(minWidth: 44, minHeight: 44)` 터치 타겟
- `withAnimation(.spring())` 스프링 애니메이션
- `GeometryReader` + `ViewThatFits` 반응형

### HTML/CSS (프레임워크 없음)
- 시맨틱 HTML: `<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`
- CSS Grid + Flexbox 레이아웃
- CSS 커스텀 프로퍼티로 디자인 토큰
- `@media (prefers-reduced-motion)` 필수
- `@container` 쿼리 (컴포넌트 반응형)

---

## 14. 다크모드 체크리스트

- [ ] 배경: 완전 검정(`#000`) 피하고 `hsl(220 15% 8-12%)` 사용
- [ ] 텍스트: `hsl(0 0% 90-95%)`, muted는 `60-70%`
- [ ] 카드/서피스: 배경보다 2-4% 밝게 (레이어 구분)
- [ ] 보더: `hsl(220 15% 20-25%)`
- [ ] 프라이머리 컬러: 채도 10-15% 낮추기 (눈부심 방지)
- [ ] 그림자: `rgba(0,0,0,0.3-0.5)` (라이트보다 강하게)
- [ ] 이미지: `brightness(0.9)` 또는 오버레이로 눈부심 완화
- [ ] 포커스 링: 밝은 색상으로 대비 확보

---

## 15. 사전 납품 체크리스트 (Pre-Delivery)

### 접근성
- [ ] 대조비 4.5:1 (본문), 3:1 (큰 텍스트, UI)
- [ ] 키보드만으로 모든 기능 접근 가능
- [ ] 스크린 리더 테스트 (VoiceOver / NVDA)
- [ ] `prefers-reduced-motion` 대응
- [ ] `prefers-color-scheme` 대응

### 인터랙션
- [ ] 터치 타겟 44×44px 이상
- [ ] 로딩 상태 (스켈레톤/스피너)
- [ ] 에러 상태 (인라인 + 토스트)
- [ ] 빈 상태 (Empty State) 디자인
- [ ] 호버 + 포커스 + 액티브 상태

### 성능
- [ ] CLS < 0.1
- [ ] LCP < 2.5s
- [ ] 이미지 최적화 (WebP/AVIF, lazy)
- [ ] 폰트 최적화 (swap, preload, subset)

### 일관성
- [ ] 디자인 토큰 사용 (raw hex ❌)
- [ ] 간격 스케일 준수 (4px 기반)
- [ ] 컴포넌트 재사용 (중복 마크업 ❌)
- [ ] 아이콘 시스템 통일 (SVG, 같은 세트)

### 반응형
- [ ] 320px ~ 2560px 테스트
- [ ] 가로 스크롤 없음
- [ ] Safe area 대응 (모바일)
- [ ] 다크모드 모든 페이지

---

## 16. 결과 없을 시 폴백

검색/참조에서 결과가 없으면:
1. 더 넓은 키워드로 재시도
2. 그래도 없으면 **이 문서의 우선순위 테이블 + 기본 원칙**으로 폴백
3. 사용자에게 "데이터베이스 매칭이 없어 기본 원칙을 적용했다"고 명시
4. **0결과를 데이터가 있는 것처럼 제시하지 말 것**
