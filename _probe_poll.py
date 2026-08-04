#!/usr/bin/env python3
"""Poll the 3 probe tasks to determine which image field happyhorse-1.1-i2v accepts."""
import urllib.request
import json
import time

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
NATIVE_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"

TASKS = {
    "prompt-only": "e01c57e9-6243-4609-a75e-e49b0d955996",
    "img_url": "e6e6cb60-fa93-4e37-bbbc-ec4b15e0fad9",
    "media": "787aa425-e8e0-480a-9d52-de7714bbdbd4",
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
            print(f"[{label}] SUCCEEDED video_url={vurl[:100]}")
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


deadline = time.time() + 240
pending = dict(TASKS)
while pending and time.time() < deadline:
    for label in list(pending.keys()):
        if poll_once(label, pending[label]):
            del pending[label]
    if pending:
        time.sleep(10)

for label in pending:
    print(f"[{label}] TIMEOUT (still not finished)")
