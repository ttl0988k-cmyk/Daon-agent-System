"""
Dynamic Model Selector — Multi-factor model scoring and selection.

Provides:
- DynamicModelSelector: replaces static role→model mapping with
  cost/context/speed/success-rate weighted scoring.
- ModelProfiles: curated or auto-populated profiles for each model family.
- ModelHistory: multi-dimensional persistent success/failure tracking 
  per (role, language, framework, model_id).

Architecture:
  Task + Role → Context Extraction (lang/framework) → Multi-Dim History → 
  Multi-Factor Scoring → Ranked Model Chain → Best Model + Fallbacks
"""

import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

import threading
_local_state = threading.local()

def set_allowed_providers(providers: Optional[list]):
    _local_state.allowed_providers = providers

def get_allowed_providers() -> Optional[list]:
    return getattr(_local_state, 'allowed_providers', None)

# ---------------------------------------------------------------------------
# Model Profile
# ---------------------------------------------------------------------------

@dataclass
class ModelProfile:
    """Static profile for a model: cost, capabilities, and metadata."""
    model_id: str
    provider: str                       # "minimax", "deepseek", "nvidia"
    display_name: str
    cost_per_1m_input: float            # USD per 1M input tokens
    cost_per_1m_output: float           # USD per 1M output tokens
    context_window: int                 # max tokens
    avg_latency_rank: int              # 1=fastest, 5=slowest (relative)
    strengths: list[str] = field(default_factory=list)  # ["code", "reasoning", "creative", "vision", "fast"]
    max_output_tokens: int = 4096
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    base_json_reliability: float = 0.9
    status: str = "Available"
    status_updated_at: float = 0.0
    # Provider-specific base_url overrides
    base_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "display_name": self.display_name,
            "cost_per_1m_input": self.cost_per_1m_input,
            "cost_per_1m_output": self.cost_per_1m_output,
            "context_window": self.context_window,
            "avg_latency_rank": self.avg_latency_rank,
            "strengths": self.strengths,
            "max_output_tokens": self.max_output_tokens,
            "base_json_reliability": self.base_json_reliability,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Default Model Profiles
# ---------------------------------------------------------------------------

# No hardcoded model profiles. All profiles are loaded dynamically from
# custom_providers.json via model_manager.get_available_models()
# (see DynamicModelSelector._load_custom_profiles).
_DEFAULT_PROFILES: list[ModelProfile] = []


# ---------------------------------------------------------------------------
# Task Context Extraction (language, framework detection)
# ---------------------------------------------------------------------------

# Known languages / frameworks for context extraction
_LANG_PATTERNS = [
    (r'\bpython\b', 'python'),
    (r'\bjavascript\b|\bjs\b', 'javascript'),
    (r'\btypescript\b|\bts\b', 'typescript'),
    (r'\brust\b', 'rust'),
    (r'\bgo\b|\bgolang\b', 'go'),
    (r'\bjava\b(?!script)', 'java'),
    (r'\bc\#|csharp', 'csharp'),
    (r'\bc\+\+|\bcpp\b', 'cpp'),
    (r'\bruby\b', 'ruby'),
    (r'\bphp\b', 'php'),
    (r'\bswift\b', 'swift'),
    (r'\bkotlin\b', 'kotlin'),
    (r'\bhtml\b', 'html'),
    (r'\bcss\b', 'css'),
    (r'\bsql\b', 'sql'),
    (r'\bshell\b|\bbash\b', 'shell'),
]

_FRAMEWORK_PATTERNS = [
    (r'\breact\b(?!-?)', 'react'),
    (r'\bvue\.?js\b|\bvue\b', 'vue'),
    (r'\bangular\b', 'angular'),
    (r'\bnext\.?js\b|\bnext\b', 'nextjs'),
    (r'\bnuxt\.?js\b|\bnuxt\b', 'nuxt'),
    (r'\bflask\b', 'flask'),
    (r'\bfastapi\b', 'fastapi'),
    (r'\bdjango\b', 'django'),
    (r'\bexpress\.?js\b|\bexpress\b', 'express'),
    (r'\bnest\.?js\b|\bnest\b', 'nestjs'),
    (r'\bspring\b', 'spring'),
    (r'\blaravel\b', 'laravel'),
    (r'\brails\b', 'rails'),
    (r'\btailwind\b', 'tailwind'),
    (r'\bbootstrap\b', 'bootstrap'),
    (r'\bpytest\b', 'pytest'),
    (r'\bvitest\b', 'vitest'),
    (r'\bjest\b', 'jest'),
    (r'\bselenium\b', 'selenium'),
    (r'\bdocker\b', 'docker'),
    (r'\bkubernetes\b|\bk8s\b', 'kubernetes'),
    (r'\bterraform\b', 'terraform'),
    (r'\baws\b', 'aws'),
    (r'\bgraphql\b', 'graphql'),
    (r'\brest api\b', 'rest-api'),
    (r'\bfigma\b', 'figma'),
    (r'\blanding[ -]?page\b', 'landing-page'),
    (r'\bdashboard\b', 'dashboard'),
]


def extract_task_context(task: str) -> dict:
    """Extract language and framework context from a task description.
    
    Returns dict with keys: 'languages' (list), 'frameworks' (list)
    """
    task_lower = task.lower()
    
    languages = []
    for pattern, lang in _LANG_PATTERNS:
        if re.search(pattern, task_lower):
            languages.append(lang)
    
    frameworks = []
    for pattern, fw in _FRAMEWORK_PATTERNS:
        if re.search(pattern, task_lower):
            frameworks.append(fw)
    
    return {
        "languages": list(dict.fromkeys(languages)),     # dedup preserve order
        "frameworks": list(dict.fromkeys(frameworks)),
    }


def build_context_keys(task_context: dict) -> list[str]:
    """Build ordered list of context keys for history lookup.
    
    Priority: framework > language > "overall"
    Returns e.g. ["fastapi", "python", "overall"]
    """
    keys = []
    ctx = task_context or {}
    for fw in ctx.get("frameworks", []):
        keys.append(fw)
    for lang in ctx.get("languages", []):
        keys.append(lang)
    keys.append("overall")
    return keys


# ---------------------------------------------------------------------------
# Model History (multi-dimensional persistent tracking)
# ---------------------------------------------------------------------------

class ModelHistory:
    """Multi-dimensional success/failure tracker per (role, language/framework, model).
    
    Key structure:
      {
        "developer": {
          "deepseek-chat": {
            "overall":    { "success": 91, "fail": 3, "total_latency_ms": 0, "count": 0 },
            "python":     { "success": 98, "fail": 1, ... },
            "fastapi":    { "success": 95, "fail": 3, ... },
            "react":      { "success": 72, "fail": 15, ... }
          }
        }
      }
    
    Data is stored in ~/.hermes/model_history.json.
    """
    
    def __init__(self):
        self._path = Path.home() / ".hermes" / "model_history.json"
        self._data: dict = self._load()
    
    def _load(self) -> dict:
        """Load history from disk."""
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.warning("Failed to load model history: %s", e)
        return {}
    
    def _save(self):
        """Save history to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _logger.warning("Failed to save model history: %s", e)
    
    def _ensure_path(self, role: str, model_id: str, context_key: str = "overall"):
        """Ensure the nested path exists and return the stats dict."""
        role_dict = self._data.setdefault(role, {})
        model_dict = role_dict.setdefault(model_id, {})
        ctx_dict = model_dict.setdefault(context_key, {
            "success": 0, "fail": 0, "total_latency_ms": 0, "count": 0,
            "total_retries": 0,
        })
        return ctx_dict
    
    def get_stats(self, role: str, model_id: str, context_key: str = "overall") -> dict:
        """Get success/failure stats for a (role, model, context) pair."""
        role_dict = self._data.get(role, {})
        model_dict = role_dict.get(model_id, {})
        return model_dict.get(context_key, {
            "success": 0, "fail": 0, "total_latency_ms": 0, "count": 0,
            "total_retries": 0,
        })
    
    def get_best_context_stats(self, role: str, model_id: str, context_keys: list[str]) -> dict:
        """Get the most specific available stats for given context keys.
        
        Searches in order: context_keys[0] → context_keys[1] → ... → "overall"
        Returns the first non-empty stats dict found.
        """
        for key in context_keys:
            stats = self.get_stats(role, model_id, key)
            if stats.get("count", 0) > 0:
                return {"context_key": key, **stats}
        # Nothing found — return empty overall
        return {"context_key": "overall", "success": 0, "fail": 0,
                "total_latency_ms": 0, "count": 0, "total_retries": 0}
    
    def record_success(self, role: str, model_id: str, latency_ms: float = 0,
                       context_keys: list[str] = None, retries: int = 0):
        """Record a successful execution into ALL matching context slots."""
        ctx_keys = context_keys or ["overall"]
        for key in ctx_keys:
            entry = self._ensure_path(role, model_id, key)
            entry["success"] += 1
            entry["total_latency_ms"] += latency_ms
            entry["count"] += 1
            entry["total_retries"] = entry.get("total_retries", 0) + max(0, int(retries))
        # Also record to "overall"
        if "overall" not in ctx_keys:
            entry = self._ensure_path(role, model_id, "overall")
            entry["success"] += 1
            entry["total_latency_ms"] += latency_ms
            entry["count"] += 1
            entry["total_retries"] = entry.get("total_retries", 0) + max(0, int(retries))
        self._save()
    
    def record_failure(self, role: str, model_id: str, latency_ms: float = 0,
                       context_keys: list[str] = None, error_type: str = "Unknown",
                       retries: int = 0):
        """Record a failed execution into ALL matching context slots."""
        ctx_keys = context_keys or ["overall"]
        for key in ctx_keys:
            entry = self._ensure_path(role, model_id, key)
            entry["fail"] += 1
            entry["total_latency_ms"] += latency_ms
            entry["count"] += 1
            entry["total_retries"] = entry.get("total_retries", 0) + max(0, int(retries))
        if "overall" not in ctx_keys:
            entry = self._ensure_path(role, model_id, "overall")
            entry["fail"] += 1
            entry["total_latency_ms"] += latency_ms
            entry["count"] += 1
            entry["total_retries"] = entry.get("total_retries", 0) + max(0, int(retries))
        self._save()
    
    def get_avg_retries(self, role: str, model_id: str,
                        context_keys: list[str] = None) -> float:
        """Get context-aware average retries per execution. 0.0 for unknown."""
        ctx_keys = context_keys or ["overall"]
        best = self.get_best_context_stats(role, model_id, ctx_keys)
        count = best.get("count", 0)
        if count == 0:
            if best.get("context_key", "") != "overall":
                overall = self.get_stats(role, model_id, "overall")
                if overall.get("count", 0) > 0:
                    return overall.get("total_retries", 0) / overall["count"]
            return 0.0
        return best.get("total_retries", 0) / count
    
    def get_success_rate(self, role: str, model_id: str,
                         context_keys: list[str] = None) -> float:
        """Get context-aware success rate as 0.0-1.0.
        
        Searches context_keys in priority order. Falls back to "overall".
        Returns 0.8 for completely unknown (optimistic prior).
        """
        ctx_keys = context_keys or ["overall"]
        best = self.get_best_context_stats(role, model_id, ctx_keys)
        total = best.get("success", 0) + best.get("fail", 0)
        if total == 0:
            # If specific context had 0 data, try overall
            if best.get("context_key", "") != "overall":
                overall = self.get_stats(role, model_id, "overall")
                total_ov = overall.get("success", 0) + overall.get("fail", 0)
                if total_ov > 0:
                    return overall["success"] / total_ov
            return 0.8  # Optimistic prior for untested
        return best["success"] / total
    
    def get_avg_latency(self, role: str, model_id: str,
                        context_keys: list[str] = None) -> float:
        """Get context-aware average latency in ms. Returns 2000 (default) for unknown."""
        ctx_keys = context_keys or ["overall"]
        best = self.get_best_context_stats(role, model_id, ctx_keys)
        count = best.get("count", 0)
        if count == 0:
            if best.get("context_key", "") != "overall":
                overall = self.get_stats(role, model_id, "overall")
                if overall.get("count", 0) > 0:
                    return overall["total_latency_ms"] / overall["count"]
            return 2000.0
        return best["total_latency_ms"] / count
    
    def get_all_role_model_stats(self, role: str, model_id: str) -> dict:
        """Get all context stats for a (role, model) pair (for debugging/display)."""
        role_dict = self._data.get(role, {})
        model_dict = role_dict.get(model_id, {})
        result = {}
        for ctx_key, stats in model_dict.items():
            total = stats.get("success", 0) + stats.get("fail", 0)
            result[ctx_key] = {
                **stats,
                "success_rate": stats["success"] / total if total > 0 else None,
            }
        return result


# ---------------------------------------------------------------------------
# Skill History (context-aware skill success tracking)
# ---------------------------------------------------------------------------

class SkillHistory:
    """Persistent per-skill, per-context success/failure tracker.
    
    Key structure:
      {
        "bill-dev": {
          "overall":    { "uses": 300, "success": 280, "fail": 20 },
          "python":     { "uses": 120, "success": 118, "fail": 2 },
          "fastapi":    { "uses": 88, "success": 71, "fail": 17 },
          "react":      { "uses": 52, "success": 51, "fail": 1 },
          "rust":       { "uses": 14, "success": 6, "fail": 8 }
        }
      }
    
    Data is stored in ~/.hermes/skill_history.json.
    """
    
    def __init__(self):
        self._path = Path.home() / ".hermes" / "skill_history.json"
        self._data: dict = self._load()
    
    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                _logger.warning("Failed to load skill history: %s", e)
        return {}
    
    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _logger.warning("Failed to save skill history: %s", e)
    
    def _ensure_path(self, skill_name: str, context_key: str = "overall"):
        skill_dict = self._data.setdefault(skill_name, {})
        return skill_dict.setdefault(context_key, {"uses": 0, "success": 0, "fail": 0})
    
    def record_use(self, skill_name: str, success: bool,
                   context_keys: list[str] = None):
        """Record a skill usage result."""
        ctx_keys = context_keys or ["overall"]
        for key in ctx_keys:
            entry = self._ensure_path(skill_name, key)
            entry["uses"] += 1
            if success:
                entry["success"] += 1
            else:
                entry["fail"] += 1
        if "overall" not in ctx_keys:
            entry = self._ensure_path(skill_name, "overall")
            entry["uses"] += 1
            if success:
                entry["success"] += 1
            else:
                entry["fail"] += 1
        self._save()
    
    def get_stats(self, skill_name: str, context_key: str = "overall") -> dict:
        """Get stats for a skill in a specific context."""
        skill_dict = self._data.get(skill_name, {})
        return skill_dict.get(context_key, {"uses": 0, "success": 0, "fail": 0})
    
    def get_best_context_stats(self, skill_name: str, context_keys: list[str]) -> dict:
        """Get the most specific available stats."""
        for key in context_keys:
            stats = self.get_stats(skill_name, key)
            if stats.get("uses", 0) > 0:
                return {"context_key": key, **stats}
        return {"context_key": "overall", "uses": 0, "success": 0, "fail": 0}
    
    def get_success_rate(self, skill_name: str, context_keys: list[str] = None) -> Optional[float]:
        """Get context-aware success rate. Returns None if no data."""
        ctx_keys = context_keys or ["overall"]
        best = self.get_best_context_stats(skill_name, ctx_keys)
        uses = best.get("uses", 0)
        if uses == 0:
            if best.get("context_key", "") != "overall":
                overall = self.get_stats(skill_name, "overall")
                if overall.get("uses", 0) > 0:
                    return overall["success"] / overall["uses"]
            return None
        return best["success"] / uses
    
    def format_for_ceo(self, skill_name: str, context_keys: list[str]) -> str:
        """Format skill history as a single-line annotation for the CEO prompt."""
        rate = self.get_success_rate(skill_name, context_keys)
        if rate is None:
            return "📊 history: no data yet"
        
        best = self.get_best_context_stats(skill_name, context_keys)
        ctx_key = best.get("context_key", "overall")
        uses = best.get("uses", 0)
        
        if rate >= 0.90:
            emoji = "✅"
        elif rate >= 0.70:
            emoji = "⚠️"
        else:
            emoji = "❌"
        
        if ctx_key != "overall":
            return f"{emoji} {ctx_key}: {rate:.0%} ({uses} uses)"
        else:
            return f"{emoji} overall: {rate:.0%} ({uses} uses)"


# ---------------------------------------------------------------------------
# Dynamic Model Selector
# ---------------------------------------------------------------------------

class DynamicModelSelector:
    """Select the best model for a task using multi-factor scoring.
    
    Scoring factors:
        1. Historical success rate (weight: 40%)  — context-aware (lang/framework)
        2. Strength match (weight: 25%)            — does the model's strengths match the task?
        3. Cost efficiency (weight: 20%)           — lower cost = better (up to budget cap)
        4. Context fit (weight: 15%)               — does the context window fit the required tokens?
    
    Usage:
        selector = DynamicModelSelector()
        chain = selector.select_for_node(
            role="developer", task="Build a FastAPI REST API",
            required_context=32000, max_budget=0.05,
            required_strength="code"
        )
    """
    
    def __init__(self, profiles: list[ModelProfile] = None):
        self._profiles: dict[str, ModelProfile] = {}
        self._history = ModelHistory()
        # Phase 2: in-memory selection log (ring buffer) so the CEO can
        # observe what the selector chose and why, then tune blend weights.
        self._selection_log: list[dict] = []
        self._selection_log_max = 200
        
        for p in (profiles or _DEFAULT_PROFILES):
            self._profiles[p.model_id] = p
        
        # Load custom provider profiles dynamically
        self._load_custom_profiles()
    
    def _load_custom_profiles(self):
        """Load ModelProfiles from the SAME source the chat/debate UI uses.

        model_manager.get_available_models() is profile-aware (active profile
        auth.json merged with default ~/.hermes) and only returns providers
        that actually have an API key. Building profiles from this list keeps
        the harness model selector 100% in sync with the chat and debate model
        dropdowns — no separate/divergent key detection that silently drops
        user-registered providers.
        """
        try:
            from api.managers.model_manager import model_manager
            groups = model_manager.get_available_models()

            # Helper: infer strengths from model_id name
            def _infer_strengths(mid: str) -> list[str]:
                mid_lower = mid.lower()
                strengths = []
                if any(k in mid_lower for k in ('code', 'coder', 'dev')):
                    strengths.append('code')
                if any(k in mid_lower for k in ('reason', 'think', 'r1', 'o1', 'o3')):
                    strengths.append('reasoning')
                if any(k in mid_lower for k in ('flash', 'fast', 'lite', 'mini', 'free')):
                    strengths.append('fast')
                if any(k in mid_lower for k in ('pro', 'ultra', 'max', 'sonnet', 'opus')):
                    strengths.extend(['code', 'reasoning'])
                if any(k in mid_lower for k in ('creative', 'art', 'image', 'vision', 'vl')):
                    strengths.append('creative')
                if not strengths:
                    strengths = ['code', 'reasoning']
                return list(dict.fromkeys(strengths))  # dedupe preserving order

            # Helper: infer latency rank from model name (1=fastest, 5=slowest)
            def _infer_latency_rank(mid: str) -> int:
                mid_lower = mid.lower()
                if any(k in mid_lower for k in ('flash', 'fast', 'lite', 'mini', 'free', 'turbo')):
                    return 1
                if any(k in mid_lower for k in ('reason', 'think', 'r1', 'o1', 'o3')):
                    return 5
                if any(k in mid_lower for k in ('pro', 'ultra', 'max')):
                    return 3
                return 2

            _loaded = []
            for g in groups:
                provider_key = g.get('provider_key') or g.get('provider')
                if not provider_key:
                    continue
                # Custom providers carry base_url; presets resolve via model_manager.
                base_url = g.get('base_url') or ''
                if not base_url:
                    try:
                        base_url = model_manager._get_base_url(provider_key) or ''
                    except Exception:
                        base_url = ''
                _count = 0
                for m in g.get('models', []):
                    model_id = m['id'] if isinstance(m, dict) else str(m)
                    if not model_id or model_id in self._profiles:
                        continue
                    self._profiles[model_id] = ModelProfile(
                        model_id=model_id,
                        provider=provider_key,
                        display_name=m.get('label', model_id) if isinstance(m, dict) else model_id,
                        cost_per_1m_input=0.30,
                        cost_per_1m_output=1.20,
                        context_window=128000,
                        avg_latency_rank=_infer_latency_rank(model_id),
                        strengths=_infer_strengths(model_id),
                        max_output_tokens=8192,
                        base_url=base_url,
                    )
                    _count += 1
                if _count:
                    _loaded.append(f"{provider_key}({_count})")

            _logger.info(
                "Loaded %d model profiles (hardcoded: %d, dynamic: %d) | unified-source providers: %s",
                len(self._profiles), len(_DEFAULT_PROFILES),
                len(self._profiles) - len(_DEFAULT_PROFILES),
                ', '.join(_loaded) or 'none',
            )
        except Exception as e:
            _logger.warning("Failed to load custom provider profiles: %s", e)
    
    def refresh_profiles(self):
        """Reload custom profiles (called after user adds/removes a provider)."""
        # Remove previously loaded custom profiles (keep hardcoded ones)
        default_ids = {p.model_id for p in _DEFAULT_PROFILES}
        keys_to_remove = [k for k in self._profiles if k not in default_ids]
        for k in keys_to_remove:
            del self._profiles[k]
        self._load_custom_profiles()
    
    # ── Profile Management ──
    
    def add_profile(self, profile: ModelProfile):
        self._profiles[profile.model_id] = profile
    
    def get_profile(self, model_id: str) -> Optional[ModelProfile]:
        return self._profiles.get(model_id)
    
    def list_profiles(self) -> list[ModelProfile]:
        return list(self._profiles.values())
    
    # ── Context Extraction (public for external use) ──
    
    @staticmethod
    def extract_context(task: str) -> dict:
        """Extract language/framework context from a task description."""
        return extract_task_context(task)
    
    # ── Multi-Factor Scoring ──
    
    def _score_model(
        self,
        profile: ModelProfile,
        role: str,
        required_context: int = 32000,
        max_budget: float = 0.05,
        required_strength: str = "code",
        max_latency_ms: float = 30000,
        context_keys: list[str] = None,
        daon_stats: dict = None,
    ) -> tuple[float, dict]:
        """Compute an 8-dimensional weighted score for a model."""
        import time
        scores = {}
        
        # New V2 weights
        weights = {
            "strength": 0.30,         # Task/Role Fit
            "success_rate": 0.20,     # Context-specific Success
            "cost": 0.10,             # Cost Efficiency
            "latency": 0.10,          # Response Latency
            "context": 0.10,          # Context Window Fit
            "reliability": 0.10,      # JSON/Tool Reliability
            "status": 0.05,           # API Health / Availability
            "load": 0.05,             # System Load Balancing
        }
        ctx_keys = context_keys or ["overall"]
        
        # 1. Strength (Task/Role Fit)
        # Model Intelligence first: when a blended FINAL capability score
        # (public benchmarks + DAON field evidence, weighted by the Evidence
        # layer) exists for this model+capability, it replaces the binary
        # name-match so real capability gaps (e.g. 3.8-Max vs 3.7) become
        # visible. Falls back to legacy name inference when no intel applies.
        _intel_score = None
        _intel_ev = {}
        try:
            from api.dynamic.model_intel import get_model_intel
            _intel_score, _intel_ev = get_model_intel().get_final_capability(
                profile.model_id, required_strength, daon_stats=daon_stats
            )
        except Exception:
            _intel_score, _intel_ev = None, {}
        scores["_intel"] = _intel_ev or {}
        if _intel_score is not None:
            scores["strength"] = _intel_score
        elif required_strength in profile.strengths:
            scores["strength"] = 1.0
        else:
            related = {
                "code": ["code", "reasoning"],
                "reasoning": ["reasoning", "code", "math", "logic"],
                "creative": ["creative", "reasoning"],
                "fast": ["fast"],
                "review": ["reasoning", "code", "qa"],
                "qa": ["reasoning", "code", "qa"],
                "design": ["creative", "reasoning"],
                "debug": ["reasoning", "code", "debug"],
            }
            matching = set(related.get(required_strength, [required_strength]))
            overlap = len(matching & set(profile.strengths))
            scores["strength"] = min(1.0, overlap / max(1, len(matching)) * 1.2)
            
        # 2. Historical Success Rate
        scores["success_rate"] = self._history.get_success_rate(
            role, profile.model_id, ctx_keys
        )
        
        # 3. Cost Efficiency
        if max_budget <= 0:
            scores["cost"] = 1.0 if profile.cost_per_1m_input <= 0 else 0.3
        else:
            cost_ratio = profile.cost_per_1m_input / max_budget
            if cost_ratio <= 0:
                scores["cost"] = 1.0
            elif cost_ratio >= 1.0:
                scores["cost"] = 0.1
            else:
                scores["cost"] = 1.0 - cost_ratio
                
        # 4. Latency
        avg_lat = self._history.get_avg_latency(role, profile.model_id, ctx_keys)
        # Convert avg_lat (ms) to a score where < 2000 is 1.0, and > max is 0.1
        if avg_lat <= 2000:
            scores["latency"] = 1.0
        elif avg_lat >= max_latency_ms:
            scores["latency"] = 0.1
        else:
            scores["latency"] = 1.0 - ((avg_lat - 2000) / max(1, max_latency_ms - 2000)) * 0.9
            
        # Add latency rank bias
        if profile.avg_latency_rank <= 2:
            scores["latency"] = min(1.0, scores["latency"] + 0.2)
            
        # 5. Context Window Fit
        if profile.context_window >= required_context * 2:
            scores["context"] = 1.0
        elif profile.context_window >= required_context:
            ratio = required_context / profile.context_window
            scores["context"] = 1.0 - (ratio - 0.5) * 0.5
        else:
            scores["context"] = 0.0
            
        # 6. JSON Reliability
        # Use base reliability, adjusted by history if available
        scores["reliability"] = profile.base_json_reliability
        # Phase 2: retry evidence — models that need many retries to succeed
        # are less reliable. avg_retries 0 = no penalty; >= 3 = heavy penalty.
        _avg_retries = self._history.get_avg_retries(role, profile.model_id, ctx_keys)
        if _avg_retries > 0:
            _retry_penalty = min(0.5, _avg_retries / 6.0)
            scores["reliability"] = max(0.0, scores["reliability"] - _retry_penalty)
        scores["_avg_retries"] = round(_avg_retries, 2)
        
        # 7. Status / API Health
        now = time.time()
        if profile.status == "Available":
            scores["status"] = 1.0
        elif profile.status == "RateLimited":
            # Penalize for 5 minutes
            if now - profile.status_updated_at < 300:
                scores["status"] = 0.0
            else:
                profile.status = "Available"
                scores["status"] = 1.0
        else:
            scores["status"] = 0.1
            
        # 8. Load Balancing (Simplified: default to 1.0, could track active in-flight requests)
        scores["load"] = 1.0 
        
        total = sum(scores[k] * weights[k] for k in weights)
        
        # Hard fail conditions
        if scores["status"] == 0.0:
            total = 0.0
            
        return max(0.0, min(1.0, total)), scores
    
    def select_for_node(
        self,
        role: str,
        task: str = "",
        preferred_model: str = None,
        required_context: int = 32000,
        max_budget: float = 0.05,
        required_strength: str = "code",
        max_latency_ms: float = 30000,
        top_k: int = 5,
    ) -> tuple[list[dict], dict]:
        """Select the best model(s) for a DAG node.
        
        Returns:
            (model_chain, context_info) where context_info contains
            the extracted languages/frameworks for downstream use.
        """
        # Extract task context
        task_context = extract_task_context(task) if task else {}
        context_keys = build_context_keys(task_context)
        
        # Score all eligible models
        scored = []
        allowed = get_allowed_providers()
        # Collect known providers dynamically from the single source of truth
        # (custom_providers.json via model_manager) — no hardcoded provider list.
        # 모델이 0개인 프로바이더(빈 preset, 차단된 프로바이더)는 "존재"로
        # 인정하지 않는다. 그렇지 않으면 싱글턴 캐시에 남은 옛 프로필이
        # 체인에 들어가 무단 과금 폴백이 발생한다.
        _known_providers = set()
        _registered_models: dict[str, set[str]] = {}
        try:
            from api.managers.model_manager import model_manager as _mm
            for _g in _mm.get_available_models():
                _pk = _g.get('provider_key') or _g.get('provider')
                _mids = set()
                for _m in _g.get('models', []):
                    _mid = _m.get('id') if isinstance(_m, dict) else str(_m)
                    if _mid:
                        _mids.add(_mid)
                if not _mids:
                    continue
                if _pk:
                    _known_providers.add(_pk)
                    _registered_models.setdefault(_pk, set()).update(_mids)
        except Exception:
            # Fallback: trust the providers of already-loaded profiles
            _known_providers = {p.provider for p in self._profiles.values() if p.provider}
            _registered_models = {}

        # Model Intelligence: aggregate DAON field stats ONCE per selection
        # round (pure in-memory arithmetic over ModelHistory) and reuse the
        # view for every candidate. Failure only disables the intel layer.
        _daon_stats = {}
        try:
            from api.dynamic.model_intel import ModelIntel
            _daon_stats = ModelIntel.daon_stats_from_history(self._history)
        except Exception:
            _daon_stats = {}

        for model_id, profile in self._profiles.items():
            if allowed is not None:
                if profile.provider not in allowed:
                    continue
            else:
                if profile.provider not in _known_providers:
                    continue
            # 서버 기동 후 프로바이더가 삭제/차단됐는데 싱글턴 캐시에 남은
            # 옛 프로필이 과금 체인에 들어가는 것을 막는다: 현재
            # custom_providers.json 에 실제로 등록된 모델만 허용.
            if _registered_models and profile.provider in _registered_models:
                if model_id not in _registered_models[profile.provider]:
                    continue
            
            score, breakdown = self._score_model(
                profile=profile,
                role=role,
                required_context=required_context,
                max_budget=max_budget,
                required_strength=required_strength,
                max_latency_ms=max_latency_ms,
                context_keys=context_keys,
                daon_stats=_daon_stats,
            )
            
            if preferred_model and model_id == preferred_model:
                score = min(1.0, score + 0.15)
            
            scored.append((score, profile, breakdown))
        
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # Build fallback chain
        chain = []
        for score, profile, breakdown in scored[:top_k]:
            api_key = self._resolve_api_key(profile.provider)
            base_url = profile.base_url
            
            chain.append({
                "model": profile.model_id,
                "provider": profile.provider,
                "api_key": api_key,
                "base_url": base_url,
                "_selector_score": score,
                "_breakdown": breakdown,
                "_cost": profile.cost_per_1m_input
            })
        
        # Safety net: ONLY when the scored chain is empty (no eligible model
        # matched). Picks the first available profile dynamically — no
        # hardcoded model fallback. 단, 현재 custom_providers.json 에 실제로
        # 등록된 모델만 허용한다(싱글턴 캐시의 옛 프로필 부활 방지).
        if not chain and self._profiles:
            first_profile = None
            for _p in self._profiles.values():
                if _registered_models:
                    _reg = _registered_models.get(_p.provider)
                    if not _reg or _p.model_id not in _reg:
                        continue
                first_profile = _p
                break
            if first_profile is not None:
                chain.append({
                    "model": first_profile.model_id,
                    "provider": first_profile.provider,
                    "api_key": self._resolve_api_key(first_profile.provider),
                    "base_url": first_profile.base_url,
                    "_selector_score": 0.0,
                    "_breakdown": {},
                    "_cost": first_profile.cost_per_1m_input
                })
        
        context_info = {
            "languages": task_context.get("languages", []),
            "frameworks": task_context.get("frameworks", []),
            "context_keys": context_keys,
        }
        
        # Phase 2: record the selection (chain + per-model evidence) into the
        # in-memory ring buffer so the CEO can observe and tune blend weights.
        try:
            self._selection_log.append({
                "ts": time.time(),
                "role": role,
                "required_strength": required_strength,
                "context_keys": list(context_keys),
                "chain": [
                    {
                        "model": c.get("model"),
                        "provider": c.get("provider"),
                        "score": round(float(c.get("_selector_score", 0.0)), 4),
                        "breakdown": c.get("_breakdown") or {},
                    }
                    for c in chain
                ],
            })
            if len(self._selection_log) > self._selection_log_max:
                self._selection_log = self._selection_log[-self._selection_log_max:]
        except Exception:
            pass  # logging must never break selection
        
        return chain, context_info
    
    def get_selection_log(self, limit: int = 50) -> list[dict]:
        """Return the most recent selection log entries (newest first)."""
        return list(reversed(self._selection_log[-limit:]))
    
    def _resolve_api_key(self, provider: str) -> str:
        """Resolve API key for ANY provider using the unified auth resolution:
        custom_providers.json → environment variable → auth.json credential pool.
        Uses api.dynamic.auth._resolve_key_from_pool() which is profile-aware
        (respects get_active_hermes_home() for multi-profile setups).
        """
        try:
            from api.dynamic.auth import _resolve_key_from_pool
            key = _resolve_key_from_pool(provider)
            if key:
                return key
        except Exception as e:
            _logger.warning("Failed to resolve API key for %s via auth pool: %s", provider, e)

        return ""
    
    # ── Role → Required Strength Mapping ──
    
    @staticmethod
    def infer_strength_from_role(role: str, node_type: str = "llm") -> str:
        role_lower = role.lower()
        
        if any(k in role_lower for k in ("design", "ui", "ux", "prada", "front")):
            return "creative"
        if any(k in role_lower for k in ("review", "qa", "sherlock", "audit", "test")):
            return "qa"
        if any(k in role_lower for k in ("debug", "fix", "troubleshoot")):
            return "debug"
        if any(k in role_lower for k in ("plan", "architect", "tony", "ceo")):
            return "reasoning"
        if "terminal" in node_type:
            return "code"
        return "code"
    
    @staticmethod
    def estimate_context_tokens(task: str, role: str = "developer") -> int:
        task_len = len(task)
        role_lower = role.lower()
        
        if any(k in role_lower for k in ("review", "qa", "sherlock")):
            base = 16000
        elif any(k in role_lower for k in ("merge", "plan", "architect")):
            base = 32000
        else:
            base = 16000
        
        if task_len > 2000:
            base += 8000
        if task_len > 5000:
            base += 8000
        
        return min(base, 128000)
    
    # ── History Recording (with context extraction) ──
    
    def record_result(self, role: str, model_id: str,
                      success: bool, latency_ms: float = 0,
                      task: str = "", retries: int = 0):
        """Record an execution result with auto-extracted context."""
        task_context = extract_task_context(task) if task else {}
        context_keys = build_context_keys(task_context)
        
        if success:
            self._history.record_success(role, model_id, latency_ms, context_keys,
                                         retries=retries)
        else:
            self._history.record_failure(role, model_id, latency_ms, context_keys,
                                         retries=retries)
    
    @property
    def history(self) -> ModelHistory:
        """Access the underlying ModelHistory for debugging/display."""
        return self._history


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_selector: Optional[DynamicModelSelector] = None
_skill_history: Optional[SkillHistory] = None


def get_model_selector() -> DynamicModelSelector:
    """Get or create the global DynamicModelSelector singleton."""
    global _selector
    if _selector is None:
        _selector = DynamicModelSelector()
        _logger.info(
            "DynamicModelSelector: initialized with %d model profiles",
            len(_selector.list_profiles())
        )
    return _selector


def get_skill_history() -> SkillHistory:
    """Get or create the global SkillHistory singleton."""
    global _skill_history
    if _skill_history is None:
        _skill_history = SkillHistory()
    return _skill_history
