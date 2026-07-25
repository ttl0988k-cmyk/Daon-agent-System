"""
DEPRECATED — Use api.workspace instead.

This module is kept for backward compatibility during migration.
All workspace operations should use api.workspace functions.
"""
import warnings

warnings.warn(
    "api.workspace_manager is deprecated. Use api.workspace instead.",
    DeprecationWarning,
    stacklevel=2,
)

from api.workspace import (  # noqa: F401, F811
    safe_resolve_ws as safe_resolve,
    list_dir,
    read_file_content,
)
