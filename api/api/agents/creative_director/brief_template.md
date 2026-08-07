# Design Brief — {MISSION_TITLE}

> Creative Director 4-layer 시스템 산출물. 라온이 디자인 미션을 수행할 때마다 생성한다.
> 이 문서는 프론트엔드 에이전트의 작업 지시서이며, 사용자의 검토 기준이기도 하다.

---

## 1. 미션 요약

**원문:** {USER_MISSION}
**타입:** {DELIVERABLE_TYPE}
**주목표:** {PRIMARY_GOAL}

## 2. 타깃 · 톤 · 제약

### 타깃
- **주:** {TARGET_AUDIENCE_PRIMARY}
- **부:** {TARGET_AUDIENCE_SECONDARY}
- **심리 특성:** {PSYCHOGRAPHICS}

### 톤
{tone_bullets}

### 제약조건
{constraints_bullets}

### 반드시 포함
{must_haves_bullets}

### 반드시 피할
{must_avoid_bullets}

## 3. Design DNA (최종 결정값)

### 색상 (Palette: {PALETTE_NAME})
| 역할 | 값 |
|---|---|
| Primary | `{primary}` |
| Accent | `{accent}` |
| Background | `{background}` |
| Surface | `{surface}` |
| Text Primary | `{text_primary}` |
| Text Secondary | `{text_secondary}` |
| 조화 방식 | {palette_harmony} |

> ⚠️ Contrast 검증: Primary vs Background = {contrast_ratio} (WCAG AA ≥ 4.5)

### 타이포그래피
- **Heading:** {heading_font}, weight {heading_weight}, letter-spacing {letter_spacing_heading}
- **Body:** {body_font}, weight {body_weight}, line-height {line_height_body}
- **Mono:** {mono_font}
- **Scale:** {scale}

### 레이아웃
- Grid: {grid}
- Max width: {max_width}
- Desktop padding: {padding_desktop} / Mobile padding: {padding_mobile}
- Alignment: {alignment}
- Glass effect: {glass_effect}
- Border radius: {border_radius}

### 애니메이션
- Entrance: {entrance}
- Hover: {hover}
- Page transition: {page_transition}
- Base duration: {duration_base}
- Easing: {easing}
- Motion intensity: {motion_intensity}/10 {reduced_motion_warning}

### 스페이싱
- Density: {density}
- Section gap: {section_gap}
- Element gap: {element_gap}

## 4. 컴포넌트 Mix

| 카테고리 | 출처 (StyleCard → Component) | Rationale |
|---|---|---|
{component_rows}

## 5. DO ✅

{do_bullets}

## 6. DON'T ❌

{dont_bullets}

## 7. 접근성 / 트렌드 경고

### 접근성
{a11y_warnings}

### 트렌드
{trend_warnings}

## 8. 점수

| 항목 | 점수 |
|---|---|
| Harmony | {harmony} |
| Trends | {trends} |
| Accessibility | {accessibility} |
| **Overall** | **{overall}** |

## 9. Caveat (overall < 0.7 일 때만 표시)

> {caveat}

## 10. 다음 단계

- [ ] 프론트엔드 에이전트가 spec 기반 코드 생성
- [ ] 사용자(또는 라온) 시각 검토
- [ ] 결과물을 DesignCard 로 라이브러리 보강 (대표님 승인 후)

---

*생성: Creative Director 4-layer ({generated_at})*
*라이브러리 버전: daon-reference-library 1.0*