"""
Media Generation Module — 이미지/영상 생성 모델 지원.

모델 타입 감지:
  - 이미지: dall-e, flux, stable-diffusion, midjourney, ideogram, playground
  - 영상: sora, kling, runway, pika, luma, minimax-video, cogvideo

API 호출:
  - 이미지: POST {base_url}/images/generations (OpenAI 호환)
  - 영상: POST {base_url}/video/generations (비동기 폴링)
"""

import json
import os
import time
import urllib.request
import urllib.error
from typing import Optional

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# ─── Model Type Detection ───

_IMAGE_PATTERNS = [
    'dall-e', 'dalle', 'flux', 'stable-diffusion', 'sdxl', 'midjourney',
    'ideogram', 'playground', 'imagen', 'image-generation', 'wanx',
    'cogview', 'kolors',
]

_VIDEO_PATTERNS = [
    'sora', 'kling', 'runway', 'pika', 'luma', 'cogvideo', 'video-generation',
    'minimax-video', 'hailuo', 'wan-video', 'mochi',
]


def detect_model_type(model_id: str) -> str:
    """Detect whether a model is 'chat', 'image', or 'video' based on its name."""
    if not model_id:
        return 'chat'
    lower = model_id.lower()
    # Suffix-based detection (e.g., wan2.7-image, wan2.7-video, flux-image)
    if lower.endswith(('-image', '_image', '-img', '_img')):
        return 'image'
    if lower.endswith(('-video', '_video', '-vid', '_vid')):
        return 'video'
    # Segment-based detection (e.g., agnes-image-2.0-flash, wan-video-v2)
    import re
    _segments = set(re.split(r'[-_.]', lower))
    if _segments & {'image', 'img'}:
        return 'image'
    if _segments & {'video', 'vid'}:
        return 'video'
    for pat in _IMAGE_PATTERNS:
        if pat in lower:
            return 'image'
    for pat in _VIDEO_PATTERNS:
        if pat in lower:
            return 'video'
    return 'chat'


# ─── Image Generation ───

def generate_image(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = "1024x1024",
    n: int = 1,
) -> dict:
    """
    Call OpenAI-compatible /images/generations endpoint.
    Returns: {"images": [{"url": ..., "b64_json": ...}], "revised_prompt": ...}
    """
    url = base_url.rstrip('/') + '/images/generations'
    payload = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error(f"Image generation HTTP {e.code}: {body[:500]}")
        raise RuntimeError(f"이미지 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        _log.error(f"Image generation error: {e}")
        raise RuntimeError(f"이미지 생성 실패: {e}")

    images = []
    for item in result.get('data', []):
        images.append({
            "url": item.get("url", ""),
            "b64_json": item.get("b64_json", ""),
        })

    return {
        "images": images,
        "revised_prompt": result.get("revised_prompt", ""),
    }


# ─── Video Generation ───

def generate_video(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    poll_interval: float = 5.0,
    max_wait: float = 300.0,
) -> dict:
    """
    Call /video/generations endpoint (async pattern).
    1. POST to create task → get task_id
    2. Poll GET /video/generations/{task_id} until done
    Returns: {"video_url": ..., "status": "completed"}
    """
    url = base_url.rstrip('/') + '/video/generations'
    payload = {
        "model": model,
        "prompt": prompt,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error(f"Video generation HTTP {e.code}: {body[:500]}")
        raise RuntimeError(f"영상 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        _log.error(f"Video generation error: {e}")
        raise RuntimeError(f"영상 생성 실패: {e}")

    # Some APIs return video_url directly (synchronous)
    if result.get("video_url") or result.get("url"):
        return {
            "video_url": result.get("video_url") or result.get("url"),
            "status": "completed",
        }

    # Async pattern: poll for completion
    task_id = result.get("id") or result.get("task_id")
    if not task_id:
        # No task_id and no direct URL — return whatever we got
        return {
            "video_url": result.get("output", {}).get("video_url", ""),
            "status": result.get("status", "unknown"),
            "raw": result,
        }

    poll_url = f"{base_url.rstrip('/')}/video/generations/{task_id}"
    start = time.time()

    while time.time() - start < max_wait:
        time.sleep(poll_interval)
        try:
            poll_req = urllib.request.Request(poll_url, headers=headers, method='GET')
            with urllib.request.urlopen(poll_req, timeout=30) as resp:
                status_result = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            _log.warning(f"Video poll error: {e}")
            continue

        status = status_result.get("status", "")
        if status in ("completed", "succeeded", "success"):
            video_url = (
                status_result.get("video_url")
                or status_result.get("output", {}).get("video_url")
                or status_result.get("url")
                or ""
            )
            return {"video_url": video_url, "status": "completed"}
        elif status in ("failed", "error", "cancelled"):
            error_msg = status_result.get("error", "알 수 없는 오류")
            raise RuntimeError(f"영상 생성 실패: {error_msg}")

    raise RuntimeError(f"영상 생성 시간 초과 ({max_wait}초)")


# ─── Unified Entry Point ───

def run_media_generation(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    model_type: str = None,
) -> dict:
    """
    Unified entry: detect type and call appropriate API.
    Returns:
      image → {"type": "image", "images": [...], "revised_prompt": ...}
      video → {"type": "video", "video_url": ..., "status": ...}
    """
    if model_type is None:
        model_type = detect_model_type(model)

    if model_type == 'image':
        result = generate_image(prompt, model, base_url, api_key)
        result["type"] = "image"
        return result
    elif model_type == 'video':
        result = generate_video(prompt, model, base_url, api_key)
        result["type"] = "video"
        return result
    else:
        raise ValueError(f"Unsupported model type: {model_type}")
