import json
p = r"C:\daon\Daon agent System\data\mcp_servers.json"
d = json.load(open(p, encoding="utf-8"))
print("count:", len(d))
for s in d:
    print(s.get("server_id"), "|", s.get("label"))
