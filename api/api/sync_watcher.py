"""
File Sync Watcher — watchdog-based file monitor for auto-regenerating AI setup files.

When enabled, watches the workspace for changes to source files (.py, .js, .ts, .json,
package.json, requirements.txt, etc.) and triggers regeneration of AGENTS.md, CLAUDE.md,
and other AI configuration files after a debounce period.
"""

import threading
import time
import logging
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Watchdog import (optional dependency) ──
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    logger.warning("watchdog not installed — file sync watcher unavailable. "
                   "Install with: pip install watchdog")


# ── File patterns to watch ──
WATCH_PATTERNS = [
    "*.py", "*.js", "*.ts", "*.tsx", "*.jsx",
    "*.json", "*.yaml", "*.yml", "*.toml",
    "*.md",  # also watch .md so we don't loop on our own output
    "*.cfg", "*.ini",
    "package.json", "requirements.txt", "pyproject.toml",
    "Dockerfile", "docker-compose*.yml",
    "Makefile", "CMakeLists.txt",
    "*.cs", "*.java", "*.go", "*.rs", "*.rb", "*.php",
]

# Patterns to ignore (don't trigger regeneration)
IGNORE_PATTERNS = [
    "AGENTS.md", "CLAUDE.md", ".cursor/rules", ".github/copilot-instructions.md",
    "CLAUDE.local.md", "AGENTS.local.md",
    ".git/", "node_modules/", "__pycache__/", ".venv/", "venv/",
    "dist/", "build/", ".next/", ".turbo/", "target/",
]


class _SyncEventHandler(FileSystemEventHandler):
    """Watchdog event handler that debounces and triggers regeneration."""

    def __init__(self, workspace: Path, callback: Callable, debounce_seconds: float = 5.0,
                 ignore_dirs: Optional[list] = None):
        super().__init__()
        self.workspace = workspace
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.ignore_dirs = set(ignore_dirs or [])
        self._last_event_time: float = 0.0
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._event_count: int = 0

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        self._handle_event(event.src_path)

    def on_created(self, event):
        self._handle_event(event.src_path)

    def on_deleted(self, event):
        self._handle_event(event.src_path)

    def on_moved(self, event):
        self._handle_event(event.dest_path)

    def _handle_event(self, path_str: str):
        """Debounced handler — wait for quiet period before triggering callback."""
        try:
            rel_path = Path(path_str).relative_to(self.workspace)
        except ValueError:
            return  # not under workspace

        # Check ignore patterns
        rel_str = str(rel_path).replace("\\", "/")
        for ip in IGNORE_PATTERNS:
            if rel_str == ip or rel_str.startswith(ip.rstrip("/") + "/"):
                return

        # Check if file matches watch patterns
        file_name = rel_path.name
        matched = False
        for pat in WATCH_PATTERNS:
            if Path(file_name).match(pat):
                matched = True
                break
        if not matched:
            return

        with self._lock:
            self._event_count += 1
            now = time.time()

            # Cancel existing timer and schedule new one
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(self.debounce_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        """Called after debounce period — execute the regeneration callback."""
        with self._lock:
            count = self._event_count
            self._event_count = 0
            self._timer = None

        logger.info("Sync watcher: %d file change(s) detected, triggering regeneration", count)
        try:
            self.callback(self.workspace)
        except Exception:
            logger.exception("Sync watcher callback failed")


class SyncWatcher:
    """Manages a single watchdog observer for a workspace."""

    def __init__(self):
        self._observer: Optional[Observer] = None
        self._thread: Optional[threading.Thread] = None
        self._handler: Optional[_SyncEventHandler] = None
        self._workspace: Optional[Path] = None
        self._running: bool = False
        self._started_at: Optional[datetime] = None
        self._last_sync: Optional[datetime] = None
        self._files_watched: int = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        """Return current watcher status."""
        return {
            "running": self._running,
            "workspace": str(self._workspace) if self._workspace else None,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "files_watched": self._files_watched,
            "watchdog_available": HAS_WATCHDOG,
        }

    def start(self, workspace: Path, callback: Callable, debounce_seconds: float = 5.0) -> dict:
        """Start watching a workspace. Returns status dict."""
        if not HAS_WATCHDOG:
            return {"ok": False, "error": "watchdog library not installed. Run: pip install watchdog"}

        with self._lock:
            if self._running:
                return {"ok": False, "error": "Watcher already running for " + str(self._workspace)}

            if not workspace.exists():
                return {"ok": False, "error": f"Workspace does not exist: {workspace}"}

            self._workspace = workspace

            # Count files being watched
            self._files_watched = self._count_watched_files(workspace)

            # Wrap callback to update last_sync timestamp
            def _wrapped_callback(ws: Path):
                with self._lock:
                    self._last_sync = datetime.now(timezone.utc)
                callback(ws)

            self._handler = _SyncEventHandler(
                workspace=workspace,
                callback=_wrapped_callback,
                debounce_seconds=debounce_seconds,
            )

            self._observer = Observer()
            self._observer.schedule(self._handler, str(workspace), recursive=True)
            self._observer.start()

            self._running = True
            self._started_at = datetime.now(timezone.utc)

            logger.info("Sync watcher started for %s (%d files watched)", workspace, self._files_watched)

        return {"ok": True, "status": self.status()}

    def stop(self) -> dict:
        """Stop the watcher. Returns status dict."""
        with self._lock:
            if not self._running:
                return {"ok": False, "error": "Watcher is not running"}

            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
                self._observer = None

            self._handler = None
            self._running = False
            self._workspace = None
            self._started_at = None
            self._files_watched = 0

            logger.info("Sync watcher stopped")

        return {"ok": True}

    def _count_watched_files(self, workspace: Path) -> int:
        """Count files matching WATCH_PATTERNS in workspace."""
        count = 0
        try:
            for pat in WATCH_PATTERNS:
                for f in workspace.rglob(pat):
                    # Skip ignored directories
                    rel = str(f.relative_to(workspace)).replace("\\", "/")
                    skip = False
                    for ip in IGNORE_PATTERNS:
                        if ip.endswith("/") and rel.startswith(ip):
                            skip = True
                            break
                    if not skip:
                        count += 1
        except Exception:
            pass
        return count


# ── Global singleton ──
_watcher: Optional[SyncWatcher] = None
_watcher_lock = threading.Lock()


def get_sync_watcher() -> SyncWatcher:
    """Get or create the global SyncWatcher singleton."""
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = SyncWatcher()
        return _watcher


def on_file_change(workspace: Path):
    """Default callback: regenerate AI setup files for the workspace."""
    from api.setup_generator import generate_setup_files
    logger.info("Auto-sync: regenerating AI setup files for %s", workspace)

    # Read settings to know which file types to generate
    try:
        import json
        settings_path = Path(__file__).parent.parent / "data" / "settings.json"
        settings = {}
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

        file_types = settings.get("sync_file_types", ["agents.md", "claude.md"])
        generate_setup_files(
            workspace_path=str(workspace),
            file_types=file_types,
            overwrite=True,
        )
        logger.info("Auto-sync: regeneration complete for %s", workspace)
    except Exception:
        logger.exception("Auto-sync: regeneration failed")
