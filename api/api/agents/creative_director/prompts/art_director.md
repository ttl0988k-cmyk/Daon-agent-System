# Layer 3: Art Director — 후보 조합 / 충돌 해소 / 품질 감사

## 역할

Design Librarian 가 선정한 후보들의 ComponentCard 를 **조합(믹싱)** 하고,
- 충돌(conflict) 해소
- 트렌드 부합도 검증 (`design_check_trends`)
- 접근성 감사 (`design_check_accessibility`)
- 전체 조화 점수 (`design_check_harmony` 또는 자체 평가)

를 거쳐 **DesignMix** 를 산출한다.

## 입력

- `creative_brief`: UX Researcher
- `ranked_candidates`: Librarian 의 순위 결과
- `component_cards`: 각 후보에서 분해된 ComponentCard 들

## 처리 흐름

```
ranked_candidates[상위 3~5]
        │
        ▼
각 후보의 ComponentCard 분해
        │
        ▼
deliverable_type 카테고리별로 1개씩 선택 (Style Mixing)
        │
        ▼
StyleMixer.mix() 호출 — harmony_score 계산
        │
        ▼
design_check_trends() — deprecated 패턴 (skeuomorphism, marquee 등) 감지
        │
        ▼
design_check_accessibility() — WCAG AA contrast / motion-safety 검증
        │
        ▼
충돌/실패 시 폴백 (두번째 후보로 교체) — 최대 3회
        │
        ▼
DesignMix 결정
```

## DesignMix 스키마

```json
{
  "components": {
    "hero": {
      "source_card_id": "linear-dashboard",
      "source_component_id": "linear-hero-split",
      "dna_override": { "colors.primary": "#8B4513" },   // 미션에 맞게 미세 조정
      "rationale": "왜 이 컴포넌트를 골랐는지 1문장"
    },
    "navbar": { ... },
    "cta": { ... },
    "footer": { ... }
  },
  "scores": {
    "harmony": 0.0~1.0,
    "trends": 0.0~1.0,
    "accessibility": 0.0~1.0,
    "overall": 0.0~1.0
  },
  "conflicts_resolved": [
    {"category": "navbar", "issue": "skeuomorphic gradient", "resolution": "플랫 디자인으로 교체"}
  ],
  "trend_warnings": [],         // deprecated 패턴 감지
  "a11y_warnings": [],          // 접근성 위반
  "final_components": [...]    // 실제 믹스에 사용된 ComponentCard
}
```

## 프롬프트

```
You are Art Director, the third layer of Creative Director 4-layer system.

You receive:
1. A CreativeBrief (user intent)
2. Ranked StyleCard candidates + their ComponentCards

Your job is to MIX components across candidates into a unified DesignMix:
- For each required category (hero, navbar, cta, footer, etc.), pick the
  best ComponentCard across candidates.
- Justify each choice in 1 sentence (style vs. brief match).
- Identify and resolve conflicts (e.g., different fonts, clashing colors).
- Verify trend relevance (no skeuomorphism, no marquee, no flash).
- Verify accessibility (WCAG AA contrast, motion safety if motion_intensity >= 8).

You do NOT generate new design DNA. You only mix and validate existing ones.

Output JSON: DesignMix (위 스키마)
```

## 호출 방법

```python
from api.api.style_card import StyleMixer, ComponentCard
from api.api.routes.style_card_routes import (
    design_check_trends,
    design_check_accessibility,
)
from api.api.agents.creative_director.prompts.art_director import (
    build_art_director_prompt,
    run_style_mixer,
)

mix = run_style_mixer(
    brief=creative_brief,
    ranked=ranking['ranked_candidates'],
    registry=registry,
)

# 자동 품질 검증
trend_result = design_check_trends(mix['final_components'])
a11y_result = design_check_accessibility(mix['final_components'])

# 점수 합산
overall = (
    mix['scores']['harmony'] * 0.4
    + trend_result.trend_score * 0.3
    + a11y_result.a11y_score * 0.3
)

# LLM 으로 rationale / conflict resolution 보강
prompt = build_art_director_prompt(
    brief=creative_brief,
    candidates=ranking,
    auto_mix=mix,
    trend=trend_result,
    a11y=a11y_result,
)
final_mix = call_llm_json(prompt)
```

## 검증 (자동)

- `overall < 0.6` 이면 → 라온이 "참조 부족 — 미션 재정의 또는 라이브러리 보강" 권고
- `trend_warnings` 비어있지 않으면 → 폴백 후보로 재시도 (최대 3회)
- `a11y_warnings` 비어있지 않으면 → Creative Director 출력에 명시적으로 경고 포함

## 다음 레이어로 전달

`DesignMix` (자동 점수 포함) + `CreativeBrief` → Creative Director 로