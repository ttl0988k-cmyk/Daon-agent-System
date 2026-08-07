"""
Layer 4: Creative Director — 최종 Design Brief 작성.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional


_PROMPT_HEADER = """You are Creative Director, the fourth and final layer of Creative Director
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
"""


def build_creative_director_prompt(brief: dict, mix: dict, template_md: str) -> str:
    parts = [
        _PROMPT_HEADER,
        "",
        "CREATIVE_BRIEF:",
        _brief_summary(brief),
        "",
        "DESIGN_MIX:",
        _mix_summary(mix),
        "",
        "TEMPLATE (Markdown):",
        template_md[:1500] + ("\n... (truncated)" if len(template_md) > 1500 else ""),
        "",
        "OUTPUT (Markdown + JSON spec, no fences in JSON):",
    ]
    return "\n".join(parts)


def load_template() -> str:
    template_path = Path(__file__).parent.parent / "brief_template.md"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return "# Design Brief\n\n## (Template not found)"


def render_brief_markdown(
    template: str,
    brief: dict,
    mix: dict,
    warnings: dict,
    caveat: Optional[str] = None,
) -> str:
    """
    Brief 템플릿의 placeholder 를 실제 값으로 치환.
    라온이 LLM 호출 없이도 stub DesignBrief 를 생성할 수 있게 해준다.
    """
    components = mix.get("components", {})
    scores = mix.get("scores", {})

    tone_bullets = "\n".join(f"- {t}" for t in brief.get("tone", [])) or "- (지정 없음)"
    constraints = "\n".join(f"- {c}" for c in brief.get("constraints", [])) or "- (없음)"
    must_haves = "\n".join(f"- {m}" for m in brief.get("must_haves", [])) or "- (없음)"
    must_avoid = "\n".join(f"- {m}" for m in brief.get("must_avoid", [])) or "- (없음)"
    component_rows = "\n".join(
        f"| {cat} | {data.get('source_card_id', '?')} → {data.get('source_component_id', '?')} | {data.get('rationale', '')} |"
        for cat, data in components.items()
    ) or "| - | - | - |"

    a11y = "\n".join(f"- {w}" for w in warnings.get("accessibility", [])) or "- (없음)"
    trend = "\n".join(f"- {w}" for w in warnings.get("trend", [])) or "- (없음)"

    replacements = {
        "{MISSION_TITLE}": brief.get("mission", "Untitled"),
        "{USER_MISSION}": brief.get("mission", ""),
        "{DELIVERABLE_TYPE}": brief.get("deliverable_type", ""),
        "{PRIMARY_GOAL}": brief.get("primary_goal", ""),
        "{TARGET_AUDIENCE_PRIMARY}": brief.get("target_audience", {}).get("primary", ""),
        "{TARGET_AUDIENCE_SECONDARY}": brief.get("target_audience", {}).get("secondary", ""),
        "{PSYCHOGRAPHICS}": brief.get("target_audience", {}).get("psychographics", ""),
        "{tone_bullets}": tone_bullets,
        "{constraints_bullets}": constraints,
        "{must_haves_bullets}": must_haves,
        "{must_avoid_bullets}": must_avoid,
        "{PALETTE_NAME}": "(Auto from mix)",
        "{primary}": "(Auto from mix)",
        "{accent}": "(Auto from mix)",
        "{background}": "(Auto from mix)",
        "{surface}": "(Auto from mix)",
        "{text_primary}": "(Auto from mix)",
        "{text_secondary}": "(Auto from mix)",
        "{palette_harmony}": "(Auto)",
        "{contrast_ratio}": "(Auto)",
        "{heading_font}": "(Auto)",
        "{heading_weight}": "(Auto)",
        "{letter_spacing_heading}": "(Auto)",
        "{body_font}": "(Auto)",
        "{body_weight}": "(Auto)",
        "{line_height_body}": "(Auto)",
        "{mono_font}": "(Auto)",
        "{scale}": "(Auto)",
        "{grid}": "(Auto)",
        "{max_width}": "(Auto)",
        "{padding_desktop}": "(Auto)",
        "{padding_mobile}": "(Auto)",
        "{alignment}": "(Auto)",
        "{glass_effect}": "(Auto)",
        "{border_radius}": "(Auto)",
        "{entrance}": "(Auto)",
        "{hover}": "(Auto)",
        "{page_transition}": "(Auto)",
        "{duration_base}": "(Auto)",
        "{easing}": "(Auto)",
        "{motion_intensity}": "(Auto)",
        "{reduced_motion_warning}": "",
        "{density}": "(Auto)",
        "{section_gap}": "(Auto)",
        "{element_gap}": "(Auto)",
        "{component_rows}": component_rows,
        "{do_bullets}": "(Auto)",
        "{dont_bullets}": "(Auto)",
        "{a11y_warnings}": a11y,
        "{trend_warnings}": trend,
        "{harmony}": f"{scores.get('harmony', 0):.2f}",
        "{trends}": f"{scores.get('trends', 0):.2f}",
        "{accessibility}": f"{scores.get('accessibility', 0):.2f}",
        "{overall}": f"{scores.get('overall', 0):.2f}",
        "{caveat}": caveat or "",
        "{generated_at}": "(stub)",
    }

    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def _brief_summary(brief: dict) -> str:
    return f"""- mission: {brief.get('mission')}
- deliverable_type: {brief.get('deliverable_type')}
- tone: {brief.get('tone')}
- primary_goal: {brief.get('primary_goal')}
- target_audience.primary: {brief.get('target_audience', {}).get('primary')}
- constraints: {brief.get('constraints')}
- must_haves: {brief.get('must_haves')}
- must_avoid: {brief.get('must_avoid')}"""


def _mix_summary(mix: dict) -> str:
    components = mix.get("components", {})
    scores = mix.get("scores", {})
    lines = [
        f"- harmony: {scores.get('harmony', 0):.2f}",
        f"- trends: {scores.get('trends', 0):.2f}",
        f"- accessibility: {scores.get('accessibility', 0):.2f}",
        f"- overall: {scores.get('overall', 0):.2f}",
        "- components:",
    ]
    for cat, data in components.items():
        lines.append(f"  - {cat}: {data.get('source_card_id')} → {data.get('source_component_id')}")
    return "\n".join(lines)