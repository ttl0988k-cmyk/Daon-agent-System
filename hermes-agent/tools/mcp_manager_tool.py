#!/usr/bin/env python3
"""
MCP Manager Tool -- Agent-Managed MCP Server Lifecycle (Gap E-0a)

Allows the DAON agent to manage MCP servers at runtime, closing the gap where
MCP registration was only reachable through the HTTP API (UI panel) and not
exposed as an agent-callable tool:

  mcp_manage action=list         -- List registered MCP servers (with connection
                                    state and tool counts) plus built-in presets.
  mcp_manage action=add          -- Register a custom MCP server (stdio command
                                    or HTTP url) and optionally connect to it.
  mcp_manage action=add_preset   -- Register a server from a built-in preset
                                    (filesystem / github / playwright / memory /
                                    sequential_thinking).
  mcp_manage action=remove       -- Disconnect and remove a registered server.
  mcp_manage action=connect      -- Connect (or reconnect) to a registered server.
  mcp_manage action=disconnect   -- Disconnect a server without removing it.
  mcp_manage action=tools        -- List the tools offered by one server.

This is the E-0a piece of the self-evolution plan (docs/SELF_EVOLUTION_PLAN.md):
the agent can now wire new capabilities into itself without user intervention
in the UI, which feeds the E-L1 "missing capability -> build/bind it" loop.

Runtime wiring: when running inside DAON (server.py adds RESOURCE_DIR/api/api
to sys.path) the api.mcp_client module is imported lazily and every call goes
through the same MCPManager singleton used by the REST routes, so state stays
consistent with the UI panel. Outside DAON the tool degrades to a clear error
so the module still imports cleanly for the standalone hermes CLI.

Secrets: auth_token values are accepted for HTTP servers that need them, but
they are NEVER echoed back in tool results (sanitized before serialization).
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_SERVER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_SENSITIVE_KEYS = {"auth_token", "token", "api_key", "apikey"}


# ---------------------------------------------------------------------------
# Lazy DAON backend imports (server.py puts RESOURCE_DIR/api/api on sys.path)
# ---------------------------------------------------------------------------

def _load_mcp_client():
    """Lazily import the DAON MCP client module (manager + presets).

    Inside DAON, server.py puts RESOURCE_DIR/api/api on sys.path before the
    top-level api/, so the ``api`` package resolves to the internal api/api
    package (matching every other DAON module, e.g. api/api/routes/mcp_routes.py).
    """
    try:
        import api.mcp_client as _mc
        return _mc
    except Exception as exc:  # pragma: no cover - DAON-only path
        logger.debug("mcp_client unavailable: %s", exc)
        return None


def _mcp_required():
    mc = _load_mcp_client()
    if mc is None:
        raise RuntimeError(
            "MCP management is only available inside the DAON agent runtime "
            "(api.mcp_client could not be imported)."
        )
    return mc


def _result(data) -> str:
    """Serialize a handler result dict to a JSON string for the tool pipeline."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _validate_server_id(server_id: str) -> Optional[str]:
    if not server_id or not _SERVER_ID_RE.match(server_id):
        return (
            f"Invalid server_id {server_id!r}. Must match [a-zA-Z0-9_-] "
            "and be 1-64 chars."
        )
    return None


def _sanitize(obj):
    """Recursively strip sensitive keys (auth tokens, api keys) from a payload."""
    if isinstance(obj, dict):
        return {
            k: ("***" if k in _SENSITIVE_KEYS and v else v)
            for k, v in ((k, _sanitize(v)) for k, v in obj.items())
        }
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _server_summary(conn_dict: dict) -> dict:
    """Compact server view for list/tools results."""
    tools = conn_dict.get("tools") or []
    return {
        "server_id": conn_dict.get("server_id", ""),
        "label": conn_dict.get("label", ""),
        "transport": conn_dict.get("transport", "stdio"),
        "connected": bool(conn_dict.get("connected")),
        "error": conn_dict.get("error", "") or "",
        "tool_count": len(tools),
        "tools": [t.get("name", "") for t in tools if isinstance(t, dict)],
    }


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _mcp_list() -> dict:
    """List registered MCP servers and available presets."""
    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    servers = [_server_summary(s) for s in mgr.list_servers()]
    presets = {
        pid: {"label": p.get("label", pid), "description": p.get("description", "")}
        for pid, p in getattr(mc, "MCP_PRESETS", {}).items()
    }
    return {"ok": True, "servers": servers, "presets": presets}


def _mcp_add(server_id: str, command: str = "", args: list = None,
             env: dict = None, cwd: str = "", label: str = "",
             transport: str = "stdio", url: str = "", auth_token: str = "",
             auto_connect: bool = True) -> dict:
    """Register a custom MCP server (stdio command or HTTP url)."""
    err = _validate_server_id(server_id)
    if err:
        return {"error": err}
    if transport not in ("stdio", "http"):
        return {"error": f"Invalid transport {transport!r}. Use 'stdio' or 'http'."}
    if transport == "stdio" and not command:
        return {"error": "command is required for stdio transport."}
    if transport == "http" and not url:
        return {"error": "url is required for http transport."}

    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    try:
        result = mgr.add_server(
            server_id=server_id,
            command=command or url,
            args=list(args or []),
            env=dict(env or {}),
            cwd=cwd or ".",
            label=label or server_id,
            transport=transport,
            url=url,
            auth_token=auth_token or "",
            auto_connect=bool(auto_connect),
        )
    except Exception as exc:
        logger.exception("mcp_manage add failed")
        return {"error": f"add_server failed: {exc}"}

    result = _sanitize(result)
    if result.get("ok") and "server" in result:
        result["server"] = _server_summary(result["server"])
    return result


def _mcp_add_preset(preset_id: str, server_id: str = "", label: str = "",
                    env: dict = None, cwd: str = "",
                    auto_connect: bool = True) -> dict:
    """Register an MCP server from a built-in preset."""
    mc = _mcp_required()
    presets = getattr(mc, "MCP_PRESETS", {})
    if preset_id not in presets:
        return {
            "error": f"Unknown preset {preset_id!r}. "
                     f"Available: {', '.join(sorted(presets.keys()))}"
        }
    preset = presets[preset_id]
    sid = server_id or preset_id
    err = _validate_server_id(sid)
    if err:
        return {"error": err}

    mgr = mc.get_mcp_manager()
    try:
        result = mgr.add_server(
            server_id=sid,
            command=preset["command"],
            args=list(preset.get("args", [])),
            env=dict(env or {}),
            cwd=cwd or ".",
            label=label or preset.get("label", sid),
            auto_connect=bool(auto_connect),
        )
    except Exception as exc:
        logger.exception("mcp_manage add_preset failed")
        return {"error": f"add_server failed: {exc}"}

    result = _sanitize(result)
    if result.get("ok") and "server" in result:
        result["server"] = _server_summary(result["server"])
    return result


def _mcp_remove(server_id: str) -> dict:
    """Disconnect and remove a registered MCP server."""
    err = _validate_server_id(server_id)
    if err:
        return {"error": err}
    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    try:
        return _sanitize(mgr.remove_server(server_id))
    except Exception as exc:
        logger.exception("mcp_manage remove failed")
        return {"error": f"remove_server failed: {exc}"}


def _mcp_connect(server_id: str) -> dict:
    """Connect (or reconnect) to a registered MCP server. Synchronous: the
    call returns once the server answered the initialize/tools-list handshake
    (internal timeouts apply), so the agent can immediately list its tools."""
    err = _validate_server_id(server_id)
    if err:
        return {"error": err}
    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    if server_id not in getattr(mgr, "_connections", {}):
        return {"error": f"Server {server_id!r} not found. Use action=list first."}
    try:
        result = mgr.connect_server(server_id)
    except Exception as exc:
        logger.exception("mcp_manage connect failed")
        return {"error": f"connect_server failed: {exc}"}
    result = _sanitize(result)
    if result.get("ok") and "server" in result:
        result["server"] = _server_summary(result["server"])
    return result


def _mcp_disconnect(server_id: str) -> dict:
    """Disconnect a server without removing it from the registry."""
    err = _validate_server_id(server_id)
    if err:
        return {"error": err}
    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    try:
        result = mgr.disconnect_server(server_id)
    except Exception as exc:
        logger.exception("mcp_manage disconnect failed")
        return {"error": f"disconnect_server failed: {exc}"}
    result = _sanitize(result)
    if result.get("ok") and "server" in result:
        result["server"] = _server_summary(result["server"])
    return result


def _mcp_tools(server_id: str) -> dict:
    """List the tools offered by one registered server (requires connection)."""
    err = _validate_server_id(server_id)
    if err:
        return {"error": err}
    mc = _mcp_required()
    mgr = mc.get_mcp_manager()
    servers = {s.get("server_id", ""): s for s in mgr.list_servers()}
    if server_id not in servers:
        return {"error": f"Server {server_id!r} not found. Use action=list first."}
    summary = _server_summary(servers[server_id])
    if not summary["connected"]:
        return {
            "ok": False,
            "error": f"Server {server_id!r} is not connected. "
                     "Use action=connect first.",
            "server": summary,
        }
    return {"ok": True, "server": summary}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

MCP_MANAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "add", "add_preset", "remove",
                     "connect", "disconnect", "tools"],
            "description": (
                "'list' shows registered servers + built-in presets. "
                "'add' registers a custom server (stdio command or http url). "
                "'add_preset' registers from a built-in preset. "
                "'remove' disconnects and deletes a server. "
                "'connect'/'disconnect' manage the connection of a registered "
                "server. 'tools' lists the tools offered by one server."
            ),
        },
        "server_id": {
            "type": "string",
            "description": (
                "MCP server id. Must match [a-zA-Z0-9_-] (1-64 chars). "
                "Required for add/remove/connect/disconnect/tools; optional "
                "for add_preset (defaults to the preset id)."
            ),
        },
        "preset_id": {
            "type": "string",
            "description": (
                "Built-in preset id for action=add_preset "
                "(see action=list for available presets)."
            ),
        },
        "command": {
            "type": "string",
            "description": "Executable to launch for stdio transport (e.g. 'npx').",
        },
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Argument list for the stdio command.",
        },
        "env": {
            "type": "object",
            "description": "Extra environment variables passed to the server process.",
        },
        "cwd": {
            "type": "string",
            "description": "Working directory for the server process (default '.').",
        },
        "label": {
            "type": "string",
            "description": "Human-readable label shown in server lists.",
        },
        "transport": {
            "type": "string",
            "enum": ["stdio", "http"],
            "description": "'stdio' launches a local process (default), "
                           "'http' connects to a remote MCP endpoint (needs url).",
        },
        "url": {
            "type": "string",
            "description": "Endpoint URL for http transport.",
        },
        "auth_token": {
            "type": "string",
            "description": (
                "Optional bearer token for http transport. Never echoed back "
                "in tool results."
            ),
        },
        "auto_connect": {
            "type": "boolean",
            "description": "Connect immediately after add/add_preset (default true).",
        },
    },
    "required": ["action"],
}


# --- Registry ---
from tools.registry import registry, tool_error  # noqa: E402


def check_mcp_manage_requirements() -> bool:
    """mcp_manage needs the DAON MCP client module on sys.path."""
    return _load_mcp_client() is not None


registry.register(
    name="mcp_manage",
    toolset="mcp",
    schema=MCP_MANAGE_SCHEMA,
    handler=lambda args, **kw: _result(_dispatch(args)),
    check_fn=check_mcp_manage_requirements,
    emoji="🧩",
    description=(
        "Manage MCP servers at runtime: list/add/add_preset/remove/connect/"
        "disconnect/tools. Lets the agent register new capability servers "
        "(stdio command or http endpoint) and use their tools without a restart."
    ),
)


def _dispatch(args: dict) -> dict:
    """Route mcp_manage args to the matching action handler."""
    action = (args.get("action") or "").strip()
    try:
        if action == "list":
            return _mcp_list()
        if action == "add":
            return _mcp_add(
                server_id=args.get("server_id", ""),
                command=args.get("command", ""),
                args=args.get("args"),
                env=args.get("env"),
                cwd=args.get("cwd", ""),
                label=args.get("label", ""),
                transport=args.get("transport", "stdio"),
                url=args.get("url", ""),
                auth_token=args.get("auth_token", ""),
                auto_connect=args.get("auto_connect", True),
            )
        if action == "add_preset":
            return _mcp_add_preset(
                preset_id=args.get("preset_id", ""),
                server_id=args.get("server_id", ""),
                label=args.get("label", ""),
                env=args.get("env"),
                cwd=args.get("cwd", ""),
                auto_connect=args.get("auto_connect", True),
            )
        if action == "remove":
            return _mcp_remove(args.get("server_id", ""))
        if action == "connect":
            return _mcp_connect(args.get("server_id", ""))
        if action == "disconnect":
            return _mcp_disconnect(args.get("server_id", ""))
        if action == "tools":
            return _mcp_tools(args.get("server_id", ""))
        return {
            "error": f"Unknown action {action!r}. Use one of: list, add, "
                     "add_preset, remove, connect, disconnect, tools."
        }
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mcp_manage unexpected error")
        return {"error": f"mcp_manage failed: {exc}"}
