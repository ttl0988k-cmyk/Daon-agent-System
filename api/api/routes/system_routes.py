# -*- coding: utf-8 -*-
"""
System Routes — build identity & uptime introspection & evolution status.

GET /api/system/build-info
    Returns build_id / build_time / git_commit so we can verify that a
    Self-Update cycle (rebuild + swap) actually replaced the running exe.

GET /api/system/last-restart
    Returns recent self-evolution / restart event data from EvolutionLedger.

POST /api/system/last-restart/ack
    Marks the recent restart notification as acknowledged.

Read-only, no secrets. Auth follows the global default policy.
"""
import os
import sys
import time

from api.helpers import j

_START_TIME = time.time()

# Bump this constant with every backend source change that goes through a
# rebuild cycle. It is compiled INTO server.exe, so "the value changed after
# a restart" proves the new binary was swapped in.
BUILD_ID = "selfupdate-demo-3"


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


def handle_get_last_restart(handler, parsed) -> bool:
    """GET /api/system/last-restart — return recent evolution/restart status."""
    try:
        from api.dynamic.evolution_ledger import get_last_restart
        last = get_last_restart()
        return j(handler, {
            "ok": True,
            "last_restart": last,
        })
    except Exception as e:
        return j(handler, {
            "ok": False,
            "error": str(e),
            "last_restart": None,
        })


def handle_post_last_restart_ack(handler, parsed) -> bool:
    """POST /api/system/last-restart/ack — acknowledge the last restart notice."""
    try:
        from api.dynamic.evolution_ledger import acknowledge_last_restart
        acked = acknowledge_last_restart()
        return j(handler, {
            "ok": True,
            "acknowledged": acked,
        })
    except Exception as e:
        return j(handler, {
            "ok": False,
            "error": str(e),
        })
