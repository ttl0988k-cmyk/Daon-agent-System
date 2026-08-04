#!/usr/bin/env python3
"""Probe happyhorse-1.1-i2v input.media list element shape."""
import urllib.request
import urllib.error
import json

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
NATIVE_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
URL = NATIVE_BASE + '/services/aigc/video-generation/video-synthesis'
IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
    "X-DashScope-Async": "enable",
}


def probe(label, payload):
    print("=" * 70)
    print(f"[{label}] payload={json.dumps(payload, ensure_ascii=False)}")
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode('utf-8'),
        headers=HEADERS, method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("  HTTP 200 ->", resp.read().decode('utf-8')[:600])
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f"  HTTP {e.code} -> {body[:600]}")
    except Exception as e:
        print(f"  ERR -> {e}")
    print()


# A: list of plain URL strings
probe("media=[url]", {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a girl walking on the street",
        "media": [IMG],
    },
})

# B: list of {type, url} objects
probe("media=[{type,url}]", {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a girl walking on the street",
        "media": [{"type": "image", "url": IMG}],
    },
})

# C: list of {image: url} objects
probe("media=[{image:url}]", {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a girl walking on the street",
        "media": [{"image": IMG}],
    },
})
