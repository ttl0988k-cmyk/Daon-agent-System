#!/usr/bin/env python3
"""Final probe: input.media=[{type:first_frame, url}] should succeed end-to-end."""
import urllib.request
import urllib.error
import json
import time

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
NATIVE_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
URL = NATIVE_BASE + '/services/aigc/video-generation/video-synthesis'
IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
    "X-DashScope-Async": "enable",
}

payload = {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a girl walking on the street, cinematic",
        "media": [{"type": "first_frame", "url": IMG}],
    },
}

req = urllib.request.Request(
    URL, data=json.dumps(payload).encode('utf-8'),
    headers=HEADERS, method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f"POST HTTP {e.code} -> {e.read().decode('utf-8', errors='replace')[:500]}")
    raise SystemExit(1)

task_id = result.get("output", {}).get("task_id")
print(f"POST OK task_id={task_id}")
if not task_id:
    raise SystemExit(1)

poll_url = f"{NATIVE_BASE}/tasks/{task_id}"
poll_headers = {"Authorization": f"Bearer {API_KEY}"}
deadline = time.time() + 280
while time.time() < deadline:
    time.sleep(10)
    preq = urllib.request.Request(poll_url, headers=poll_headers, method='GET')
    with urllib.request.urlopen(preq, timeout=30) as resp:
        r = json.loads(resp.read().decode('utf-8'))
    out = r.get("output", {})
    st = out.get("task_status", "")
    print(f"  status={st}")
    if st == "SUCCEEDED":
        vurl = out.get("video_url") or out.get("url") or ""
        print(f"SUCCESS video_url={vurl}")
        break
    elif st in ("FAILED", "CANCELED", "UNKNOWN"):
        print(f"FAILED code={out.get('code')} msg={str(out.get('message'))[:400]}")
        break
else:
    print("TIMEOUT")
