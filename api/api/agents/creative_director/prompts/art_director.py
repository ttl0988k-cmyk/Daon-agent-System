"""
Layer 3: Art Director — Style Mixing + 트렌드/접근성 검증 + DesignMix 결정.
"""

from __future__ import annotations
from typing import Optional


_PROMPT_HEADER = """You are Art Director, the third layer of Creative Director 4-layer system.

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
"""


def run_style_mixer(brief: dict, ranking: dict, registry) -> dict:
    """
    후보 ranking 으로부터 Style Mixing 자동화.
    각 카테고리별로 첫 번째 후보의 컴포넌트를 기본 선택.
    """
    target_categories = _categories_for_brief(brief)
    ranked = ranking.get("ranked_candidates", [])

    components = {}
    for cat in target_categories:
        # 후보들의 ComponentCard 중 해당 카테고리 첫 번째 것 선택
        chosen = None
        for rc in ranked:
            card_id = rc.get("card_id")
            if not card_id:
                continue
            card = registry.get(card_id) if hasattr(registry, "get") else None
            if not card:
                continue
            decomposed = getattr(card, "decomposed_cards", []) or []
            for comp in decomposed:
                if getattr(comp, "category", "") == cat:
                    chosen = {
                        "category": cat,
                        "source_card_id": card_id,
                        "source_component_id": getattr(comp, "id", ""),
                        "dna_override": {},
                        "rationale": "highest-ranked candidate has this component",
                    }
                    break
            if chosen:
                break
        if chosen:
            components[cat] = chosen
        else:
            components[cat] = {
                "category": cat,
                "source_card_id": None,
                "source_component_id": None,
                "dna_override": {},
                "rationale": "no matching component in library",
            }

    # 자동 점수 (실제 StyleMixer 가 있으면 그 결과 사용)
    harmony = _compute_harmony(components)

    return {
        "components": components,
        "scores": {"harmony": harmony, "trends": 0.0, "accessibility": 0.0, "overall": 0.0},
        "conflicts_resolved": [],
        "trend_warnings": [],
        "a11y_warnings": [],
        "final_components": list(components.values()),
    }


def build_art_director_prompt(brief, ranking, auto_mix, trend_result, a11y_result) -> str:
    parts = [
        _PROMPT_HEADER,
        "",
        "CREATIVE_BRIEF:",
        f"mission: {brief.get('mission')}",
        f"deliverable_type: {brief.get('deliverable_type')}",
        f"tone: {brief.get('tone')}",
        "",
        f"RANKED CANDIDATES ({len(ranking.get('ranked_candidates', []))}):",
    ]
    for rc in ranking.get("ranked_candidates", [])[:5]:
        parts.append(f"  - {rc.get('card_id')}: {rc.get('why', '')}")
    parts.append("")
    parts.append("AUTO MIX (Style Mixer):")
    parts.append(str(auto_mix))
    parts.append("")
    parts.append(f"TREND CHECK: score={getattr(trend_result, 'trend_score', '?')}, warnings={getattr(trend_result, 'warnings', [])}")
    parts.append(f"ACCESSIBILITY CHECK: score={getattr(a11y_result, 'a11y_score', '?')}, warnings={getattr(a11y_result, 'warnings', [])}")
    parts.append("")
    parts.append("OUTPUT JSON:")
    parts.append("""
{
  "components": { "category": { "source_card_id": "...", "rationale": "..." } },
  "scores": { "harmony": 0.0~1.0, "trends": 0.0~1.0, "accessibility": 0.0~1.0, "overall": 0.0~1.0 },
  "conflicts_resolved": [ { "category": "...", "issue": "...", "resolution": "..." } ],
  "trend_warnings": [],
  "a11y_warnings": [],
  "final_components": []
}
""")
    return "\n".join(parts)


def _categories_for_brief(brief: dict) -> list[str]:
    deliverable = brief.get("deliverable_type", "other")
    from .design_librarian import categories_for
    return categories_for(deliverable)


def _compute_harmony(components: dict) -> float:
    """간단한 harmony 점수 — 모든 카테고리가 결정됐는지 + source 가 있는지."""
    if not components:
        return 0.0
    decided = sum(1 for c in components.values() if c.get("source_card_id"))
    return decided / len(components)