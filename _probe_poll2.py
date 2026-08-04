#!/usr/bin/env python3
"""Poll round-2 probe tasks: which input.media element shape succeeds."""
import urllib.request
import json
import time

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
NATIVE_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"

TASKS = {
    "media=[url]": "789dced5-da64-4506-a5ce-0e05c48997a7",
    "media=[{type,url}]": "a2a03e95-8b8a-42a9-9c2f-f336fec4626a",
    "media=[{image:url}]": "f1dc882b-8034-4c7d-9051-5bcd66747ecd",
}

HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def poll_once(label, task_id):
    url = f"{NATIVE_BASE}/tasks/{task_id}"
    req = urllib.request.Request(url, headers=HEADERS, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode('utf-8'))
        out = r.get("output", {})
        st = out.get("task_status", "")
        if st == "SUCCEEDED":
            vurl = out.get("video_url") or out.get("url") or ""
            print(f"[{label}] SUCCEEDED video_url={vurl[:120]}")
            return True
        elif st in ("FAILED", "CANCELED", "UNKNOWN"):
            print(f"[{label}] {st} -> code={out.get('code')} msg={str(out.get('message'))[:300]}")
            return True
        else:
            print(f"[{label}] {st} (still running)")
            return False
    except Exception as e:
        print(f"[{label}] poll error: {e}")
        return False


deadline = time.time() + 280
pending = dict(TASKS)
while pending and time.time() < deadline:
    for label in list(pending.keys()):
        if poll_once(label, pending[label]):
            del pending[label]
    if pending:
        time.sleep(10)

for label in pending:
    print(f"[{label}] TIMEOUT (still not finished)")
