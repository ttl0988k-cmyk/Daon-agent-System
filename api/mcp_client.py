"""
MCP (Model Context Protocol) Client for Daon Agent System.
Enables AI agents to connect to external MCP servers for tool extensibility.

Supports:
- stdio transport (subprocess-based MCP servers)
- HTTP transport (remote MCP servers, e.g. PlayMCP Gateway)
- JSON-RPC 2.0 protocol
- Tool discovery (tools/list)
- Tool execution (tools/call)
- Resource management (resources/list, resources/read)
"""
import base64
import json as _json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

TRANSPORT_STDIO = 'stdio'
TRANSPORT_HTTP = 'http'

# ── MCP Server Connection ─────────────────────────────────────────────────────

class MCPServerConnection:
    """Represents a single MCP server connection (stdio subprocess or HTTP)."""

    def __init__(self, server_id: str, command: str, args: list[str] = None,
                 env: dict = None, cwd: str = None, label: str = '',
                 transport: str = TRANSPORT_STDIO,
                 url: str = '', auth_token: str = ''):
        self.server_id = server_id
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = cwd or '.'
        self.label = label or server_id
        self.transport = transport  # 'stdio' or 'http'
        self.url = url  # HTTP endpoint URL
        self.auth_token = auth_token  # Bearer token for HTTP auth
        self.session_id: str = ''  # Mcp-Session-Id from initialize response
        self.process: Optional[subprocess.Popen] = None
        self.tools: list[dict] = []
        self.resources: list[dict] = []
        self.connected = False
        self.error: str = ''
        self._lock = threading.RLock()
        self._request_id = 0
        self._pending: dict[str, threading.Event] = {}
        self._responses: dict[str, dict] = {}
        self._last_stderr = ''
        self._reader_thread: Optional[threading.Thread] = None

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def connect(self) -> bool:
        """Connect to the MCP server (stdio or HTTP)."""
        with self._lock:
            if self.connected:
                return True

        if self.transport == TRANSPORT_HTTP:
            return self._connect_http()
        else:
            return self._connect_stdio()

    def _connect_stdio(self) -> bool:
        """Launch the MCP server subprocess and perform initialize handshake."""
        with self._lock:
            try:
                import os as _os
                _env = {**dict(_os.environ), **self.env}
                import shutil
                resolved_cmd = shutil.which(self.command) or self.command

                self.process = subprocess.Popen(
                    [resolved_cmd] + self.args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.cwd,
                    env=_env,
                    text=True,
                    encoding='utf-8',
                    bufsize=1,
                )
                # Start reader thread for stdout
                self._reader_thread = threading.Thread(
                    target=self._read_loop, daemon=True
                )
                self._reader_thread.start()

                # Start stderr drain thread to prevent pipe buffer deadlock on Windows
                self._stderr_thread = threading.Thread(
                    target=self._stderr_loop, daemon=True
                )
                self._stderr_thread.start()
            except FileNotFoundError:
                self.error = f"Command not found: {self.command}"
                _logger.error("MCP server '%s': %s", self.label, self.error)
                return False
            except Exception as e:
                self.error = f"Start failed: {e}"
                _logger.error("MCP server '%s': %s", self.label, self.error)
                return False

        # OUTSIDE the lock: Send initialize request and wait for response
        init_result = self._send_request('initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {
                'tools': {},
                'resources': {},
            },
            'clientInfo': {
                'name': 'DaonAgentSystem',
                'version': '1.0.0',
            },
        }, timeout=180.0)

        if init_result and 'error' not in init_result:
            # Send initialized notification
            self._send_notification('notifications/initialized', {})
            with self._lock:
                self.connected = True

            # Discover tools & resources (outside lock since it sends requests)
            self._discover_tools()

            _logger.info("MCP server '%s' connected successfully", self.label)
            return True
        else:
            with self._lock:
                if init_result:
                    self.error = init_result.get('error', {}).get('message', 'Unknown init error')
                else:
                    self.error = self._last_stderr if self._last_stderr else 'No response'
            _logger.error("MCP server '%s' init failed: %s", self.label, self.error)
            self.disconnect()
            return False

    def _connect_http(self) -> bool:
        """Connect to an HTTP-based MCP server (e.g. PlayMCP Gateway)."""
        import urllib.request
        import urllib.error

        if not self.url:
            self.error = "HTTP transport requires a URL"
            _logger.error("MCP server '%s': %s", self.label, self.error)
            return False

        # Step 1: Send initialize request and capture Mcp-Session-Id from headers
        init_payload = _json.dumps({
            'jsonrpc': '2.0',
            'id': self._next_id(),
            'method': 'initialize',
            'params': {
                'protocolVersion': '2025-06-18',
                'capabilities': {'tools': {}},
                'clientInfo': {
                    'name': 'DaonAgentSystem',
                    'version': '1.0.0',
                },
            },
        }).encode('utf-8')

        req = urllib.request.Request(
            self.url,
            data=init_payload,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
                'Authorization': f'Bearer {self.auth_token}',
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                # Extract Mcp-Session-Id from response headers
                session_id = resp.headers.get('Mcp-Session-Id', '')
                init_body = _json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            self.error = f"HTTP {e.code}: {e.reason}"
            _logger.error("MCP server '%s': %s", self.label, self.error)
            return False
        except Exception as e:
            self.error = f"HTTP init failed: {e}"
            _logger.error("MCP server '%s': %s", self.label, self.error)
            return False

        if not session_id:
            self.error = "No Mcp-Session-Id in initialize response"
            _logger.error("MCP server '%s': %s", self.label, self.error)
            return False

        with self._lock:
            self.session_id = session_id

        if 'error' in init_body:
            self.error = init_body.get('error', {}).get('message', 'Unknown init error')
            _logger.error("MCP server '%s' init failed: %s", self.label, self.error)
            return False

        _logger.info("MCP server '%s' initialized (HTTP), session: %s", self.label, session_id[:8] + '...')

        # Step 2: Send initialized notification
        self._send_request_http('notifications/initialized', {})

        with self._lock:
            self.connected = True

        # Step 3: Discover tools
        self._discover_tools_http()

        _logger.info("MCP server '%s' connected successfully (HTTP)", self.label)
        return True

    def _send_request_http(self, method: str, params: dict = None, timeout: float = 30.0) -> Optional[dict]:
        """Send a JSON-RPC request over HTTP and wait for response."""
        import urllib.request
        import urllib.error

        rid = self._next_id()
        payload = _json.dumps({
            'jsonrpc': '2.0',
            'id': rid,
            'method': method,
            'params': params or {},
        }).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'Authorization': f'Bearer {self.auth_token}',
        }
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers=headers,
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = _json.loads(resp.read().decode('utf-8'))
                return body
        except urllib.error.HTTPError as e:
            _logger.error("MCP HTTP request %s failed: HTTP %s", method, e.code)
            return {'error': {'message': f'HTTP {e.code}: {e.reason}'}}
        except Exception as e:
            _logger.error("MCP HTTP request %s failed: %s", method, e)
            return {'error': {'message': str(e)}}

    def _discover_tools_http(self):
        """Discover available tools from the HTTP MCP server."""
        result = self._send_request_http('tools/list', timeout=15.0)
        if result and 'result' in result:
            self.tools = result['result'].get('tools', [])
            _logger.info("MCP server '%s' offers %d tools", self.label, len(self.tools))
        # Also discover resources
        result = self._send_request_http('resources/list', timeout=10.0)
        if result and 'result' in result:
            self.resources = result['result'].get('resources', [])

    def disconnect(self):
        """Terminate the MCP server (stdio subprocess or HTTP session)."""
        with self._lock:
            self.connected = False
            if self.transport == TRANSPORT_HTTP:
                # No persistent connection to close; just clear state
                self.session_id = ''
                self.tools = []
                self.resources = []
                for evt in self._pending.values():
                    evt.set()
                self._pending.clear()
                self._responses.clear()
                return

            if self.process:
                try:
                    self.process.stdin.close()
                    self.process.stdout.close() if self.process.stdout else None
                    self.process.stderr.close() if self.process.stderr else None
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2)
                except Exception:
                    pass
                self.process = None
            self.tools = []
            self.resources = []
            # Signal any pending requests
            for evt in self._pending.values():
                evt.set()
            self._pending.clear()
            self._responses.clear()

    def _read_loop(self):
        """Read JSON-RPC responses from stdout."""
        if not self.process or not self.process.stdout:
            return
        try:
            buffer = ''
            while self.process and self.process.poll() is None:
                try:
                    char = self.process.stdout.read(1)
                    if not char:
                        break
                except Exception:
                    break
                buffer += char
                if char == '\n' and buffer.strip():
                    try:
                        msg = _json.loads(buffer.strip())
                        rid = msg.get('id')
                        if rid is not None:
                            with self._lock:
                                self._responses[str(rid)] = msg
                                evt = self._pending.pop(str(rid), None)
                            if evt:
                                evt.set()
                    except _json.JSONDecodeError:
                        pass
                    buffer = ''
        except Exception as e:
            _logger.debug("MCP read loop ended: %s", e)

    def _stderr_loop(self):
        """Read stderr in background to prevent pipe buffer deadlock on Windows."""
        if not self.process or not self.process.stderr:
            return
        try:
            while self.process and self.process.poll() is None:
                line = self.process.stderr.readline()
                if not line:
                    break
                line_str = line.rstrip('\n')
                self._last_stderr = line_str
                _logger.debug("MCP stderr [%s]: %s", self.label, line_str)
        except Exception:
            pass

    def _send_request(self, method: str, params: dict = None, timeout: float = 10.0) -> Optional[dict]:
        """Send a JSON-RPC request and wait for response (stdio or HTTP)."""
        if self.transport == TRANSPORT_HTTP:
            return self._send_request_http(method, params, timeout)

        if not self.process or not self.process.stdin:
            return None
        rid = self._next_id()
        request = _json.dumps({
            'jsonrpc': '2.0',
            'id': rid,
            'method': method,
            'params': params or {},
        })
        evt = threading.Event()
        with self._lock:
            self._pending[str(rid)] = evt
        try:
            self.process.stdin.write(request + '\n')
            self.process.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending.pop(str(rid), None)
            _logger.error("MCP send failed: %s", e)
            return None
        end_time = time.time() + timeout
        while True:
            if evt.wait(timeout=0.1):
                with self._lock:
                    return self._responses.pop(str(rid), None)
            if self.process is None or self.process.poll() is not None:
                with self._lock:
                    self._pending.pop(str(rid), None)
                _logger.error("MCP process died while waiting for response to %s", method)
                return None
            if time.time() > end_time:
                with self._lock:
                    self._pending.pop(str(rid), None)
                _logger.warning("MCP request %s timed out", method)
                return {'error': {'message': f'Request timed out after {timeout}s'}}

    def _send_notification(self, method: str, params: dict = None):
        """Send a JSON-RPC notification (no response expected)."""
        if self.transport == TRANSPORT_HTTP:
            self._send_request_http(method, params)
            return

        if not self.process or not self.process.stdin:
            return
        try:
            msg = _json.dumps({
                'jsonrpc': '2.0',
                'method': method,
                'params': params or {},
            })
            self.process.stdin.write(msg + '\n')
            self.process.stdin.flush()
        except Exception:
            pass

    def _discover_tools(self):
        """Discover available tools from the MCP server."""
        if self.transport == TRANSPORT_HTTP:
            self._discover_tools_http()
            return
        result = self._send_request('tools/list', timeout=5.0)
        if result and 'result' in result:
            self.tools = result['result'].get('tools', [])
            _logger.info("MCP server '%s' offers %d tools", self.label, len(self.tools))
        # Also discover resources
        result = self._send_request('resources/list', timeout=5.0)
        if result and 'result' in result:
            self.resources = result['result'].get('resources', [])

    def call_tool(self, tool_name: str, arguments: dict, timeout: float = 30.0) -> dict:
        """Execute a tool on the MCP server."""
        if not self.connected:
            return {'error': 'MCP server not connected'}
        result = self._send_request('tools/call', {
            'name': tool_name,
            'arguments': arguments,
        }, timeout=timeout)
        if result and 'result' in result:
            return {'ok': True, 'result': result['result']}
        elif result and 'error' in result:
            return {'ok': False, 'error': result['error']}
        else:
            return {'ok': False, 'error': 'No response from MCP server'}

    def to_dict(self) -> dict:
        """Serialize connection status."""
        return {
            'server_id': self.server_id,
            'label': self.label,
            'command': self.command,
            'transport': self.transport,
            'connected': self.connected,
            'error': self.error,
            'expired': getattr(self, 'expired', False),
            'tools_count': len(self.tools),
            'tools': self.tools,
            'resources_count': len(self.resources),
        }


# ── MCP Manager ───────────────────────────────────────────────────────────────

class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self._connections: dict[str, MCPServerConnection] = {}
        self._lock = threading.RLock()
        # Resolve absolute path: use BASE_DIR from api.config for PyInstaller compat
        try:
            from api.config import BASE_DIR
            self._config_path = BASE_DIR / 'data' / 'mcp_servers.json'
        except ImportError:
            self._config_path = Path('data/mcp_servers.json')
        self._load_config()

    def _save_config(self):
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            config_data = []
            with self._lock:
                for server_id, conn in self._connections.items():
                    entry = {
                        'server_id': server_id,
                        'command': conn.command,
                        'args': conn.args,
                        'env': conn.env,
                        'cwd': conn.cwd,
                        'label': conn.label,
                        'transport': conn.transport,
                    }
                    if conn.transport == TRANSPORT_HTTP:
                        entry['url'] = conn.url
                        entry['auth_token'] = conn.auth_token
                    config_data.append(entry)
            with open(self._config_path, 'w', encoding='utf-8') as f:
                _json.dump(config_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _logger.error("Failed to save MCP config: %s", e)

    @staticmethod
    def _is_jwt_expired(token: str) -> bool:
        """Check if a JWT token is expired. Returns True if expired/invalid."""
        if not token or '.' not in token:
            return False  # Not a JWT, can't check
        try:
            parts = token.split('.')
            if len(parts) < 2:
                return False
            # Add padding for base64url decode
            payload_b64 = parts[1] + '=' * (4 - len(parts[1]) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get('exp', 0)
            return time.time() > exp
        except Exception:
            return False  # Can't decode, assume valid

    def _load_config(self):
        if not self._config_path.exists():
            # Load defaults if config doesn't exist
            # filesystem + playwright + memory are always connected;
            # other servers should be explicitly enabled by the user via the MCP UI panel.
            defaults = ['filesystem', 'playwright', 'memory']
            for preset_id in defaults:
                if preset_id in MCP_PRESETS:
                    preset = MCP_PRESETS[preset_id]
                    args = list(preset['args'])
                    # Dynamically add workspace paths to filesystem MCP allowed dirs
                    if preset_id == 'filesystem':
                        try:
                            from api.workspace import get_last_workspace
                            ws_path = get_last_workspace()
                            if ws_path and ws_path not in args:
                                args.append(ws_path)
                        except Exception:
                            pass
                    self.add_server(
                        server_id=preset_id,
                        command=preset['command'],
                        args=args,
                        label=preset['label'],
                        auto_connect=False
                    )
                    threading.Thread(target=self.connect_server, args=(preset_id,), daemon=True).start()
            return

        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                config_data = _json.load(f)
            for srv in config_data:
                server_id = srv.get('server_id')
                transport = srv.get('transport', TRANSPORT_STDIO)
                auth_token = srv.get('auth_token', '')
                
                is_expired = False
                if transport == TRANSPORT_HTTP and auth_token and self._is_jwt_expired(auth_token):
                    _logger.warning(
                        "MCP server '%s': JWT token is expired. Marking as expired.",
                        server_id or 'unknown'
                    )
                    is_expired = True

                self.add_server(
                    server_id=server_id,
                    command=srv.get('command'),
                    args=srv.get('args'),
                    env=srv.get('env'),
                    cwd=srv.get('cwd'),
                    label=srv.get('label'),
                    transport=transport,
                    url=srv.get('url', ''),
                    auth_token=auth_token,
                    auto_connect=not is_expired
                )
                
                if is_expired:
                    conn = self._connections.get(server_id)
                    if conn:
                        conn.expired = True
                        conn.error = "Token Expired (재인증 필요)"
                else:
                    if server_id:
                        threading.Thread(target=self.connect_server, args=(server_id,), daemon=True).start()
        except Exception as e:
            _logger.error("Failed to load MCP config: %s", e)

    def add_server(self, server_id: str, command: str, args: list[str] = None,
                   env: dict = None, cwd: str = None, label: str = '',
                   transport: str = TRANSPORT_STDIO, url: str = '',
                   auth_token: str = '', auto_connect: bool = True) -> dict:
        """Register and optionally connect to an MCP server."""
        with self._lock:
            if server_id in self._connections:
                return {'ok': False, 'error': f'Server {server_id} already exists'}
            conn = MCPServerConnection(
                server_id=server_id, command=command, args=args,
                env=env, cwd=cwd, label=label,
                transport=transport, url=url, auth_token=auth_token,
            )
            self._connections[server_id] = conn

        if auto_connect:
            conn.connect()

        result = {'ok': True, 'server': conn.to_dict()}
        if not conn.connected and conn.error:
            with self._lock:
                self._connections.pop(server_id, None)
            result['ok'] = False
            result['error'] = conn.error
        else:
            self._save_config()
        return result

    def remove_server(self, server_id: str) -> dict:
        """Disconnect and remove an MCP server."""
        with self._lock:
            conn = self._connections.pop(server_id, None)
        if conn:
            conn.disconnect()
            self._save_config()
            return {'ok': True, 'removed': server_id}
        return {'ok': False, 'error': 'Server not found'}

    def connect_server(self, server_id: str) -> dict:
        """Reconnect to a registered MCP server."""
        conn = self._connections.get(server_id)
        if not conn:
            return {'ok': False, 'error': 'Server not found'}
        success = conn.connect()
        result = {'ok': success, 'server': conn.to_dict()}
        if not success:
            result['error'] = conn.error or 'Failed to connect'
        return result

    def disconnect_server(self, server_id: str) -> dict:
        """Disconnect from an MCP server."""
        conn = self._connections.get(server_id)
        if not conn:
            return {'ok': False, 'error': 'Server not found'}
        conn.disconnect()
        return {'ok': True, 'server': conn.to_dict()}

    def list_servers(self) -> list[dict]:
        """List all registered MCP server connections."""
        return [conn.to_dict() for conn in self._connections.values()]

    def get_server(self, server_id: str) -> Optional[MCPServerConnection]:
        """Get a specific MCP server connection."""
        return self._connections.get(server_id)

    def call_tool(self, server_id: str, tool_name: str, arguments: dict,
                  timeout: float = 30.0) -> dict:
        """Execute a tool on a specific MCP server."""
        conn = self._connections.get(server_id)
        if not conn:
            return {'ok': False, 'error': f'Server {server_id} not found'}
        if not conn.connected:
            return {'ok': False, 'error': f'Server {server_id} is not connected'}
        return conn.call_tool(tool_name, arguments, timeout)

    def get_all_tools(self) -> list[dict]:
        """Get all tools from all connected MCP servers."""
        all_tools = []
        for conn in self._connections.values():
            if conn.connected:
                for tool in conn.tools:
                    all_tools.append({
                        **tool,
                        '_mcp_server': conn.server_id,
                        '_mcp_label': conn.label,
                    })
        return all_tools

    def shutdown(self):
        """Disconnect all MCP servers."""
        with self._lock:
            for conn in list(self._connections.values()):
                conn.disconnect()
            self._connections.clear()


# ── Global singleton ──────────────────────────────────────────────────────────

_mcp_manager: Optional[MCPManager] = None
_manager_lock = threading.Lock()


def get_mcp_manager() -> MCPManager:
    """Get or create the global MCP manager singleton."""
    global _mcp_manager
    with _manager_lock:
        if _mcp_manager is None:
            _mcp_manager = MCPManager()
        return _mcp_manager


# ── Built-in MCP server presets ───────────────────────────────────────────────

MCP_PRESETS = {
    'filesystem': {
        'label': '📁 파일 시스템 MCP',
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-filesystem', '.'],
        'description': '안전한 파일 시스템 작업 (허용된 디렉토리 내 읽기/쓰기/목록 조회)',
    },
    'github': {
        'label': '🐙 깃허브(GitHub) MCP',
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-github'],
        'description': 'GitHub 저장소 관리 (이슈, PR, 파일 등)',
    },
    'playwright': {
        'label': '🎭 플레이라이트(Playwright) MCP',
        'command': 'npx',
        'args': ['-y', '@playwright/mcp'],
        'description': 'Playwright를 이용한 향상된 브라우저 제어 및 자동화',
    },
    'memory': {
        'label': '🧠 메모리(Memory) MCP',
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-memory'],
        'description': '영구적인 지식 그래프 기반의 에이전트 기억 저장소',
    },
    'sequential_thinking': {
        'label': '🤔 순차적 사고(Sequential Thinking) MCP',
        'command': 'npx',
        'args': ['-y', '@modelcontextprotocol/server-sequential-thinking'],
        'description': '복잡한 문제를 단계별로 생각하도록 돕는 추론 도구',
    },
}
