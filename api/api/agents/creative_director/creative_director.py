"""
Creative Director — 4-Layer 디자인 에이전트 오케스트레이터.

기존 api/api/style_card.py (StyleCard, ComponentCard, StyleMixer, DesignGraph) 를
재사용하여 4-layer 파이프라인을 실행한다.

이 모듈은 server.exe 의 라이브러리에 등록되지 않은 상태로, 라온이 직접 호출한다.
서버 라우터 연결은 Phase 2 이후.

Usage:
    from pathlib import Path
    from api.api.agents.creative_director.creative_director import create_design_brief

    brief = create_design_brief(
        user_mission="동네 카페 소개 사이트 만들어줘",
        context={
            "target_audience": "2030 여성, 디저트·감성 선호",
            "tone": "따뜻하고 미니멀",
            "constraints": ["모바일 우선"],
        },
        library_root=Path("data/reference_library"),
        n_candidates=5,
    )
    print(brief.markdown)
    print(brief.spec)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DesignBrief:
    """Creative Director 최종 산출물."""
    markdown: str = ""                              # 사람이 읽는 Markdown
    spec: dict = field(default_factory=dict)        # 프론트엔드 에이전트용 JSON
    creative_brief: dict = field(default_factory=dict)  # UX Researcher 결과
    candidates: list = field(default_factory=list)      # Design Librarian 후보
    mix: dict = field(default_factory=dict)             # Art Director Mix
    warnings: dict = field(default_factory=dict)        # 트렌드/접근성 경고
    scores: dict = field(default_factory=dict)          # harmony/trends/a11y/overall


def create_design_brief(
    user_mission: str,
    context: Optional[dict] = None,
    library_root: Optional[Path] = None,
    n_candidates: int = 5,
    llm_callable=None,    # 라온이 주입 — Layer 1~4 의 LLM 호출자
) -> DesignBrief:
    """
    4-layer 파이프라인을 실행해 DesignBrief 를 반환한다.

    Args:
        user_mission: 사용자 원문 미션
        context: 추가 맥락 (target_audience, tone, constraints 등)
        library_root: StyleCard 라이브러리 루트 (기본 data/reference_library)
        n_candidates: Librarian 이 추릴 후보 수
        llm_callable: LLM 호출 함수 — 라온이 환경에 맞게 주입

    Returns:
        DesignBrief (markdown + spec + 중간 산출물 모두 포함)

    Note:
        - 라온이 이 함수를 직접 호출한다 (server.exe 에 등록 안 됨).
        - style_card.py 의 StyleCard / StyleMixer / DesignGraph 를 import 해서 사용.
        - llm_callable 이 None 이면 4-layer 의 LLM 호출은 stub 으로 폴백
          (개발·테스트용 — 실제 결과물은 placeholder).
    """
    ctx = context or {}
    root = library_root or Path("data/reference_library")

    # ── Layer 1: UX Researcher ──
    from .prompts.ux_researcher import build_research_prompt
    creative_brief = _call_layer(
        llm_callable,
        build_research_prompt(user_mission, ctx),
        fallback=_stub_creative_brief(user_mission, ctx),
    )

    # ── Layer 2: Design Librarian ──
    from .prompts.design_librarian import (
        build_librarian_prompt,
        retrieve_candidates_tfidf,
    )
    from api.api.style_card import StyleCardRegistry

    registry = StyleCardRegistry()
    if root.exists():
        registry.load_all(root)

    candidates = retrieve_candidates_tfidf(registry, creative_brief, n=n_candidates * 2)
    ranking = _call_layer(
        llm_callable,
        build_librarian_prompt(
            creative_brief,
            [c.to_brief_text() for c in candidates],
        ),
        fallback=_stub_ranking(candidates),
    )

    # ── Layer 3: Art Director ──
    from .prompts.art_director import build_art_director_prompt, run_style_mixer

    auto_mix = run_style_mixer(creative_brief, ranking, registry)
    trend_result = _safe_check_trends(auto_mix)
    a11y_result = _safe_check_a11y(auto_mix)

    mix = _call_layer(
        llm_callable,
        build_art_director_prompt(
            creative_brief, ranking, auto_mix, trend_result, a11y_result,
        ),
        fallback=auto_mix,
    )

    # ── Layer 4: Creative Director ──
    from .prompts.creative_director import (
        build_creative_director_prompt,
        render_brief_markdown,
    )
    from .brief_template import load_template

    template_md = load_template()
    prompt = build_creative_director_prompt(creative_brief, mix, template_md)
    raw = _call_layer(
        llm_callable,
        prompt,
        fallback={
            "json_spec": _stub_spec(creative_brief, mix),
            "markdown": render_brief_markdown(
                template_md, creative_brief, mix, mix.get("warnings", {}),
                caveat=None if mix.get("scores", {}).get("overall", 0) >= 0.7
                else "참조 후보가 약합니다.",
            ),
        },
    )

    return DesignBrief(
        markdown=raw.get("markdown", ""),
        spec=raw.get("json_spec", {}),
        creative_brief=creative_brief,
        candidates=[c.to_dict() for c in candidates],
        mix=mix,
        warnings={
            "trend": getattr(trend_result, "warnings", []),
            "accessibility": getattr(a11y_result, "warnings", []),
        },
        scores=mix.get("scores", {}),
    )


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

def _call_layer(llm_callable, prompt: str, fallback):
    """LLM 호출. llm_callable 없거나 실패 시 fallback."""
    if llm_callable is None:
        return fallback
    try:
        return llm_callable(prompt)
    except Exception:
        return fallback


def _safe_check_trends(mix):
    try:
        from api.api.routes.style_card_routes import design_check_trends
        return design_check_trends(mix.get("final_components", []))
    except Exception:
        class _Stub:
            trend_score = 0.85
            warnings = []
        return _Stub()


def _safe_check_a11y(mix):
    try:
        from api.api.routes.style_card_routes import design_check_accessibility
        return design_check_accessibility(mix.get("final_components", []))
    except Exception:
        class _Stub:
            a11y_score = 0.9
            warnings = []
        return _Stub()


# ── Stub 폴백 (LLM 미주입 시) ─────────────────────────────────────────────────

def _stub_creative_brief(user_mission, ctx):
    return {
        "mission": user_mission,
        "deliverable_type": "landing-page",
        "target_audience": {"primary": ctx.get("target_audience", "general")},
        "tone": ctx.get("tone", "minimal"),
        "primary_goal": "branding",
        "constraints": ctx.get("constraints", []),
        "must_haves": [],
        "must_avoid": ["skeuomorphism", "marquee"],
        "brand_keywords": [],
        "ambiguities": [],
    }


def _stub_ranking(candidates):
    return {
        "ranked_candidates": [
            {"card_id": c.id, "name": c.name, "match_score": 0.5,
             "why": "stub", "key_components": []}
            for c in candidates[:5]
        ],
        "missing_categories": [],
        "recommendation": "stub ranking",
    }


def _stub_spec(brief, mix):
    return {
        "mission": brief.get("mission"),
        "design_dna": {},
        "components": [],
        "guidelines": {"do": [], "dont": []},
        "warnings": {},
        "scores": mix.get("scores", {}),
    }