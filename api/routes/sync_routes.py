"""
Sync Routes — API endpoints for controlling the file-sync watcher.

POST /api/sync/start   — Start the file watcher for a workspace
POST /api/sync/stop    — Stop the file watcher
GET  /api/sync/status  — Get watcher status

Also provides:
POST /api/sync/hook/install   — Install git hooks (post-commit, post-merge)
POST /api/sync/hook/uninstall — Remove installed git hooks
"""

from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def handle_post_sync_start(handler, body: dict) -> bool:
    """POST /api/sync/start — Start the file sync watcher.

    Body: {
        "workspace": "/path/to/project",
        "debounce_seconds": 5.0  (optional, default 5.0)
    }

    Response: { "ok": true, "status": {...} } or { "ok": false, "error": "..." }
    """
    from api.sync_watcher import get_sync_watcher, on_file_change

    workspace = body.get("workspace", "").strip()
    if not workspace:
        handler.send_json({"ok": False, "error": "workspace is required"}, 400)
        return True

    debounce = float(body.get("debounce_seconds", 5.0))
    debounce = max(1.0, min(debounce, 60.0))  # clamp 1–60s

    watcher = get_sync_watcher()
    result = watcher.start(Path(workspace), on_file_change, debounce_seconds=debounce)
    status_code = 200 if result.get("ok") else 400
    handler.send_json(result, status_code)
    return True


def handle_post_sync_stop(handler, body: dict) -> bool:
    """POST /api/sync/stop — Stop the file sync watcher.

    Body: {} (no parameters needed)

    Response: { "ok": true } or { "ok": false, "error": "..." }
    """
    from api.sync_watcher import get_sync_watcher

    watcher = get_sync_watcher()
    result = watcher.stop()
    status_code = 200 if result.get("ok") else 400
    handler.send_json(result, status_code)
    return True


def handle_get_sync_status(handler, parsed) -> bool:
    """GET /api/sync/status — Get the current watcher status.

    Response: { "running": true, "workspace": "...", "started_at": "...",
                 "last_sync": "...", "files_watched": 45 }
    """
    from api.sync_watcher import get_sync_watcher

    watcher = get_sync_watcher()
    handler.send_json(watcher.status())
    return True


def handle_post_sync_hook_install(handler, body: dict) -> bool:
    """POST /api/sync/hook/install — Install git hooks for auto-sync.

    Body: {
        "workspace": "/path/to/project",
        "hooks": ["post-commit", "post-merge"]  (optional, default: both)
    }

    Installs .git/hooks/post-commit and .git/hooks/post-merge that call:
        python -c "from api.sync_watcher import on_file_change; on_file_change(...)"

    Response: { "ok": true, "installed": ["post-commit", "post-merge"] }
    """
    workspace = body.get("workspace", "").strip()
    if not workspace:
        handler.send_json({"ok": False, "error": "workspace is required"}, 400)
        return True

    ws_path = Path(workspace)
    git_hooks_dir = ws_path / ".git" / "hooks"
    if not git_hooks_dir.exists():
        handler.send_json({"ok": False, "error": "No .git/hooks directory found. Is this a git repo?"}, 400)
        return True

    hooks = body.get("hooks", ["post-commit", "post-merge"])
    if isinstance(hooks, str):
        hooks = [hooks]

    # Hook script template
    hook_template = '''#!/bin/sh
# Daon Auto-Sync Hook: regenerates AI setup files on {hook_event}
# Installed by Daon Agent System

DAON_ROOT="{daon_root}"
WORKSPACE="{workspace}"

if [ -d "$DAON_ROOT" ]; then
    cd "$DAON_ROOT" && python -c "
import sys
sys.path.insert(0, '.')
from pathlib import Path
from api.sync_watcher import on_file_change
on_file_change(Path(r'$WORKSPACE'))
" 2>&1 | grep -v "^$" || true
fi
'''

    daon_root = Path(__file__).parent.parent.parent.resolve()
    installed = []

    for hook_name in hooks:
        hook_path = git_hooks_dir / hook_name
        hook_content = hook_template.format(
            hook_event=hook_name,
            daon_root=str(daon_root).replace("\\", "/"),
            workspace=str(ws_path).replace("\\", "/"),
        )

        try:
            hook_path.write_text(hook_content, encoding="utf-8")
            # Make executable (chmod +x)
            if hasattr(hook_path, "chmod"):
                hook_path.chmod(0o755)
            installed.append(hook_name)
            logger.info("Git hook installed: %s", hook_path)
        except Exception as e:
            logger.error("Failed to install git hook %s: %s", hook_name, e)

    if not installed:
        handler.send_json({"ok": False, "error": "Failed to install any hooks"}, 500)
        return True

    handler.send_json({"ok": True, "installed": installed})
    return True


def handle_post_sync_hook_uninstall(handler, body: dict) -> bool:
    """POST /api/sync/hook/uninstall — Remove installed git hooks.

    Body: {
        "workspace": "/path/to/project",
        "hooks": ["post-commit"]  (optional, default: remove all known hooks)
    }

    Response: { "ok": true, "removed": ["post-commit"] }
    """
    workspace = body.get("workspace", "").strip()
    if not workspace:
        handler.send_json({"ok": False, "error": "workspace is required"}, 400)
        return True

    ws_path = Path(workspace)
    git_hooks_dir = ws_path / ".git" / "hooks"
    if not git_hooks_dir.exists():
        handler.send_json({"ok": False, "error": "No .git/hooks directory found"}, 400)
        return True

    known_hooks = ["post-commit", "post-merge"]
    hooks = body.get("hooks", known_hooks)
    if isinstance(hooks, str):
        hooks = [hooks]

    removed = []
    for hook_name in hooks:
        hook_path = git_hooks_dir / hook_name
        if not hook_path.exists():
            continue
        try:
            content = hook_path.read_text(encoding="utf-8")
            if "Daon Auto-Sync Hook" in content:
                hook_path.unlink()
                removed.append(hook_name)
                logger.info("Git hook removed: %s", hook_path)
        except Exception as e:
            logger.error("Failed to remove git hook %s: %s", hook_name, e)

    handler.send_json({"ok": True, "removed": removed})
    return True
