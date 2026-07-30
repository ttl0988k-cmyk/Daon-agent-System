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


def _dashscope_native_base(base_url: str) -> str:
    """Derive the DashScope native API base (.../api/v1) from an OpenAI-compatible base_url.

    .../compatible-mode/v1 → .../api/v1  (same domain, different path prefix)
    """
    stripped = base_url.rstrip('/')
    if '/compatible-mode/v1' in stripped:
        return stripped.replace('/compatible-mode/v1', '/api/v1')
    if stripped.endswith('/v1'):
        return stripped[:-3] + '/api/v1'
    return stripped + '/api/v1'


def _extract_images_from_output(output: dict) -> list:
    """Extract [{url, b64_json}] from a DashScope `output` object.

    Handles both shapes:
      - output.results[]: {url, b64_image}
      - output.choices[].message.content[]: {image: <url|data-uri>}
    """
    images: list = []
    if not isinstance(output, dict):
        return images

    # Shape A: output.results[]
    for r in output.get("results") or []:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or r.get("image") or ""
        b64 = r.get("b64_image") or r.get("b64_json") or ""
        if url or b64:
            images.append({"url": url, "b64_json": b64})

    # Shape B: output.choices[].message.content[]
    for choice in output.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        content = message.get("content")
        parts = content if isinstance(content, list) else ([{"text": content}] if isinstance(content, str) else [])
        for part in parts:
            if not isinstance(part, dict):
                continue
            img = part.get("image") or part.get("url") or ""
            if img:
                if img.startswith("data:"):
                    # data:image/png;base64,.... → strip prefix
                    b64 = img.split(",", 1)[1] if "," in img else img
                    images.append({"url": "", "b64_json": b64})
                else:
                    images.append({"url": img, "b64_json": ""})

    return images


def _generate_image_dashscope_native(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = "1024x1024",
    n: int = 1,
    poll_interval: float = 3.0,
    max_wait: float = 120.0,
) -> dict:
    """
    DashScope native image generation for Wan2.7+ (Alibaba WAN/wanx models).

    The OpenAI-compatible /images/generations endpoint returns 404 on Alibaba
    domains (e.g. token-plan.*), and the legacy Wan2.5/2.6 async endpoint
    (/services/aigc/text2image/image-synthesis) returns
    `AccessDenied: current user api does not support asynchronous calls` for
    Wan2.7 plans.  Per the latest Wan2.7 docs the correct endpoints are:

      Sync  (preferred): POST {native_base}/services/aigc/multimodal-generation/generation
      Async (fallback) : POST {native_base}/services/aigc/image-generation/generation
                         + header `X-DashScope-Async: enable`
                         + GET {native_base}/tasks/{task_id} polling

    native_base is derived from base_url via _dashscope_native_base().
    """
    native_base = _dashscope_native_base(base_url)

    def _post(url: str, payload: dict, async_mode: bool):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if async_mode:
            headers["X-DashScope-Async"] = "enable"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers,
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode('utf-8'))

    # ── 1) Synchronous: multimodal-generation/generation (preferred for Wan2.7) ──
    sync_url = native_base + '/services/aigc/multimodal-generation/generation'
    sync_payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {"size": size, "n": n},
    }
    _log.info("[media] DashScope sync POST %s | model=%s", sync_url, model)
    sync_err = None
    try:
        result = _post(sync_url, sync_payload, async_mode=False)
        _log.info("[media] DashScope sync response keys=%s", list(result.keys()) if isinstance(result, dict) else type(result))
        images = _extract_images_from_output(result.get("output", {}))
        if images:
            _log.info("[media] DashScope sync OK: %d image(s)", len(images))
            return {"images": images, "revised_prompt": ""}
        # 200 but no images → treat as failure, fall through to async
        sync_err = f"동기 응답에 이미지 없음 — {json.dumps(result)[:200]}"
        _log.warning("[media] DashScope sync returned no images: %s", sync_err)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        sync_err = f"HTTP {e.code}: {body[:200]}"
        _log.warning("[media] DashScope sync failed (%s) → trying async endpoint", sync_err)
    except Exception as e:
        sync_err = str(e)
        _log.warning("[media] DashScope sync error (%s) → trying async endpoint", sync_err)

    # ── 2) Asynchronous: image-generation/generation + polling (fallback) ──
    async_url = native_base + '/services/aigc/image-generation/generation'
    async_payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": n},
    }
    _log.info("[media] DashScope async POST %s | model=%s", async_url, model)
    try:
        result = _post(async_url, async_payload, async_mode=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("DashScope async HTTP %s at %s: %s", e.code, async_url, body[:500])
        raise RuntimeError(
            f"DashScope 네이티브 API 실패 (동기: {sync_err} / 비동기 HTTP {e.code}): {body[:200]}"
        )

    output = result.get("output", {})

    # Some async plans return results directly
    images = _extract_images_from_output(output)
    if images and not output.get("task_id"):
        return {"images": images, "revised_prompt": ""}

    task_id = output.get("task_id")
    if not task_id:
        raise RuntimeError(
            f"DashScope 네이티브 API: task_id 없음 (동기: {sync_err}) — {json.dumps(result)[:200]}"
        )

    poll_url = f"{native_base}/tasks/{task_id}"
    poll_headers = {"Authorization": f"Bearer {api_key}"}
    start = time.time()

    while time.time() - start < max_wait:
        time.sleep(poll_interval)
        try:
            poll_req = urllib.request.Request(poll_url, headers=poll_headers, method='GET')
            with urllib.request.urlopen(poll_req, timeout=30) as resp:
                status_result = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            _log.warning("DashScope poll error: %s", e)
            continue

        task_status = status_result.get("output", {}).get("task_status", "")
        if task_status == "SUCCEEDED":
            images = _extract_images_from_output(status_result.get("output", {}))
            if images:
                return {"images": images, "revised_prompt": ""}
            raise RuntimeError(f"DashScope 이미지 생성 성공이나 결과 파싱 실패: {json.dumps(status_result)[:200]}")
        elif task_status in ("FAILED", "CANCELED", "UNKNOWN"):
            err_msg = status_result.get("output", {}).get("message", "알 수 없는 오류")
            raise RuntimeError(f"DashScope 이미지 생성 실패: {err_msg}")

    raise RuntimeError(f"DashScope 이미지 생성 시간 초과 ({max_wait}초)")


def generate_image(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = "1024x1024",
    n: int = 1,
) -> dict:
    """
    Generate an image.

    Routing:
      - Wan/wanx (Alibaba DashScope) models skip the OpenAI-compatible
        /images/generations endpoint entirely (it returns 404/403 on
        token-plan.* domains) and go straight to the DashScope native API
        (sync multimodal-generation → async image-generation fallback).
      - All other models use the OpenAI-compatible /images/generations
        endpoint, falling back to the DashScope native API on 404/403.

    Returns: {"images": [{"url": ..., "b64_json": ...}], "revised_prompt": ...}
    """
    _log.info("① [media] generate_image entered | model=%s", model)
    _log.info("② [media] base_url=%s | api_key=%s", base_url, "set" if api_key else "MISSING")

    # Wan/wanx (DashScope) models: go straight to the native API.
    _lower_model = (model or '').lower()
    if ('wan' in _lower_model) or ('wanx' in _lower_model):
        _log.info("③ [media] Wan/wanx model detected → using DashScope native API directly")
        return _generate_image_dashscope_native(prompt, model, base_url, api_key, size, n)

    url = base_url.rstrip('/') + '/images/generations'
    _log.info("③ [media] url=%s", url)

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

    _log.info("④ [media] sending request to %s", url)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            _log.info("⑤ [media] response status=%s", getattr(resp, 'status', 200))
            _raw_body = resp.read().decode('utf-8', errors='replace')
            _ctype = ''
            try:
                _ctype = resp.headers.get('content-type', '')
            except Exception:
                pass
            _log.info("⑥ [media] content-type=%s | body_len=%d | body[:800]=%s",
                      _ctype, len(_raw_body), _raw_body[:800])
            result = json.loads(_raw_body)
            _log.info("⑥b [media] parsed json keys=%s", list(result.keys()) if isinstance(result, dict) else type(result))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("⑤ [media] HTTP error %s at %s: %s", e.code, url, body[:500])
        # 404/403 → OpenAI-compatible endpoint missing/disallowed. Try the
        # DashScope native API (Alibaba WAN/wanx models expose a native
        # endpoint, not /images/generations, on domains such as token-plan.*).
        if e.code in (404, 403):
            _log.info("[media] Falling back to DashScope native API (HTTP %s)", e.code)
            try:
                return _generate_image_dashscope_native(prompt, model, base_url, api_key, size, n)
            except Exception as native_err:
                _log.error("DashScope native fallback failed: %s", native_err)
                raise RuntimeError(f"이미지 생성 실패 (HTTP {e.code} + 네이티브 fallback 실패): {native_err}")
        raise RuntimeError(f"이미지 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        _log.error("⑤ [media] request error: %s", e)
        raise RuntimeError(f"이미지 생성 실패: {e}")

    images = []
    for item in result.get('data', []):
        images.append({
            "url": item.get("url", ""),
            "b64_json": item.get("b64_json", ""),
        })

    _log.info("⑦ [media] extracted %d image(s) | urls=%s | b64=%s",
              len(images),
              [ (im.get('url') or '')[:120] for im in images ],
              [ bool(im.get('b64_json')) for im in images ])

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
    _log.info("[media] run_media_generation entered | model=%s type=%s base_url=%s", model, model_type, base_url)
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
