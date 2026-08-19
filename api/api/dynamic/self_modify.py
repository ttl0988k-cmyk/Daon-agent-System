"""
Gap E-2: Self-modification safety governance pipeline.

A self-modification run may only pass through this order:

    git checkpoint (auto-commit) -> approval gate (existing approval) -> apply fix
      -> probe regression check -> on pass commit confirm / on fail git revert auto-restore

Design principles:
- Every external effect (git execution, probe execution, applying the fix) is an
  injectable runner/callback. The server wires real implementations; probes wire fakes.
- Order enforcement: the state machine forbids skipping stages.
- On failure, auto-restore to the checkpoint (git reset --hard).
- Reuses existing governance assets: approval gate (api.approval) and the
  delegation guard (api.dynamic.delegation).
"""
import time

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# Pipeline states (ordered)
STATE_INIT = "init"
STATE_CHECKPOINTED = "checkpointed"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATE_APPLIED = "applied"
STATE_VERIFIED = "verified"
STATE_COMMITTED = "committed"
STATE_REVERTED = "reverted"
STATE_REJECTED = "rejected"


class SelfModifyError(Exception):
    """Pipeline order violation or stage failure."""


def _default_git_runner(args, cwd=None):
    """Run a git command via subprocess. Returns (returncode, combined_output)."""
    import subprocess
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=60
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return 1, str(e)


def _default_probe_runner(probe_path, cwd=None):
    """Run a probe script with the current interpreter. Returns (ok, output)."""
    import subprocess
    import sys
    try:
        proc = subprocess.run(
            [sys.executable, str(probe_path)], cwd=cwd,
            capture_output=True, text=True, timeout=300
        )
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return False, str(e)


class SelfModifyPipeline:
    """Coordinates one self-modification attempt through the safety pipeline.

    Parameters:
      git_runner: callable(args) -> (returncode, output). Injected for tests.
      probe_runner: callable(probe_path) -> (ok, output). Injected for tests.
      approval: module/object exposing set_pending/has_pending/get_history/approve/reject.
                None disables the approval gate (auto-approve) for dev/probe use.
      session_id: approval session key.
      delegation_ctx: optional harness delegation context. When provided, the
                delegation guard (check_delegation_guard) gates the run.
      limits: limits dict passed to the delegation guard.
      cwd: working directory for git/probe commands.
    """

    def __init__(self, git_runner=None, probe_runner=None, approval=None,
                 session_id=None, delegation_ctx=None, limits=None, cwd=None):
        self.git = git_runner or _default_git_runner
        self.probe_runner = probe_runner or _default_probe_runner
        self.approval = approval
        self.session_id = session_id or "self-modify"
        self.delegation_ctx = delegation_ctx
        self.limits = limits or {}
        self.cwd = cwd
        self.state = STATE_INIT
        self.history = []
        self.checkpoint_ref = None

    # ── internals ──────────────────────────────────────────────────────────

    def _record(self, stage, detail):
        self.history.append({
            "stage": stage,
            "state": self.state,
            "detail": detail,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

    def _require_state(self, expected):
        if self.state != expected:
            raise SelfModifyError(
                f"Order violation: expected state '{expected}', got '{self.state}'"
            )

    def _git(self, args):
        code, out = self.git(args, cwd=self.cwd) if _accepts_cwd(self.git) else self.git(args)
        return code, (out or "").strip()

    def _guard_check(self, description):
        if self.delegation_ctx is None:
            return
        from api.dynamic.delegation import check_delegation_guard
        ok, reason = check_delegation_guard(self.delegation_ctx, self.limits, description)
        if not ok:
            raise SelfModifyError(f"Delegation guard rejected self-modify: {reason}")

    def _rollback_to_checkpoint(self):
        if not self.checkpoint_ref:
            return False
        code, out = self._git(["reset", "--hard", self.checkpoint_ref])
        if code != 0:
            _log.error("self-modify rollback failed: %s", out)
            return False
        self._git(["clean", "-fd"])
        return True

    def _wait_approval(self, timeout, poll_interval):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.approval.has_pending(self.session_id):
                for entry in reversed(self.approval.get_history(self.session_id, limit=10)):
                    if entry.get("status") in ("approved", "rejected"):
                        return entry["status"]
                return "rejected"
            time.sleep(poll_interval)
        return "timeout"

    # ── stages ─────────────────────────────────────────────────────────────

    def _stage_checkpoint(self, description):
        self._require_state(STATE_INIT)
        code, ref = self._git(["rev-parse", "HEAD"])
        if code != 0:
            raise SelfModifyError(f"git rev-parse failed: {ref}")
        self.checkpoint_ref = ref
        code, out = self._git(["add", "-A"])
        if code != 0:
            raise SelfModifyError(f"git add failed: {out}")
        code, out = self._git(["commit", "--allow-empty", "-m",
                               f"self-modify checkpoint: {description}"])
        if code != 0:
            raise SelfModifyError(f"git checkpoint commit failed: {out}")
        self.state = STATE_CHECKPOINTED
        self._record("checkpoint", {"ref": ref})

    def _stage_approval(self, description, timeout, poll_interval):
        self._require_state(STATE_CHECKPOINTED)
        self.state = STATE_AWAITING_APPROVAL
        if self.approval is None:
            decision = "approved"
        else:
            self.approval.set_pending(self.session_id, {
                "type": "self_modify",
                "status": "pending",
                "description": description,
                "checkpoint_ref": self.checkpoint_ref,
            })
            decision = self._wait_approval(timeout, poll_interval)
        self._record("approval", {"decision": decision})
        if decision == "approved":
            return
        self._rollback_to_checkpoint()
        self.state = STATE_REJECTED
        self._record("rollback", {"reason": decision})

    def _stage_apply(self, apply_fn):
        self._require_state(STATE_AWAITING_APPROVAL)
        try:
            apply_fn()
        except Exception as e:
            self._rollback_to_checkpoint()
            self.state = STATE_REVERTED
            self._record("apply_failed", {"error": str(e)})
            raise SelfModifyError(f"apply_fn failed: {e}")
        self.state = STATE_APPLIED
        self._record("apply", {})

    def _stage_verify(self, probe_paths):
        self._require_state(STATE_APPLIED)
        failures = []
        for probe in probe_paths:
            ok, out = self.probe_runner(probe, cwd=self.cwd) if _accepts_cwd(self.probe_runner) else self.probe_runner(probe)
            self._record("probe", {"path": str(probe), "ok": bool(ok)})
            if not ok:
                failures.append(str(probe))
        if failures:
            self._rollback_to_checkpoint()
            self.state = STATE_REVERTED
            self._record("revert", {"reason": "probe failed", "failed": failures})
            raise SelfModifyError(f"Probe regression failed: {failures}")
        self.state = STATE_VERIFIED
        self._record("verify", {"ok": True})

    def _stage_finalize(self, description):
        self._require_state(STATE_VERIFIED)
        code, out = self._git(["add", "-A"])
        if code != 0:
            raise SelfModifyError(f"git add failed: {out}")
        code, out = self._git(["commit", "--allow-empty", "-m",
                               f"self-modify: {description}"])
        if code != 0:
            raise SelfModifyError(f"git finalize commit failed: {out}")
        self.state = STATE_COMMITTED
        self._record("commit", {})

    # ── public entry ───────────────────────────────────────────────────────

    def run(self, description, apply_fn, probe_paths, timeout=300, poll_interval=0.2):
        """Execute the full pipeline. Returns a result dict.

        description: human-readable summary of the modification.
        apply_fn: callable that applies the modification (raises on failure).
        probe_paths: list of probe script paths to run for regression.
        """
        result = {"ok": False, "state": self.state, "history": self.history}
        try:
            self._require_state(STATE_INIT)
            self._guard_check(description)
            self._stage_checkpoint(description)
            self._stage_approval(description, timeout, poll_interval)
            if self.state == STATE_REJECTED:
                result.update(ok=False, state=self.state, reason="approval rejected")
                return result
            self._stage_apply(apply_fn)
            self._stage_verify(probe_paths)
            self._stage_finalize(description)
            result.update(ok=True, state=self.state)
            return result
        except SelfModifyError as e:
            result.update(ok=False, state=self.state, error=str(e))
            return result


def _accepts_cwd(fn):
    """Return True if fn accepts a cwd keyword argument."""
    import inspect
    try:
        sig = inspect.signature(fn)
        return "cwd" in sig.parameters
    except (TypeError, ValueError):
        return False
