"""
incorporation.py — Gap E-L4: incorporation governance.

The immutable order of the self-building loop (0A section / 4.2 section):

    create -> isolate -> verify -> approve -> incorporate -> use
    (E-L2)    (E-L3)    (E-L4)    (E-L4)    (E-L4)        (existing)

- create: the E-L2 Builder sub-team produces a DRAFT artifact
- isolate: E-L3 worktree + workspace safety invariants
- verify / approve / incorporate: this module — the governance pipeline
- use: approved skills enter the CEO catalog (existing SkillRegistry behavior)

Governance pipeline (order enforced, see INCORPORATION_ORDER):
  0. entry gate: only DRAFT artifacts may enter (approved/rejected -> refused)
  1. verify: probe(s) must pass (missing/failing probe -> reject, no promote)
  2. approve: approver gate (no approver registered -> deny — risk 5 default)
  3. incorporate: only then promote (reuses existing promote_skill)

Design principles (consistent with E-L1~E-L3):
- Every stage is injectable (probe_runner / approver / promoter) so probes
  can verify the order enforcement with fakes.
- Never raises — the result is always a dict.
- The stage history (result["stages"]) records the exact execution order
  (audit trail proving the immutable order was respected).
- The existing manual UI promote path (admin_routes) is a human-approval
  surface and is intentionally left untouched by this governance pipeline.
"""

import logging
import subprocess
import sys

_logger = logging.getLogger(__name__)

# --- Immutable order ------------------------------------------------------

# Full loop order (documentation + probe assertion surface).
IMMUTABLE_ORDER = ("create", "isolate", "verify", "approve", "incorporate", "use")

# The sub-order this module enforces (governance pipeline stages).
INCORPORATION_ORDER = ("entry", "verify", "approve", "incorporate")

STAGE_ENTRY = "entry"
STAGE_VERIFY = "verify"
STAGE_APPROVE = "approve"
STAGE_INCORPORATE = "incorporate"

STATUS_INCORPORATED = "incorporated"
STATUS_REJECTED = "rejected"
STATUS_ERROR = "error"

LIFECYCLE_DRAFT = "draft"
_BLOCKED_LIFECYCLES = ("approved", "rejected", "incorporated")


# --- Artifact accessors ---------------------------------------------------

def artifact_name(artifact):
    """Extract the artifact name from a dict artifact or a plain string.

    Returns an empty string when the name is missing. Never raises.
    """
    try:
        if isinstance(artifact, dict):
            return str(artifact.get("name") or "").strip()
        return str(artifact or "").strip()
    except Exception:
        return ""


def artifact_lifecycle(artifact):
    """Extract the lifecycle status of an artifact.

    Missing/empty status means 'draft' — Builder artifacts (E-L2) are always
    produced as drafts. Never raises.
    """
    try:
        if isinstance(artifact, dict):
            status = str(artifact.get("status") or "").strip().lower()
            return status or LIFECYCLE_DRAFT
        return LIFECYCLE_DRAFT
    except Exception:
        return LIFECYCLE_DRAFT


# --- Stage 0: entry gate --------------------------------------------------

def check_entry_gate(artifact):
    """Stage 0: only draft artifacts may enter governance.

    Returns (ok, reason). Already approved/rejected/incorporated artifacts are
    refused — re-incorporation is not a governance operation.
    """
    name = artifact_name(artifact)
    if not name:
        return False, "artifact name is missing"
    lifecycle = artifact_lifecycle(artifact)
    if lifecycle in _BLOCKED_LIFECYCLES:
        return False, (
            "artifact '%s' is already '%s' — only drafts may enter governance"
            % (name, lifecycle)
        )
    return True, "draft artifact '%s' accepted" % name


# --- Stage 1: verify (probe) ----------------------------------------------

def _default_probe_runner(probe_path, cwd=None):
    """Run a probe script with the current interpreter. Returns (ok, output)."""
    try:
        proc = subprocess.run(
            [sys.executable, str(probe_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            cwd=cwd,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output
    except Exception as e:
        return False, "probe runner error: %s" % e


def verify_artifact(artifact, probe_runner=None):
    """Stage 1: probe verification. Returns (ok, detail).

    - Missing/empty probe_paths -> fail (governance REQUIRES a probe; the
      E-L2 Builder mission mandates one).
    - Every probe must pass; the first failure rejects the artifact.
    - A probe_runner exception is a failure (fail-safe).
    """
    runner = probe_runner or _default_probe_runner
    probes = []
    if isinstance(artifact, dict):
        raw = artifact.get("probe_paths")
        if isinstance(raw, (list, tuple)):
            probes = [str(p).strip() for p in raw if str(p or "").strip()]
        elif isinstance(raw, str) and raw.strip():
            probes = [raw.strip()]
    if not probes:
        return False, "no probe_paths — governance requires probe verification"
    for probe_path in probes:
        try:
            result = runner(probe_path)
        except Exception as e:
            return False, "probe runner raised for %s: %s" % (probe_path, e)
        if isinstance(result, (tuple, list)) and len(result) >= 1:
            ok = bool(result[0])
            detail = str(result[1]) if len(result) > 1 else ""
        else:
            ok = bool(result)
            detail = ""
        if not ok:
            suffix = (" — " + detail[:300]) if detail else ""
            return False, "probe failed: %s%s" % (probe_path, suffix)
    return True, "%d probe(s) passed" % len(probes)


# --- Stage 2: approve -------------------------------------------------------

def approve_artifact(artifact, approver=None):
    """Stage 2: approval gate. Returns (allowed, reason).

    - No approver registered -> DENY (risk 5 safe default, same rule as the
      E-L2 builder gate). Auto-incorporation requires an explicit approver.
    - Approver exception -> deny (fail-safe).
    - The approver may return a bool or an (allowed, reason) tuple.
    """
    if approver is None:
        return False, "no approver registered — incorporation requires explicit approval"
    try:
        result = approver(artifact)
    except Exception as e:
        return False, "approver raised: %s" % e
    if isinstance(result, (tuple, list)) and len(result) >= 1:
        allowed = bool(result[0])
        reason = str(result[1]) if len(result) > 1 else ""
    else:
        allowed = bool(result)
        reason = ""
    return allowed, reason or ("approved" if allowed else "denied by approver")


# --- Stage 3: incorporate ---------------------------------------------------

def default_skill_promoter(artifact):
    """Default promoter: reuse the existing SkillRegistry.promote_skill.

    Lazy import keeps this module probe-friendly and free of import cycles.
    Returns True on success. Never raises (returns False on any failure).
    """
    name = artifact_name(artifact)
    if not name:
        return False
    try:
        from api.skill_registry import get_skill_registry
        return bool(get_skill_registry().promote_skill(name))
    except Exception as e:
        _logger.warning("default_skill_promoter failed for %s: %s", name, e)
        return False


# --- Governance pipeline ----------------------------------------------------

def run_incorporation(artifact, probe_runner=None, approver=None, promoter=None):
    """Run the full governance pipeline. ORDER ENFORCED. Returns a result dict.

    Stages run strictly in INCORPORATION_ORDER; the first failing gate stops
    the pipeline and nothing after it is ever called (verify failure -> no
    approval request, approval denial -> no promote).

    Result: {"ok": bool, "status": incorporated|rejected|error, "name": str,
             "stages": [{"stage", "ok", "detail"}, ...], "reason": str}

    Never raises.
    """
    stages = []
    name = artifact_name(artifact)
    promote_fn = promoter or default_skill_promoter

    def _finish(ok, status, reason):
        return {
            "ok": ok,
            "status": status,
            "name": name,
            "stages": stages,
            "reason": reason,
        }

    try:
        # Stage 0: entry gate (drafts only)
        ok, reason = check_entry_gate(artifact)
        stages.append({"stage": STAGE_ENTRY, "ok": ok, "detail": reason})
        if not ok:
            return _finish(False, STATUS_REJECTED, reason)

        # Stage 1: verify (probe must pass before anything else can happen)
        ok, detail = verify_artifact(artifact, probe_runner=probe_runner)
        stages.append({"stage": STAGE_VERIFY, "ok": ok, "detail": detail})
        if not ok:
            return _finish(False, STATUS_REJECTED, "verification failed: %s" % detail)

        # Stage 2: approve (only verified artifacts reach the approver)
        allowed, reason = approve_artifact(artifact, approver=approver)
        stages.append({"stage": STAGE_APPROVE, "ok": allowed, "detail": reason})
        if not allowed:
            return _finish(False, STATUS_REJECTED, "approval denied: %s" % reason)

        # Stage 3: incorporate — reachable only after every gate passed
        try:
            promoted = bool(promote_fn(artifact))
        except Exception as e:
            stages.append({
                "stage": STAGE_INCORPORATE, "ok": False,
                "detail": "promoter raised: %s" % e,
            })
            return _finish(False, STATUS_ERROR, "promoter raised: %s" % e)
        stages.append({
            "stage": STAGE_INCORPORATE, "ok": promoted,
            "detail": "promoted" if promoted else "promote failed",
        })
        if not promoted:
            return _finish(False, STATUS_ERROR, "promote failed after all gates passed")
        return _finish(True, STATUS_INCORPORATED, "artifact '%s' incorporated" % name)
    except Exception as e:
        _logger.warning("run_incorporation unexpected error: %s", e)
        return _finish(False, STATUS_ERROR, "unexpected error: %s" % e)
