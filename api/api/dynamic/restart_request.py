"""
Gap E-3: Server-side restart request (wire protocol to the Electron supervisor).

The server NEVER restarts itself (watcher/watched separation). It only records
a restart request file in STATE_DIR; the Electron main process (the watcher)
polls for the file and performs:

    kill server -> respawn -> health check -> on failure git rollback + retry

Guard (plan risk 3): a restart is refused while dynamic harness jobs are still
active (running / clarifying / awaiting_approval).
"""
import json
import os
import time
from pathlib import Path

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

REQUEST_FILE_NAME = "restart-request.json"
REQUEST_TYPE = "self_modify_restart"
REQUEST_VERSION = 1

# Job statuses that block a restart (mirrors dynamic_jobs._CANCELLABLE_STATUSES).
_ACTIVE_STATUSES = ("running", "clarifying", "awaiting_approval")


class RestartRequestError(Exception):
    """Restart request refused (busy jobs, missing reason, write failure)."""


def _resolve_state_dir(state_dir=None):
    if state_dir is not None:
        return Path(state_dir)
    from api.config import STATE_DIR
    return STATE_DIR


def get_restart_request_path(state_dir=None):
    """Return the path of the restart request file."""
    return _resolve_state_dir(state_dir) / REQUEST_FILE_NAME


def count_active_jobs(jobs=None):
    """Count harness jobs that are still active.

    ``jobs`` is injectable for probes; by default the live dynamic_jobs
    registry is read under its lock.
    """
    if jobs is None:
        from api import dynamic_jobs as dj
        with dj._DYNAMIC_JOBS_LOCK:
            jobs = [dict(j) for j in dj._DYNAMIC_JOBS.values()]
    active = [j for j in jobs if j.get("status") in _ACTIVE_STATUSES]
    return len(active)


def request_restart(reason, checkpoint_ref=None, rebuild=False, state_dir=None, jobs=None):
    """Record a restart request. Returns the payload dict.

    reason: human-readable summary of why the restart is needed (required).
    checkpoint_ref: git ref the supervisor can roll back to if the restarted
                    server fails its health check (optional).
    rebuild: True when backend Python source changed and the supervisor must
             rebuild + swap server.exe while it is down (optional, default False).
    Raises RestartRequestError when active jobs exist or reason is empty.
    """
    reason = str(reason or "").strip()
    if not reason:
        raise RestartRequestError("reason is required for a restart request")
    active = count_active_jobs(jobs)
    if active > 0:
        raise RestartRequestError(
            f"restart refused: {active} active job(s) still running"
        )
    payload = {
        "type": REQUEST_TYPE,
        "version": REQUEST_VERSION,
        "reason": reason,
        "checkpoint_ref": checkpoint_ref,
        "rebuild": bool(rebuild),
        "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "server_pid": os.getpid(),
    }
    path = get_restart_request_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same filesystem
    except OSError as e:
        raise RestartRequestError(f"cannot write restart request: {e}")
    _log.info("self-modify restart requested: %s (checkpoint=%s)",
              reason, checkpoint_ref)
    return payload


def consume_restart_request(state_dir=None):
    """Read and remove the restart request file.

    Returns the payload dict, or None when the file is absent or corrupt
    (a corrupt file is removed so it cannot block future requests).
    """
    path = get_restart_request_path(state_dir)
    if not path.exists():
        return None
    data = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = None
    except (OSError, ValueError):
        data = None
    try:
        path.unlink()
    except OSError:
        pass
    return data
