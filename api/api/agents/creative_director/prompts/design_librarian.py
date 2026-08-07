"""
Layer 2: Design Librarian — TF-IDF 후보 검색 + LLM 랭킹 프롬프트.
"""

from __future__ import annotations
from typing import Optional


_PROMPT_HEADER = """You are Design Librarian, the second layer of Creative Director 4-layer system.

You receive a CreativeBrief (from UX Researcher) and a list of StyleCard
candidates already retrieved by TF-IDF from the reference library.

Your ONLY job is to RANK and EXPLAIN the candidates:
- Which ones best match the brief's tone, audience, and must-haves?
- Which ones have components that fit the deliverable_type?
- For each candidate, summarize its Design DNA in 2 sentences
  (use StyleCard.to_brief_text() output as input).

You do NOT mix components. You do NOT generate new design ideas.
You only analyze what already exists in the library.
"""


_CATEGORY_MAP = {
    "landing-page": ["hero", "navbar", "cta", "footer", "features", "pricing", "testimonial"],
    "website": ["hero", "navbar", "footer", "features", "contact"],
    "dashboard": ["sidebar", "card", "chart", "table", "stats"],
    "portfolio": ["hero", "gallery", "card", "footer"],
    "component": [],
    "mobile-app": ["navbar", "card", "form", "button"],
    "other": ["hero", "features", "footer"],
}


def categories_for(deliverable_type: str) -> list[str]:
    return _CATEGORY_MAP.get(deliverable_type, _CATEGORY_MAP["other"])


def retrieve_candidates_tfidf(registry, brief: dict, n: int = 10) -> list:
    """
    Phase 1 TF-IDF 검색. 키워드 = brand_keywords + tone + must_haves.

    StyleCardRegistry.search_by_tags() 가 있으면 그걸 먼저 쓰고,
    없으면 _cards.values() 를 직접 순회하며 키워드 매칭한다.
    """
    keywords = set()
    keywords.update(brief.get("brand_keywords", []))
    keywords.update(brief.get("tone", []))
    keywords.update(brief.get("must_haves", []))
    keywords.update([brief.get("deliverable_type", "")])

    target_categories = set(categories_for(brief.get("deliverable_type", "other")))
    if target_categories:
        keywords.update(target_categories)

    # 태그 매칭 (search_by_tags)
    if hasattr(registry, "search_by_tags"):
        try:
            tag_results = registry.search_by_tags(list(keywords))
            if tag_results:
                return tag_results[:n]
        except Exception:
            pass

    # 폴백: _cards 직접 순회 + 키워드 매칭
    candidates = []
    for card in getattr(registry, "_cards", {}).values():
        if not hasattr(card, "to_search_document"):
            continue
        doc = card.to_search_document().lower()
        score = sum(1 for kw in keywords if kw.lower() in doc)
        if score > 0:
            candidates.append((score, card))
    candidates.sort(key=lambda x: -x[0])
    return [c for _, c in candidates[:n]]


def build_librarian_prompt(brief: dict, candidate_summaries: list[str]) -> str:
    parts = [
        _PROMPT_HEADER,
        "",
        "CREATIVE_BRIEF:",
        _brief_to_compact(brief),
        "",
        f"CANDIDATES ({len(candidate_summaries)} cards retrieved by TF-IDF):",
    ]
    for i, summary in enumerate(candidate_summaries, 1):
        parts.append(f"\n--- Candidate {i} ---")
        parts.append(summary)
    parts.append("")
    parts.append("OUTPUT JSON (no fences):")
    parts.append("""
{
  "ranked_candidates": [
    {"card_id": "...", "name": "...", "match_score": 0.0~1.0, "why": "2 sentences", "key_components": ["hero","cta"]}
  ],
  "missing_categories": ["list"],
  "recommendation": "1-2 sentences for Art Director"
}
""")
    return "\n".join(parts)


def _brief_to_compact(brief: dict) -> str:
    """CreativeBrief 를 Librarian 프롬프트에 넣기 좋은 형태로 요약."""
    return "\n".join([
        f"mission: {brief.get('mission', '')}",
        f"deliverable_type: {brief.get('deliverable_type', '')}",
        f"target_audience.primary: {brief.get('target_audience', {}).get('primary', '')}",
        f"tone: {', '.join(brief.get('tone', []))}",
        f"primary_goal: {brief.get('primary_goal', '')}",
        f"constraints: {brief.get('constraints', [])}",
        f"must_haves: {brief.get('must_haves', [])}",
        f"must_avoid: {brief.get('must_avoid', [])}",
        f"brand_keywords: {brief.get('brand_keywords', [])}",
        f"target_categories: {categories_for(brief.get('deliverable_type', 'other'))}",
    ])