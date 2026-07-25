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


def handle_get_capability_diagnose(handler, parsed) -> bool:
    """GET /api/capability/diagnose?session_id=... — TRACE-inspired capability diagnosis."""
    from urllib.parse import parse_qs

    qs = parse_qs(parsed.query) if parsed.query else {}
    session_id = qs.get('session_id', [None])[0]

    if not session_id:
        return j_err(handler, 'Missing ?session_id= parameter')

    try:
        from api.capability_diagnosis import diagnose_session
        result = diagnose_session(session_id)
        if result.get('ok') is False:
            return j_err(handler, result.get('error', 'Diagnosis failed'), status=500)
        return j_ok(handler, result)
    except Exception as e:
        _logger.error("Capability diagnosis failed: %s", e)
        return j_err(handler, f'Diagnosis failed: {e}')


def handle_get_capability_tests(handler, parsed) -> bool:
    """GET /api/capability/tests?session_id=... — generate skill unit tests from diagnosis."""
    from urllib.parse import parse_qs

    qs = parse_qs(parsed.query) if parsed.query else {}
    session_id = qs.get('session_id', [None])[0]

    if not session_id:
        return j_err(handler, 'Missing ?session_id= parameter')

    try:
        # First run diagnosis
        from api.capability_diagnosis import diagnose_session
        diagnosis = diagnose_session(session_id)
        if diagnosis.get('ok') is False:
            return j_err(handler, diagnosis.get('error', 'Diagnosis failed'), status=500)

        # Then generate tests
        from api.skill_test_generator import generate_skill_tests
        tests = generate_skill_tests(diagnosis)
        return j_ok(handler, tests)
    except Exception as e:
        _logger.error("Skill test generation failed: %s", e)
        return j_err(handler, f'Test generation failed: {e}')


def handle_post_capability_route(handler, body: dict) -> bool:
    """POST /api/capability/route — route skills for a task (TRACE MoE Gate)."""
    task = body.get('task', '')
    if not task:
        return j_err(handler, 'Missing "task" in request body')

    try:
        # Optionally load diagnosis history from session
        diagnosis_history = body.get('diagnosis_history', None)
        from api.skill_router import route_skills_for_task
        result = route_skills_for_task(task, diagnosis_history=diagnosis_history)
        return j_ok(handler, result)
    except Exception as e:
        _logger.error("Skill routing failed: %s", e)
        return j_err(handler, f'Skill routing failed: {e}')


def handle_get_capability_mappings(handler, parsed) -> bool:
    """GET /api/capability/mappings — return capability→skill/MCP mapping table."""
    try:
        from api.skill_router import get_all_capability_mappings
        result = get_all_capability_mappings()
        return j_ok(handler, result)
    except Exception as e:
        _logger.error("Capability mappings load failed: %s", e)
        return j_err(handler, f'Failed: {e}')


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

def _extract_ott(raw_text: str) -> str:
    """Extract the oneTimeToken value from a pasted connection prompt.
    
    The user may paste the entire PlayMCP connection dialog text, which looks like:
        oneTimeToken: eyJhbGci...
    or:
        oneTimeToken=eyJhbGci...
    or the token may be on its own line after some descriptive text.
    
    Returns the cleaned token value, or the original text if no pattern matches.
    """
    import re
    
    if not raw_text or not raw_text.strip():
        return raw_text
    
    text = raw_text.strip()
    
    # Pattern 1: "oneTimeToken: <token>" (colon-separated)
    m = re.search(r'oneTimeToken\s*[:：]\s*([^\s\n\r]+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    
    # Pattern 2: "oneTimeToken=<token>" (equals-separated)
    m = re.search(r'oneTimeToken\s*=\s*([^\s\n\r]+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    
    # Pattern 3: If the text has multiple lines, take the last non-empty line
    # (common when the token is on the last line after descriptive text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) > 1:
        # Check if the last line looks like a JWT or token (starts with eyJ or contains no spaces)
        last = lines[-1]
        if len(last) > 20 and ' ' not in last:
            return last
    
    return text


def handle_post_mcp_exchange_ott(handler, body: dict) -> bool:
    """POST /api/mcp/exchange-ott - Exchange OTT for access token and update config."""
    try:
        require(body, 'server_id', 'oneTimeToken')
        server_id = body['server_id']
        raw_ott = body['oneTimeToken']
        
        # Extract the actual token from a possibly pasted connection prompt
        ott = _extract_ott(raw_ott)
        
        import urllib.request
        import json
        from pathlib import Path
        
        _logger.info(f"OTT exchange: server_id={server_id}, raw_len={len(raw_ott)}, extracted_len={len(ott)}, ott_first10={ott[:10]}..., raw_first80={repr(raw_ott[:80])}")
        
        # Warn if extraction changed the value significantly
        if ott != raw_ott.strip():
            _logger.info(f"OTT was extracted from prompt text. Original had {len(raw_ott)} chars, extracted token has {len(ott)} chars.")
        
        # 1. Exchange OTT via Kakao API
        url = 'https://playmcp.kakao.com/api/v1/auths/otts:exchange'
        import urllib.error
        _payload = json.dumps({'tokenValue': ott.strip()}).encode('utf-8')
        _logger.info(f"OTT exchange payload (first 100 chars): {_payload[:100]}")
        req = urllib.request.Request(url, data=_payload, headers={'Content-Type': 'application/json', 'Accept': '*/*'})
        try:
            response = urllib.request.urlopen(req, timeout=15)
            data = json.loads(response.read().decode('utf-8'))
            access_token = data.get('accessToken', {}).get('tokenValue')
            if not access_token:
                _logger.error(f"OTT exchange response missing accessToken: {json.dumps(data, ensure_ascii=False)[:500]}")
                return j_err(handler, 'Failed to extract accessToken from response')
        except urllib.error.HTTPError as e:
            error_body = ''
            try:
                error_body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            _logger.error(f"OTT Exchange HTTP {e.code}: {error_body}")
            return j_err(handler, f"OTT Exchange Failed (HTTP {e.code}): {error_body[:300]}")
        except Exception as e:
            _logger.error(f"OTT Exchange Failed: {e}")
            return j_err(handler, f"OTT Exchange Failed: {str(e)}")
            
        # 2. Update mcp_servers.json in persistent STATE_DIR
        try:
            from api.config import STATE_DIR
            config_path = STATE_DIR / 'mcp_servers.json'
        except ImportError:
            from pathlib import Path
            config_path = Path('data/mcp_servers.json')
            
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                servers = json.load(f)
            found = False
            for srv in servers:
                if srv.get('server_id') == server_id:
                    srv['auth_token'] = access_token
                    found = True
                    break
            if not found:
                _logger.warning("Server '%s' not found in mcp_servers.json", server_id)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(servers, f, indent=2, ensure_ascii=False)
                
        # 3. Update active manager and reconnect
        mgr = get_mcp_manager()
        lock = getattr(mgr, '_lock', None)
        if lock:
            with lock:
                conns = getattr(mgr, '_connections', {})
                if server_id in conns:
                    conn = conns[server_id]
                    conn.auth_token = access_token
                    if hasattr(conn, 'expired'):
                        conn.expired = False
                    if hasattr(conn, 'error'):
                        conn.error = ''
        
        # Trigger reconnect async
        threading.Thread(target=mgr.connect_server, args=(server_id,), daemon=True).start()
        return j_ok(handler, {'message': 'Token updated and reconnecting...'})
    
    except ValueError as e:
        return j_err(handler, str(e))
    except Exception as e:
        _logger.exception("exchange-ott unexpected error")
        return j_err(handler, 'Internal error: ' + type(e).__name__ + ': ' + str(e))
