"""갭 E-0a 기능 프로브: mcp_manage 도구의 액션 라우팅 + 스키마 + 시크릿 sanitize 검증.

실제 MCP 서버 연결 없이 페이크 매니저로 전 액션 경로를 모의 검증한다.
실행: python _probe/probe_gap_e0a.py  (워크스페이스 루트에서)
"""
import json
import sys
import types

sys.path.insert(0, "api")
sys.path.insert(0, "hermes-agent")

import api.mcp_client as mc  # noqa: E402

# ---------------------------------------------------------------------------
# 페이크 MCP 매니저: memory 연결됨, github 미연결
# ---------------------------------------------------------------------------
_fake_conns = {
    "memory": types.SimpleNamespace(
        connected=True, error="",
        to_dict=lambda: {
            "server_id": "memory", "label": "Memory MCP", "transport": "stdio",
            "connected": True, "error": "",
            "tools": [{"name": "create_entities"}, {"name": "search_nodes"}],
        },
    ),
    "github": types.SimpleNamespace(
        connected=False, error="spawn failed",
        to_dict=lambda: {
            "server_id": "github", "label": "GitHub MCP", "transport": "stdio",
            "connected": False, "error": "spawn failed", "tools": [],
        },
    ),
}

fake = types.SimpleNamespace()
fake._connections = _fake_conns
fake.list_servers = lambda: [c.to_dict() for c in _fake_conns.values()]
fake.add_server = lambda **kw: {
    "ok": True,
    "server": {"server_id": kw["server_id"], "label": kw.get("label", ""),
               "transport": kw.get("transport", "stdio"), "connected": False,
               "error": "", "tools": [], "auth_token": kw.get("auth_token", "")},
}
fake.remove_server = lambda sid: (
    {"ok": True, "removed": sid} if sid in _fake_conns
    else {"ok": False, "error": "Server not found"}
)
fake.connect_server = lambda sid: (
    {"ok": True, "server": _fake_conns[sid].to_dict()} if sid in _fake_conns
    else {"ok": False, "error": "Server not found"}
)
fake.disconnect_server = lambda sid: (
    {"ok": True, "server": _fake_conns[sid].to_dict()} if sid in _fake_conns
    else {"ok": False, "error": "Server not found"}
)
mc.get_mcp_manager = lambda: fake

import tools.mcp_manager_tool as mt  # noqa: E402

# ---------------------------------------------------------------------------
# 1. 액션 라우팅: list
# ---------------------------------------------------------------------------
r = mt._dispatch({"action": "list"})
assert r["ok"] is True, r
ids = [s["server_id"] for s in r["servers"]]
assert "memory" in ids and "github" in ids, ids
assert "filesystem" in r["presets"] and "github" in r["presets"], r["presets"].keys()
print("action=list OK:", ids)

# ---------------------------------------------------------------------------
# 2. 액션 라우팅: add (stdio) + 시크릿 sanitize
# ---------------------------------------------------------------------------
r = mt._dispatch({
    "action": "add", "server_id": "my-server", "command": "npx",
    "args": ["-y", "@some/mcp"], "transport": "stdio",
})
assert r["ok"] is True, r
assert r["server"]["server_id"] == "my-server", r
print("action=add (stdio) OK")

r = mt._dispatch({
    "action": "add", "server_id": "remote", "transport": "http",
    "url": "https://example.com/mcp", "auth_token": "SECRET-TOKEN-123",
})
assert r["ok"] is True, r
# auth_token은 결과에서 마스킹되어야 함
assert "SECRET-TOKEN-123" not in json.dumps(r, ensure_ascii=False), r
print("action=add (http) + secret sanitize OK")

# 잘못된 입력 검증
r = mt._dispatch({"action": "add", "server_id": "bad id!", "command": "x"})
assert "error" in r and "Invalid server_id" in r["error"], r
r = mt._dispatch({"action": "add", "server_id": "ok", "transport": "stdio"})
assert "error" in r and "command is required" in r["error"], r
r = mt._dispatch({"action": "add", "server_id": "ok", "transport": "http"})
assert "error" in r and "url is required" in r["error"], r
print("action=add validation OK")

# ---------------------------------------------------------------------------
# 3. 액션 라우팅: add_preset
# ---------------------------------------------------------------------------
r = mt._dispatch({"action": "add_preset", "preset_id": "memory"})
assert r["ok"] is True and r["server"]["server_id"] == "memory", r
r = mt._dispatch({"action": "add_preset", "preset_id": "nonexistent"})
assert "error" in r and "Unknown preset" in r["error"], r
print("action=add_preset OK")

# ---------------------------------------------------------------------------
# 4. 액션 라우팅: connect / disconnect / remove
# ---------------------------------------------------------------------------
r = mt._dispatch({"action": "connect", "server_id": "memory"})
assert r["ok"] is True and r["server"]["connected"] is True, r
r = mt._dispatch({"action": "connect", "server_id": "ghost"})
assert "error" in r and "not found" in r["error"], r
r = mt._dispatch({"action": "disconnect", "server_id": "github"})
assert r["ok"] is True, r
r = mt._dispatch({"action": "remove", "server_id": "github"})
assert r["ok"] is True and r["removed"] == "github", r
print("action=connect/disconnect/remove OK")

# ---------------------------------------------------------------------------
# 5. 액션 라우팅: tools (연결된 서버만)
# ---------------------------------------------------------------------------
r = mt._dispatch({"action": "tools", "server_id": "memory"})
assert r["ok"] is True and r["server"]["tool_count"] == 2, r
assert "create_entities" in r["server"]["tools"], r
r = mt._dispatch({"action": "tools", "server_id": "github"})
assert r.get("ok") is False and "not connected" in r["error"], r
print("action=tools OK")

# ---------------------------------------------------------------------------
# 6. 알 수 없는 액션 + 스키마 무결성
# ---------------------------------------------------------------------------
r = mt._dispatch({"action": "bogus"})
assert "error" in r and "Unknown action" in r["error"], r

schema = mt.MCP_MANAGE_SCHEMA
assert schema["required"] == ["action"], schema["required"]
assert set(schema["properties"]["action"]["enum"]) == {
    "list", "add", "add_preset", "remove", "connect", "disconnect", "tools"
}, schema["properties"]["action"]["enum"]
print("schema integrity OK")

# ---------------------------------------------------------------------------
# 7. registry 등록 확인 (check_fn 포함)
# ---------------------------------------------------------------------------
from tools.registry import registry  # noqa: E402

entry = registry.get_entry("mcp_manage")
assert entry is not None, "mcp_manage not registered"
assert mt.check_mcp_manage_requirements() is True
print("registry registration OK")

print("ALL GAP-E0A PROBES PASSED")
