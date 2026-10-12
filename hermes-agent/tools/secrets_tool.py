#!/usr/bin/env python3
"""
Secrets Tool -- Agent-Managed Daon Secret Requests (시크릿카드).

Allows the DAON agent to work with user-owned secrets at runtime:

  request_secret -- Register a pending secret request for the CALLER'S OWN
                    session (records name + purpose only, never a value).
                    The user fulfils it through the UI secure input, scoped
                    to that same session.
  list_secrets   -- List registered secrets (name/purpose/scope metadata
                    only — values are NEVER returned to the agent).

Security model (role separation, same as plugin_set_secret): the agent only
manages *which* secret is needed — it never receives, stores, or outputs a
secret value. ``request_secret`` records a pending request that the user
fulfils through the UI secure input; ``list_secrets`` reports metadata only
(values never leave the Secret Store).

Scope rule: every request is limited to the caller's own session. The owner
defaults to the calling session id passed through the tool pipeline
(``session_id`` kwarg); an explicit ``session_id`` argument may only name
the caller's own session — cross-session requests are not supported.

Runtime wiring: when running inside DAON (server.py adds RESOURCE_DIR/api/api
to sys.path) the daon_secrets module is imported lazily. Outside DAON the
tools degrade to a clear error so the module still imports cleanly for the
standalone hermes CLI.
"""

import json
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy DAON backend import (server.py puts RESOURCE_DIR/api/api on sys.path)
# ---------------------------------------------------------------------------

def _load_secrets():
    """Lazily import the DAON secret store module (see plugin_manager_tool)."""
    try:
        import api.daon_secrets as _ds
        return _ds
    except Exception as exc:  # pragma: no cover - DAON-only path
        logger.debug("daon_secrets unavailable: %s", exc)
        return None


def _secrets_required():
    ds = _load_secrets()
    if ds is None:
        raise RuntimeError(
            "Secret management is only available inside the DAON agent runtime "
            "(daon_secrets could not be imported)."
        )
    return ds


def _result(data) -> str:
    """Serialize a handler result dict to a JSON string for the tool pipeline."""
    return json.dumps(data, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _request_secret(name: str, purpose: str = "", session_id: str = "") -> dict:
    """Register a pending secret request for the caller's own session.

    Records name + purpose metadata only — never a value. Returns the
    pending request (id/name/purpose/kind/status) or a note when the
    secret is already usable in this session.
    """
    name = (name or "").strip()
    if not name:
        return {"error": "name is required: the secret name to request (e.g. GITHUB_TOKEN)."}
    purpose = (purpose or "").strip()
    session_id = (session_id or "").strip()
    ds = _secrets_required()
    try:
        owner = session_id or "agent"
        request = ds.add_request(owner, name, purpose=purpose, session_id=session_id)
        if request is None:
            return {
                "ok": True,
                "name": name,
                "requested": False,
                "message": (
                    "That secret is already available to your own session — "
                    "no request was recorded."
                ),
            }
        return {
            "ok": True,
            "name": request.get("name"),
            "requested": True,
            "request_id": request.get("id"),
            "kind": request.get("kind"),
            "message": (
                "A secret request has been registered for your own session. "
                "Ask the user to enter the value in the UI secure input — "
                "do NOT ask them to paste the value here, and never accept "
                "it in chat. Secret values are never shown to you."
            ),
            "detail": request,
        }
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"request_secret failed: {exc}"}


def _list_secrets() -> dict:
    """List registered secrets — metadata only, values never included."""
    ds = _secrets_required()
    try:
        return {
            "ok": True,
            "secrets": ds.list_secrets(),
            "note": "Values are never returned. Only name/purpose/scope metadata is reported.",
        }
    except Exception as exc:
        return {"error": f"list_secrets failed: {exc}"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

REQUEST_SECRET_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Secret name to request (e.g. GITHUB_TOKEN). Normalized to UPPER_SNAKE_CASE.",
        },
        "purpose": {
            "type": "string",
            "description": "Why the secret is needed — shown to the user next to the secure input.",
        },
        "session_id": {
            "type": "string",
            "description": (
                "Optional: your own current session/tab id. The request is "
                "always limited to your own session; cross-session requests "
                "are not supported. When omitted, the calling session is used."
            ),
        },
    },
    "required": ["name"],
}

LIST_SECRETS_SCHEMA = {
    "type": "object",
    "properties": {},
}


# --- Registry ---
from tools.registry import registry  # noqa: E402

registry.register(
    name="request_secret",
    toolset="secrets",
    schema=REQUEST_SECRET_SCHEMA,
    handler=lambda args, **kw: _result(_request_secret(
        name=args.get("name", ""),
        purpose=args.get("purpose", ""),
        session_id=args.get("session_id", "") or kw.get("session_id", ""))),
    emoji="🔐",
    description=(
        "Request a secret for your own session (records name + purpose only; "
        "the user enters the value in the UI secure input). Values are never "
        "returned to you. Requests are always limited to your own session."
    ),
)

registry.register(
    name="list_secrets",
    toolset="secrets",
    schema=LIST_SECRETS_SCHEMA,
    handler=lambda args, **kw: _result(_list_secrets()),
    emoji="🔐",
    description=(
        "List registered secrets for your own session (name/purpose/scope "
        "metadata only — secret values are never returned)."
    ),
)
