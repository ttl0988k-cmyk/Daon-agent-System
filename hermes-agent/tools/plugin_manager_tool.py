#!/usr/bin/env python3
"""
Plugin Manager Tool -- Agent-Managed External Plugin Lifecycle

Allows the DAON agent to manage external plugins at runtime:

  plugin_import  -- Import an external plugin from a git URL or a local folder
                    (plugin.yaml manifest), register it, and switch it on globally.
  plugin_list    -- List installed plugins (bundled + user) with their enabled state.
  plugin_toggle  -- Enable/disable a plugin globally, or scoped to one session/tab.
  plugin_remove  -- Remove a user plugin (bundled plugins are untouched).
  plugin_create  -- Scaffold a brand-new plugin (plugin.yaml + SKILL.md template)
                    and register it immediately, so the agent can grow its own
                    plugin ecosystem straight from a conversation.
  plugin_set_secret -- Inspect / request / remove a plugin API credential. The
                    agent NEVER receives or stores the secret value itself; it
                    only registers a pending request that the UI secure input
                    fulfils, so the user keeps full control over the key value.

Plugins are the agent's composable capability packs: each one can carry
skills (procedural knowledge), MCP servers, extra tools, and hooks.  The
agent can import an existing plugin (git/folder), toggle its scope, or
author a fresh plugin from scratch and have it usable right away.

Runtime wiring: when running inside DAON (server.py adds RESOURCE_DIR/api/api
to sys.path) the plugin_gateway / plugin_state modules are imported lazily and
every mutation keeps the Hermes skill/tool/MCP environments in sync through
sync_plugin_skill_env().  Outside DAON the tools degrade to a clear error so
the module still imports cleanly for the standalone hermes CLI.
"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Lazy DAON backend imports (server.py puts RESOURCE_DIR/api/api on sys.path)
# ---------------------------------------------------------------------------

def _load_gateway():
    """Lazily import the DAON plugin gateway module.

    Inside DAON, server.py puts RESOURCE_DIR/api/api on sys.path before the
    top-level api/, so the ``api`` package resolves to the internal api/api
    package and the correct import is ``api.plugin_gateway`` (matching every
    other DAON module, e.g. api/api/routes/plugin_routes.py).
    """
    try:
        import api.plugin_gateway as _pg
        return _pg
    except Exception as exc:  # pragma: no cover - DAON-only path
        logger.debug("plugin_gateway unavailable: %s", exc)
        return None


def _load_state_api():
    """Lazily import the DAON plugin state module (see _load_gateway)."""
    try:
        import api.plugin_state as _ps
        return _ps
    except Exception as exc:  # pragma: no cover - DAON-only path
        logger.debug("plugin_state unavailable: %s", exc)
        return None


def _gateway_required():
    pg = _load_gateway()
    if pg is None:
        raise RuntimeError(
            "Plugin management is only available inside the DAON agent runtime "
            "(plugin_gateway could not be imported)."
        )
    return pg


def _result(data) -> str:
    """Serialize a handler result dict to a JSON string for the tool pipeline."""
    return json.dumps(data, ensure_ascii=False, default=str)


def _validate_name(name: str) -> Optional[str]:
    if not name or not _PLUGIN_NAME_RE.match(name):
        return (
            f"Invalid name {name!r}. Must match [a-zA-Z0-9_-] and be 1-64 chars."
        )
    return None


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _plugin_import(identifier: str, source_type: str = "auto", force: bool = False) -> dict:
    """Import an external plugin (git URL or local folder) and register it."""
    identifier = (identifier or "").strip()
    if not identifier:
        return {"error": "identifier is required: a git URL or a local folder path."}
    pg = _gateway_required()
    try:
        result = pg.import_plugin(
            identifier,
            source_type=source_type or "auto",
            force=bool(force),
        )
        return {
            "ok": True,
            "name": result.get("name"),
            "enabled": result.get("enabled"),
            "path": result.get("path"),
            "description": result.get("description", ""),
            "source": result.get("source"),
        }
    except Exception as exc:
        return {"error": f"Failed to import plugin: {exc}"}


def _plugin_list() -> dict:
    """List installed plugins (bundled + user) with enabled state."""
    pg = _gateway_required()
    try:
        plugins = pg.list_installed_plugins()
        return {"ok": True, "count": len(plugins), "plugins": plugins}
    except Exception as exc:
        return {"error": f"Failed to list plugins: {exc}"}


def _plugin_toggle(name: str, enabled: bool, session_id: Optional[str] = None) -> dict:
    """Enable/disable a plugin globally, or scoped to one session/tab."""
    name = (name or "").strip()
    if not name:
        return {"error": "name is required."}
    pg = _gateway_required()
    ps = _load_state_api()
    if ps is None:
        return {"error": "Plugin state API unavailable inside this runtime."}
    try:
        plugin = pg.get_plugin(name)
        if plugin is None:
            return {
                "error": f"Plugin '{name}' is not installed. Use plugin_list to see available plugins."
            }
    except Exception as exc:
        return {"error": f"Failed to look up plugin '{name}': {exc}"}

    try:
        if session_id:
            ps.set_session_plugin(str(session_id), name, bool(enabled))
        else:
            ps.set_plugin_global_enabled(name, bool(enabled))
    except Exception as exc:
        return {"error": f"Failed to toggle plugin '{name}': {exc}"}

    # Keep Hermes skill/tool/MCP environments in sync
    try:
        pg.sync_plugin_skill_env()
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("sync_plugin_skill_env failed: %s", exc)

    return {
        "ok": True,
        "name": name,
        "enabled": bool(enabled),
        "scope": "session" if session_id else "global",
        "session_id": session_id or None,
    }


def _plugin_remove(name: str) -> dict:
    """Remove a user plugin (bundled plugins are untouched)."""
    name = (name or "").strip()
    if not name:
        return {"error": "name is required."}
    pg = _gateway_required()
    try:
        removed = pg.remove_plugin(name)
        if not removed:
            return {
                "error": f"Plugin '{name}' was not found or is bundled "
                         "(bundled plugins cannot be removed)."
            }
        return {"ok": True, "removed": True, "name": name}
    except Exception as exc:
        return {"error": f"Failed to remove plugin '{name}': {exc}"}


_TOOL_INIT_TEMPLATE = '''"""__PLUGIN_NAME__ plugin tool module (scaffolded by plugin_create).

The PluginManager imports this file and calls register(ctx) once.
ctx.register_tool() delegates to tools.registry.register, so the tool
appears in the agent tool surface after plugin (re-)discovery.
"""

from __future__ import annotations

TOOL_NAME = "__TOOL_NAME__"

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "input": {
            "type": "string",
            "description": "Placeholder argument - replace with real parameters.",
        },
    },
    "required": [],
}


def _check_requirements() -> bool:
    """Return True when the tool is usable in the current environment."""
    return True


def _handle(args: dict, **kwargs) -> str:
    """Tool handler entry point. Replace this body with real logic."""
    return "__TOOL_NAME__ (plugin __PLUGIN_NAME__) received: " + repr(args)


def register(ctx) -> None:
    """Called once by the PluginManager when this plugin is loaded."""
    ctx.register_tool(
        name=TOOL_NAME,
        toolset="__TOOLSET__",
        schema=TOOL_SCHEMA,
        handler=_handle,
        check_fn=_check_requirements,
        description="__TOOL_DESCRIPTION__",
    )
'''


def _plugin_create(
    name: str,
    description: str = "",
    version: str = "0.1.0",
    author: str = "",
    skill_name: Optional[str] = None,
    skill_description: Optional[str] = None,
    skill_content: Optional[str] = None,
    secrets: Optional[list] = None,
    tool_template: Optional[str] = None,
    tool_description: Optional[str] = None,
) -> dict:
    """Scaffold a brand-new plugin (plugin.yaml + SKILL.md) and register it.

    todo-15 path: the agent authors its own plugin from a conversation and
    it becomes usable immediately (globally ON, skills/tools/MCP synced).

    ``secrets`` optionally declares the API keys this plugin needs as a list of
    strings ('GITHUB_TOKEN') or dicts ({'name': ..., 'description': ...}). The
    agent never stores their values — the user enters them via the UI secure
    input once the plugin is used.

    ``tool_template`` optionally names a tool to scaffold (E-0c): an
    ``__init__.py`` with a minimal ``register(ctx)`` + ``ctx.register_tool``
    implementation (schema/handler/check_fn placeholders) is generated and the
    tool is declared in the plugin.yaml ``tools`` list. When set without an
    explicit ``skill_name``, the plugin is tool-only (no SKILL.md).
    """
    name = (name or "").strip()
    err = _validate_name(name)
    if err:
        return {"error": err}

    tool_name = (tool_template or "").strip()
    if tool_name:
        err = _validate_name(tool_name)
        if err:
            return {"error": f"Invalid tool_template: {err}"}

    # A skill is scaffolded unless the caller asks for a tool-only plugin
    # (tool_template set without an explicit skill_name).
    scaffold_skill = skill_name is not None or not tool_name
    if scaffold_skill:
        skill_name = (skill_name or name).strip()
        err = _validate_name(skill_name)
        if err:
            return {"error": f"Invalid skill_name: {err}"}

    tool_desc_clean = (
        (tool_description or "").replace("\n", " ").replace('"', "'").strip()
        or f"Tool provided by the {name} plugin."
    )

    pg = _gateway_required()

    desc = (description or "").replace("\n", " ").strip()
    author_clean = (author or "").replace("\n", " ").strip()
    ver = (version or "0.1.0").strip()

    # secrets → plugin.yaml 'secrets:' 블록 (문자열 또는 {name, description} dict)
    secrets_lines: list[str] = []
    for s in (secrets or []):
        if isinstance(s, dict):
            s_name = str(s.get("name") or "").strip()
            s_desc = str(s.get("description") or "").strip()
        else:
            s_name = str(s or "").strip()
            s_desc = ""
        if not s_name:
            continue
        secrets_lines.append(f"  - name: {s_name}")
        if s_desc:
            secrets_lines.append(f"    description: {s_desc}")
    secrets_block = ("secrets:\n" + "\n".join(secrets_lines) + "\n") if secrets_lines else ""

    skills_block = ""
    if scaffold_skill:
        skills_block = (
            "skills:\n"
            f"  - name: {skill_name}\n"
            f"    path: skills/{skill_name}\n"
        )

    tools_block = f"tools:\n  - {tool_name}\n" if tool_name else ""

    plugin_yaml = (
        "name: {name}\n"
        "version: {version}\n"
        "description: {description}\n"
        "author: {author}\n"
        "{skills_block}"
        "{tools_block}"
        "{secrets_block}"
    ).format(
        name=name,
        version=ver,
        description=desc,
        author=author_clean,
        skills_block=skills_block,
        tools_block=tools_block,
        secrets_block=secrets_block,
    )

    skill_md = ""
    if scaffold_skill:
        skill_desc = (
            (skill_description or "").replace("\n", " ").strip()
            or f"Skill provided by the {name} plugin."
        )
        skill_body = (skill_content or "").strip() or (
            f"# {skill_name}\n\n"
            f"A skill from the {name} plugin. Replace this content by editing the "
            f"skill via the skill_manage tool, or add more files under this directory."
        )
        skill_md = (
            "---\n"
            "name: {skill_name}\n"
            "description: {skill_description}\n"
            "---\n\n"
            "{skill_body}\n"
        ).format(
            skill_name=skill_name,
            skill_description=skill_desc,
            skill_body=skill_body,
        )

    # Write the scaffold into a temp dir, then hand it to import_plugin(folder).
    # import_plugin copies it into the user plugins dir and enables it globally.
    try:
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            (plugin_dir / "plugin.yaml").write_text(plugin_yaml, encoding="utf-8")
            if scaffold_skill:
                (plugin_dir / "skills" / skill_name).mkdir(parents=True, exist_ok=True)
                (plugin_dir / "skills" / skill_name / "SKILL.md").write_text(
                    skill_md, encoding="utf-8"
                )
            if tool_name:
                init_src = _TOOL_INIT_TEMPLATE
                for _ph, _val in (
                    ("__TOOL_NAME__", tool_name),
                    ("__PLUGIN_NAME__", name),
                    ("__TOOLSET__", f"plugin-{name}"),
                    ("__TOOL_DESCRIPTION__", tool_desc_clean),
                ):
                    init_src = init_src.replace(_ph, _val)
                (plugin_dir / "__init__.py").write_text(init_src, encoding="utf-8")
            result = pg.import_plugin(str(plugin_dir), source_type="folder", force=False)
        result = dict(result)
        result["scaffolded"] = True
        if scaffold_skill:
            result["skill"] = {
                "name": skill_name,
                "path": f"skills/{skill_name}/SKILL.md",
            }
        if tool_name:
            result["tool"] = {
                "name": tool_name,
                "path": "__init__.py",
                "toolset": f"plugin-{name}",
            }
        return result
    except Exception as exc:
        return {"error": f"Failed to scaffold plugin '{name}': {exc}"}


def _plugin_set_secret(
    plugin: str,
    action: str = "status",
    key: str = "",
    session_id: str = "",
) -> dict:
    """Inspect / request / remove a plugin API credential.

    Security model (role separation): the agent only manages *which* credential
    is needed — it never receives or stores the secret value. ``request``
    registers a pending credential request that the user fulfils through the UI
    secure input; ``status`` reports set/unset state only (values never leave
    the Credential Store); ``remove`` deletes a stored key.
    """
    plugin = (plugin or "").strip()
    if not plugin:
        return {"error": "plugin is required."}
    action = (action or "status").strip().lower()
    if action not in ("status", "request", "remove"):
        return {
            "error": f"Invalid action {action!r}. Use one of: status, request, remove."
        }

    pg = _gateway_required()
    try:
        if action == "status":
            info = pg.get_plugin_credential_status(plugin)
            return {
                "ok": True,
                "plugin": plugin,
                "authenticated": bool(info.get("authenticated")),
                "secrets": info.get("secrets", []),
                "note": "Values are never returned. Only the set/unset state is reported.",
            }

        if action == "request":
            key = (key or "").strip()
            if not key:
                return {"error": "key is required for action='request'."}
            result = pg.request_plugin_credential(
                plugin, key, session_id=(session_id or "")
            )
            return {
                "ok": True,
                "plugin": plugin,
                "key": key,
                "requested": True,
                "message": (
                    "A credential request has been registered. Ask the user to "
                    "enter the value in the UI secure input — do NOT ask them to "
                    "paste the value here, and never accept it in chat."
                ),
                "detail": result,
            }

        # action == "remove"
        key = (key or "").strip()
        if not key:
            return {"error": "key is required for action='remove'."}
        removed = pg.delete_plugin_credential(plugin, key)
        if not removed:
            return {
                "error": f"No stored credential '{key}' found for plugin '{plugin}'."
            }
        return {"ok": True, "plugin": plugin, "key": key, "removed": True}
    except Exception as exc:
        return {"error": f"plugin_set_secret failed: {exc}"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

PLUGIN_IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "identifier": {
            "type": "string",
            "description": (
                "External plugin source: a git URL (https://..., git@..., ssh://...) "
                "or a local folder path containing plugin.yaml."
            ),
        },
        "source_type": {
            "type": "string",
            "enum": ["auto", "git", "folder"],
            "description": (
                "How to interpret identifier. 'auto' detects git URLs automatically "
                "(default). Use 'folder' for a local path, 'git' to force a clone."
            ),
        },
        "force": {
            "type": "boolean",
            "description": "Overwrite an existing plugin with the same name.",
        },
    },
    "required": ["identifier"],
}

PLUGIN_LIST_SCHEMA = {
    "type": "object",
    "properties": {},
}

PLUGIN_TOGGLE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Plugin name to toggle (from plugin_list).",
        },
        "enabled": {
            "type": "boolean",
            "description": "True to enable, False to disable.",
        },
        "session_id": {
            "type": "string",
            "description": (
                "Optional session/tab id. When provided the toggle is scoped to "
                "that session only; otherwise it applies globally."
            ),
        },
    },
    "required": ["name", "enabled"],
}

PLUGIN_REMOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Plugin name to remove (user plugins only).",
        },
    },
    "required": ["name"],
}

PLUGIN_CREATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "New plugin name. Must match [a-zA-Z0-9_-] (1-64 chars).",
        },
        "description": {
            "type": "string",
            "description": "Short plugin description shown in plugin_list.",
        },
        "version": {
            "type": "string",
            "description": "Plugin version (default '0.1.0').",
        },
        "author": {
            "type": "string",
            "description": "Plugin author label.",
        },
        "skill_name": {
            "type": "string",
            "description": "Name of the first skill to scaffold (defaults to the plugin name).",
        },
        "skill_description": {
            "type": "string",
            "description": "Description for the first skill.",
        },
        "skill_content": {
            "type": "string",
            "description": (
                "SKILL.md body for the first skill (optional; a template is used "
                "otherwise). Edit it later via the skill_manage tool."
            ),
        },
        "secrets": {
            "type": "array",
            "description": (
                "Optional API-key declarations written to the plugin.yaml 'secrets' "
                "block. Each entry is a string ('GITHUB_TOKEN') or an object "
                "{'name': ..., 'description': ...}. Values are entered by the user "
                "via the UI secure input; the agent never receives them."
            ),
            "items": {"type": ["string", "object"]},
        },
        "tool_template": {
            "type": "string",
            "description": (
                "Optional tool name to scaffold inside the plugin (e.g. 'my_lookup'). "
                "When set, an __init__.py with a minimal register(ctx) + "
                "ctx.register_tool implementation (schema + handler + check_fn "
                "placeholders) is generated and the tool is declared in the "
                "plugin.yaml 'tools' list. Must match [a-zA-Z0-9_-] (1-64 chars). "
                "If skill_name is not given, the plugin is tool-only (no SKILL.md)."
            ),
        },
        "tool_description": {
            "type": "string",
            "description": "Optional description for the scaffolded tool.",
        },
    },
    "required": ["name"],
}

PLUGIN_SET_SECRET_SCHEMA = {
    "type": "object",
    "properties": {
        "plugin": {
            "type": "string",
            "description": "Plugin name (from plugin_list).",
        },
        "action": {
            "type": "string",
            "enum": ["status", "request", "remove"],
            "description": (
                "'status' reports which secret keys are set (default; never returns values). "
                "'request' registers a pending request so the user enters the value via the "
                "UI secure input — the agent never receives the value. "
                "'remove' deletes a stored key."
            ),
        },
        "key": {
            "type": "string",
            "description": (
                "Secret key name declared in the plugin's plugin.yaml 'secrets' block "
                "(e.g. GITHUB_TOKEN). Required for action='request' and action='remove'."
            ),
        },
        "session_id": {
            "type": "string",
            "description": (
                "Optional session/tab id to associate with a pending credential request."
            ),
        },
    },
    "required": ["plugin", "action"],
}


# --- Registry ---
from tools.registry import registry, tool_error  # noqa: E402

registry.register(
    name="plugin_import",
    toolset="plugin",
    schema=PLUGIN_IMPORT_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_import(
        identifier=args.get("identifier", ""),
        source_type=args.get("source_type", "auto"),
        force=args.get("force", False))),
    emoji="🔌",
    description="Import an external plugin (git URL or local folder with plugin.yaml) and register it globally.",
)

registry.register(
    name="plugin_list",
    toolset="plugin",
    schema=PLUGIN_LIST_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_list()),
    emoji="🔌",
    description="List installed plugins (bundled + user) with their enabled state.",
)

registry.register(
    name="plugin_toggle",
    toolset="plugin",
    schema=PLUGIN_TOGGLE_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_toggle(
        name=args.get("name", ""),
        enabled=args.get("enabled", False),
        session_id=args.get("session_id"))),
    emoji="🔌",
    description="Enable or disable a plugin globally, or scoped to one session/tab.",
)

registry.register(
    name="plugin_remove",
    toolset="plugin",
    schema=PLUGIN_REMOVE_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_remove(
        name=args.get("name", ""))),
    emoji="🔌",
    description="Remove a user plugin (bundled plugins are untouched).",
)

registry.register(
    name="plugin_create",
    toolset="plugin",
    schema=PLUGIN_CREATE_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_create(
        name=args.get("name", ""),
        description=args.get("description", ""),
        version=args.get("version", "0.1.0"),
        author=args.get("author", ""),
        skill_name=args.get("skill_name"),
        skill_description=args.get("skill_description"),
        skill_content=args.get("skill_content"),
        secrets=args.get("secrets"),
        tool_template=args.get("tool_template"),
        tool_description=args.get("tool_description"))),
    emoji="🔌",
    description="Scaffold a brand-new plugin (plugin.yaml + SKILL.md, optionally a tool via tool_template) and register it immediately.",
)

registry.register(
    name="plugin_set_secret",
    toolset="plugin",
    schema=PLUGIN_SET_SECRET_SCHEMA,
    handler=lambda args, **kw: _result(_plugin_set_secret(
        plugin=args.get("plugin", ""),
        action=args.get("action", "status"),
        key=args.get("key", ""),
        session_id=args.get("session_id", ""))),
    emoji="🔑",
    description=(
        "Inspect, request, or remove a plugin API credential. The agent never "
        "receives or stores secret values: 'request' asks the user to enter the "
        "value in the UI secure input, 'status' reports set/unset state only, "
        "'remove' deletes a stored key."
    ),
)
