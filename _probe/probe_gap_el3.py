#!/usr/bin/env python3
"""Gap E-L3 feature probe: workspace safety invariants (SPEC 9.5) + git worktree isolation (E-4a).

Validates api/api/dynamic/isolation.py end-to-end with fake git/probe runners:
  1. Invariant 3 -- key sanitization ([A-Za-z0-9._-] only, hash suffix on change)
  2. Invariant 2 -- path containment (normalized prefix check, escape blocked)
  3. Invariant 1 -- cwd == workspace path validation
  4. WorktreeIsolation lifecycle (create / merge_back / discard) with fake git
  5. run_isolated_self_modify integration (success merge / failure discard /
     apply_fn path injection / creation failure)
  6. Static export surface checks

No server, no real git repository needed for groups 1-4 and most of group 5.
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0


def check(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        print("FAIL: %s" % msg)
        sys.exit(1)


from api.dynamic.isolation import (  # noqa: E402
    IsolationError,
    WorktreeIsolation,
    is_path_inside_root,
    run_isolated_self_modify,
    sanitize_workspace_key,
    validate_cwd_is_workspace,
    validate_path_inside_root,
)


# ── Group 1: Invariant 3 -- key sanitization ────────────────────────────────

k_clean = sanitize_workspace_key("simple-name_1.0")
check(k_clean == "simple-name_1.0", "clean identifier unchanged")

k_dirty = sanitize_workspace_key("issue/123")
check(k_dirty.startswith("issue_123-"), "sanitized prefix correct")
check(re.match(r"^[A-Za-z0-9._-]+$", k_dirty) is not None,
      "sanitized key uses only allowed characters")
suffix = k_dirty.split("-")[-1]
check(len(suffix) == 16, "hash suffix is 16 hex chars (64 bits)")
check(re.match(r"^[0-9a-f]{16}$", suffix) is not None, "suffix is lowercase hex")

check(sanitize_workspace_key("issue/123") == k_dirty, "sanitization deterministic")

k_a = sanitize_workspace_key("a/b")
k_b = sanitize_workspace_key("a:b")
check(k_a.startswith("a_b-") and k_b.startswith("a_b-"),
      "distinct identifiers sanitize to same base")
check(k_a != k_b, "collision-resistant: distinct identifiers get distinct keys")

check(sanitize_workspace_key("") == "_", "empty identifier falls back to underscore")
check(sanitize_workspace_key(None) == "_", "None identifier falls back to underscore")

k_space = sanitize_workspace_key("run 42/x")
check(" " not in k_space and "/" not in k_space, "spaces and slashes replaced")

# ── Group 2: Invariant 2 -- path containment ────────────────────────────────

_tmp = tempfile.TemporaryDirectory(prefix="probe_el3_")
TMP = Path(_tmp.name).resolve()
ws_root = TMP / "wsroot"
ws_sub = ws_root / "sub" / "deep"
ws_sub.mkdir(parents=True)
outside = TMP / "outside"
outside.mkdir()

check(is_path_inside_root(ws_sub, ws_root) is True, "nested path inside root")
check(is_path_inside_root(ws_root, ws_root) is True, "root itself is inside root")
check(is_path_inside_root(ws_root / "not-yet-created", ws_root) is True,
      "nonexistent path inside root still contained")
check(is_path_inside_root(outside, ws_root) is False, "sibling path outside root")
check(is_path_inside_root(ws_root / ".." / "outside", ws_root) is False,
      "dot-dot traversal escapes root")
check(is_path_inside_root(str(outside), ws_root) is False,
      "absolute path outside root rejected")
check(is_path_inside_root(None, ws_root) is False, "None path returns False (no raise)")

resolved = validate_path_inside_root(ws_sub, ws_root)
check(resolved == ws_sub.resolve(), "validate returns resolved path when inside")

try:
    validate_path_inside_root(outside, ws_root)
    check(False, "validate_path_inside_root must raise on escape")
except IsolationError:
    check(True, "validate_path_inside_root raises IsolationError on escape")
except Exception:
    check(False, "validate_path_inside_root raised wrong exception type")

# ── Group 3: Invariant 1 -- cwd == workspace ────────────────────────────────

check(validate_cwd_is_workspace(ws_sub, ws_sub) is True, "identical cwd matches")
check(validate_cwd_is_workspace(str(ws_sub), ws_sub) is True,
      "str vs Path of same dir matches")
check(validate_cwd_is_workspace(outside, ws_root) is False,
      "different cwd does not match")
check(validate_cwd_is_workspace(None, ws_root) is False,
      "None cwd returns False (no raise)")

# ── Group 4: WorktreeIsolation lifecycle (fake git) ─────────────────────────


class FakeGitRunner:
    """Records calls; returns (0, 'fake-ok') unless a prefix is in fail_on."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or {}

    def __call__(self, args, cwd=None):
        self.calls.append((list(args), cwd))
        for prefix, result in self.fail_on.items():
            if tuple(args[:len(prefix)]) == tuple(prefix):
                return result
        return 0, "fake-ok"

    def find(self, prefix):
        return [c for c in self.calls if tuple(c[0][:len(prefix)]) == tuple(prefix)]


state_dir = TMP / "state"
repo_root = TMP / "repo"
repo_root.mkdir()

fake = FakeGitRunner()
iso = WorktreeIsolation(repo_root=repo_root, git_runner=fake,
                        state_dir=state_dir, run_id="run 42/x")
check(iso.branch_name.startswith("self-modify/run_42_x-"),
      "branch name uses sanitized run_id")
check(iso.created is False and iso.disposed is False, "fresh isolation not created")
check(iso.worktree_path is None, "worktree_path None before create")
check(str(iso.worktrees_dir).endswith("worktrees"),
      "worktrees_dir under state_dir/worktrees")

wt = iso.create()
check(isinstance(wt, Path), "create returns Path")
check(iso.created is True, "created flag set")
add_calls = fake.find(("worktree", "add"))
check(len(add_calls) == 1, "git worktree add called once")
check("-b" in add_calls[0][0] and iso.branch_name in add_calls[0][0],
      "worktree add creates the isolation branch")
check(is_path_inside_root(wt, state_dir / "worktrees"),
      "worktree path inside managed worktrees dir (Invariant 2)")
check(iso.worktree_path == wt, "worktree_path property matches")

try:
    iso.create()
    check(False, "double create must raise")
except IsolationError:
    check(True, "double create raises IsolationError")

iso.merge_back()
merge_calls = fake.find(("merge",))
check(len(merge_calls) == 1 and "--no-edit" in merge_calls[0][0],
      "merge_back merges branch with --no-edit")
remove_calls = fake.find(("worktree", "remove"))
check(len(remove_calls) == 1 and "--force" not in remove_calls[0][0],
      "merge_back removes worktree without force")
branch_calls = fake.find(("branch",))
check(any("-d" in c[0] for c in branch_calls), "merge_back deletes branch with -d")
check(iso.disposed is True, "disposed after merge_back")

try:
    iso.merge_back()
    check(False, "merge_back after dispose must raise")
except IsolationError:
    check(True, "merge_back after dispose raises IsolationError")

# discard path
fake2 = FakeGitRunner()
iso2 = WorktreeIsolation(repo_root=repo_root, git_runner=fake2,
                         state_dir=state_dir, run_id="run-discard")
iso2.create()
iso2.discard()
remove2 = fake2.find(("worktree", "remove"))
check(len(remove2) == 1 and "--force" in remove2[0][0],
      "discard force-removes worktree")
branch2 = fake2.find(("branch",))
check(any("-D" in c[0] for c in branch2), "discard deletes branch with -D")
check(iso2.disposed is True, "disposed after discard")
iso2.discard()  # idempotent
check(True, "double discard is a safe no-op")

iso3 = WorktreeIsolation(repo_root=repo_root, git_runner=FakeGitRunner(),
                         state_dir=state_dir, run_id="run-never")
iso3.discard()
check(iso3.disposed is False or True, "discard before create never raises")

try:
    iso3.merge_back()
    check(False, "merge_back before create must raise")
except IsolationError:
    check(True, "merge_back before create raises IsolationError")

# merge failure -> cherry-pick fallback
fake4 = FakeGitRunner(fail_on={("merge",): (1, "conflict")})
iso4 = WorktreeIsolation(repo_root=repo_root, git_runner=fake4,
                         state_dir=state_dir, run_id="run-cp")
iso4.create()
iso4.merge_back()
check(len(fake4.find(("cherry-pick",))) == 1,
      "merge failure falls back to cherry-pick")
check(iso4.disposed is True, "disposed after cherry-pick merge")

# both merge and cherry-pick fail
fake5 = FakeGitRunner(fail_on={("merge",): (1, "c"), ("cherry-pick",): (1, "c")})
iso5 = WorktreeIsolation(repo_root=repo_root, git_runner=fake5,
                         state_dir=state_dir, run_id="run-bothfail")
iso5.create()
try:
    iso5.merge_back()
    check(False, "merge_back must raise when merge and cherry-pick both fail")
except IsolationError:
    check(True, "merge_back raises when both strategies fail")

# worktree add failure
fake6 = FakeGitRunner(fail_on={("worktree", "add"): (128, "fatal")})
iso6 = WorktreeIsolation(repo_root=repo_root, git_runner=fake6,
                         state_dir=state_dir, run_id="run-addfail")
try:
    iso6.create()
    check(False, "create must raise when git worktree add fails")
except IsolationError:
    check(True, "create raises IsolationError on git failure")
check(iso6.created is False, "created stays False on failure")

# git runner without cwd kwarg still works
plain_calls = []


def plain_git(args):
    plain_calls.append(list(args))
    return 0, "ok"


iso7 = WorktreeIsolation(repo_root=repo_root, git_runner=plain_git,
                         state_dir=state_dir, run_id="run-plain")
wt7 = iso7.create()
check(isinstance(wt7, Path), "plain git runner (no cwd param) supported")
check(any(c[:2] == ["worktree", "add"] for c in plain_calls),
      "plain runner received worktree add")
iso7.discard()

# ── Group 5: run_isolated_self_modify integration ───────────────────────────


def probe_ok(probe_path, cwd=None):
    return True, "probe ok"


def probe_fail(probe_path, cwd=None):
    return False, "probe fail"


# success path: apply_fn receives worktree path, pipeline cwd is worktree
fakeS = FakeGitRunner()
applied_paths = []


def apply_with_path(wt_path):
    applied_paths.append(wt_path)


resS = run_isolated_self_modify(
    "test fix", apply_with_path, ["probe_x"], repo_root=repo_root,
    git_runner=fakeS, probe_runner=probe_ok, state_dir=state_dir,
    run_id="run-success")
check(resS["ok"] is True, "isolated run succeeds")
check(resS["isolated"] is True, "result marked isolated")
check(resS["merge"] == "ok", "success path merges back")
check(resS["branch"].startswith("self-modify/"), "result carries branch name")
check(len(applied_paths) == 1, "apply_fn called once")
check(Path(resS["worktree_path"]) == applied_paths[0],
      "apply_fn received the isolated worktree path")
rev_calls = [c for c in fakeS.calls if c[0][:1] == ["rev-parse"]]
check(rev_calls and rev_calls[0][1] == resS["worktree_path"],
      "pipeline git commands ran with cwd = worktree (Invariant 1)")
check(len(fakeS.find(("merge",))) == 1, "success path performed merge")

# zero-arg apply_fn supported
called_noarg = []


def apply_no_arg():
    called_noarg.append(1)


fakeZ = FakeGitRunner()
resZ = run_isolated_self_modify(
    "noarg fix", apply_no_arg, ["probe_x"], repo_root=repo_root,
    git_runner=fakeZ, probe_runner=probe_ok, state_dir=state_dir,
    run_id="run-noarg")
check(resZ["ok"] is True and called_noarg == [1],
      "zero-arg apply_fn called without path injection")

# failure path: probe fails -> discard
fakeF = FakeGitRunner()
resF = run_isolated_self_modify(
    "bad fix", lambda wt: None, ["probe_x"], repo_root=repo_root,
    git_runner=fakeF, probe_runner=probe_fail, state_dir=state_dir,
    run_id="run-probefail")
check(resF["ok"] is False, "probe failure fails the run")
check(resF["merge"] == "discarded", "failed run discards isolation")
remF = fakeF.find(("worktree", "remove"))
check(remF and "--force" in remF[0][0], "failed run force-removes worktree")

# apply_fn raises -> pipeline reverts, isolation discarded, no raise out
fakeA = FakeGitRunner()


def apply_raises(wt_path):
    raise RuntimeError("boom")


resA = run_isolated_self_modify(
    "raising fix", apply_raises, ["probe_x"], repo_root=repo_root,
    git_runner=fakeA, probe_runner=probe_ok, state_dir=state_dir,
    run_id="run-applyraise")
check(resA["ok"] is False, "apply_fn exception fails the run")
check(resA["merge"] == "discarded", "apply failure discards isolation")

# approval rejection -> discard
class RejectingApproval:
    def __init__(self):
        self._pending = {}
        self._history = []

    def set_pending(self, sid, data):
        self._history.append({"status": "rejected"})

    def has_pending(self, sid):
        return sid in self._pending

    def get_history(self, sid, limit=10):
        return self._history


fakeR = FakeGitRunner()
resR = run_isolated_self_modify(
    "rejected fix", lambda wt: None, ["probe_x"], repo_root=repo_root,
    git_runner=fakeR, probe_runner=probe_ok, approval=RejectingApproval(),
    session_id="sess-el3", state_dir=state_dir, run_id="run-rejected")
check(resR["ok"] is False, "approval rejection fails the run")
check(resR["merge"] == "discarded", "rejected run discards isolation")

# worktree creation failure -> isolated False, no raise
fakeC = FakeGitRunner(fail_on={("worktree", "add"): (128, "fatal")})
resC = run_isolated_self_modify(
    "no worktree", lambda wt: None, ["probe_x"], repo_root=repo_root,
    git_runner=fakeC, probe_runner=probe_ok, state_dir=state_dir,
    run_id="run-wtfail")
check(resC["ok"] is False, "worktree creation failure fails the run")
check(resC["isolated"] is False, "result marked not isolated")
check("error" in resC, "error message present")

# ── Group 6: static export surface ──────────────────────────────────────────

import api.dynamic.isolation as iso_mod  # noqa: E402

for name in ("sanitize_workspace_key", "is_path_inside_root",
             "validate_path_inside_root", "validate_cwd_is_workspace",
             "WorktreeIsolation", "IsolationError", "run_isolated_self_modify"):
    check(hasattr(iso_mod, name), "module exports %s" % name)

check(iso_mod._HASH_SUFFIX_LEN == 16, "hash suffix length constant is 16")
check((ROOT / "api" / "api" / "dynamic" / "isolation.py").exists(),
      "isolation.py exists in api/api/dynamic")

_tmp.cleanup()

print("ALL GAP-E-L3 PROBES PASSED (%d checks)" % CHECKS)
