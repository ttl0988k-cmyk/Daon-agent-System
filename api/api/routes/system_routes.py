"""
System Routes — build identity & uptime introspection.

GET /api/system/build-info
    Returns build_id / build_time / git_commit so we can verify that a
    Self-Update cycle (rebuild + swap) actually replaced the running exe.

Read-only, no secrets. Auth follows the global default policy
(PUBLIC_PATHS is intentionally NOT extended).
"""
import os
import sys
import time

from api.helpers import j

_START_TIME = time.time()

# Bump this constant with every backend source change that goes through a
# rebuild cycle. It is compiled INTO server.exe, so "the value changed after
# a restart" proves the new binary was swapped in.
BUILD_ID = "selfupdate-demo-1"


def _best_effort_git_commit():
    try:
        import subprocess
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def handle_get_build_info(handler, parsed) -> bool:
    """GET /api/system/build-info — identity of the currently running process."""
    frozen = bool(getattr(sys, "frozen", False))
    try:
        if frozen:
            src = sys.executable
        else:
            src = os.path.abspath(__file__)
        build_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(src)))
        build_src = os.path.basename(src)
    except Exception:
        build_time = None
        build_src = None

    j(handler, {
        "ok": True,
        "build_id": BUILD_ID,
        "build_time": build_time,
        "build_source": build_src,
        "frozen": frozen,
        "git_commit": _best_effort_git_commit(),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
        "uptime_s": int(time.time() - _START_TIME),
    })
    return True
