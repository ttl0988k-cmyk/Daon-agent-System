"""
Layer 1: UX Researcher — 프롬프트 빌더 + CreativeBrief 검증.
"""

from __future__ import annotations
from typing import Optional


_PROMPT_HEADER = """You are UX Researcher, the first layer of Creative Director 4-layer system.

Your ONLY job is to extract user intent from a design mission. You do NOT
design, recommend styles, or generate code. You produce a CreativeBrief JSON
that downstream layers (Design Librarian → Art Director → Creative Director)
will use to choose references and produce a design.

Hard rules:
- Extract only what the user said or strongly implied.
- If something is unclear, put it in `ambiguities` rather than inventing.
- Never invent target audience, tone, or constraints.
- Output must be valid JSON only (no prose, no markdown fences).
"""


_SCHEMA = """
{
  "mission": "string, one-line summary",
  "deliverable_type": "website | landing-page | dashboard | portfolio | component | mobile-app | other",
  "target_audience": {
    "primary": "string",
    "secondary": "string or null",
    "psychographics": "1-3 short phrases"
  },
  "tone": ["max 3 adjectives"],
  "primary_goal": "conversion | branding | information | sales | portfolio",
  "constraints": ["list of technical/design constraints"],
  "must_haves": ["required sections/components"],
  "must_avoid": ["things to explicitly avoid (e.g., skeuomorphism, marquee)"],
  "brand_keywords": ["5-10 keywords from user's mission or context"],
  "references": ["user-mentioned URLs, if any"],
  "platform_target": ["mobile", "desktop", "tablet"],
  "ambiguities": ["things that were unclear — ask user if 3+ items"]
}
"""


def build_research_prompt(user_mission: str, raw_context: Optional[dict] = None) -> str:
    ctx = raw_context or {}
    parts = [_PROMPT_HEADER, "", "USER_MISSION:", f'"{user_mission}"', ""]
    if ctx:
        parts.append("ADDITIONAL_CONTEXT (from conversation):")
        parts.append(str(ctx))
        parts.append("")
    parts.append("OUTPUT SCHEMA (fill every field, use null/[] if unknown):")
    parts.append(_SCHEMA)
    parts.append("")
    parts.append("Return JSON only. No markdown fences, no preamble.")
    return "\n".join(parts)


def validate_creative_brief(brief: dict) -> list[str]:
    """CreativeBrief 의 필드를 검증하고, 문제점 리스트를 반환한다."""
    issues = []
    if not brief.get("mission"):
        issues.append("mission 누락")
    if not brief.get("deliverable_type"):
        issues.append("deliverable_type 누락")
    if not brief.get("target_audience", {}).get("primary"):
        issues.append("target_audience.primary 누락")
    if not brief.get("tone"):
        issues.append("tone 누락 — 기본값(minimal, warm) 권장")
    if len(brief.get("ambiguities", [])) >= 3:
        issues.append(f"ambiguities {len(brief['ambiguities'])}개 — 사용자 확인 필요")
    return issues