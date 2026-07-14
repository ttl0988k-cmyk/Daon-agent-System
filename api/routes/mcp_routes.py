"""
MCP (Model Context Protocol) API Routes for Daon Agent System.
Provides REST endpoints for MCP server management and tool execution.
"""
import logging
from api.helpers import j, j_ok, j_err, require
from api.mcp_client import get_mcp_manager, MCP_PRESETS
import threading

_logger = logging.getLogger(__name__)


def handle_get_mcp_servers(handler, parsed) -> bool:
    """GET /api/mcp/servers — list all registered MCP server connections."""
    mgr = get_mcp_manager()
    servers = mgr.list_servers()
    return j_ok(handler, {'servers': servers})


def handle_get_mcp_presets(handler, parsed) -> bool:
    """GET /api/mcp/presets — list built-in MCP server presets."""
    presets = {}
    for pid, preset in MCP_PRESETS.items():
        presets[pid] = {
            'label': preset['label'],
            'command': preset['command'],
            'args': preset['args'],
            'description': preset['description'],
        }
    return j_ok(handler, {'presets': presets})


def handle_get_mcp_recommend(handler, parsed) -> bool:
    """GET /api/mcp/recommend?workspace=... — recommend MCP servers for a workspace."""
    from urllib.parse import parse_qs
    from pathlib import Path as _Path

    qs = parse_qs(parsed.query) if parsed.query else {}
    workspace_param = qs.get('workspace', [None])[0]

    if not workspace_param:
        return j_err(handler, 'Missing ?workspace= parameter')

    ws_path = _Path(workspace_param)
    if not ws_path.exists():
        return j_err(handler, f'Workspace not found: {workspace_param}')

    # Run recommendation engine
    try:
        mgr = get_mcp_manager()
        servers = mgr.list_servers()
        existing_ids = [s.get('server_id', '') for s in servers]

        from api.mcp_recommender import recommend_mcp_servers
        result = recommend_mcp_servers(str(ws_path), existing_server_ids=existing_ids)
        return j_ok(handler, result)
    except Exception as e:
        _logger.error("MCP recommend failed: %s", e)
        return j_err(handler, f'Recommendation failed: {e}')


def handle_post_mcp_server_add(handler, body: dict) -> bool:
    """POST /api/mcp/servers/add — register a new MCP server."""
    try:
        require(body, 'server_id')
    except ValueError as e:
        return j_err(handler, str(e))

    server_id = body['server_id']
    command = body.get('command', 'npx')
    args = body.get('args', [])
    env = body.get('env', {})
    cwd = body.get('cwd', '.')
    label = body.get('label', server_id)
    transport = body.get('transport', 'stdio')
    url = body.get('url', '')
    auth_token = body.get('auth_token', '')
    auto_connect = body.get('auto_connect', True)

    mgr = get_mcp_manager()
    result = mgr.add_server(
        server_id=server_id,
        command=command,
        args=args,
        env=env,
        cwd=cwd,
        label=label,
        transport=transport,
        url=url,
        auth_token=auth_token,
        auto_connect=False,
    )
    if result.get('ok'):
        if auto_connect:
            threading.Thread(target=mgr.connect_server, args=(server_id,), daemon=True).start()
        return j(handler, result)
    else:
        return j_err(handler, result.get('error', 'Failed to add server'))


def handle_post_mcp_server_remove(handler, body: dict) -> bool:
    """POST /api/mcp/servers/remove — disconnect and remove an MCP server."""
    try:
        require(body, 'server_id')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = get_mcp_manager()
    result = mgr.remove_server(body['server_id'])
    if result.get('ok'):
        return j(handler, result)
    else:
        return j_err(handler, result.get('error', 'Failed to remove server'))


def handle_post_mcp_server_connect(handler, body: dict) -> bool:
    """POST /api/mcp/servers/connect — connect to an MCP server."""
    try:
        require(body, 'server_id')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = get_mcp_manager()
    server_id = body['server_id']
    if server_id not in mgr._connections:
        return j_err(handler, 'Server not found')
        
    threading.Thread(target=mgr.connect_server, args=(server_id,), daemon=True).start()
    return j(handler, {'ok': True, 'message': 'Connecting...'})


def handle_post_mcp_server_disconnect(handler, body: dict) -> bool:
    """POST /api/mcp/servers/disconnect — disconnect from an MCP server."""
    try:
        require(body, 'server_id')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = get_mcp_manager()
    result = mgr.disconnect_server(body['server_id'])
    if result.get('ok'):
        return j(handler, result)
    else:
        return j_err(handler, result.get('error', 'Failed to disconnect'))


def handle_post_mcp_server_add_preset(handler, body: dict) -> bool:
    """POST /api/mcp/servers/add-preset — add an MCP server from a built-in preset."""
    try:
        require(body, 'preset_id')
    except ValueError as e:
        return j_err(handler, str(e))

    preset_id = body['preset_id']
    if preset_id not in MCP_PRESETS:
        return j_err(handler, f'Unknown preset: {preset_id}. Available: {", ".join(MCP_PRESETS.keys())}')

    preset = MCP_PRESETS[preset_id]
    server_id = body.get('server_id', preset_id)
    label = body.get('label', preset['label'])
    auto_connect = body.get('auto_connect', True)
    cwd_override = body.get('cwd', None)

    mgr = get_mcp_manager()
    result = mgr.add_server(
        server_id=server_id,
        command=preset['command'],
        args=list(preset['args']),
        env=body.get('env', {}),
        cwd=cwd_override or '.',
        label=label,
        auto_connect=False,
    )
    if result.get('ok'):
        if auto_connect:
            threading.Thread(target=mgr.connect_server, args=(server_id,), daemon=True).start()
        return j(handler, result)
    else:
        return j_err(handler, result.get('error', 'Failed to add preset server'))


def handle_post_mcp_tool_call(handler, body: dict) -> bool:
    """POST /api/mcp/tools/call — execute a tool on an MCP server."""
    try:
        require(body, 'server_id', 'tool_name')
    except ValueError as e:
        return j_err(handler, str(e))

    mgr = get_mcp_manager()
    result = mgr.call_tool(
        server_id=body['server_id'],
        tool_name=body['tool_name'],
        arguments=body.get('arguments', {}),
        timeout=float(body.get('timeout', 30.0)),
    )
    return j(handler, result)
