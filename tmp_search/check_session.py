# -*- coding: utf-8 -*-
"""현재 세션(26ab4f099371) 저장 상태 진단: 마지막 응답이 온전히 저장됐는지 확인."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

p = r"C:\Users\ttl09\AppData\Local\DAON Agent System\data\sessions\26ab4f099371.json"
d = json.load(open(p, encoding="utf-8"))
msgs = d.get("messages", [])
print("세션 키:", list(d.keys()))
print("총 메시지:", len(msgs))
print()
for m in msgs[-8:]:
    r = m.get("role")
    c = m.get("content") or ""
    if isinstance(c, list):
        c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    c = str(c)
    print("=" * 60)
    print(f"[{r}] ({len(c)}자)")
    print("시작:", c[:150].replace("\n", " / "))
    print("끝  :", c[-150:].replace("\n", " / "))
