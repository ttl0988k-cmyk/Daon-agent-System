"""
Model Intelligence DB — public benchmark + DAON field evidence layer.

Agreed 5-layer schema per model:

  IDENTITY   provider / family / generation / tier / parent (lineage)
  PUBLIC     curated benchmark scores + source + as_of date (mandatory)
  DAON       field stats aggregated LIVE from ModelHistory (view, not stored)
  EVIDENCE   w_pub / w_daon confidence weights (computed per query)
  FINAL      per-capability 0..1 blended score + flags

Blend rule (Bayesian, prevents 1-2 samples flipping benchmarks):

  w_daon     = n / (n + K_DAON)
  daon_score = (success_rate * n + K_DAON * public_prior) / (n + K_DAON)
               (shrinkage toward the public prior)
  w_pub      = source_confidence * freshness_decay(as_of)
  FINAL      = (w_pub * public + w_daon * daon_score) / (w_pub + w_daon)

Lineage rule — inherit experience context, NEVER inherit performance:
  A new model_id always starts with empty DAON stats (ModelHistory is keyed
  by model_id). Only when BOTH public and DAON data are missing may the
  model borrow `parent FINAL * LINEAGE_DISCOUNT`, flagged lineage_estimate.
  Lineage is never written into ModelHistory.

Storage:
  Seed (repo):   api/api/dynamic/model_intel_seed.json
  Runtime DB:    ~/.hermes/model_intel.json  (entries override the seed)

Performance: pure in-memory dict arithmetic, no network / disk / LLM calls
at query time (files load once per process). Phase 2 TODO: avg_retries
recording (needs a hook in runner._run_node_with_retries).
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# DAON sample count at which field evidence reaches 50% weight: n / (n + K).
K_DAON = 15

# Discount applied when a model borrows its parent's FINAL as a prior.
LINEAGE_DISCOUNT = 0.5

# Public benchmark freshness: full weight within this many days of as_of,
# then linear decay down to FRESHNESS_FLOOR.
FRESHNESS_FULL_DAYS = 180
FRESHNESS_FLOOR = 0.4

# Harness sub-agents are tool-using agents: blend the mapped capability with
# the agentic axis when both are available.
AGENTIC_BLEND = 0.3

# Undated public source: usable but discounted.
_UNDATED_FRESHNESS = 0.7

# Capability axes shown to the CEO.
CAPABILITIES = ["coding", "reasoning", "debugging", "agentic", "tool_use", "context"]

# Benchmark name (lowercased) -> capability axes it evidences.
BENCHMARK_CAPABILITY_MAP = {
    "swe-bench": ["coding", "agentic"],
    "swe-bench verified": ["coding", "agentic"],
    "swe-bench pro": ["coding", "debugging"],
    "terminal-bench": ["agentic", "tool_use"],
    "livecodebench": ["coding"],
    "gpqa": ["reasoning"],
    "mmlu": ["reasoning"],
    "aime": ["reasoning"],
    "tool use": ["tool_use", "agentic"],
    "tau-bench": ["tool_use", "agentic"],
    "cybersecurity": ["reasoning", "debugging"],
    "long context": ["context"],
}

# Selector strength keyword -> primary capability axis. Strengths without a
# mapping (fast) keep the legacy binary behaviour.
# creative/design -> coding: 디자이너 노드도 프론트엔드 '코드'를 생산하며,
# ROLE_CAPABILITY_MAP["designer"]="coding" 과 정렬시켜야 DAON 필드 증거
# (designer 역할의 실행 결과)가 creative 조회에 반영된다. 이 매핑이 없으면
# Intel 블렌딩이 아예 작동하지 않아 이름 매칭 점수로 만능화된다 — "더 나은
# 모델이 있는데도 하위 모델이 코딩/디자인을 맡는" 문제(2026-08-24 대표님
# 지적)의 원인 중 하나였다.
STRENGTH_CAPABILITY_MAP = {
    "code": "coding",
    "reasoning": "reasoning",
    "debug": "debugging",
    "qa": "debugging",
    "review": "debugging",
    "creative": "coding",
    "design": "coding",
}

# Harness role -> capability axis (for DAON field aggregation).
ROLE_CAPABILITY_MAP = {
    "developer": "coding",
    "debugger": "debugging",
    "reviewer": "debugging",
    "planner": "reasoning",
    "designer": "coding",
}

_SEED_PATH = Path(__file__).resolve().parent / "model_intel_seed.json"
_DB_PATH = Path.home() / ".hermes" / "model_intel.json"


def _normalize_id(model_id: str) -> str:
    """Case/punctuation-insensitive key: 'Qwen3.8-Max' -> 'qwen38max'."""
    return "".join(ch for ch in (model_id or "").lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# ModelIntel
# ---------------------------------------------------------------------------

class ModelIntel:
    """Loads the intelligence DB and answers capability queries.

    All query methods are pure memory arithmetic; files are read once in
    __init__ (call refresh() after editing the JSON on disk).
    """

    def __init__(self):
        self._entries: dict = {}  # normalized model id -> entry dict
        self._load()

    # ── Persistence ──

    def _load(self):
        merged: dict = {}
        # Seed first, runtime DB second so runtime entries override the seed.
        for path in (_SEED_PATH, _DB_PATH):
            try:
                if not path.exists():
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
                models = raw.get("models", {}) if isinstance(raw, dict) else {}
                if not isinstance(models, dict):
                    continue
                for mid, entry in models.items():
                    if not mid or not isinstance(entry, dict):
                        continue
                    merged[_normalize_id(mid)] = entry
            except Exception as e:
                _logger.warning("model_intel: failed to load %s: %s", path, e)
        self._entries = merged

    def refresh(self):
        """Reload both files (after the CEO/user edits the DB on disk)."""
        self._load()

    def has_entry(self, model_id: str) -> bool:
        return _normalize_id(model_id) in self._entries

    # ── PUBLIC layer: benchmarks -> capabilities ──

    @staticmethod
    def _normalize_score(value) -> Optional[float]:
        """Accept 0..1 or 0..100 scales; return clamped 0..1 or None."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))

    def _public_capabilities(self, entry: dict) -> dict:
        """Map benchmark scores onto capability axes (average per axis).

        Explicitly curated `public.capabilities` values override computed
        ones so the CEO can hand-tune an axis when benchmarks disagree.
        """
        pub = entry.get("public") or {}
        benchmarks = pub.get("benchmarks") or {}
        explicit = pub.get("capabilities") or {}

        acc = {cap: [] for cap in CAPABILITIES}
        for name, bm in benchmarks.items():
            raw_score = bm.get("score") if isinstance(bm, dict) else bm
            score = self._normalize_score(raw_score)
            if score is None:
                continue
            for cap in BENCHMARK_CAPABILITY_MAP.get(str(name).lower(), []):
                acc[cap].append(score)

        result = {}
        for cap in CAPABILITIES:
            if cap in explicit:
                v = self._normalize_score(explicit[cap])
                if v is not None:
                    result[cap] = v
                    continue
            if acc[cap]:
                result[cap] = sum(acc[cap]) / len(acc[cap])
        return result

    # ── Freshness decay ──

    @staticmethod
    def _freshness(as_of) -> float:
        if not as_of:
            return _UNDATED_FRESHNESS
        s = str(as_of)
        parsed = None
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                parsed = datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s[:7], fmt).date()
                break
            except Exception:
                continue
        if parsed is None:
            return _UNDATED_FRESHNESS
        days = (date.today() - parsed).days
        if days <= FRESHNESS_FULL_DAYS:
            return 1.0
        decay = 1.0 - ((days - FRESHNESS_FULL_DAYS) / 365.0) * (1.0 - FRESHNESS_FLOOR)
        return max(FRESHNESS_FLOOR, decay)

    def _entry_freshness(self, entry: dict) -> float:
        """Conservative: use the OLDEST as_of among all dated sources."""
        pub = entry.get("public") or {}
        dates = []
        if pub.get("as_of"):
            dates.append(pub["as_of"])
        for bm in (pub.get("benchmarks") or {}).values():
            if isinstance(bm, dict) and bm.get("as_of"):
                dates.append(bm["as_of"])
        if not dates:
            return _UNDATED_FRESHNESS
        return min(self._freshness(d) for d in dates)

    # ── DAON layer: live aggregation from ModelHistory ──

    @staticmethod
    def daon_stats_from_history(history) -> dict:
        """Aggregate a ModelHistory instance into per-model field stats.

        Returns:
            { normalized_model_id: { capability:
                {"samples": n, "success_rate": r, "avg_latency_ms": ms} } }

        Duck-typed: only needs history._data shaped like ModelHistory's.
        """
        pooled: dict = {}
        try:
            data = getattr(history, "_data", None) or {}
        except Exception:
            return {}
        for role, cap in ROLE_CAPABILITY_MAP.items():
            for mid, ctxs in (data.get(role, {}) or {}).items():
                stats = (ctxs or {}).get("overall") or {}
                succ = int(stats.get("success", 0) or 0)
                fail = int(stats.get("fail", 0) or 0)
                n = succ + fail
                if n <= 0:
                    continue
                slot = pooled.setdefault(_normalize_id(mid), {}).setdefault(
                    cap, {"samples": 0, "success": 0, "lat_sum": 0.0, "lat_n": 0})
                slot["samples"] += n
                slot["success"] += succ
                slot["lat_sum"] += float(stats.get("total_latency_ms", 0) or 0)
                slot["lat_n"] += int(stats.get("count", 0) or 0)

        result = {}
        for nk, caps in pooled.items():
            result[nk] = {}
            for cap, c in caps.items():
                result[nk][cap] = {
                    "samples": c["samples"],
                    "success_rate": c["success"] / c["samples"],
                    "avg_latency_ms": (c["lat_sum"] / c["lat_n"]) if c["lat_n"] else 0.0,
                }
        return result

    # ── EVIDENCE + FINAL layers ──

    def _blend_capability(self, nk: str, cap: str, daon_stats: dict, depth: int):
        """Blend PUBLIC + DAON for ONE capability of ONE normalized model id.

        Returns (final_score or None, evidence_dict).
        """
        entry = self._entries.get(nk)
        if entry is None:
            return None, {}
        evidence = {
            "capability": cap, "public": None, "daon_samples": 0,
            "w_pub": 0.0, "w_daon": 0.0, "flag": None,
        }

        public_val = self._public_capabilities(entry).get(cap)
        evidence["public"] = public_val

        daon = ((daon_stats or {}).get(nk) or {}).get(cap)
        n = int(daon.get("samples", 0)) if daon else 0
        evidence["daon_samples"] = n

        # No own data at all -> lineage prior (discounted, flagged) or None.
        if public_val is None and n == 0:
            if depth < 2:
                parent = (entry.get("identity") or {}).get("parent")
                if parent:
                    p_score, _ = self._blend_capability(
                        _normalize_id(parent), cap, daon_stats, depth + 1)
                    if p_score is not None:
                        evidence["flag"] = "lineage_estimate"
                        return max(0.0, min(1.0, p_score * LINEAGE_DISCOUNT)), evidence
            return None, evidence

        # Evidence weights.
        w_pub = 0.0
        if public_val is not None:
            pub = entry.get("public") or {}
            try:
                src_conf = max(0.0, min(1.0, float(pub.get("source_confidence", 0.8))))
            except (TypeError, ValueError):
                src_conf = 0.8
            w_pub = src_conf * self._entry_freshness(entry)

        w_daon = 0.0
        daon_val = None
        if n > 0:
            w_daon = n / (n + K_DAON)
            prior = public_val if public_val is not None else 0.8
            rate = float(daon.get("success_rate", 0.0) or 0.0)
            daon_val = (rate * n + K_DAON * prior) / (n + K_DAON)

        if public_val is not None and daon_val is not None:
            denom = w_pub + w_daon
            final = ((w_pub * public_val + w_daon * daon_val) / denom
                     if denom > 1e-9 else public_val)
        elif daon_val is not None:
            final = daon_val
        else:
            final = public_val

        if public_val is not None and n == 0:
            evidence["flag"] = "high_potential_unverified"
        evidence["w_pub"] = round(w_pub, 3)
        evidence["w_daon"] = round(w_daon, 3)
        return max(0.0, min(1.0, final)), evidence

    def get_final_capability(self, model_id: str, strength_or_capability: str,
                             daon_stats: dict = None):
        """Public API: FINAL capability score for a model.

        Args:
            model_id: model id string (any case/punctuation variant).
            strength_or_capability: selector strength ("code", "debug", ...)
                or a capability axis name ("coding", "agentic", ...).
            daon_stats: output of daon_stats_from_history() (reused across
                models in one selection round).

        Returns:
            (final_score 0..1 or None when no intel applies, evidence dict)
        """
        cap = STRENGTH_CAPABILITY_MAP.get(strength_or_capability, strength_or_capability)
        if cap not in CAPABILITIES:
            return None, {}
        nk = _normalize_id(model_id)
        if nk not in self._entries:
            return None, {}

        score, ev = self._blend_capability(nk, cap, daon_stats or {}, depth=0)
        # Harness nodes are agents: blend the agentic axis into non-agentic
        # capabilities when agentic evidence exists.
        if score is not None and cap != "agentic":
            ag_score, _ = self._blend_capability(nk, "agentic", daon_stats or {}, depth=0)
            if ag_score is not None:
                score = (1.0 - AGENTIC_BLEND) * score + AGENTIC_BLEND * ag_score
                ev["agentic_blended"] = True
        return score, ev

    # ── CEO-facing compressed view ──

    @staticmethod
    def _sources_line(entry: dict) -> str:
        pub = entry.get("public") or {}
        names, dates = [], []
        for name, bm in (pub.get("benchmarks") or {}).items():
            if isinstance(bm, dict):
                if bm.get("score") is None:
                    continue
                names.append(str(name))
                if bm.get("as_of"):
                    dates.append(str(bm["as_of"])[:7])
            elif isinstance(bm, (int, float)):
                names.append(str(name))
        if not names:
            return ""
        out = ", ".join(names[:4])
        if dates:
            out += f" ({min(dates)})"
        return out

    def format_ceo_line(self, model_id: str, daon_stats: dict = None) -> str:
        """Compact 3-line CEO summary. Returns '' when nothing to report."""
        nk = _normalize_id(model_id)
        entry = self._entries.get(nk)
        if entry is None:
            return ""

        star_parts = []
        has_public = False
        has_daon_any = False
        for cap in CAPABILITIES:
            score, ev = self.get_final_capability(model_id, cap, daon_stats=daon_stats)
            if ev.get("public") is not None:
                has_public = True
            if ev.get("daon_samples", 0) > 0:
                has_daon_any = True
            if score is None:
                star_parts.append(f"{cap}: -")
            else:
                n_stars = max(1, min(5, int(round(score * 5))))
                star_parts.append(f"{cap}: {'★' * n_stars}{'☆' * (5 - n_stars)}")

        ev_parts = []
        sources = self._sources_line(entry)
        if sources:
            ev_parts.append(f"근거: {sources}")
        d_caps = (daon_stats or {}).get(nk) or {}
        total_samples = sum(int(v.get("samples", 0)) for v in d_caps.values())
        ev_parts.append(f"DAON 실전: {total_samples}회" if total_samples
                        else "DAON 실전: 아직 없음")

        if has_public and not has_daon_any:
            ev_parts.append("상태: 잠재력 높음 / 실전 검증 부족")
        elif not has_public and not has_daon_any:
            if (entry.get("identity") or {}).get("parent"):
                ev_parts.append("상태: 계보 추정 (부모 모델 prior, 성능 미상속)")
            else:
                return ""  # zero information -> do not spend CEO tokens
        else:
            conf = "높음" if total_samples >= 15 else ("중간" if total_samples >= 5 else "낮음")
            ev_parts.append(f"신뢰도: {conf}")

        return (f"{model_id}\n  " + " | ".join(star_parts)
                + "\n  " + " | ".join(ev_parts))


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_intel_singleton: Optional[ModelIntel] = None


def get_model_intel() -> ModelIntel:
    """Global ModelIntel singleton (files load once per process)."""
    global _intel_singleton
    if _intel_singleton is None:
        _intel_singleton = ModelIntel()
        _logger.info("ModelIntel: loaded %d entries", len(_intel_singleton._entries))
    return _intel_singleton
