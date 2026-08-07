# Layer 4: Creative Director — 최종 Design Brief 작성

## 역할

Art Director 의 DesignMix + UX Researcher 의 CreativeBrief 를 종합해
프론트엔드 에이전트(또는 사용자)가 **바로 실행 가능한 Design Brief** 를 만든다.

## 입력

- `creative_brief`: UX Researcher
- `design_mix`: Art Director (자동 검증 통과한 Mix)

## 출력: DesignBrief

두 가지 형식으로 동시에 출력:

### A. Markdown (사람이 읽음)

→ `brief_template.md` 양식 참고. 다음을 포함:
1. 미션 요약
2. 타깃·톤·제약조건
3. Design Mix 요약 (각 컴포넌트별 출처 + rationale)
4. **최종 Design DNA** — color/typography/layout/animation/spacing 명세
5. 컴포넌트별 가이드 (DO/DON'T)
6. 접근성 / 트렌드 경고 (있다면)
7. 다음 단계 (코드 생성 또는 사용자 검토)

### B. JSON spec (프론트엔드 에이전트용)

```json
{
  "mission": "...",
  "design_dna": {
    "colors": { "primary": "...", "accent": "...", "background": "...", ... },
    "typography": { "heading_font": "...", "body_font": "...", ... },
    "layout": { "grid": "...", "max_width": "...", ... },
    "animation": { "entrance": "...", "duration_base": "...", ... },
    "spacing": { "density": "...", "section_gap": "...", ... }
  },
  "components": [
    {
      "category": "hero",
      "source": "linear-dashboard → linear-hero-split",
      "structure": "split-hero (좌측 타이틀 + 우측 일러스트)",
      "key_props": { "background": "...", "cta_text": "...", ... }
    }
  ],
  "guidelines": {
    "do": [...],
    "dont": [...]
  },
  "warnings": {
    "trend": [],
    "accessibility": []
  },
  "scores": {
    "harmony": 0.85,
    "trends": 0.92,
    "accessibility": 0.88,
    "overall": 0.88
  }
}
```

## 프롬프트

```
You are Creative Director, the fourth and final layer of Creative Director
4-layer system.

You receive:
1. CreativeBrief (UX Researcher) — user intent
2. DesignMix (Art Director) — pre-validated component mix with scores

Your job is to produce the FINAL Design Brief that a frontend engineer
(or the user) can execute on. Be SPECIFIC:

- Pick concrete values: exact HEX colors, exact font names, exact px sizes.
  No "use a warm color" — say "#C2785A terracotta".
- For each component category, write 3-5 sentences describing layout,
  spacing, typography, motion.
- Include DO and DON'T (at least 3 each).
- Surface any trend or accessibility warnings as prominent notes.
- If any score < 0.7, add a "Caveat" section explaining why.

Output two things:
1. The Markdown Design Brief (use the brief_template.md structure).
2. A JSON spec suitable for direct frontend code generation.

Both must reference the SAME decisions. The JSON is the source of truth;
the Markdown is the human-readable explanation of the JSON.
```

## 호출 방법

```python
from api.api.agents.creative_director.prompts.creative_director import (
    build_creative_director_prompt,
    render_brief_markdown,
)
from api.api.agents.creative_director.brief_template import render_template

prompt = build_creative_director_prompt(
    brief=creative_brief,
    mix=design_mix,
    template_path=Path("api/api/agents/creative_director/brief_template.md"),
)
raw = call_llm_json_or_markdown(prompt)   # JSON spec + Markdown

markdown = render_brief_markdown(
    template=template_text,
    brief=creative_brief,
    mix=design_mix,
    warnings=design_mix.get('warnings', {}),
    caveat=None if design_mix['scores']['overall'] >= 0.7 else "참조 후보가 약해 — 후속 후보 추가 권장",
)

return DesignBrief(markdown=markdown, spec=raw['json_spec'])
```

## 검증 (최종)

| 검증 항목 | 기준 |
|---|---|
| 모든 카테고리별 컴포넌트 결정 | ✅ 필수 |
| Design DNA 5종 모두 명세 | ✅ 필수 |
| DO/DON'T 각 3개 이상 | ✅ 필수 |
| HEX 코드 일관성 (3종 이상 통일된 팔레트) | ✅ 필수 |
| 접근성 경고 0개 | ✅ 권장 |
| 트렌드 점수 ≥ 0.7 | ✅ 권장 |
| overall ≥ 0.7 | ✅ 권장 (미만 시 Caveat) |

## 다음 단계

- 프론트엔드 에이전트가 spec 을 읽고 코드 생성
- 또는 사용자가 Markdown 을 검토하고 직접 구현
- 결과물에 대한 평가는 별도 라운드에서 (대표님 승인 → StyleCard 로 라이브러리 보강)