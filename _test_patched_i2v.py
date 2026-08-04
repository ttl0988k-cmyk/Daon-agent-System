#!/usr/bin/env python3
"""End-to-end test of the PATCHED _generate_video_dashscope_native with image_url."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api'))

from api.media_generation import _generate_video_dashscope_native  # noqa: E402

API_KEY = "sk-sp-H.XYEY.22c4.MEQCIALplCIxF4srVNxYpEuh0-cKO4wylCsWd8tsDB3Rx8XBAiB1wKPPGJFXaUyYcatOWybybw5ps-6GpUW37K8imCdQFg"
BASE_URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
IMG = "https://dashscope.oss-cn-beijing.aliyuncs.com/images/dog_and_girl.jpeg"

result = _generate_video_dashscope_native(
    prompt="a girl walking on the street, cinematic lighting",
    model="happyhorse-1.1-i2v",
    base_url=BASE_URL,
    api_key=API_KEY,
    image_url=IMG,
    max_wait=300.0,
)
print("RESULT:", result)
assert result.get("status") == "completed", "not completed"
assert result.get("video_url"), "no video_url"
print("PATCHED ADAPTER I2V TEST: PASS")
