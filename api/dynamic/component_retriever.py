"""
Component Retriever — TF-IDF semantic search for Component-level references.

This is the Phase C counterpart to StyleCardRetriever. While StyleCardRetriever
operates on whole Style Cards (flat search), ComponentRetriever indexes
individual ComponentCards decomposed from Style Cards, enabling:

    1. Per-category Top-N ranking (e.g., Top Hero, Top CTA, Top Footer)
    2. Style Mixing — select the best component per category
    3. Category/sub-category filtered semantic search

Architecture:
    StyleCardRegistry → decompose_to_components() → DesignGraph
                                                            ↓
    ComponentCard.to_search_document() → TF-IDF Index → Cosine Similarity
                                                            ↑
                                            Intent Keywords → Query Embedding
                                                            ↓
                                    retrieve_top_per_category() → {hero: [...], cta: [...], ...}
                                    retrieve_component_mix()    → {hero: Card, cta: Card, ...}

Reuses KeywordEmbeddingBackend from skill_retriever.py (same as StyleCardRetriever).
No API dependency — works offline.
"""

import logging
from typing import Optional

_logger = logging.getLogger(__name__)

# ── Default categories for Style Mixing (in priority order) ─────────────────

DEFAULT_MIX_CATEGORIES = [
    "hero",
    "navbar",
    "typography",
    "color",
    "spacing",
    "animation",
    "cta",
    "features",
    "footer",
    "card",
    "gallery",
    "testimonial",
    "pricing",
    "contact",
]


class ComponentRetriever:
    """Retrieve the most relevant ComponentCards per category for Style Mixing.

    Indexes ComponentCards (not whole StyleCards) to enable fine-grained
    component-level search. The core operation is ``retrieve_top_per_category()``
    which powers the Style Mixing workflow.

    Usage:
        retriever = ComponentRetriever()
        retriever.rebuild_index(design_graph)
        mix = retriever.retrieve_component_mix("glassmorphism saas dashboard", top_n=1)
        # mix = {"hero": ComponentCard, "navbar": ComponentCard, "cta": ComponentCard, ...}
    """

    def __init__(self):
        self._backend = None
        self._index: dict[str, dict] = {}  # component_id → {vector, component}
        self._index_version = 0

    def _init_backend(self):
        """Lazy-init the KeywordEmbeddingBackend."""
        if self._backend is not None:
            return
        from api.dynamic.skill_retriever import KeywordEmbeddingBackend
        self._backend = KeywordEmbeddingBackend()

    # ── indexing ────────────────────────────────────────────────────────

    def rebuild_index(self, design_graph) -> int:
        """Rebuild TF-IDF index from all ComponentCards in the design graph.

        Args:
            design_graph: ``DesignGraph`` instance with ingested components.

        Returns:
            Number of ComponentCards indexed.
        """
        self._init_backend()

        documents = []
        comp_ids = []

        for comp_id, comp in design_graph._components.items():
            doc_text = comp.to_search_document()
            if not doc_text.strip():
                continue
            documents.append(doc_text)
            comp_ids.append(comp_id)

        if not documents:
            _logger.warning("ComponentRetriever: no components to index")
            return 0

        self._backend.build_idf(documents)

        self._index = {}
        for i, comp_id in enumerate(comp_ids):
            vec = self._backend.embed(documents[i])
            self._index[comp_id] = {
                "vector": vec,
                "component": design_graph._components[comp_id],
            }

        self._index_version += 1
        _logger.info(
            "ComponentRetriever: indexed %d components (version %d)",
            len(self._index), self._index_version,
        )
        return len(self._index)

    def rebuild_from_component_registry(self, component_registry) -> int:
        """[하위호환] Alias for rebuild_index — rebuild from DesignGraph."""
        return self.rebuild_index(component_registry)

    # ── similarity ──────────────────────────────────────────────────────

    def _cosine_similarity(self, vec1, vec2) -> float:
        """Compute cosine similarity between two TF-IDF vectors."""
        if not vec1 or not vec2:
            return 0.0

        dot = 0.0
        norm1 = 0.0
        norm2 = 0.0

        if isinstance(vec1, dict) and isinstance(vec2, dict):
            all_keys = set(vec1.keys()) | set(vec2.keys())
            for k in all_keys:
                v1 = vec1.get(k, 0.0)
                v2 = vec2.get(k, 0.0)
                dot += v1 * v2
                norm1 += v1 * v1
                norm2 += v2 * v2
        else:
            import math
            for a, b in zip(vec1, vec2):
                dot += a * b
                norm1 += a * a
                norm2 += b * b

        import math
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot / (math.sqrt(norm1) * math.sqrt(norm2))

    # ── retrieval ───────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
        sub_category_filter: Optional[str] = None,
        min_score: float = 0.0,
    ) -> list:
        """Retrieve top-K ComponentCards matching the intent query.

        Args:
            query: Intent analysis keywords (e.g., "hero glassmorphism dark saas").
            top_k: Maximum number of results.
            category_filter: Restrict to a component category (e.g., "hero", "cta").
            sub_category_filter: Further restrict to a sub-category.
            min_score: Minimum cosine similarity threshold (0.0 to 1.0).

        Returns:
            List of ``(ComponentCard, score)`` tuples, sorted by score descending.
        """
        if not self._index:
            _logger.warning("ComponentRetriever: index is empty, call rebuild_index() first")
            return []

        self._init_backend()
        query_vec = self._backend.embed(query)

        scores = []
        for comp_id, entry in self._index.items():
            comp = entry["component"]

            if category_filter and comp.category != category_filter:
                continue
            if sub_category_filter and comp.sub_category != sub_category_filter:
                continue

            score = self._cosine_similarity(query_vec, entry["vector"])
            if score >= min_score:
                scores.append((comp, round(score, 4)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    # ── per-category retrieval (Style Mixing core) ──────────────────────

    def retrieve_top_per_category(
        self,
        query: str,
        categories: Optional[list[str]] = None,
        top_n: int = 3,
        min_score: float = 0.0,
    ) -> dict[str, list]:
        """Retrieve top-N ComponentCards for each category independently.

        This is the core Style Mixing operation: for each component category
        (hero, navbar, cta, footer, etc.), find the best-matching components
        from different reference sources.

        Args:
            query: Intent analysis keywords.
            categories: List of categories to include. Defaults to
                        ``DEFAULT_MIX_CATEGORIES``.
            top_n: Number of top results per category.
            min_score: Minimum similarity threshold.

        Returns:
            Dict mapping category → list of ``(ComponentCard, score)`` tuples.
            Only categories with at least one result are included.
        """
        if categories is None:
            categories = list(DEFAULT_MIX_CATEGORIES)

        result: dict[str, list] = {}
        for cat in categories:
            matches = self.retrieve(
                query,
                top_k=top_n,
                category_filter=cat,
                min_score=min_score,
            )
            if matches:
                result[cat] = matches

        return result

    def retrieve_component_mix(
        self,
        query: str,
        categories: Optional[list[str]] = None,
        top_n: int = 1,
        min_score: float = 0.0,
    ) -> dict[str, list]:
        """Convenience alias for ``retrieve_top_per_category()``.

        The name reflects the Style Mixing intent: pick the best component
        from each category and combine them into a unified design.
        """
        return self.retrieve_top_per_category(
            query=query,
            categories=categories,
            top_n=top_n,
            min_score=min_score,
        )

    # ── formatting ──────────────────────────────────────────────────────

    def to_mix_context(
        self,
        component_mix: dict[str, list],
        max_per_category: int = 1,
    ) -> str:
        """Format a component mix result for Creative Director prompt injection.

        Produces a structured markdown block showing selected components
        per category with inline style hints and source attribution.

        Args:
            component_mix: Result from ``retrieve_component_mix()``.
            max_per_category: How many top results to include per category.

        Returns:
            Markdown-formatted context block.
        """
        if not component_mix:
            return "* (No matching components found in Reference Library)*"

        lines = [
            "## 🧩 Component-Level Reference Mix (Style Mixing)",
            "",
            "Below are the best-matching components per category, sourced from",
            "different reference designs. The Creative Director should synthesize",
            "these into a unified, harmonious design language.",
            "",
        ]

        # Category order: prioritize structural → visual → interactive
        priority_order = [
            "hero", "navbar", "header", "cta", "footer",
            "features", "pricing", "testimonial", "contact",
            "card", "gallery", "modal", "sidebar", "form",
            "typography", "color", "spacing", "animation",
        ]

        ordered_cats = [c for c in priority_order if c in component_mix]
        # Append any remaining categories not in priority list
        ordered_cats += [c for c in component_mix if c not in ordered_cats]

        for cat in ordered_cats:
            entries = component_mix[cat][:max_per_category]
            if not entries:
                continue

            lines.append(f"### {cat.upper()}")
            lines.append("")

            for comp, score in entries:
                lines.append(f"- **{comp.name}** (score: {score})")
                lines.append(f"  {comp.to_brief_text()[:300].strip()}")
                if comp.source_url:
                    lines.append(f"  📎 {comp.source_url}")
                lines.append("")

        return "\n".join(lines)

    def to_compact_hints(
        self,
        component_mix: dict[str, list],
        max_per_category: int = 1,
    ) -> str:
        """Format a compact, inline version of the component mix.

        Suitable for injecting directly into system prompts where
        token budget is limited.

        Returns:
            Single-line style hints per category.
        """
        if not component_mix:
            return ""

        hints = []
        for cat, entries in sorted(component_mix.items()):
            for comp, score in entries[:max_per_category]:
                brief = comp.to_brief_text()
                # Compact to single line
                first_line = brief.split("\n")[0] if brief else comp.name
                hints.append(f"[{cat}] {first_line} (score: {score})")

        return "\n".join(hints)

    # ── properties ──────────────────────────────────────────────────────

    @property
    def index_size(self) -> int:
        return len(self._index)

    @property
    def version(self) -> int:
        return self._index_version


# ── Global singleton ───────────────────────────────────────────────────────

_component_retriever: Optional[ComponentRetriever] = None


def get_component_retriever() -> ComponentRetriever:
    """ComponentRetriever 싱글톤을 반환한다."""
    global _component_retriever
    if _component_retriever is None:
        _component_retriever = ComponentRetriever()
    return _component_retriever
