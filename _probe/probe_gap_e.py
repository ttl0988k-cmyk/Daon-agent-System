#!/usr/bin/env python3
"""Gap E-2 probe: self-modification safety governance pipeline (full mock).

Validates SelfModifyPipeline (api/api/dynamic/self_modify.py) end-to-end with
fake git/probe/approval runners -- no server, no real git repository needed:

  Group 1: module surface (states, error type, runner introspection)
  Group 2: happy path (auto-approve) -- checkpoint -> apply -> probe -> commit
  Group 3: approval gate -- approve / reject / timeout via fake approval
  Group 4: failure paths -- apply exception, probe regression, git failures
  Group 5: delegation guard, order enforcement, history ledger

Run:  python _probe/probe_gap_e.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from api.dynamic.self_modify import (  # noqa: E402
    SelfModifyPipeline,
    SelfModifyError,
    STATE_INIT,
    STATE_CHECKPOINTED,
    STATE_AWAITING_APPROVAL,
    STATE_APPLIED,
    STATE_VERIFIED,
    STATE_COMMITTED,
    STATE_REVERTED,
    STATE_REJECTED,
    MAX_COMMITS_PER_RUN,
    try_consume_commit_budget,
    count_commits,
    reset_commit_budget,
    _accepts_cwd,
    _default_git_runner,
    _default_probe_runner,
)

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


# ── fakes ────────────────────────────────────────────────────────────────


class FakeGitRunner:
    """Records every git invocation; programmable failures."""

    def __init__(self, fail_rules=None, fail_commit_nth=None, head="abc123f"):
        self.calls = []
        self.fail_rules = fail_rules or {}
        self.fail_commit_nth = fail_commit_nth
        self.head = head
        self._commit_count = 0

    def __call__(self, args, cwd=None):
        self.calls.append(list(args))
        first = args[0] if args else ""
        if first == "commit":
            self._commit_count += 1
            if self.fail_commit_nth and self._commit_count == self.fail_commit_nth:
                return 1, "forced commit failure"
        if first in self.fail_rules:
            return self.fail_rules[first]
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, self.head + "\n"
        return 0, ""


class FakeProbeRunner:
    """Returns canned pass/fail per probe path (default: pass)."""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def __call__(self, probe_path, cwd=None):
        self.calls.append(str(probe_path))
        ok = self.results.get(str(probe_path), True)
        return ok, ("probe ok" if ok else "probe boom")


class FakeApproval:
    """Mimics api.approval surface: set_pending / has_pending / get_history."""

    def __init__(self, decision="approved"):
        self.decision = decision  # approved | rejected | hang
        self.pending = {}
        self.history = []
        self.set_pending_calls = []

    def set_pending(self, session_id, data):
        self.set_pending_calls.append((session_id, dict(data)))
        if self.decision == "hang":
            self.pending[session_id] = dict(data)
            return
        entry = dict(data)
        entry["status"] = self.decision
        entry["reviewer"] = "probe"
        self.history.append(entry)

    def has_pending(self, session_id):
        return session_id in self.pending

    def get_history(self, session_id, limit=30):
        return self.history[-limit:]


def make_pipeline(git=None, probe=None, approval=None, **kw):
    return SelfModifyPipeline(
        git_runner=git or FakeGitRunner(),
        probe_runner=probe or FakeProbeRunner(),
        approval=approval,
        **kw,
    )


# ── Group 1: module surface ──────────────────────────────────────────────
print("[1] module surface")

check(STATE_INIT == "init", "STATE_INIT value")
check(STATE_CHECKPOINTED == "checkpointed", "STATE_CHECKPOINTED value")
check(STATE_AWAITING_APPROVAL == "awaiting_approval", "STATE_AWAITING_APPROVAL value")
check(STATE_APPLIED == "applied", "STATE_APPLIED value")
check(STATE_VERIFIED == "verified", "STATE_VERIFIED value")
check(STATE_COMMITTED == "committed", "STATE_COMMITTED value")
check(STATE_REVERTED == "reverted", "STATE_REVERTED value")
check(STATE_REJECTED == "rejected", "STATE_REJECTED value")
check(issubclass(SelfModifyError, Exception), "SelfModifyError is an Exception")
check(callable(_default_git_runner), "default git runner exists")
check(callable(_default_probe_runner), "default probe runner exists")
check(_accepts_cwd(lambda args: (0, "")) is False, "cwd-less runner detected")


def _cwd_aware(args, cwd=None):
    return 0, ""


check(_accepts_cwd(_cwd_aware) is True, "cwd-aware runner detected")

# ── Group 2: happy path (auto-approve, approval=None) ────────────────────
print("[2] happy path (auto-approve)")

git = FakeGitRunner()
probe = FakeProbeRunner()
applied = []
p = make_pipeline(git=git, probe=probe)
res = p.run("add feature X", lambda: applied.append(1), ["_probe/probe_gap_e1.py"])

check(res["ok"] is True, "happy path ok")
check(res["state"] == STATE_COMMITTED, "happy path ends committed")
check(p.state == STATE_COMMITTED, "pipeline state committed")
check(applied == [1], "apply_fn executed exactly once")
check(p.checkpoint_ref == "abc123f", "checkpoint ref captured (stripped)")
check(len(git.calls) == 5, "exactly 5 git calls on happy path")
check(git.calls[0] == ["rev-parse", "HEAD"], "call 1: rev-parse HEAD")
check(git.calls[1] == ["add", "-A"], "call 2: add -A")
check(git.calls[2][:2] == ["commit", "--allow-empty"], "call 3: checkpoint commit")
check("self-modify checkpoint:" in git.calls[2][3], "checkpoint commit message")
check("add feature X" in git.calls[2][3], "checkpoint message carries description")
check(git.calls[3] == ["add", "-A"], "call 4: add -A (finalize)")
check(git.calls[4][:2] == ["commit", "--allow-empty"], "call 5: finalize commit")
check(git.calls[4][3].startswith("self-modify: "), "finalize commit message")
check("checkpoint" not in git.calls[4][3], "finalize message is not the checkpoint one")
check(not any(c[0] in ("reset", "clean") for c in git.calls), "no rollback on happy path")
check(probe.calls == ["_probe/probe_gap_e1.py"], "probe executed once")

stages = [h["stage"] for h in p.history]
check(stages == ["checkpoint", "approval", "apply", "probe", "verify", "commit"],
      "history stage order")
check(all({"stage", "state", "detail", "at"} <= set(h) for h in p.history),
      "history entries carry stage/state/detail/at")
_appr_rec = [h for h in p.history if h["stage"] == "approval"][0]
check(_appr_rec["detail"]["decision"] == "approved", "auto-approve recorded")

# ── Group 3: approval gate ───────────────────────────────────────────────
print("[3] approval gate (approve / reject / timeout)")

# 3a. explicit approve via fake approval gate
appr = FakeApproval("approved")
git = FakeGitRunner()
p = make_pipeline(git=git, approval=appr, session_id="sess-e2")
res = p.run("fix parser", lambda: None, [])
check(res["ok"] is True and res["state"] == STATE_COMMITTED, "approved run commits")
check(len(appr.set_pending_calls) == 1, "set_pending called once")
sid, data = appr.set_pending_calls[0]
check(sid == "sess-e2", "approval session id used")
check(data["type"] == "self_modify", "pending type is self_modify")
check(data["status"] == "pending", "pending status set")
check(data["checkpoint_ref"] == "abc123f", "pending carries checkpoint ref")
check(data["description"] == "fix parser", "pending carries description")

# 3b. reject -> rollback, apply never runs
appr = FakeApproval("rejected")
git = FakeGitRunner()
applied = []
p = make_pipeline(git=git, approval=appr)
res = p.run("risky change", lambda: applied.append(1), [])
check(res["ok"] is False, "rejected run fails")
check(res["state"] == STATE_REJECTED, "rejected run ends rejected")
check(res.get("reason") == "approval rejected", "rejection reason surfaced")
check(applied == [], "apply_fn never ran after rejection")
check(["reset", "--hard", "abc123f"] in git.calls, "rejection rolls back to checkpoint")
check(["clean", "-fd"] in git.calls, "rejection cleans untracked files")

# 3c. timeout (pending never resolved) -> rollback
appr = FakeApproval("hang")
git = FakeGitRunner()
p = make_pipeline(git=git, approval=appr)
res = p.run("slow approve", lambda: None, [], timeout=0.3, poll_interval=0.05)
check(res["ok"] is False and res["state"] == STATE_REJECTED, "timeout ends rejected")
check(["reset", "--hard", "abc123f"] in git.calls, "timeout rolls back to checkpoint")
check(any(h["stage"] == "rollback" and h["detail"]["reason"] == "timeout"
          for h in p.history), "timeout recorded in history")

# ── Group 4: failure paths ───────────────────────────────────────────────
print("[4] failure paths (apply / probe / git)")

# 4a. apply_fn raises -> revert + rollback, probes never run
git = FakeGitRunner()
probe = FakeProbeRunner()
p = make_pipeline(git=git, probe=probe)


def boom():
    raise RuntimeError("write failed")


res = p.run("broken apply", boom, ["p1"])
check(res["ok"] is False, "apply failure fails the run")
check(res["state"] == STATE_REVERTED, "apply failure ends reverted")
check("apply_fn failed" in res["error"], "apply error surfaced")
check("write failed" in res["error"], "original exception message kept")
check(probe.calls == [], "probes skipped after apply failure")
check(["reset", "--hard", "abc123f"] in git.calls, "apply failure rolls back")
check(any(h["stage"] == "apply_failed" for h in p.history), "apply_failed recorded")

# 4b. probe regression fails -> revert + rollback
git = FakeGitRunner()
probe = FakeProbeRunner({"p_good": True, "p_bad": False})
applied = []
p = make_pipeline(git=git, probe=probe)
res = p.run("probe fails", lambda: applied.append(1), ["p_good", "p_bad"])
check(res["ok"] is False and res["state"] == STATE_REVERTED, "probe failure reverts")
check("Probe regression failed" in res["error"], "probe failure error surfaced")
check("p_bad" in res["error"], "failed probe named in error")
check(applied == [1], "apply ran before verify")
check(probe.calls == ["p_good", "p_bad"], "all probes attempted")
check(["reset", "--hard", "abc123f"] in git.calls, "probe failure rolls back")
check(any(h["stage"] == "revert" for h in p.history), "revert recorded in history")

# 4c. git rev-parse fails -> abort before checkpoint
git = FakeGitRunner(fail_rules={"rev-parse": (1, "not a git repository")})
p = make_pipeline(git=git)
res = p.run("no repo", lambda: None, [])
check(res["ok"] is False and res["state"] == STATE_INIT, "rev-parse failure stays init")
check("git rev-parse failed" in res["error"], "rev-parse error surfaced")
check(len(git.calls) == 1, "no further git calls after rev-parse failure")

# 4d. checkpoint commit fails -> abort
git = FakeGitRunner(fail_rules={"commit": (1, "no identity")})
p = make_pipeline(git=git)
res = p.run("no commit", lambda: None, [])
check(res["ok"] is False and res["state"] == STATE_INIT, "checkpoint failure stays init")
check("git checkpoint commit failed" in res["error"], "checkpoint error surfaced")

# 4e. finalize commit (2nd commit) fails -> stuck verified, not committed
git = FakeGitRunner(fail_commit_nth=2)
p = make_pipeline(git=git)
res = p.run("finalize fails", lambda: None, [])
check(res["ok"] is False and res["state"] == STATE_VERIFIED,
      "finalize failure stops at verified")
check("git finalize commit failed" in res["error"], "finalize error surfaced")

# 4f. rollback itself fails -> still reports revert, no crash
git = FakeGitRunner(fail_rules={"reset": (1, "locked")})
probe = FakeProbeRunner({"p_bad": False})
p = make_pipeline(git=git, probe=probe)
res = p.run("rollback fails", lambda: None, ["p_bad"])
check(res["ok"] is False and res["state"] == STATE_REVERTED,
      "failed rollback still ends reverted")
check("Probe regression failed" in res["error"], "probe error still surfaced")

# 4g. runners without a cwd parameter are also accepted
plain_git_calls = []


def plain_git(args):
    plain_git_calls.append(list(args))
    if args[:2] == ["rev-parse", "HEAD"]:
        return 0, "deadbee"
    return 0, ""


plain_probe_calls = []


def plain_probe(path):
    plain_probe_calls.append(str(path))
    return True, "ok"


p = SelfModifyPipeline(git_runner=plain_git, probe_runner=plain_probe)
res = p.run("plain runners", lambda: None, ["px"])
check(res["ok"] is True and res["state"] == STATE_COMMITTED, "cwd-less runners work")
check(p.checkpoint_ref == "deadbee", "cwd-less git runner used")
check(plain_probe_calls == ["px"], "cwd-less probe runner used")

# ── Group 5: delegation guard + order enforcement ────────────────────────
print("[5] delegation guard + order enforcement")

# 5a. guard passes (depth 0, max_depth 1)
git = FakeGitRunner()
p = make_pipeline(git=git, delegation_ctx={"depth": 0},
                  limits={"delegation": {"max_depth": 1}})
res = p.run("guarded ok", lambda: None, [])
check(res["ok"] is True and res["state"] == STATE_COMMITTED, "guard allows depth 0")

# 5b. depth exceeded -> blocked before any git call
git = FakeGitRunner()
p = make_pipeline(git=git, delegation_ctx={"depth": 1},
                  limits={"delegation": {"max_depth": 1}})
res = p.run("too deep", lambda: None, [])
check(res["ok"] is False and res["state"] == STATE_INIT, "depth breach stays init")
check("Delegation guard rejected" in res["error"], "depth breach error surfaced")
check(git.calls == [], "guard blocks before checkpoint")

# 5c. empty description == empty spawn_reason -> blocked
p = make_pipeline(delegation_ctx={"depth": 0},
                  limits={"delegation": {"max_depth": 1}})
res = p.run("", lambda: None, [])
check(res["ok"] is False, "empty spawn_reason blocked")
check("spawn_reason" in res["error"], "spawn_reason error surfaced")

# 5d. non-dict delegation context -> blocked
p = make_pipeline(delegation_ctx="not-a-dict")
res = p.run("bad ctx", lambda: None, [])
check(res["ok"] is False and "Delegation guard rejected" in res["error"],
      "non-dict ctx blocked")

# 5e. default limits (None) -> max_depth 1 applies, depth 0 passes
p = make_pipeline(delegation_ctx={"depth": 0})
res = p.run("default limits", lambda: None, [])
check(res["ok"] is True, "default limits allow depth 0")

# 5f. direct stage call out of order -> SelfModifyError
p = make_pipeline()
try:
    p._stage_verify(["x"])
    check(False, "expected SelfModifyError for out-of-order verify")
except SelfModifyError as e:
    check("Order violation" in str(e), "order violation message")

# 5g. checkpoint stage cannot run twice
p = make_pipeline()
p._stage_checkpoint("manual")
check(p.state == STATE_CHECKPOINTED, "manual checkpoint advances state")
try:
    p._stage_checkpoint("again")
    check(False, "expected SelfModifyError for double checkpoint")
except SelfModifyError:
    check(True, "double checkpoint forbidden")

# 5h. a finished pipeline cannot run again
git = FakeGitRunner()
p = make_pipeline(git=git)
r1 = p.run("first", lambda: None, [])
check(r1["ok"] is True, "first run succeeds")
r2 = p.run("second", lambda: None, [])
check(r2["ok"] is False and "Order violation" in r2.get("error", ""),
      "second run rejected")
check(len(git.calls) == 5, "second run performed no git calls")

# ── Group 6: commit budget (리스크 1: 무한 자기 수정 루프 차단) ─────────
print("[6] commit budget (risk 1)")

check(MAX_COMMITS_PER_RUN == 4, "MAX_COMMITS_PER_RUN default is 4")
check(callable(try_consume_commit_budget), "budget consumer exists")
check(callable(count_commits), "budget counter reader exists")
check(callable(reset_commit_budget), "budget reset exists")

# 6a. unit: consume up to the cap, then fail-safe refuse
reset_commit_budget("k-unit")
ok1, n1 = try_consume_commit_budget("k-unit", max_commits=2)
ok2, n2 = try_consume_commit_budget("k-unit", max_commits=2)
ok3, n3 = try_consume_commit_budget("k-unit", max_commits=2)
check(ok1 is True and n1 == 1, "first slot consumed")
check(ok2 is True and n2 == 2, "second slot consumed")
check(ok3 is False and n3 == 2, "third slot refused at cap")
check(count_commits("k-unit") == 2, "count_commits reflects usage")
reset_commit_budget("k-unit")
check(count_commits("k-unit") == 0, "reset clears the counter")

# 6b. unit: invalid inputs are fail-safe
okE, nE = try_consume_commit_budget("", max_commits=2)
check(okE is False and nE == 0, "empty key refused")
okZ, nZ = try_consume_commit_budget("k-zero", max_commits=0)
check(okZ is False and nZ == 0, "zero cap refused")
okN, nN = try_consume_commit_budget("k-neg", max_commits=-3)
check(okN is False and nN == 0, "negative cap refused")

# 6c. pipeline: default cap (4) allows a full happy path (2 commits)
reset_commit_budget("k-happy")
git = FakeGitRunner()
p = make_pipeline(git=git, commit_budget_key="k-happy")
res = p.run("budget happy path", lambda: None, [])
check(res["ok"] is True and res["state"] == STATE_COMMITTED,
      "default cap allows happy path")
check(count_commits("k-happy") == 2, "happy path consumed exactly 2 slots")

# 6d. pipeline: cap 1 blocks the finalize commit (fail-safe at verified)
reset_commit_budget("k-tight")
git = FakeGitRunner()
p = make_pipeline(git=git, commit_budget_key="k-tight", max_commits=1)
res = p.run("tight budget", lambda: None, [])
check(res["ok"] is False and res["state"] == STATE_VERIFIED, "cap 1 stops at verified")
check("Commit budget exhausted" in res["error"], "budget error surfaced")
check("k-tight" in res["error"], "budget key named in error")
check(len(git.calls) == 3, "no finalize git calls after budget refusal")

# 6e. pipeline: shared budget across repeated runs blocks the loop
reset_commit_budget("k-loop")
git1 = FakeGitRunner()
p1 = make_pipeline(git=git1, commit_budget_key="k-loop", max_commits=2)
r1 = p1.run("first attempt", lambda: None, [])
check(r1["ok"] is True, "first attempt passes with shared budget")
git2 = FakeGitRunner()
p2 = make_pipeline(git=git2, commit_budget_key="k-loop", max_commits=2)
r2 = p2.run("second attempt", lambda: None, [])
check(r2["ok"] is False and r2["state"] == STATE_INIT,
      "second attempt blocked at checkpoint")
check("Commit budget exhausted" in r2["error"], "loop blocked with budget error")
check(git2.calls == [], "blocked run performs no git calls")
check(count_commits("k-loop") == 2, "shared counter stayed at cap")

# 6f. pipeline: no key = budget disabled (backward compatible)
git = FakeGitRunner()
p = make_pipeline(git=git)
res = p.run("no budget key", lambda: None, [])
check(res["ok"] is True, "budget disabled without key")
check(p.commit_budget_key is None, "default key stays None")

print(f"ALL GAP-E (E-2) PROBES PASSED ({CHECKS} checks)")
