"""
Creative Director 4-layer REST API Routes.

4-layer 디자인 에이전트 시스템 (UX Researcher → Design Librarian →
Art Director → Creative Director) 의 REST 엔드포인트.

Phase 2 (2026-08-07) — server.py 의 직접 dispatch 가 아닌
`api/api/routes/__init__.py` 의 `handle_get` / `handle_post` 경로를 통해 호출된다.
이 라우트 파일은 style_card_routes.py 와 동일한 패턴.

Endpoints:
    GET  /api/creative-director/health
        — health check + 라이브러리 카드 수
    GET  /api/creative-director/cards
        — 등록된 StyleCard 목록 (library_root)
    POST /api/creative-director/brief
        — 4-layer 파이프라인 실행 → DesignBrief 반환
    POST /api/creative-director/cards/extract
        — 텍스트 디자인 설명 → StyleCard 자동 추출 (Phase 3)
"""

import logging
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import j_ok, j_err, require

_logger = logging.getLogger(__name__)

# 라이브러리 루트 — 다온 프로젝트 루트의 data/reference_library/
_LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "data" / "reference_library"


# ── GET Routes ──────────────────────────────────────────────────────────


def handle_get_creative_director_health(handler, parsed) -> bool:
    """GET /api/creative-director/health — Creative Director 모듈 상태 확인."""
    try:
        from api.api.agents.creative_director import create_design_brief
        ok = callable(create_design_brief)
        card_count = 0
        if _LIBRARY_ROOT.exists():
            cards_dir = _LIBRARY_ROOT / "cards"
            if cards_dir.exists():
                card_count = len(list(cards_dir.glob("*.yaml")))
        return j_ok(handler, {
            "ok": ok,
            "phase": 2,
            "library_root": str(_LIBRARY_ROOT),
            "library_exists": _LIBRARY_ROOT.exists(),
            "card_count": card_count,
            "version": "1.0",
            "layers": [
                "ux_researcher",
                "design_librarian",
                "art_director",
                "creative_director",
            ],
        })
    except Exception as e:
        _logger.exception("Creative Director health check failed")
        return j_err(handler, f"health check failed: {e}", 500)


def handle_get_creative_director_cards(handler, parsed) -> bool:
    """GET /api/creative-director/cards — 등록된 모든 StyleCard 메타."""
    try:
        from api.api.style_card import StyleCardRegistry

        registry = StyleCardRegistry()
        if _LIBRARY_ROOT.exists():
            registry.load_all(_LIBRARY_ROOT)

        cards = []
        for card_id, card in sorted(registry._cards.items()):
            cards.append({
                "id": card.id,
                "name": card.name,
                "category": card.category,
                "tags": getattr(card, "tags", []),
                "source": getattr(card, "source", ""),
                "created": getattr(card, "created", ""),
                "score": getattr(card, "evaluation", {}).get("score", 0) if getattr(card, "evaluation", None) else 0,
                "components": len(getattr(card, "decomposed_cards", []) or []),
            })

        return j_ok(handler, {
            "cards": cards,
            "total": len(cards),
            "library_root": str(_LIBRARY_ROOT),
        })
    except Exception as e:
        _logger.exception("List cards failed")
        return j_err(handler, f"list cards failed: {e}", 500)


# ── POST Routes ─────────────────────────────────────────────────────────


def handle_post_creative_director_brief(handler, body: dict) -> bool:
    """POST /api/creative-director/brief — 4-layer DesignBrief 생성.

    Body:
        {
          "user_mission": "string, 필수",
          "context": { "target_audience": "...", "tone": "...", "constraints": [...] },
          "library_root": "string, 선택 (기본 data/reference_library)",
          "n_candidates": int, 선택 (기본 5)
        }

    Returns:
        DesignBrief dict: {
            markdown, spec, creative_brief, candidates, mix, warnings, scores
        }
    """
    try:
        user_mission = require(body, "user_mission", str)
        context = body.get("context") or {}
        library_root = Path(body["library_root"]) if body.get("library_root") else _LIBRARY_ROOT
        n_candidates = int(body.get("n_candidates", 5))

        from api.api.agents.creative_director import create_design_brief

        # llm_callable 은 None → stub 모드로 동작 (Phase 1 의 stub 검증 로직 그대로).
        # Phase 2+ 에서 라온이 라온 자신의 LLM 호출을 주입할 수 있다.
        brief = create_design_brief(
            user_mission=user_mission,
            context=context,
            library_root=library_root,
            n_candidates=n_candidates,
            llm_callable=None,
        )

        return j_ok(handler, {
            "markdown": brief.markdown,
            "spec": brief.spec,
            "creative_brief": brief.creative_brief,
            "candidates": brief.candidates,
            "mix": brief.mix,
            "warnings": brief.warnings,
            "scores": brief.scores,
        })
    except KeyError as e:
        return j_err(handler, f"missing required field: {e}", 400)
    except Exception as e:
        _logger.exception("create_design_brief failed")
        return j_err(handler, f"creative_director brief failed: {e}", 500)


def handle_post_creative_director_card_extract(handler, body: dict) -> bool:
    """POST /api/creative-director/cards/extract — 텍스트 → StyleCard 자동 추출.

    Body:
        {
          "name": "string, 필수",
          "description": "string, 필수 (디자인 설명)",
          "category": "string, 선택 (VALID_COMPONENT_CATEGORIES 중)",
          "source": "string, 선택 (URL 또는 'internal:...')",
          "tags": ["list", "string"]
        }

    Phase 2: stub 으로 카드를 만들어 저장만 한다. Phase 3 에서 StyleCardExtractor 가
    LLM 으로 디자인 DNA 를 채워넣는다.
    """
    try:
        name = require(body, "name", str)
        description = require(body, "description", str)
        category = body.get("category", "other")
        source = body.get("source", f"internal:{name.lower().replace(' ', '-')}")
        tags = body.get("tags", [])

        # Phase 2 stub: 기본 StyleCard 생성 (DNA 는 기본값)
        # Phase 3 에서 StyleCardExtractor.extract_from_text() 호출로 교체
        from api.api.style_card import (
            StyleCard, DesignDNA, ColorDNA, TypographyDNA,
            LayoutDNA, AnimationDNA, SpacingDNA,
            Composition, Guidelines, Evaluation,
        )
        import uuid as _uuid
        from datetime import datetime, timezone

        card = StyleCard(
            id=f"{name.lower().replace(' ', '-')}-{_uuid.uuid4().hex[:6]}",
            name=name,
            source=source,
            category=category,
            created=datetime.now(timezone.utc).isoformat(),
            description=description,
            tags=tags,
            design_dna=DesignDNA(),
            composition=Composition(),
            guidelines=Guidelines(),
            evaluation=Evaluation(score=0.0, reviewed=False),
            decomposed_cards=[],
        )

        # 저장
        cards_dir = _LIBRARY_ROOT / "cards"
        cards_dir.mkdir(parents=True, exist_ok=True)
        card.save(cards_dir)

        return j_ok(handler, {
            "ok": True,
            "card": card.to_dict(),
            "path": str(cards_dir / f"{card.id}.yaml"),
            "phase": 2,
            "note": "DNA 는 기본값. Phase 3 에서 StyleCardExtractor 가 자동 채움.",
        })
    except KeyError as e:
        return j_err(handler, f"missing required field: {e}", 400)
    except Exception as e:
        _logger.exception("extract card failed")
        return j_err(handler, f"card extract failed: {e}", 500)