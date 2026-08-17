"""갭 B 기능 프로브: 컴파일러 MCP 바인딩 시맨틱 + 플러그인 해석 + planner 카탈로그 블록.

실제 MCP 서버 연결 없이 페이크 매니저로 _inject_mcp_tools의
None/[]/목록 시맨틱을 검증한다.
"""
import sys
import types

sys.path.insert(0, "api")

import api.mcp_client as mc

# 페이크 MCP 매니저: github/filesystem 연결됨, memory 미연결
fake = types.SimpleNamespace()
fake._connections = {
    "github": types.SimpleNamespace(connected=True),
    "filesystem": types.SimpleNamespace(connected=True),
    "memory": types.SimpleNamespace(connected=False),
}
# planner._build_mcp_catalog_block()이 사용하는 list_servers() — 실제 to_dict() 형식
fake.list_servers = lambda: [
    {"server_id": "github", "label": "GitHub MCP", "connected": True,
     "tools": [{"name": "create_issue"}, {"name": "search_repos"}]},
    {"server_id": "filesystem", "label": "Filesystem MCP", "connected": True,
     "tools": [{"name": "read_file"}]},
    {"server_id": "memory", "label": "Memory MCP", "connected": False, "tools": []},
]
mc.get_mcp_manager = lambda: fake

from api.dynamic.compiler import AgentCompiler  # noqa: E402

r_none = AgentCompiler._inject_mcp_tools(["file"])
r_empty = AgentCompiler._inject_mcp_tools(["file"], [])
r_sel = AgentCompiler._inject_mcp_tools(["file"], ["github", "memory"])

assert r_none == ["file", "mcp-github", "mcp-filesystem"], r_none
assert r_empty == ["file"], r_empty
# memory는 미연결이라 제외, github만 주입
assert r_sel == ["file", "mcp-github"], r_sel
print("MCP binding semantics OK:", r_none, r_empty, r_sel)

# 플러그인 해석: None/빈 목록은 빈 결과
assert AgentCompiler._resolve_plugin_skills(None) == []
assert AgentCompiler._resolve_plugin_skills([]) == []
print("Plugin resolve empty-input OK")

# planner 카탈로그 빌더: MCP 카탈로그 블록이 페이크 서버를 나열하는지
import api.dynamic.planner as planner_mod  # noqa: E402

block = planner_mod._build_mcp_catalog_block()
assert "AVAILABLE MCP SERVERS" in block, block[:200]
assert "'github'" in block and "'filesystem'" in block and "'memory'" in block
assert "CONNECTED" in block and "NOT CONNECTED" in block
print("MCP catalog block OK")

env_block = planner_mod._build_environment_options_block()
assert '"local"' in env_block and '"sandbox"' in env_block
print("Environment options block OK")

print("ALL GAP-B PROBES PASSED")
