#!/usr/bin/env python3
"""Probe DashScope happyhorse-1.1-i2v to discover the required input-image field name.

Strategy: send an I2V request with NO image field. The API should reject it and
echo back which field it expects (img_url / media / image_url), revealing the
correct schema without needing a real image.
"""
import urllib.request
import urllib.error
import json

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
NATIVE_BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
URL = NATIVE_BASE + '/services/aigc/video-generation/video-synthesis'

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


# Probe 1: I2V with prompt only — expect an error naming the missing image field.
probe("i2v prompt-only", {
    "model": "happyhorse-1.1-i2v",
    "input": {"prompt": "a cat walking on the street"},
})

# Probe 2: I2V with img_url field (Wan-style) — see if accepted or rejected.
probe("i2v img_url", {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a cat walking on the street",
        "img_url": "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg",
    },
})

# Probe 3: I2V with media field — alternative schema.
probe("i2v media", {
    "model": "happyhorse-1.1-i2v",
    "input": {
        "prompt": "a cat walking on the street",
        "media": "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg",
    },
})
