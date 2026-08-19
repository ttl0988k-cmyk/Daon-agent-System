"""
Gap E-L2: Builder Agent role — spawns a tool-building sub-team for missing capabilities.

E-L1 (capability_resolver) decides THAT a capability must be built and records a
builder request (``builder_queue`` on the merged plan). E-L2 is the role that
CONSUMES that queue: for each request it

    1. classifies the build target   (skill | plugin | mcp)
    2. passes the approval gate      (리스크 5: 승인 게이트 기본 강제)
    3. composes the Builder mission  (draft-only constraints + acceptance criteria)
    4. spawns a sub-team via delegate_team (갭 D 위임 가드/예산을 그대로 통과)

The spawned sub-team is a normal nested Dynamic Harness run, so it inherits the
fractal quality gates (acceptance verification + self-healing). The artifacts it
produces are DRAFTS only — promotion into the registry is governed by E-L4
(생성 → 격리 → 검증 → 승인 → 편입 → 사용 불변 순서).

Safety defaults:
- The gate DENIES by default: without a registered approver no build team is
  spawned. Auto mode is an explicit opt-in (an approver that returns allow).
- Every stage is injectable so the probe (_probe/probe_gap_el2.py) can run the
  full dispatch with fakes — no live server, delegation, or LLM required.
- dispatch never raises: a failing spawn is recorded as an error hand-off and
  the queue keeps moving.
"""

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# Build targets (wire-stable strings).
BUILD_TARGET_SKILL = "skill"
BUILD_TARGET_PLUGIN = "plugin"
BUILD_TARGET_MCP = "mcp"

# Dispatch outcomes (wire-stable strings).
DISPATCH_SPAWNED = "spawned"
DISPATCH_DENIED = "denied"
DISPATCH_ERROR = "error"

_SPAWN_REASON_PREFIX = "Builder(E-L2)"


def classify_build_target(capability, classifier=None):
    """Decide WHAT to build for a missing capability: skill | plugin | mcp.

    Deterministic keyword heuristic (conservative — the default artifact is the
    lightest one, a skill). ``classifier(cap) -> str`` overrides entirely.
    """
    if classifier is not None:
        try:
            target = str(classifier(capability) or "").strip().lower()
        except Exception as e:
            _log.warning("custom classifier failed for cap=%r: %s", capability, e)
            target = ""
        if target in (BUILD_TARGET_SKILL, BUILD_TARGET_PLUGIN, BUILD_TARGET_MCP):
            return target
        return BUILD_TARGET_SKILL
    text = str(capability or "").lower()
    if "mcp" in text or "model context protocol" in text:
        return BUILD_TARGET_MCP
    if "plugin" in text or "hook" in text or "tool registration" in text:
        return BUILD_TARGET_PLUGIN
    return BUILD_TARGET_SKILL


def build_builder_task(request, build_target=None):
    """Compose the Builder sub-team mission from a builder request.

    Returns (task_text, acceptance_criteria). The mission makes the constraints
    explicit: draft-only, no self-promotion, workspace-bound, probe required.
    """
    if isinstance(request, dict):
        cap = str(request.get("capability") or "").strip() or str(request)
    else:
        cap = str(request or "").strip()
    target = build_target or classify_build_target(cap)
    task_text = (
        "[Builder 서브팀 미션] 능력 제작 요청 (갭 E-L2)\n"
        f"- 결핍 능력: {cap}\n"
        f"- 제작 대상: {target} (초안 draft)\n"
        "- 제작 지침:\n"
        "  1. 초안(draft)으로만 제작한다. 정식 등록(promote)은 절대 스스로 수행하지 않는다 —\n"
        "     편입은 거버넌스 절차(갭 E-L4)만 수행할 수 있다.\n"
        "  2. 제작은 워크스페이스 경로 안에서만 수행한다.\n"
        "  3. 초안을 검증하는 프로브/테스트를 반드시 작성하고 통과시켜야 한다.\n"
        "  4. 산출물: 초안 파일 경로 + 프로브 통과 결과 + 간단한 사용법을 결과에 포함한다.\n"
    )
    criteria = [
        f"결핍 능력 '{cap}'에 대한 {target} 초안 산출물이 존재한다",
        "초안을 검증하는 프로브/테스트가 작성되어 통과한다",
        "초안은 정식 등록되지 않고 draft 상태로 남는다",
        "결과에 산출물 경로와 사용법이 포함된다",
    ]
    return task_text, criteria


def default_builder_gate(request, approver=None):
    """Approval gate for Builder spawns (리스크 5 기본 강제).

    Returns (allowed, reason). Without a registered approver the gate DENIES —
    DAON never starts building on its own until an approver is wired (E-L4
    connects the approval UI; auto mode is an approver that explicitly allows).
    An approver exception is treated as denial (fail-safe).
    """
    if approver is None:
        return False, ("Builder spawn requires approval: no approver registered "
                       "(safe default; auto mode is a separate toggle)")
    try:
        decision = approver(request)
    except Exception as e:
        _log.warning("builder approver failed: %s", e)
        return False, f"approver error: {e}"
    if decision:
        return True, "approved"
    return False, "denied by approver"


def default_builder_spawner(task, spawn_reason, acceptance_criteria, preferred_model=None):
    """Spawn the Builder sub-team through the delegate_team tool (갭 D 가드 통과).

    Returns {"ok": bool, "child_run_id": str|None, "final_output": str,
    "error": str|None}. Never raises.
    """
    try:
        from tools.delegate_team_tool import delegate_team
    except Exception as e:
        return {"ok": False, "child_run_id": None, "final_output": "",
                "error": f"delegate_team unavailable: {e}"}
    try:
        raw = delegate_team(task=task, spawn_reason=spawn_reason,
                            acceptance_criteria=acceptance_criteria,
                            preferred_model=preferred_model)
    except Exception as e:
        return {"ok": False, "child_run_id": None, "final_output": "",
                "error": f"delegate_team raised: {e}"}
    payload = raw
    if isinstance(raw, str):
        try:
            import json
            payload = json.loads(raw)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    delegated = bool(payload.get("delegated"))
    return {
        "ok": delegated,
        "child_run_id": payload.get("child_run_id"),
        "final_output": str(payload.get("final_output") or ""),
        "error": None if delegated else str(payload.get("reason") or "delegation refused"),
    }


def dispatch_builder_requests(builder_queue, spawner=None, gate=None, approver=None,
                              preferred_model=None, classifier=None, log_callback=None):
    """Consume a builder_queue (from E-L1) and spawn Builder sub-teams.

    Returns a list of hand-off records (input order):
        {"capability", "build_target", "status": spawned|denied|error,
         "reason", "child_run_id", "final_output", "spawn_reason"}
    Never raises — each failure becomes an error record and the queue continues.
    """
    spawner = spawner or default_builder_spawner
    gate = gate or default_builder_gate
    records = []
    if not builder_queue:
        return records
    for request in builder_queue:
        if isinstance(request, dict):
            cap = str(request.get("capability") or "").strip()
        else:
            cap = str(request or "").strip()
        if not cap:
            continue
        target = classify_build_target(cap, classifier=classifier)
        spawn_reason = f"{_SPAWN_REASON_PREFIX}: 결핍 능력 '{cap}' 제작 서브팀 ({target})"
        record = {
            "capability": cap,
            "build_target": target,
            "status": DISPATCH_ERROR,
            "reason": "",
            "child_run_id": None,
            "final_output": "",
            "spawn_reason": spawn_reason,
        }
        try:
            allowed, reason = gate(request, approver) if _accepts_approver(gate) \
                else gate(request)
        except Exception as e:
            allowed, reason = False, f"gate error: {e}"
        if not allowed:
            record["status"] = DISPATCH_DENIED
            record["reason"] = str(reason or "")
            records.append(record)
            if log_callback:
                try:
                    log_callback("Builder", f"제작 요청 게이트 거부: {cap} ({record['reason']})",
                                 "warning")
                except Exception:
                    pass
            continue
        try:
            task_text, criteria = build_builder_task(request, build_target=target)
            outcome = spawner(task_text, spawn_reason, criteria, preferred_model) \
                if _accepts_model(spawner) else spawner(task_text, spawn_reason, criteria)
        except Exception as e:
            outcome = {"ok": False, "child_run_id": None, "final_output": "",
                       "error": f"spawn error: {e}"}
        if not isinstance(outcome, dict):
            outcome = {"ok": False, "child_run_id": None, "final_output": "",
                       "error": "spawner returned non-dict"}
        if outcome.get("ok"):
            record["status"] = DISPATCH_SPAWNED
            record["child_run_id"] = outcome.get("child_run_id")
            record["final_output"] = str(outcome.get("final_output") or "")
            record["reason"] = "spawned"
        else:
            record["status"] = DISPATCH_ERROR
            record["reason"] = str(outcome.get("error") or "spawn failed")
        records.append(record)
        if log_callback:
            try:
                log_callback("Builder",
                             f"제작 요청 처리: {cap} -> {record['status']} ({record['reason']})",
                             "running")
            except Exception:
                pass
    _log.info("builder dispatch: %d request(s), %d spawned",
              len(records), sum(1 for r in records if r["status"] == DISPATCH_SPAWNED))
    return records


def _accepts_approver(fn):
    """True when gate(request, approver) is supported (2-arg gates)."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        return len(params) >= 2
    except Exception:
        return True


def _accepts_model(fn):
    """True when spawner accepts a 4th positional argument (preferred_model)."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        return len(params) >= 4
    except Exception:
        return True


__all__ = [
    "BUILD_TARGET_SKILL",
    "BUILD_TARGET_PLUGIN",
    "BUILD_TARGET_MCP",
    "DISPATCH_SPAWNED",
    "DISPATCH_DENIED",
    "DISPATCH_ERROR",
    "classify_build_target",
    "build_builder_task",
    "default_builder_gate",
    "default_builder_spawner",
    "dispatch_builder_requests",
]
