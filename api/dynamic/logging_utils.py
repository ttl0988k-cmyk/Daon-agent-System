"""
Shared logging utilities for the api.dynamic package.

Provides a consistent logger factory that all dynamic modules use,
replacing ad-hoc print() calls with structured logging.
"""

import logging
import sys

# Module-level loggers keyed by __name__
_loggers: dict[str, logging.Logger] = {}

# Track if we've already configured the root logger
_root_configured = False


def _ensure_root_logger():
    """Ensure the root logger has a handler that outputs to stdout."""
    global _root_configured
    if _root_configured:
        return
    
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    
    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """Return (or create) a logger for the given module name.

    Loggers are configured once on first access with a uniform format:
        [TAG] message
    and emit to stdout at INFO level.
    """
    # Ensure root logger is configured first
    _ensure_root_logger()
    
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        # Compact format matching the legacy print() style: [TAG] msg
        handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False  # don't bubble to root logger

    _loggers[name] = logger
    return logger
