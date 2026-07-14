"""
Style Card REST API Routes for Daon Agent System.

REST endpoints for:
  - List / search Style Cards in Reference Library
  - Get individual Style Card content
  - Extract Style Card from design description (text-to-card)
  - Delete Style Cards
  - Rebuild search index
"""

import logging
from urllib.parse import parse_qs

from api.helpers import j, j_ok, j_err, require

_logger = logging.getLogger(__name__)


# ── GET Routes ──────────────────────────────────────────────────────────


def handle_get_style_cards(handler, parsed) -> bool:
    """GET /api/style-cards — list all Style Cards.

    Query params:
        category: (optional) filter by category
        tags: (optional) comma-separated tag filter
        q: (optional) semantic search query
        top_k: (optional) number of results for search (default 10)
    """
    from api.style_card import get_style_card_registry
    from api.dynamic.style_card_retriever import get_style_card_retriever

    query_params = parse_qs(parsed.query)
    category = query_params.get("category", [None])[0]
    tags_str = query_params.get("tags", [None])[0]
    search_q = query_params.get("q", [None])[0]
    top_k = int(query_params.get("top_k", ["10"])[0])

    registry = get_style_card_registry()

    # Semantic search path
    if search_q:
        retriever = get_style_card_retriever()
        retriever.rebuild_index(registry)
        results = retriever.retrieve(
            search_q,
            top_k=top_k,
            category_filter=category,
            min_score=0.0,
        )
        cards_list = []
        for card, score in results:
            cards_list.append(
                {
                    "id": card.id,
                    "name": card.name,
                    "category": card.category,
                    "sub_category": card.sub_category,
                    "tags": card.tags,
                    "source_url": card.source_url,
                    "source_type": card.source_type,
                    "score": round(score, 4),
                    "evaluation": {
                        "score": card.evaluation.score,
                        "originality": card.evaluation.originality,
                        "accessibility": card.evaluation.accessibility,
                        "responsiveness": card.evaluation.responsiveness,
                        "trend_relevance": card.evaluation.trend_relevance,
                        "reviewed": card.evaluation.reviewed,
                    },
                    "inline_hint": card.to_inline_style_hint(),
                }
            )
        return j_ok(handler, {"cards": cards_list, "total": len(cards_list)})

    # List/filter path
    if category:
        cards = registry.get_by_category(category)
    else:
        cards = list(registry._cards.values())

    if tags_str:
        filter_tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]
        cards = [
            c
            for c in cards
            if any(t in [tag.lower() for tag in c.tags] for t in filter_tags)
        ]

    cards_list = [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "sub_category": c.sub_category,
            "tags": c.tags,
            "source_url": c.source_url,
            "source_type": c.source_type,
            "version": c.version,
            "evaluation": {
                "score": c.evaluation.score,
                "originality": c.evaluation.originality,
                "accessibility": c.evaluation.accessibility,
                "responsiveness": c.evaluation.responsiveness,
            },
            "inline_hint": c.to_inline_style_hint(),
        }
        for c in cards
    ]

    return j_ok(handler, {"cards": cards_list, "total": len(cards_list)})


def handle_get_style_card_content(handler, parsed) -> bool:
    """GET /api/style-cards/content?id=... — get full Style Card content."""
    query_params = parse_qs(parsed.query)
    card_id = query_params.get("id", [None])[0]
    if not card_id:
        return j_err(handler, "Missing 'id' parameter")

    from api.style_card import get_style_card_registry

    registry = get_style_card_registry()
    card = registry.get(card_id)
    if not card:
        return j_err(handler, f"Style Card not found: {card_id}")

    return j_ok(handler, {"card": card.to_dict()})


def handle_get_style_cards_categories(handler, parsed) -> bool:
    """GET /api/style-cards/categories — list all categories with counts."""
    from api.style_card import get_style_card_registry

    registry = get_style_card_registry()
    categories = {}
    for cat, cards in registry._by_category.items():
        categories[cat] = len(cards)

    return j_ok(handler, {"categories": categories, "total_cards": len(registry._cards)})


# ── POST Routes ─────────────────────────────────────────────────────────


def handle_post_style_card_extract(handler, body: dict) -> bool:
    """POST /api/style-cards/extract — extract Style Card from text description.

    Body:
        description: (required) natural language design description
        name: (optional) card name
        category: (optional) component category (default "misc")
        source_url: (optional) reference URL
    """
    try:
        require(body, "description")
    except ValueError as e:
        return j_err(handler, str(e))

    import threading

    result = {"path": None, "error": None}

    def _extract():
        try:
            from api.dynamic.style_card_extractor import extract_style_card_from_text

            path = extract_style_card_from_text(
                description=body["description"],
                card_name=body.get("name", ""),
                category=body.get("category", "misc"),
                source_url=body.get("source_url", ""),
            )
            result["path"] = path
        except Exception as e:
            _logger.exception("[StyleCard] Extraction failed")
            result["error"] = str(e)

    threading.Thread(target=_extract, daemon=True).start()

    return j(handler, {
        "ok": True,
        "message": "Style Card extraction started in background.",
    })


def handle_post_style_card_save(handler, body: dict) -> bool:
    """POST /api/style-cards/save — save a manually crafted Style Card.

    Body:
        card: (required) full Style Card dict
    """
    try:
        require(body, "card")
    except ValueError as e:
        return j_err(handler, str(e))

    try:
        from api.style_card import (
            StyleCard,
            get_references_dir,
            get_style_card_registry,
        )

        card_data = body["card"]
        card = StyleCard._from_dict(card_data)
        card.evaluate()

        refs_dir = get_references_dir()
        card_path = card.save(refs_dir)

        registry = get_style_card_registry()
        registry.add(card, save=False)
        registry.rebuild_index()

        return j_ok(handler, {
            "message": f"Style Card saved: {card.id}",
            "path": str(card_path),
            "evaluation": {
                "score": card.evaluation.score,
                "originality": card.evaluation.originality,
                "accessibility": card.evaluation.accessibility,
                "responsiveness": card.evaluation.responsiveness,
            },
        })
    except Exception as e:
        _logger.exception("[StyleCard] Save failed")
        return j_err(handler, f"Failed to save Style Card: {e}")


def handle_post_style_card_delete(handler, body: dict) -> bool:
    """POST /api/style-cards/delete — delete a Style Card.

    Body:
        id: (required) Style Card ID to delete
    """
    try:
        require(body, "id")
    except ValueError as e:
        return j_err(handler, str(e))

    from api.style_card import get_style_card_registry

    registry = get_style_card_registry()
    success = registry.remove(body["id"])

    if success:
        registry.rebuild_index()
        return j_ok(handler, {"message": f"Style Card deleted: {body['id']}"})
    else:
        return j_err(handler, f"Style Card not found: {body['id']}")


def handle_post_style_cards_rebuild_index(handler, body: dict) -> bool:
    """POST /api/style-cards/rebuild-index — rebuild the search index."""
    from api.style_card import get_style_card_registry
    from api.dynamic.style_card_retriever import get_style_card_retriever

    registry = get_style_card_registry()
    retriever = get_style_card_retriever()
    count = retriever.rebuild_index(registry)

    return j_ok(handler, {
        "message": f"Index rebuilt with {count} cards",
        "indexed": count,
    })
