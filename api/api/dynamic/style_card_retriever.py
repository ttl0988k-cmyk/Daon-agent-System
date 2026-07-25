"""
Style Card Retriever — TF-IDF semantic search for Style Card references.

Mirrors SemanticSkillRetriever pattern but operates on StyleCardRegistry
instead of SkillRegistry. Used by Creative Director to find relevant
design references based on intent analysis keywords.

Architecture:
    StyleCardRegistry (.yaml files) → TF-IDF Index → Cosine Similarity → Top-K Style Cards
                                               ↑
                           Intent Keywords → Query Embedding
"""

import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


class StyleCardRetriever:
    """Retrieve the most relevant Style Cards for design intent keywords.

    Reuses KeywordEmbeddingBackend from skill_retriever.py for consistent
    TF-IDF vectorization. No API dependency — works offline.

    Usage:
        retriever = StyleCardRetriever()
        retriever.rebuild_index(style_card_registry)
        top_cards = retriever.retrieve("glassmorphism hero dark saas", top_k=5)
    """

    def __init__(self):
        self._backend = None
        self._index: dict[str, dict] = {}  # card_id → {vector, card}
        self._index_version = 0

    def _init_backend(self):
        """Lazy-init the KeywordEmbeddingBackend."""
        if self._backend is not None:
            return
        from api.dynamic.skill_retriever import KeywordEmbeddingBackend
        self._backend = KeywordEmbeddingBackend()

    def rebuild_index(self, style_card_registry) -> int:
        """Rebuild the TF-IDF index from all loaded Style Cards.

        Args:
            style_card_registry: StyleCardRegistry instance with loaded cards.

        Returns:
            Number of Style Cards indexed.
        """
        self._init_backend()

        documents = []
        card_ids = []

        for card_id, card in style_card_registry._cards.items():
            doc_text = card.to_search_document()
            if not doc_text.strip():
                continue
            documents.append(doc_text)
            card_ids.append(card_id)

        if not documents:
            _logger.warning("StyleCardRetriever: no cards to index")
            return 0

        # Build IDF
        self._backend.build_idf(documents)

        # Build index
        self._index = {}
        for i, card_id in enumerate(card_ids):
            vec = self._backend.embed(documents[i])
            self._index[card_id] = {
                "vector": vec,
                "card": style_card_registry._cards[card_id],
            }

        self._index_version += 1
        _logger.info(
            "StyleCardRetriever: indexed %d cards (version %d)",
            len(self._index), self._index_version,
        )
        return len(self._index)

    def _cosine_similarity(self, vec1, vec2) -> float:
        """Compute cosine similarity between two TF-IDF vectors."""
        if not vec1 or not vec2:
            return 0.0

        dot = 0.0
        norm1 = 0.0
        norm2 = 0.0

        # vec1 is a dict (keyword backend), vec2 is a dict
        if isinstance(vec1, dict) and isinstance(vec2, dict):
            all_keys = set(vec1.keys()) | set(vec2.keys())
            for k in all_keys:
                v1 = vec1.get(k, 0.0)
                v2 = vec2.get(k, 0.0)
                dot += v1 * v2
                norm1 += v1 * v1
                norm2 += v2 * v2
        else:
            # Fallback for list vectors
            import math
            for a, b in zip(vec1, vec2):
                dot += a * b
                norm1 += a * a
                norm2 += b * b

        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0

        import math
        return dot / (math.sqrt(norm1) * math.sqrt(norm2))

    def retrieve(self, query: str, top_k: int = 5,
                 category_filter: Optional[str] = None,
                 min_score: float = 0.0) -> list:
        """Retrieve top-K Style Cards matching the intent query.

        Args:
            query: Intent analysis keywords (e.g., "glassmorphism hero dark saas").
            top_k: Maximum number of results.
            category_filter: Optionally restrict to a component category
                             (e.g., "hero", "navbar", "cta").
            min_score: Minimum cosine similarity threshold (0.0 to 1.0).

        Returns:
            List of (StyleCard, score) tuples, sorted by score descending.
        """
        if not self._index:
            _logger.warning("StyleCardRetriever: index is empty, call rebuild_index() first")
            return []

        self._init_backend()
        query_vec = self._backend.embed(query)

        scores = []
        for card_id, entry in self._index.items():
            card = entry["card"]

            # Apply category filter
            if category_filter and card.category != category_filter:
                continue

            score = self._cosine_similarity(query_vec, entry["vector"])
            if score >= min_score:
                scores.append((card, round(score, 4)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def retrieve_for_brief(self, query: str, top_k: int = 5) -> str:
        """Retrieve Style Cards and format them for Design Brief injection.

        Returns a markdown-ready text block listing the top cards with scores
        and inline style hints.
        """
        results = self.retrieve(query, top_k=top_k, min_score=0.01)
        if not results:
            return "* (No matching Style Cards found in Reference Library)*"

        lines = ["### 📚 Reference Library Matches", ""]
        for i, (card, score) in enumerate(results, 1):
            lines.append(f"{i}. **{card.name}** `[{card.category}]` — Score: {score}")
            lines.append(f"   {card.to_inline_style_hint()}")
            if card.source_url:
                lines.append(f"   Source: {card.source_url}")
            lines.append("")

        return "\n".join(lines)

    def retrieve_design_dna_context(self, query: str, top_k: int = 3) -> str:
        """Retrieve condensed Design DNA for prompt injection.

        Returns a compact block suitable for appending to the system prompt
        of a frontend agent node.
        """
        results = self.retrieve(query, top_k=top_k, min_score=0.05)
        if not results:
            return ""

        lines = ["## 🎨 Design DNA Context (from Reference Library)", ""]
        for card, _ in results:
            lines.append(card.to_brief_text())

        return "\n".join(lines)

    @property
    def index_size(self) -> int:
        return len(self._index)


# ── Global singleton ───────────────────────────────────────────────────────

_style_card_retriever: Optional[StyleCardRetriever] = None


def get_style_card_retriever() -> StyleCardRetriever:
    """StyleCardRetriever singleton."""
    global _style_card_retriever
    if _style_card_retriever is None:
        _style_card_retriever = StyleCardRetriever()
    return _style_card_retriever
