"""
Harness limits configuration and artifact cleanup utilities.

Provides:
- _load_harness_limits(): loads config/harness_limits.yaml or returns safe defaults
- cleanup_harness_artifacts(): terminates zombie child processes associated with a JIT run
"""

from pathlib import Path

import yaml

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _load_harness_limits() -> dict:
    """Load limits from config/harness_limits.yaml or fallback to default values."""
    default_limits = {
        "node": {"max_retries": 3, "max_wall_time_seconds": 3600},
        "plan": {"max_attempts": 5},
        "mission": {"max_total_wall_time_seconds": 10800, "max_total_tokens": 2000000, "max_recovery_attempts": 5},
        "scoring": {"pass_threshold": 80, "max_score": 100},
    }
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "harness_limits.yaml"
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if data:
                for k1 in default_limits:
                    if k1 in data:
                        if isinstance(default_limits[k1], dict) and isinstance(data[k1], dict):
                            default_limits[k1].update(data[k1])
                        else:
                            default_limits[k1] = data[k1]
    except Exception as e:
        _log.warning("Failed to load harness_limits.yaml: %s", e)
    return default_limits


def cleanup_harness_artifacts(run_id: str) -> None:
    """Clean up residual child processes and lock files associated with this JIT run."""
    _log.info("Cleaning up resources for JIT Run ID: %s...", run_id)
    try:
        import psutil

        # Collect persistent MCP server PIDs to prevent killing them
        mcp_pids = set()
        try:
            from api.mcp_client import get_mcp_manager
            mcp_mgr = get_mcp_manager()
            for conn in mcp_mgr._connections.values():
                if conn.process and conn.process.pid:
                    mcp_pids.add(conn.process.pid)
            if mcp_pids:
                _log.info("Identified %d active persistent MCP server PID(s) to exclude from cleanup: %s", len(mcp_pids), mcp_pids)
        except Exception as e:
            _log.debug("Failed to retrieve active MCP server PIDs: %s", e)

        current_proc = psutil.Process()
        children = current_proc.children(recursive=True)
        to_kill = []
        for child in children:
            try:
                if child.pid in mcp_pids:
                    _log.info("Skipping persistent MCP server process PID %d (%s)", child.pid, child.name())
                    continue
                cmdline = child.cmdline()
                cmd_str = " ".join(cmdline).lower()
                if "python" in cmd_str or "node" in cmd_str or "playwright" in cmd_str:
                    _log.info("Terminating zombie child process PID %d (%s)", child.pid, child.name())
                    child.terminate()
                    to_kill.append(child)
            except Exception as e:
                _log.debug("Non-critical: failed to terminate child PID %d: %s", child.pid, e)

        if to_kill:
            gone, alive = psutil.wait_procs(to_kill, timeout=2)
            for child in alive:
                try:
                    _log.info("Force-killing zombie child process PID %d", child.pid)
                    child.kill()
                except Exception as e:
                    _log.debug("Non-critical: failed to kill child PID %d: %s", child.pid, e)
    except Exception as e:
        _log.warning("Failed to cleanup background processes: %s", e)
