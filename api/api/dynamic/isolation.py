"""
Gap E-L3: Workspace safety invariants (SPEC 9.5) + git worktree isolation (E-4a).

SPEC 9.5 Safety Invariants:
  1. Agent runs only inside the per-issue workspace path (cwd == workspace_path).
  2. Workspace path MUST stay inside workspace root (normalized prefix check).
  3. Workspace key is sanitized: only [A-Za-z0-9._-] allowed; other chars become '_';
     if sanitization changes the identifier, a stable hash suffix (>= 64 bits) is appended.

E-4a worktree isolation:
  Self-modify experiments run in a dedicated git worktree (branch: self-modify/<run_id>).
  On verification pass: merge back + remove worktree.
  On failure/rejection: git worktree remove --force (complete discard).
  Worktree path: STATE_DIR/worktrees/<sanitized_key> (outside repo, managed directory).

Design principles (consistent with E-L1/E-L2):
- Every external effect (git execution) is injectable. Probes wire fakes.
- Functions never raise unexpectedly; they return (ok, reason) or raise IsolationError.
- WorktreeIsolation lifecycle: create -> use worktree_path as cwd -> merge_back/discard.
"""
import hashlib
import re
import time
from pathlib import Path

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# Allowed characters in workspace keys (SPEC 9.5 Invariant 3)
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")
_HASH_SUFFIX_LEN = 16  # 64 bits in hex characters


class IsolationError(Exception):
    """Path invariant violation or worktree lifecycle failure."""


# ── Invariant 3: key sanitization ──────────────────────────────────────────


def sanitize_workspace_key(identifier: str) -> str:
    """Sanitize an identifier for use as a workspace directory name.

    Only [A-Za-z0-9._-] are allowed. Other characters become '_'.
    If sanitization changes the text, a stable hash suffix (64 bits) of the
    original identifier is appended for collision resistance (SPEC 9.5 Inv. 3).

    Deterministic: same input always produces same output.
    """
    text = str(identifier) if identifier else "_"
    sanitized = _SANITIZE_RE.sub("_", text)
    if sanitized != text:
        suffix = hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_SUFFIX_LEN]
        sanitized = f"{sanitized}-{suffix}"
    if not sanitized:
        sanitized = "_"
    return sanitized


# ── Invariant 2: path containment ───────────────────────────────────────────


def is_path_inside_root(path, root) -> bool:
    """Check whether path is inside root after normalization. Never raises."""
    try:
        resolved = Path(path).resolve()
        resolved_root = Path(root).resolve()
        resolved.relative_to(resolved_root)
        return True
    except (ValueError, OSError, TypeError):
        return False


def validate_path_inside_root(path, root) -> Path:
    """Resolve path and verify it is inside root. Returns resolved Path.

    Raises IsolationError if path escapes root (SPEC 9.5 Invariant 2).
    """
    try:
        resolved = Path(path).resolve()
        resolved_root = Path(root).resolve()
        resolved.relative_to(resolved_root)
        return resolved
    except (ValueError, OSError) as e:
        raise IsolationError(
            f"Path invariant violation: '{path}' is outside root '{root}'"
        ) from e


# ── Invariant 1: cwd == workspace ───────────────────────────────────────────


def validate_cwd_is_workspace(cwd, workspace_path) -> bool:
    """Verify that cwd matches the workspace path (SPEC 9.5 Invariant 1).

    Returns True if they resolve to the same directory, False otherwise.
    Never raises.
    """
    try:
        return Path(cwd).resolve() == Path(workspace_path).resolve()
    except (OSError, TypeError):
        return False


# ── E-4a: git worktree isolation ────────────────────────────────────────────


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


def _accepts_cwd(fn):
    """Return True if fn accepts a cwd keyword argument."""
    import inspect
    try:
        sig = inspect.signature(fn)
        return "cwd" in sig.parameters
    except (TypeError, ValueError):
        return False


class WorktreeIsolation:
    """Manages a git worktree for isolated self-modification (E-4a).

    Lifecycle: create() -> use worktree_path as cwd -> merge_back() or discard().

    Parameters:
      repo_root: path to the main git repository.
      git_runner: callable(args, cwd=None) -> (returncode, output). Injectable.
      state_dir: directory for worktree storage (default: STATE_DIR/worktrees).
      run_id: unique identifier for this self-modify run.
    """

    def __init__(self, repo_root, git_runner=None, state_dir=None, run_id=None):
        self.repo_root = Path(repo_root)
        self.git = git_runner or _default_git_runner
        self.run_id = run_id or f"run-{int(time.time())}"
        self._state_dir = state_dir
        self.branch_name = f"self-modify/{sanitize_workspace_key(self.run_id)}"
        self._worktree_path = None
        self._created = False
        self._disposed = False

    @property
    def worktree_path(self):
        """Path to the isolated worktree (None before create())."""
        return self._worktree_path

    @property
    def worktrees_dir(self):
        """Managed directory for worktrees (STATE_DIR/worktrees)."""
        if self._state_dir is not None:
            return Path(self._state_dir) / "worktrees"
        try:
            from api.config import STATE_DIR
            return Path(STATE_DIR) / "worktrees"
        except Exception:
            return Path(".daon_state") / "worktrees"

    @property
    def created(self):
        return self._created

    @property
    def disposed(self):
        return self._disposed

    def _git(self, args, cwd=None):
        effective_cwd = cwd or str(self.repo_root)
        if _accepts_cwd(self.git):
            code, out = self.git(args, cwd=effective_cwd)
        else:
            code, out = self.git(args)
        return code, (out or "").strip()

    def create(self) -> Path:
        """Create the isolated worktree. Returns worktree path.

        Raises IsolationError on failure.
        """
        if self._created:
            raise IsolationError("Worktree already created")
        if self._disposed:
            raise IsolationError("Worktree already disposed")

        wt_dir = self.worktrees_dir
        wt_dir.mkdir(parents=True, exist_ok=True)
        key = sanitize_workspace_key(self.run_id)
        self._worktree_path = wt_dir / key

        # Invariant 2: worktree path must be inside worktrees_dir
        validate_path_inside_root(self._worktree_path, wt_dir)

        code, out = self._git([
            "worktree", "add", str(self._worktree_path),
            "-b", self.branch_name
        ])
        if code != 0:
            raise IsolationError(f"git worktree add failed: {out}")
        self._created = True
        _log.info("E-L3 worktree created: %s (branch %s)",
                  self._worktree_path, self.branch_name)
        return self._worktree_path

    def merge_back(self):
        """Merge the worktree branch into the current branch, then remove worktree.

        Raises IsolationError if merge fails.
        """
        if not self._created:
            raise IsolationError("Worktree not created")
        if self._disposed:
            raise IsolationError("Worktree already disposed")

        # Merge from main repo
        code, out = self._git(["merge", self.branch_name, "--no-edit"])
        if code != 0:
            _log.warning("E-L3 merge failed (trying cherry-pick): %s", out)
            code2, out2 = self._git(["cherry-pick", self.branch_name])
            if code2 != 0:
                raise IsolationError(
                    f"merge and cherry-pick both failed: {out2}"
                )

        self._remove_worktree(force=False)
        self._disposed = True
        _log.info("E-L3 worktree merged and removed: %s", self.branch_name)

    def discard(self):
        """Force-remove the worktree and delete the branch (complete discard).

        Safe to call multiple times. Never raises.
        """
        if not self._created or self._disposed:
            return
        try:
            self._remove_worktree(force=True)
        except Exception as e:
            _log.warning("E-L3 discard cleanup error: %s", e)
        self._disposed = True
        _log.info("E-L3 worktree discarded: %s", self.branch_name)

    def _remove_worktree(self, force=False):
        """Remove the worktree directory and delete the branch."""
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(self._worktree_path))
        code, out = self._git(args)
        if code != 0:
            _log.warning("git worktree remove failed: %s", out)
        # Delete the branch
        branch_flag = "-D" if force else "-d"
        self._git(["branch", branch_flag, self.branch_name])


# ── Convenience: isolated self-modify run ───────────────────────────────────


def _apply_accepts_path(fn):
    """True when apply_fn takes one required positional argument (worktree path)."""
    import inspect
    try:
        sig = inspect.signature(fn)
        required = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        return len(required) >= 1
    except (TypeError, ValueError):
        return False


def run_isolated_self_modify(description, apply_fn, probe_paths, repo_root,
                             git_runner=None, probe_runner=None, approval=None,
                             session_id=None, state_dir=None, run_id=None,
                             timeout=300, poll_interval=0.2):
    """Run a self-modification in an isolated worktree (E-L3 + E-4a).

    Creates worktree -> runs SelfModifyPipeline with cwd=worktree_path ->
    merges on success / discards on failure.

    Returns a result dict with 'ok', 'isolated', 'merge' keys.
    Never raises.
    """
    from api.dynamic.self_modify import SelfModifyPipeline

    iso = WorktreeIsolation(
        repo_root=repo_root, git_runner=git_runner,
        state_dir=state_dir, run_id=run_id
    )
    try:
        wt_path = iso.create()
    except IsolationError as e:
        return {"ok": False, "error": f"worktree creation failed: {e}",
                "isolated": False}

    pipeline = SelfModifyPipeline(
        git_runner=git_runner, probe_runner=probe_runner,
        approval=approval, session_id=session_id, cwd=str(wt_path)
    )

    # If apply_fn accepts a path argument, inject the isolated worktree path
    # so the modification targets the worktree (not the main repo).
    if _apply_accepts_path(apply_fn):
        effective_apply = lambda: apply_fn(wt_path)
    else:
        effective_apply = apply_fn

    result = pipeline.run(description, effective_apply, probe_paths,
                          timeout=timeout, poll_interval=poll_interval)

    if result.get("ok"):
        try:
            iso.merge_back()
            result["merge"] = "ok"
        except IsolationError as e:
            result["merge"] = f"failed: {e}"
    else:
        iso.discard()
        result["merge"] = "discarded"

    result["isolated"] = True
    result["worktree_path"] = str(wt_path)
    result["branch"] = iso.branch_name
    return result
