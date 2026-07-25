"""
Hermes Web UI — Route handlers for GET and POST endpoints.
Extracted from server.py (Sprint 11) so server.py is a thin shell.

Phase 2 refactor: routes split into api/routes/ sub-modules.
This file is now a thin re-export shim for backward compatibility.
"""
from api.routes import handle_get, handle_post  # noqa: F401
