"""
Semantic Skill Retriever — Embedding-based skill selection for the CEO planner.

Provides:
- SemanticSkillRetriever: replaces hardcoded Rule-based skill mapping with
  semantic similarity search across the Skill Registry.
- Pluggable embedding backends: keyword (baseline), minimax (API), or custom.
- Auto-rebuild on registry changes.

Architecture:
  Skill Registry (.md files) → Embedding Index → Cosine Similarity → Top-K Skills
                                          ↑
                              CEO Task → Task Embedding
"""

import json
import logging
import math
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lightweight embedding backends
# ---------------------------------------------------------------------------

class KeywordEmbeddingBackend:
    """Simple keyword-overlap based embedding (no API dependency).
    
    Tokenizes text into word n-grams (1-3), computes TF-IDF-like vectors,
    and compares via cosine similarity. Serves as a baseline/fallback.
    """
    
    def __init__(self):
        self._idf_cache: dict[str, float] = {}
        self._doc_count = 0
    
    def _tokenize(self, text: str) -> list[str]:
        """Extract normalized tokens + n-grams from text."""
        # Normalize: lowercase, remove special chars, split
        text = text.lower()
        text = re.sub(r'[^a-z0-9가-힣\s]', ' ', text)
        words = [w for w in text.split() if len(w) > 1]
        
        # 1-grams + 2-grams + 3-grams
        tokens = list(words)
        for i in range(len(words) - 1):
            tokens.append(words[i] + '_' + words[i + 1])
        for i in range(len(words) - 2):
            tokens.append(words[i] + '_' + words[i + 1] + '_' + words[i + 2])
        return tokens
    
    def _build_tf_vector(self, tokens: list[str]) -> dict[str, float]:
        """Build normalized TF vector."""
        vec = {}
        for t in tokens:
            vec[t] = vec.get(t, 0) + 1
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        return vec
    
    def embed(self, text: str) -> dict[str, float]:
        """Convert text to a sparse vector (token → weight)."""
        tokens = self._tokenize(text)
        vec = self._build_tf_vector(tokens)
        # Apply IDF weighting
        for token in vec:
            if token in self._idf_cache:
                vec[token] *= self._idf_cache[token]
        return vec
    
    def build_idf(self, documents: list[str]):
        """Build IDF cache from a corpus of documents."""
        self._doc_count = len(documents)
        doc_freq: dict[str, int] = {}
        for doc in documents:
            tokens = set(self._tokenize(doc))
            for t in tokens:
                doc_freq[t] = doc_freq.get(t, 0) + 1
        
        self._idf_cache = {}
        for token, df in doc_freq.items():
            self._idf_cache[token] = math.log((self._doc_count + 1) / (df + 1)) + 1


class MiniMaxEmbeddingBackend:
    """Use MiniMax Embedding API for semantic vectors (requires MINIMAX_API_KEY)."""
    
    def __init__(self):
        self._api_key: Optional[str] = None
        self._base_url = "https://api.minimax.io/v1"
        self._cache: OrderedDict = OrderedDict()
        self._cache_max = 500
    
    def _get_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        try:
            from api.dynamic.auth import _get_minimax_api_key
            self._api_key = _get_minimax_api_key()
        except Exception as e:
            _logger.warning("Failed to resolve MiniMax API key: %s", e)
            import os
            self._api_key = os.getenv("MINIMAX_API_KEY", "")
        if not self._api_key:
            raise ValueError("MINIMAX_API_KEY not available for embedding")
        return self._api_key
    
    def embed(self, text: str) -> list[float]:
        """Get dense embedding vector from MiniMax API."""
        import urllib.request
        import urllib.error
        
        # Check cache
        cache_key = text[:200]
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return list(self._cache[cache_key])
        
        api_key = self._get_api_key()
        url = f"{self._base_url}/embeddings"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "model": "embo-01",
            "texts": [text],
            "type": "query",
        }).encode("utf-8")
        
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                vec = data["vectors"][0]
                
                # Cache the result
                self._cache[cache_key] = tuple(vec)
                if len(self._cache) > self._cache_max:
                    self._cache.popitem(last=False)
                
                return vec
        except Exception as e:
            _logger.warning("MiniMax embedding failed: %s — falling back to keyword", e)
            raise


# ---------------------------------------------------------------------------
# Semantic Skill Retriever
# ---------------------------------------------------------------------------

class SemanticSkillRetriever:
    """Retrieve the most relevant skills for a task using semantic similarity.
    
    Usage:
        retriever = SemanticSkillRetriever()
        retriever.rebuild_index(skill_registry)
        top_skills = retriever.retrieve("Create a login page with auth", top_k=5)
    """
    
    def __init__(self, backend: str = "auto"):
        """
        Args:
            backend: "auto" (tries MiniMax, falls back to keyword),
                     "keyword", or "minimax"
        """
        self._backend_name = backend
        self._backend = None
        self._index: dict[str, dict] = {}       # skill_name → {vector, entry}
        self._index_version = 0
        self._last_registry_hash = ""
    
    def _init_backend(self):
        """Lazy-init the embedding backend."""
        if self._backend is not None:
            return
        
        if self._backend_name == "keyword":
            self._backend = KeywordEmbeddingBackend()
        elif self._backend_name == "minimax":
            try:
                self._backend = MiniMaxEmbeddingBackend()
            except Exception as e:
                _logger.warning("MiniMax backend unavailable, using keyword fallback: %s", e)
                self._backend = KeywordEmbeddingBackend()
        else:  # "auto"
            try:
                self._backend = MiniMaxEmbeddingBackend()
                _logger.info("SemanticSkillRetriever: using MiniMax embedding backend")
            except Exception as e:
                _logger.info("SemanticSkillRetriever: using Keyword embedding backend (fallback): %s", e)
                self._backend = KeywordEmbeddingBackend()
    
    def _compute_registry_hash(self, registry) -> str:
        """Compute a hash of the skill registry to detect changes."""
        try:
            names = sorted(registry._skills.keys())
            return "|".join(f"{n}:{registry._skills[n].version}" for n in names)
        except Exception as e:
            _logger.warning("Failed to compute registry hash: %s", e)
            return str(time.time())
    
    def rebuild_index(self, skill_registry) -> int:
        """Rebuild the embedding index from the skill registry.
        
        Only indexes APPROVED skills (curated + approved auto-skills).
        Returns the number of skills indexed.
        """
        self._init_backend()
        
        from api.skill_registry import SKILL_APPROVED, SKILL_DRAFT
        
        # Build text corpus for IDF (keyword backend)
        documents = []
        skill_names = []
        skill_entries = []
        
        for name, entry in skill_registry._skills.items():
            # Only index approved or curated skills
            if entry.lifecycle != SKILL_APPROVED and entry.source != "curated":
                continue
            
            # Build a rich text representation for embedding
            # Include all semantic fields for maximum retrieval accuracy
            text_parts = [
                entry.title or name,
                entry.category or "",
                " ".join(entry.tags) if entry.tags else "",
                entry.purpose or "",
                entry.when_to_use or "",
                entry.when_not_to_use or "",
                entry.inputs or "",
                entry.outputs or "",
                entry.examples or "",
                entry.constraints or "",
                entry.success_criteria or "",
                # First 500 chars of body content for semantic context
                entry.content[:500] if entry.content else "",
            ]
            # Filter out empty strings
            text_parts = [p for p in text_parts if p]
            doc_text = " ".join(text_parts)
            
            documents.append(doc_text)
            skill_names.append(name)
            skill_entries.append(entry)
        
        # Build IDF for keyword backend
        if isinstance(self._backend, KeywordEmbeddingBackend):
            self._backend.build_idf(documents)
        
        # Build index
        self._index = {}
        for i, name in enumerate(skill_names):
            vec = self._backend.embed(documents[i])
            self._index[name] = {
                "vector": vec,
                "entry": skill_entries[i],
            }
        
        self._index_version += 1
        _logger.info(
            "SemanticSkillRetriever: indexed %d skills (version %d, backend=%s)",
            len(self._index), self._index_version,
            type(self._backend).__name__
        )
        return len(self._index)
    
    def _cosine_similarity(self, vec1, vec2) -> float:
        """Compute cosine similarity between two vectors.
        
        Supports both dense (list) and sparse (dict) vectors.
        """
        if isinstance(vec1, list) and isinstance(vec2, list):
            # Dense vectors
            dot = sum(a * b for a, b in zip(vec1, vec2))
            norm1 = math.sqrt(sum(a * a for a in vec1))
            norm2 = math.sqrt(sum(b * b for b in vec2))
        elif isinstance(vec1, dict) and isinstance(vec2, dict):
            # Sparse vectors (keyword backend)
            dot = 0.0
            norm1_sq = 0.0
            norm2_sq = 0.0
            # Use the smaller dict for iteration
            if len(vec1) <= len(vec2):
                for k, v1 in vec1.items():
                    v2 = vec2.get(k, 0)
                    dot += v1 * v2
                    norm1_sq += v1 * v1
                for v2 in vec2.values():
                    norm2_sq += v2 * v2
            else:
                for k, v2 in vec2.items():
                    v1 = vec1.get(k, 0)
                    dot += v1 * v2
                    norm2_sq += v2 * v2
                for v1 in vec1.values():
                    norm1_sq += v1 * v1
            norm1 = math.sqrt(norm1_sq)
            norm2 = math.sqrt(norm2_sq)
        else:
            return 0.0
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)
    
    def retrieve(self, task: str, top_k: int = 5, 
                 min_score: float = 0.05) -> list[dict]:
        """Retrieve the top-K most relevant skills for a task.
        
        Args:
            task: The user's task description.
            top_k: Maximum number of skills to return.
            min_score: Minimum similarity score threshold (0.0~1.0).
            
        Returns:
            List of dicts with keys: name, score, entry (SkillEntry)
        """
        self._init_backend()
        
        if not self._index:
            return []
        
        # Embed the task
        task_vec = self._backend.embed(task)
        
        # Score all skills
        scored = []
        for name, data in self._index.items():
            score = self._cosine_similarity(task_vec, data["vector"])
            if score >= min_score:
                scored.append({
                    "name": name,
                    "score": round(score, 4),
                    "entry": data["entry"],
                })
        
        # Sort by score descending, take top_k
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
    
    def retrieve_for_ceo_prompt(self, task: str, top_k: int = 8) -> str:
        """Retrieve skills and format them for inclusion in the CEO planner prompt.
        
        Returns a markdown-formatted string ready for prompt injection.
        """
        results = self.retrieve(task, top_k=top_k)
        
        if not results:
            return (
                "[Semantic Skill Recommendations]\n"
                "(No relevant skills found in the registry. "
                "Use Rule-based selection or general-purpose skills.)\n"
            )
        
        lines = [
            "[Semantic Skill Recommendations — based on task similarity]",
            "The following skills were semantically matched to this task.",
            "You MAY select from these instead of relying solely on hardcoded rules:",
            "",
        ]
        
        for i, r in enumerate(results):
            entry = r["entry"]
            tags_str = ", ".join(entry.tags[:3]) if entry.tags else ""
            conflict_str = ""
            all_conflicts = list(entry.conflicts_with) + list(getattr(entry, 'graph_conflicts', []))
            if all_conflicts:
                conflict_str = f" ⚠️ conflicts with: {', '.join(all_conflicts)}"
            
            # Graph relationship indicators
            graph_info = []
            if getattr(entry, 'graph_requires', None):
                graph_info.append(f"requires: [{', '.join(entry.graph_requires)}]")
            if getattr(entry, 'graph_compatible', None):
                graph_info.append(f"compatible: [{', '.join(entry.graph_compatible[:3])}]")
            
            lines.append(
                f"{i + 1}. **{entry.name}** v{entry.version} "
                f"(score: {r['score']:.3f}, category: {entry.category})"
            )
            lines.append(f"   Title: {entry.title}")
            if entry.purpose:
                lines.append(f"   Purpose: {entry.purpose}")
            if entry.when_to_use:
                lines.append(f"   When to use: {entry.when_to_use}")
            if entry.when_not_to_use:
                lines.append(f"   When NOT to use: {entry.when_not_to_use}")
            if graph_info:
                lines.append(f"   Graph: {' | '.join(graph_info)}")
            if entry.inputs:
                lines.append(f"   Inputs: {entry.inputs}")
            if entry.outputs:
                lines.append(f"   Outputs: {entry.outputs}")
            if entry.examples:
                lines.append(f"   Examples: {entry.examples}")
            if entry.constraints:
                lines.append(f"   Constraints: {entry.constraints}")
            if entry.success_criteria:
                lines.append(f"   Success criteria: {entry.success_criteria}")
            if tags_str:
                lines.append(f"   Tags: [{tags_str}]")
            if conflict_str:
                lines.append(f"  {conflict_str}")
            lines.append("")
        
        # Add guidance for CEO (Retriever ≠ Auto-Select enforcement)
        lines.append(
            "**Selection Guidance**: These are RETRIEVER RECOMMENDATIONS only. "
            "YOU are the CEO. Cross-check similarity scores against Skill History (past success rates) "
            "and Skill Graph (requires/compatible/conflicts) before making your final selection. "
            "You MAY override any recommendation. The highest score does NOT guarantee the best skill."
        )
        
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_retriever: Optional[SemanticSkillRetriever] = None


def get_skill_retriever(backend: str = "auto") -> SemanticSkillRetriever:
    """Get or create the global SemanticSkillRetriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = SemanticSkillRetriever(backend=backend)
    return _retriever
