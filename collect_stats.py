#!/usr/bin/env python3
import os, json, glob
ROOT = r"C:\daon\Daon agent System"
def count_lines(pattern):
    total = 0
    files = glob.glob(pattern, recursive=True)
    for f in files:
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                total += sum(1 for _ in fh)
        except: pass
    return total, len(files)

py_loc, py_files = count_lines(os.path.join(ROOT, "dist_new", "api", "**", "*.py"))
js_loc, js_files = count_lines(os.path.join(ROOT, "dist_new", "static", "**", "*.js"))
hermes_loc, hermes_files = count_lines(os.path.join(ROOT, "hermes-agent", "**", "*.py"))

skills = glob.glob(os.path.join(ROOT, "dist_new", "skills", "*.md"))
roles = glob.glob(os.path.join(ROOT, "dist_new", "skills", "roles", "*.md"))
route_files = glob.glob(os.path.join(ROOT, "dist_new", "api", "routes", "*.py"))
dynamic_files = [f for f in glob.glob(os.path.join(ROOT, "dist_new", "api", "dynamic", "*.py")) if "__init__" not in f and "REFACTOR" not in f]
frontend = glob.glob(os.path.join(ROOT, "dist_new", "static", "modules", "*.js"))
tools = glob.glob(os.path.join(ROOT, "hermes-agent", "tools", "*.py"))

# count route paths
route_count = 0
init_path = os.path.join(ROOT, "dist_new", "api", "routes", "__init__.py")
with open(init_path, 'r', encoding='utf-8', errors='ignore') as f:
    for line in f:
        if "path ==" in line:
            route_count += 1

# count MCP servers
mcp_count = 0
mcp_path = os.path.join(ROOT, "dist_new", "data", "mcp_servers.json")
if os.path.exists(mcp_path):
    try:
        with open(mcp_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            mcp_count = len(data.get("mcpServers", {}))
    except: pass

print(f"PY_LOC={py_loc}")
print(f"PY_FILES={py_files}")
print(f"JS_LOC={js_loc}")
print(f"JS_FILES={js_files}")
print(f"HERMES_LOC={hermes_loc}")
print(f"HERMES_FILES={hermes_files}")
print(f"SKILLS={len(skills)}")
print(f"ROLES={len(roles)}")
print(f"ROUTE_FILES={len(route_files)}")
print(f"ROUTE_PATHS={route_count}")
print(f"DYNAMIC_MODULES={len(dynamic_files)}")
print(f"FRONTEND_MODULES={len(frontend)}")
print(f"TOOL_MODULES={len(tools)}")
print(f"MCP_SERVERS={mcp_count}")
