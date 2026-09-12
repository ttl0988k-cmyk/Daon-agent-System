# -*- coding: utf-8 -*-
"""
tests/test_self_build_guard.py - DAON self-build guard regression
=================================================================

Guards against the 2026-09-12 incident: the agent ran `npx electron-builder`
during a self-modification, producing build artifacts that never reached the
installed / portable copies.

The fix is a code guard in the agent's own terminal tool
(hermes-agent/tools/terminal_tool.py -> _self_build_guard). This suite verifies:

  1. Source contract  - the guard functions exist and terminal_tool invokes the
                        guard with the command's effective cwd.
  2. Blocked cases    - electron-builder / PyInstaller / npm run build targeting
                        DAON are refused (guard returns a message).
  3. Allowed cases    - --help/--version, npm test, and unrelated commands pass.
  4. Integration      - terminal_tool() returns status "blocked" for a DAON
                        electron-builder invocation.

The guard functions are extracted from source and executed standalone so this
suite has no dependency on the hermes-agent runtime environment.
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMINAL_TOOL = ROOT / "hermes-agent" / "tools" / "terminal_tool.py"

_failures = []
_checks = 0


def ok(cond, msg):
    global _checks
    _checks += 1
    if not cond:
        _failures.append(msg)
        print(f"  FAIL: {msg}")
    return cond


def _load_guard_namespace():
    """Extract the guard block from terminal_tool.py and exec it standalone."""
    src = TERMINAL_TOOL.read_text(encoding="utf-8")
    start = src.index("def _looks_like_help_or_version_command")
    end = src.index("def terminal_tool(")
    block = src[start:end]
    ns = {}
    exec("import re\nimport os\n" + block, ns)  # noqa: S102 - trusted local source
    return src, ns


def group1_source_contract(src, ns):
    """Guard functions exist and terminal_tool wires them in."""
    print("\n[1] Source contract")
    ok(callable(ns.get("_self_build_guard")), "_self_build_guard must be defined")
    ok(callable(ns.get("_is_daon_workspace")), "_is_daon_workspace must be defined")
    ok("_DAON_SELF_MODIFY_MARKERS" in ns, "DAON self-modify markers must be defined")
    # terminal_tool must pass the effective cwd so an omitted workdir cannot
    # bypass the guard from inside the DAON repo.
    ok(
        "_self_build_guard(command, workdir or cwd)" in src,
        "terminal_tool must call _self_build_guard(command, workdir or cwd)",
    )
    # Guard must run before the foreground/background nudge returns.
    gi = src.index("build_guard = _self_build_guard(")
    bi = src.index("_foreground_background_guidance(command)")
    ok(gi < bi, "self-build guard must run before the background-mode nudge")
    # No non-ASCII in the guard's returned text (cp949 console policy).
    ok(
        all(ord(c) < 128 for c in ns["_self_build_guard"]("npx electron-builder")),
        "guard message must be ASCII-only (cp949 console safety)",
    )


def group2_blocked(ns):
    """DAON self-builds must be refused."""
    print("\n[2] Blocked cases")
    guard = ns["_self_build_guard"]
    daon_cwd = str(ROOT)
    cases = [
        ("bare electron-builder (implicit DAON cwd)", "npx electron-builder --win --x64", None),
        ("electron-builder with explicit DAON cwd", "npx electron-builder", daon_cwd),
        ("electron-builder --config yml", "npx electron-builder --config electron-builder.yml", None),
        ("PyInstaller spec build", "python -m PyInstaller daon-server.spec --noconfirm", None),
        ("PyInstaller legacy spec build", "python -m PyInstaller daon-server.spec --noconfirm", daon_cwd),
        ("bare pyinstaller.exe", "pyinstaller daon-server.spec", None),
        ("npm run build in DAON cwd", "npm run build", daon_cwd),
        ("npm run build (implicit DAON cwd)", "npm run build", None),
        ("yarn build in DAON cwd", "yarn build", daon_cwd),
        ("electron-builder with win-unpacked marker", "npx electron-builder --project dist\\win-unpacked", None),
        ("_sync_build.py + PyInstaller", "python _sync_build.py && python -m PyInstaller daon-server.spec", None),
    ]
    for label, command, cwd in cases:
        result = guard(command, cwd)
        ok(isinstance(result, str) and "Blocked" in result,
           f"must block [{label}]: {command!r} (cwd={cwd!r})")


def group3_allowed(ns):
    """Informational and unrelated commands must pass."""
    print("\n[3] Allowed cases")
    import tempfile

    guard = ns["_self_build_guard"]
    # A real, existing directory that is NOT the DAON repo (so _is_daon_workspace
    # returns False). ROOT/docs would still match the DAON path fragment.
    elsewhere = tempfile.gettempdir()
    ok(not ns["_is_daon_workspace"](elsewhere),
       f"temp dir must not be detected as DAON workspace: {elsewhere}")
    cases = [
        ("electron-builder --help", "npx electron-builder --help", None),
        ("electron-builder --version", "npx electron-builder --version", None),
        ("pyinstaller --help", "python -m PyInstaller --help", None),
        ("npm test", "npm test", None),
        ("git status", "git status", None),
        ("run unrelated project build outside DAON", "npm run build", elsewhere),
        ("empty command", "", None),
        ("non-string command", None, None),
    ]
    for label, command, cwd in cases:
        result = guard(command, cwd)
        # Allowed == None, or an advisory "Note:" for an ambiguous bare build.
        ok(result is None or result.startswith("Note:"),
           f"must allow [{label}]: {command!r} -> {result!r}")


def group4_integration():
    """terminal_tool() itself must surface the block, not execute."""
    print("\n[4] terminal_tool integration")
    sys.path.insert(0, str(ROOT / "hermes-agent"))
    try:
        import json
        import tools.terminal_tool as tt
    except Exception as e:  # pragma: no cover - environment fallback
        ok(False, f"hermes-agent terminal_tool import failed: {e}")
        return
    out = tt.terminal_tool(command="npx electron-builder --win --x64")
    data = json.loads(out)
    ok(data.get("status") == "blocked", f"status must be 'blocked', got {data.get('status')!r}")
    ok("supervisor" in (data.get("error") or ""), "error must point to the supervisor path")
    ok(bool(data.get("recovery")), "blocked response must include supervisor recovery guidance")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("================================================================")
    print("   DAON SELF-BUILD GUARD REGRESSION (test_self_build_guard)     ")
    print("================================================================")

    src, ns = _load_guard_namespace()
    group1_source_contract(src, ns)
    group2_blocked(ns)
    group3_allowed(ns)
    group4_integration()

    print("\n================================================================")
    if _failures:
        print(f"SELF-BUILD GUARD: {len(_failures)}/{_checks} CHECKS FAILED")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"SELF-BUILD GUARD: ALL {_checks} CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
