"""
Gap E-L1: Capability resolution branch — the heart of the Self-Expanding Loop.

Implements the 0A decision chain (대표님 원안, 2026-08-18):

    missing_caps
         -> "capability insufficient"
             -> 1) search existing Skills      (기존 Skill 검색)
             -> 2) assign another Agent        (다른 Agent 배정)
             -> 3) still impossible -> Builder (★ Builder 호출, hand off to E-L2)

The chain is ORDERED and SHORT-CIRCUITS: the first step that resolves a
capability wins, and later steps are not attempted. The Builder is only
invoked when both the skill search and the agent assignment fail — that is
the exact moment DAON starts building its own capability.

E-L1 implements the BRANCH (routing + decision) only. The actual Builder
Agent (E-L2), isolation (E-L3) and promotion governance (E-L4) are separate
gaps; here the Builder step only RECORDS the intent to build (a builder
request) so E-L2 can consume it.

Every step with side effects (skill registry lookup, agent assignment,
builder invocation) is injectable so the probe (_probe/probe_gap_el1.py) can
run the full chain with fakes — no live server, registry, or LLM required.
"""

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# Resolution outcomes (wire-stable strings).
RESOLVED_BY_SKILL = "resolved_by_skill"
RESOLVED_BY_AGENT = "resolved_by_agent"
NEEDS_BUILDER = "needs_builder"

# Ordered step names — the decision chain tries them in this exact order.
STEP_SKILL = "skill"
STEP_AGENT = "agent"
STEP_BUILDER = "builder"
STEP_ORDER = (STEP_SKILL, STEP_AGENT, STEP_BUILDER)


class CapabilityResolutionError(Exception):
    """Raised only for programmer errors (e.g. non-iterable capability list).

    Step failures themselves never raise out of the resolver — a failing step
    is treated as "this step cannot resolve" and the chain moves on. This keeps
    the loop fail-safe: a broken skill lookup must never block a task.
    """


# ── Default step implementations (used in production, overridable in probes) ──


def default_skill_searcher(cap, registry=None):
    """Step 1: search the skill registry for a skill covering the capability.

    Returns {"skill": <name>} on a match, or None. Matching is a conservative
    case-insensitive token overlap between the capability text and skill
    names/catalog, so it never fabricates a skill that is clearly unrelated.
    """
    cap_tokens = _tokens(cap)
    if not cap_tokens:
        return None
    try:
        if registry is None:
            from api.skill_registry import get_skill_registry
            registry = get_skill_registry()
        entries = getattr(registry, "skills", None) or {}
        best_name = None
        best_score = 0
        for name in entries:
            score = len(cap_tokens & _tokens(name))
            if score > best_score:
                best_score = score
                best_name = name
        if best_name and best_score > 0:
            return {"skill": best_name, "score": best_score}
    except Exception as e:  # fail-safe: lookup problems must not block the chain
        _log.warning("skill search failed for cap=%r: %s", cap, e)
    return None


def default_agent_assigner(cap, known_roles=None):
    """Step 2: decide whether an existing specialist agent can take the work.

    Returns {"agent": <role>} when a known role clearly matches, else None.
    The default is conservative: novel capabilities have no pre-existing agent,
    so this returns None and the chain proceeds to the Builder. Probes inject a
    fake assigner to exercise the branch.
    """
    cap_tokens = _tokens(cap)
    if not cap_tokens:
        return None
    roles = known_roles if known_roles is not None else ()
    for role in roles:
        if cap_tokens & _tokens(role):
            return {"agent": role}
    return None


def default_builder(cap):
    """Step 3: record the intent to build a new capability (hand off to E-L2).

    E-L1 does NOT build anything — it produces a builder request describing the
    missing capability so the Builder Agent gap (E-L2) can consume it. This is
    the exact branch point where DAON starts creating its own tools.
    """
    return {
        "builder_request": {
            "capability": str(cap),
            "source": "capability_resolver",
            "status": "pending",  # E-L2 transitions this through the build chain
        }
    }


def _tokens(text):
    """Lowercase alphanumeric token set for conservative matching."""
    import re
    try:
        return set(re.findall(r"[a-z0-9_]+", str(text).lower()))
    except Exception:
        return set()


# ── Resolver ──


class CapabilityResolver:
    """Runs the ordered decision chain over a list of missing capabilities.

    Injectable steps (each ``step(cap) -> dict | None``; None = cannot resolve):
        skill_searcher:  Step 1 — existing skill lookup.
        agent_assigner:  Step 2 — existing agent assignment.
        builder:         Step 3 — builder intent recorder (E-L2 hand-off).

    ``enable_builder=False`` turns the chain into a pure router (used when the
    harness must not spawn build work, e.g. delegated child runs at max depth).
    """

    def __init__(self, skill_searcher=None, agent_assigner=None, builder=None,
                 enable_builder=True):
        self.skill_searcher = skill_searcher or default_skill_searcher
        self.agent_assigner = agent_assigner or default_agent_assigner
        self.builder = builder or default_builder
        self.enable_builder = enable_builder

    def resolve_one(self, cap):
        """Run the decision chain for a single capability.

        Returns a resolution record:
            {
                "capability": str,
                "outcome": RESOLVED_BY_SKILL | RESOLVED_BY_AGENT | NEEDS_BUILDER,
                "detail": dict,        # step payload (skill/agent/builder_request)
                "steps_tried": [str],  # steps attempted, in order
            }
        """
        steps_tried = []

        # Step 1 — existing skill
        steps_tried.append(STEP_SKILL)
        detail = self._safe_call(self.skill_searcher, cap)
        if detail:
            return self._record(cap, RESOLVED_BY_SKILL, detail, steps_tried)

        # Step 2 — another agent
        steps_tried.append(STEP_AGENT)
        detail = self._safe_call(self.agent_assigner, cap)
        if detail:
            return self._record(cap, RESOLVED_BY_AGENT, detail, steps_tried)

        # Step 3 — Builder (only when enabled)
        if self.enable_builder:
            steps_tried.append(STEP_BUILDER)
            detail = self._safe_call(self.builder, cap)
            if detail:
                return self._record(cap, NEEDS_BUILDER, detail, steps_tried)

        # Nothing resolved and builder unavailable/failed — still report the
        # branch outcome so the caller can surface it instead of silently passing.
        return self._record(cap, NEEDS_BUILDER, {"builder_request": None}, steps_tried)

    def resolve(self, missing_caps):
        """Resolve a list of capabilities. Returns (resolutions, builder_queue).

        resolutions:   list of per-capability resolution records (input order).
        builder_queue: subset of builder requests for capabilities that need
                       building (what E-L2 consumes).
        """
        if missing_caps is None:
            missing_caps = []
        if isinstance(missing_caps, str):
            missing_caps = [missing_caps]
        try:
            caps = list(missing_caps)
        except TypeError:
            raise CapabilityResolutionError(
                "missing_caps must be iterable, got %r" % type(missing_caps))

        resolutions = []
        builder_queue = []
        for cap in caps:
            cap = str(cap or "").strip()
            if not cap:
                continue
            rec = self.resolve_one(cap)
            resolutions.append(rec)
            if rec["outcome"] == NEEDS_BUILDER and rec["detail"].get("builder_request"):
                builder_queue.append(rec["detail"]["builder_request"])
        if resolutions:
            _log.info("capability resolution: %d cap(s), %d need builder",
                      len(resolutions), len(builder_queue))
        return resolutions, builder_queue

    # -- internals --

    @staticmethod
    def _safe_call(fn, cap):
        """Call a step; any exception means 'this step cannot resolve'."""
        try:
            return fn(cap)
        except Exception as e:
            _log.warning("resolution step failed for cap=%r: %s", cap, e)
            return None

    @staticmethod
    def _record(cap, outcome, detail, steps_tried):
        return {
            "capability": cap,
            "outcome": outcome,
            "detail": detail or {},
            "steps_tried": list(steps_tried),
        }


__all__ = [
    "RESOLVED_BY_SKILL",
    "RESOLVED_BY_AGENT",
    "NEEDS_BUILDER",
    "STEP_SKILL",
    "STEP_AGENT",
    "STEP_BUILDER",
    "STEP_ORDER",
    "CapabilityResolver",
    "CapabilityResolutionError",
    "default_skill_searcher",
    "default_agent_assigner",
    "default_builder",
]
