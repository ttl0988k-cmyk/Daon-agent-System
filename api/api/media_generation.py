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
    'cogview', 'kolors', 'image-01',
]

_VIDEO_PATTERNS = [
    'sora', 'kling', 'runway', 'pika', 'luma', 'cogvideo', 'video-generation',
    'minimax-video', 'hailuo', 'wan-video', 'mochi', 't2v-01', 'i2v-01',
    'happyhorse',
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
    # Segment-based detection (e.g., agnes-image-2.0-flash, wan-video-v2,
    # happyhorse-1.1-t2v — t2v/i2v/r2v are text/image/reference-to-video)
    import re
    _segments = set(re.split(r'[-_.]', lower))
    if _segments & {'image', 'img'}:
        return 'image'
    if _segments & {'video', 'vid', 't2v', 'i2v', 'r2v'}:
        return 'video'
    for pat in _IMAGE_PATTERNS:
        if pat in lower:
            return 'image'
    for pat in _VIDEO_PATTERNS:
        if pat in lower:
            return 'video'
    return 'chat'


# ─── Prompt Enhancement (LLM-based) ───

_ENHANCE_SYSTEM_IMAGE = (
    "You are an expert prompt engineer for AI image generation models (DALL-E, Flux, Stable Diffusion, Midjourney, etc.).\n"
    "Given a user's brief or vague description, produce a SINGLE optimized English prompt that will generate the best possible image.\n\n"
    "Rules:\n"
    "1. Output ONLY the enhanced prompt text — no explanations, no quotes, no markdown.\n"
    "2. Include: subject, style, composition, lighting, color palette, mood, quality modifiers.\n"
    "3. Keep it under 150 words.\n"
    "4. If the user's intent is ambiguous, make a reasonable creative choice.\n"
    "5. Preserve the user's core intent — do NOT change what they want, only enhance HOW it's described.\n"
    "6. Add quality boosters: 'highly detailed', 'professional', '8k', etc. when appropriate.\n"
    "7. For logos/icons: add 'vector style, clean, minimal, scalable'.\n"
    "8. For photos: add 'photorealistic, natural lighting, depth of field'.\n"
    "9. For illustrations: add 'digital art, vibrant, detailed illustration'.\n"
)

_ENHANCE_SYSTEM_VIDEO = (
    "You are an expert prompt engineer for AI video generation models (Sora, Kling, Runway, Pika, etc.).\n"
    "Given a user's brief or vague description, produce a SINGLE optimized English prompt that will generate the best possible video.\n\n"
    "Rules:\n"
    "1. Output ONLY the enhanced prompt text — no explanations, no quotes, no markdown.\n"
    "2. Include: subject, action/motion, camera movement, style, lighting, mood, duration feel.\n"
    "3. Keep it under 100 words.\n"
    "4. Describe MOTION explicitly: 'slowly panning', 'zooming in', 'gentle wind blowing'.\n"
    "5. Preserve the user's core intent — do NOT change what they want, only enhance HOW it's described.\n"
    "6. Add cinematic quality: 'cinematic', 'smooth motion', 'high quality', '4k'.\n"
)


def _enhance_media_prompt(prompt: str, media_type: str = "image") -> str:
    """Use an LLM to enhance a vague user prompt into an optimized generation prompt.
    
    Falls back to the original prompt if enhancement fails (never blocks generation).
    """
    if not prompt or not prompt.strip():
        return prompt

    # Skip enhancement if prompt is already detailed (heuristic: > 80 words)
    word_count = len(prompt.split())
    if word_count > 80:
        _log.info("[media-enhance] Prompt already detailed (%d words), skipping enhancement", word_count)
        return prompt

    system_instruction = _ENHANCE_SYSTEM_IMAGE if media_type == "image" else _ENHANCE_SYSTEM_VIDEO

    try:
        from api.dynamic.direct_calls import _call_direct
        enhanced = _call_direct(
            prompt=f"User request: {prompt}",
            system_instruction=system_instruction,
            preferred_model=None,
        )
        enhanced = enhanced.strip().strip('"').strip("'").strip()
        # Remove markdown code blocks if present
        if enhanced.startswith("```"):
            lines = enhanced.split("\n")
            enhanced = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
        
        if enhanced and len(enhanced) > 10:
            _log.info("[media-enhance] Enhanced prompt (%s): '%s' → '%s'",
                      media_type, prompt[:60], enhanced[:80])
            return enhanced
        else:
            _log.warning("[media-enhance] Enhancement returned too short, using original")
            return prompt
    except Exception as e:
        _log.warning("[media-enhance] Enhancement failed (%s), using original prompt: %s", media_type, e)
        return prompt


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


def _dashscope_size(size: str) -> str:
    """Normalize an image size to DashScope's `width*height` format.

    DashScope (Wan2.7) rejects `1024x1024` with
    `Invalid size format ... expected format: width*height`.  Accept the
    common OpenAI-style `x` separator and convert it to `*`.
    """
    s = (size or '').strip()
    if not s:
        return '1024*1024'
    # 'x' / 'X' separator → '*' (only when it looks like WxH)
    import re
    m = re.match(r'^\s*(\d+)\s*[xX×]\s*(\d+)\s*$', s)
    if m:
        return f"{m.group(1)}*{m.group(2)}"
    return s


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
    ds_size = _dashscope_size(size)
    _log.info("[media] DashScope size normalized: %r → %r", size, ds_size)

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
        "parameters": {"size": ds_size, "n": n},
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
        # 400 = 파라미터 오류 (size 형식 등) → 재시도/fallback 무의미, 즉시 실패
        if e.code == 400:
            _log.error("[media] DashScope sync HTTP 400 (parameter error): %s", body[:500])
            raise RuntimeError(f"DashScope 파라미터 오류 (HTTP 400): {body[:300]}")
        # 403/404 = 권한/엔드포인트 문제 → async endpoint로 fallback
        _log.warning("[media] DashScope sync failed (%s) → trying async endpoint", sync_err)
    except Exception as e:
        sync_err = str(e)
        _log.warning("[media] DashScope sync error (%s) → trying async endpoint", sync_err)

    # ── 2) Asynchronous: image-generation/generation + polling (fallback) ──
    async_url = native_base + '/services/aigc/image-generation/generation'
    async_payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"size": ds_size, "n": n},
    }
    _log.info("[media] DashScope async POST %s | model=%s", async_url, model)
    try:
        result = _post(async_url, async_payload, async_mode=True)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("DashScope async HTTP %s at %s: %s", e.code, async_url, body[:500])
        # 400 = 파라미터 오류 → 즉시 실패
        if e.code == 400:
            raise RuntimeError(f"DashScope 파라미터 오류 (HTTP 400): {body[:300]}")
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


def _minimax_aspect_ratio(size: str) -> str:
    """Map a frontend size string ("WxH") to a MiniMax aspect_ratio string.

    MiniMax image-01 accepts aspect ratios ("1:1", "16:9", "9:16", ...) rather
    than pixel dimensions. The frontend mediaSizeSelect offers:
      1024x1024 / 512x512 → "1:1", 1792x1024 → "16:9", 1024x1792 → "9:16".
    Falls back to the closest of 1:1 / 16:9 / 9:16 by orientation.
    """
    width, height = 1024, 1024
    try:
        parts = str(size).lower().split('x')
        if len(parts) == 2:
            width, height = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass

    if width <= 0 or height <= 0:
        return "1:1"
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"


def _generate_image_minimax(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = "1024x1024",
    n: int = 1,
) -> dict:
    """
    MiniMax image-01 native API adapter.
    Endpoint: POST {base_url}/image_generation  (underscore — NOT /image/generation)
    Body: {"model": "image-01", "prompt": "...", "aspect_ratio": "16:9",
           "response_format": "url", "n": 3, "prompt_optimizer": true}
    Response: {"base_resp": {"status_code": 0},
               "data": {"image_urls": ["https://..."]}}
    """
    aspect_ratio = _minimax_aspect_ratio(size)
    count = max(1, int(n or 1))

    url = base_url.rstrip('/') + '/image_generation'
    payload = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "url",
        "n": count,
        "prompt_optimizer": True,
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

    _log.info("[media-minimax] POST %s | aspect_ratio=%s n=%d", url, aspect_ratio, count)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("[media-minimax] HTTP %s: %s", e.code, body[:500])
        raise RuntimeError(f"MiniMax 이미지 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        _log.error("[media-minimax] request error: %s", e)
        raise RuntimeError(f"MiniMax 이미지 생성 실패: {e}")

    # Check base_resp status
    base_resp = result.get('base_resp', {})
    if base_resp.get('status_code', 0) != 0:
        raise RuntimeError(f"MiniMax 이미지 생성 실패: {base_resp.get('status_msg', 'unknown error')}")

    # Extract image URLs from data.image_urls (response_format="url")
    images = []
    data = result.get('data', {})
    if isinstance(data, dict):
        for u in data.get('image_urls', []) or []:
            if u:
                images.append({"url": u, "b64_json": ""})
        # Fallback: some responses embed base64 under data.image
        if not images and data.get('image'):
            images.append({"url": "", "b64_json": data.get('image', '')})

    if not images:
        raise RuntimeError(f"MiniMax 이미지 생성 성공이나 이미지 데이터 없음: {json.dumps(result)[:300]}")

    _log.info("[media-minimax] %d image(s) extracted | urls=%s",
              len(images), [(im.get('url') or '')[:120] for im in images])
    return {
        "images": images,
        "revised_prompt": "",
    }


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

    # Auto-enhance prompt for optimal image quality (any model, any agent)
    prompt = _enhance_media_prompt(prompt, media_type="image")

    # Wan/wanx (DashScope) models: go straight to the native API.
    _lower_model = (model or '').lower()
    if ('wan' in _lower_model) or ('wanx' in _lower_model):
        _log.info("③ [media] Wan/wanx model detected → using DashScope native API directly")
        return _generate_image_dashscope_native(prompt, model, base_url, api_key, size, n)

    # MiniMax image-01: dedicated adapter (different endpoint + response schema)
    if 'image-01' in _lower_model or 'minimax' in (base_url or '').lower():
        _log.info("③ [media] MiniMax image model detected → using MiniMax native API")
        return _generate_image_minimax(prompt, model, base_url, api_key, size, n)

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


def _generate_video_minimax(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    poll_interval: float = 10.0,
    max_wait: float = 600.0,
) -> dict:
    """
    MiniMax video generation native API adapter.
    1. POST {base_url}/video_generation → {"task_id": "..."}
    2. GET {base_url}/query/video_generation?task_id=... → {"status": "Success", "file_id": "..."}
    3. POST {base_url}/files/retrieve → {"file": {"download_url": "..."}}
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    # Step 1: Create task
    create_url = base_url.rstrip('/') + '/video_generation'
    payload = {"model": model, "prompt": prompt}
    req = urllib.request.Request(
        create_url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    _log.info("[media-minimax] POST %s", create_url)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8', errors='replace'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("[media-minimax] video create HTTP %s: %s", e.code, body[:500])
        raise RuntimeError(f"MiniMax 영상 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        raise RuntimeError(f"MiniMax 영상 생성 실패: {e}")

    base_resp = result.get('base_resp', {})
    if base_resp.get('status_code', 0) != 0:
        raise RuntimeError(f"MiniMax 영상 생성 실패: {base_resp.get('status_msg', 'unknown')}")

    task_id = result.get('task_id')
    if not task_id:
        raise RuntimeError(f"MiniMax 영상 생성: task_id 없음: {json.dumps(result)[:300]}")

    _log.info("[media-minimax] video task_id=%s, polling...", task_id)

    # Step 2: Poll until done
    poll_url = f"{base_url.rstrip('/')}/query/video_generation?task_id={task_id}"
    start = time.time()
    file_id = None

    while time.time() - start < max_wait:
        time.sleep(poll_interval)
        try:
            poll_req = urllib.request.Request(poll_url, headers=headers, method='GET')
            with urllib.request.urlopen(poll_req, timeout=30) as resp:
                status_result = json.loads(resp.read().decode('utf-8', errors='replace'))
        except Exception as e:
            _log.warning("[media-minimax] poll error: %s", e)
            continue

        status = status_result.get('status', '')
        _log.info("[media-minimax] poll status=%s", status)

        if status == 'Success':
            file_id = status_result.get('file_id')
            break
        elif status in ('Fail', 'Failed', 'Error'):
            err = status_result.get('base_resp', {}).get('status_msg', 'unknown')
            raise RuntimeError(f"MiniMax 영상 생성 실패: {err}")
        # Preparing / Queueing → continue polling

    if not file_id:
        raise RuntimeError(f"MiniMax 영상 생성 시간 초과 ({max_wait}초)")

    # Step 3: Retrieve download URL
    retrieve_url = base_url.rstrip('/') + '/files/retrieve'
    retrieve_payload = {"file_id": file_id}
    retrieve_req = urllib.request.Request(
        retrieve_url,
        data=json.dumps(retrieve_payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(retrieve_req, timeout=30) as resp:
            file_result = json.loads(resp.read().decode('utf-8', errors='replace'))
    except Exception as e:
        raise RuntimeError(f"MiniMax 영상 파일 조회 실패: {e}")

    download_url = file_result.get('file', {}).get('download_url', '')
    if not download_url:
        raise RuntimeError(f"MiniMax 영상 다운로드 URL 없음: {json.dumps(file_result)[:300]}")

    _log.info("[media-minimax] video_url=%s", download_url[:120])
    return {"video_url": download_url, "status": "completed"}


def _generate_video_dashscope_native(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = None,
    image_url: str = None,
    poll_interval: float = 5.0,
    max_wait: float = 300.0,
) -> dict:
    """
    DashScope native video generation for HappyHorse / Wan video models
    (Alibaba token-plan.* 및 dashscope.* 도메인).

    The OpenAI-compatible /video/generations endpoint returns 404 on Alibaba
    domains — video models are only served on the native async API:

      POST {native_base}/services/aigc/video-generation/video-synthesis
           + header `X-DashScope-Async: enable`
      → {"output": {"task_id": ..., "task_status": "PENDING"}}
      GET  {native_base}/tasks/{task_id}   (polling)
      → {"output": {"task_status": "SUCCEEDED", "video_url": ...}}

    native_base is derived from base_url via _dashscope_native_base().
    """
    native_base = _dashscope_native_base(base_url)

    url = native_base + '/services/aigc/video-generation/video-synthesis'
    _input = {"prompt": prompt}
    if image_url:
        # I2V (happyhorse-1.1-i2v / Wan i2v): input.media is a list of
        # MediaItem objects — {"type": "first_frame", "url": ...}.
        # Verified against the live API: missing/invalid shapes fail async
        # validation with `Field required: input.media` /
        # `Input should be 'first_frame': input.media.0.type`.
        _input["media"] = [{"type": "first_frame", "url": image_url}]
    payload = {
        "model": model,
        "input": _input,
    }
    if size:
        payload["parameters"] = {"size": _dashscope_size(size)}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    _log.info("[media] DashScope video POST %s | model=%s", url, model)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        _log.error("[media] DashScope video HTTP %s: %s", e.code, body[:500])
        raise RuntimeError(f"DashScope 영상 생성 실패 (HTTP {e.code}): {body[:200]}")
    except Exception as e:
        _log.error("[media] DashScope video request error: %s", e)
        raise RuntimeError(f"DashScope 영상 생성 실패: {e}")

    task_id = result.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"DashScope 영상 생성: task_id 없음 — {json.dumps(result)[:200]}")

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
            _log.warning("DashScope video poll error: %s", e)
            continue

        output = status_result.get("output", {})
        task_status = output.get("task_status", "")
        if task_status == "SUCCEEDED":
            video_url = output.get("video_url") or output.get("url") or ""
            if not video_url:
                # results[] shape fallback
                for r in output.get("results") or []:
                    if isinstance(r, dict) and r.get("url"):
                        video_url = r["url"]
                        break
            if video_url:
                _log.info("[media] DashScope video OK: %s", video_url[:120])
                return {"video_url": video_url, "status": "completed"}
            raise RuntimeError(f"DashScope 영상 생성 성공이나 URL 없음: {json.dumps(status_result)[:200]}")
        elif task_status in ("FAILED", "CANCELED", "UNKNOWN"):
            err_msg = output.get("message") or output.get("code") or "알 수 없는 오류"
            raise RuntimeError(f"DashScope 영상 생성 실패: {err_msg}")

    raise RuntimeError(f"DashScope 영상 생성 시간 초과 ({max_wait}초)")


def generate_video(
    prompt: str,
    model: str,
    base_url: str,
    api_key: str,
    size: str = None,
    image_url: str = None,
    poll_interval: float = 5.0,
    max_wait: float = 300.0,
) -> dict:
    """
    Call /video/generations endpoint (async pattern).
    1. POST to create task → get task_id
    2. Poll GET /video/generations/{task_id} until done
    Returns: {"video_url": ..., "status": "completed"}
    """
    # Auto-enhance prompt for optimal video quality (any model, any agent)
    prompt = _enhance_media_prompt(prompt, media_type="video")

    # MiniMax video models: dedicated adapter (different endpoint + polling schema)
    _lower_model = (model or '').lower()
    if 't2v-01' in _lower_model or 'i2v-01' in _lower_model or 'minimax' in (base_url or '').lower():
        _log.info("[media] MiniMax video model detected → using MiniMax native API")
        return _generate_video_minimax(prompt, model, base_url, api_key)

    # DashScope (Alibaba) video models — HappyHorse/Wan: the OpenAI-compatible
    # /video/generations endpoint returns 404 on aliyuncs.com domains; use the
    # native async video-synthesis API instead.
    _lower_base = (base_url or '').lower()
    if 'aliyuncs.com' in _lower_base or _lower_model.startswith(('happyhorse', 'wan')):
        _log.info("[media] DashScope video model detected → using native API")
        return _generate_video_dashscope_native(
            prompt, model, base_url, api_key,
            size=size, image_url=image_url,
            poll_interval=poll_interval, max_wait=max_wait,
        )

    url = base_url.rstrip('/') + '/video/generations'
    payload = {
        "model": model,
        "prompt": prompt,
    }
    if size:
        payload["size"] = size
    if image_url:
        payload["image_url"] = image_url
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
    size: str = None,
    n: int = None,
    image_url: str = None,
) -> dict:
    """
    Unified entry: detect type and call appropriate API.
    size/n come from the frontend media-option panel (aspect ratio / count).
    Returns:
      image → {"type": "image", "images": [...], "revised_prompt": ...}
      video → {"type": "video", "video_url": ..., "status": ...}
    """
    _log.info("[media] run_media_generation entered | model=%s type=%s base_url=%s size=%s n=%s image_url=%s", model, model_type, base_url, size, n, (image_url or '')[:80])
    if model_type is None:
        model_type = detect_model_type(model)

    if model_type == 'image':
        img_kwargs = {}
        if size:
            img_kwargs['size'] = size
        if n and n >= 1:
            img_kwargs['n'] = int(n)
        result = generate_image(prompt, model, base_url, api_key, **img_kwargs)
        result["type"] = "image"
        return result
    elif model_type == 'video':
        vid_kwargs = {}
        if size:
            vid_kwargs['size'] = size
        if image_url:
            vid_kwargs['image_url'] = image_url
        result = generate_video(prompt, model, base_url, api_key, **vid_kwargs)
        result["type"] = "video"
        return result
    else:
        raise ValueError(f"Unsupported model type: {model_type}")


# ─── Workspace File Saving (Gap 2) ───

def _resolve_workspace_dir() -> str:
    """도구 실행 시 기준이 되는 워크스페이스 디렉토리.

    터미널 도구와 동일하게 TERMINAL_CWD 환경변수를 우선 사용하고,
    없으면 현재 작업 디렉토리를 쓴다. save_path 상대경로는 이 디렉토리 기준.
    """
    return os.getenv("TERMINAL_CWD") or os.getcwd()


def _ext_from_data_url(header: str) -> str:
    h = (header or "").lower()
    if "jpeg" in h or "jpg" in h:
        return ".jpg"
    if "webp" in h:
        return ".webp"
    if "gif" in h:
        return ".gif"
    return ".png"


def save_generated_media(source: str, save_path: str, index: int = 0) -> str:
    """생성된 미디어(data: base64 또는 http(s) URL)를 워크스페이스에 파일로 저장한다.

    Args:
        source: 'data:image/...;base64,...' 또는 'http(s)://...' URL.
        save_path: 저장 대상. 디렉토리(끝이 / 또는 \\, 빈 문자열, 기존 디렉토리)이면
                   그 안에 'generated_<ts>_<index>.<ext>'로 자동 이름 저장.
                   파일명(확장자 포함)이면 그 경로에 저장하되 다중 결과 시 '_<index>' 삽입.
        index: 다중 생성 시 0부터의 순번.

    Returns:
        워크스페이스 기준 상대경로 (슬래시 정규화). 저장 실패 시 예외 발생.
    """
    import base64
    import time as _time

    workspace = _resolve_workspace_dir()

    # 1) 소스에서 바이트 + 확장자 추출
    if source.startswith("data:"):
        header, _, b64 = source.partition(",")
        raw = base64.b64decode(b64)
        ext = _ext_from_data_url(header)
    elif source.startswith("http://") or source.startswith("https://"):
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
        ext = os.path.splitext(source.split("?")[0])[1] or ".png"
    else:
        raise ValueError(f"Unsupported media source: {source[:48]}")

    # 2) 저장 경로 결정
    sp = (save_path or "").strip()
    is_dir = (
        sp == ""
        or sp.endswith(("/", "\\"))
        or os.path.isdir(os.path.join(workspace, sp))
    )
    if is_dir:
        ts = int(_time.time())
        fname = f"generated_{ts}_{index}{ext}"
        target_dir = os.path.join(workspace, sp) if sp else workspace
        full_path = os.path.join(target_dir, fname)
    else:
        if index > 0:
            stem, e = os.path.splitext(sp)
            sp = f"{stem}_{index}{e or ext}"
        full_path = os.path.join(workspace, sp)

    parent = os.path.dirname(full_path) or workspace
    os.makedirs(parent, exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(raw)

    # 3) 워크스페이스 기준 상대경로 반환
    try:
        return os.path.relpath(full_path, workspace).replace("\\", "/")
    except ValueError:
        return full_path.replace("\\", "/")


# ─── Shared Registry Registration (chat streaming + dynamic harness) ───

def _collect_media_models():
    """등록된 이미지/비디오 모델 id 목록을 반환한다."""
    from api.managers.model_manager import model_manager as _mm
    image_models, video_models = [], []
    try:
        for g in _mm.get_available_models():
            for m in g.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                mtype = m.get('type') if isinstance(m, dict) else None
                if not mtype:
                    mtype = _mm.get_model_type(mid)
                if mtype == 'image' and mid not in image_models:
                    image_models.append(mid)
                elif mtype == 'video' and mid not in video_models:
                    video_models.append(mid)
    except Exception as e:
        _log.warning("[media] collect models failed: %s", e)
    return image_models, video_models


def _make_media_resolver(image_models, video_models):
    """generate_image/generate_video 공용 실행 클로저를 만든다.

    save_path 인자가 있으면 생성 결과를 워크스페이스에 파일로 저장하고
    응답에 saved_paths(워크스페이스 상대경로)를 포함한다.
    """
    import json as _json
    from api.managers.model_manager import model_manager as _mm

    def resolve_and_run(args: dict, media_type: str) -> str:
        prompt = (args.get('prompt') or '').strip()
        if not prompt:
            return _json.dumps({"error": "prompt is required"}, ensure_ascii=False)
        candidates = image_models if media_type == 'image' else video_models
        model_id = (args.get('model') or '').strip()
        if not model_id:
            if not candidates:
                return _json.dumps({"error": f"No {media_type} model registered. Add one in Settings."}, ensure_ascii=False)
            model_id = candidates[0]
        try:
            resolved_model, provider, base_url = _mm.resolve_model_provider(model_id)
        except Exception as _re:
            return _json.dumps({"error": f"model resolve failed: {_re}"}, ensure_ascii=False)
        if not base_url:
            try:
                base_url = _mm._get_base_url(provider) or ''
            except Exception:
                base_url = ''
        try:
            api_key = _mm._get_api_key(provider) or ''
        except Exception:
            api_key = ''
        if not base_url:
            return _json.dumps({"error": f"No base_url for provider '{provider}'"}, ensure_ascii=False)
        if not api_key:
            return _json.dumps({"error": f"No API key for provider '{provider}'"}, ensure_ascii=False)
        try:
            result = run_media_generation(
                prompt=prompt,
                model=resolved_model,
                base_url=base_url,
                api_key=api_key,
                model_type=media_type,
                size=args.get('size') or None,
                n=args.get('n') or None,
                image_url=(args.get('image_url') or '').strip() or None,
            )
        except Exception as _ge:
            return _json.dumps({"error": f"{media_type} generation failed: {_ge}"}, ensure_ascii=False)

        save_path = (args.get('save_path') or '').strip()

        if media_type == 'image':
            imgs = result.get('images', []) if isinstance(result, dict) else []
            urls = []
            for im in imgs:
                if im.get('b64_json'):
                    urls.append(f"data:image/png;base64,{im['b64_json']}")
                elif im.get('url'):
                    urls.append(im['url'])
            resp = {
                "ok": True,
                "model": resolved_model,
                "image_urls": urls,
                "instruction": "Embed each image in your reply using markdown: ![generated image](URL). Use the exact URLs provided.",
            }
            if save_path and urls:
                saved = []
                for idx, u in enumerate(urls):
                    try:
                        saved.append(save_generated_media(u, save_path, idx))
                    except Exception as _se:
                        _log.warning("[media] image save failed (idx=%d): %s", idx, _se)
                if saved:
                    resp["saved_paths"] = saved
                    resp["instruction"] = (
                        "Images were also saved to the workspace. Reference them in HTML/CSS using "
                        "the relative paths in 'saved_paths' (e.g. <img src=\"assets/hero.png\">). "
                        "You may also embed the data/HTTP URLs via markdown."
                    )
            return _json.dumps(resp, ensure_ascii=False)
        else:
            vurl = result.get('video_url', '') if isinstance(result, dict) else ''
            resp = {
                "ok": True,
                "model": resolved_model,
                "video_url": vurl,
                "instruction": "Share the video using markdown: [video](URL) or an HTML <video controls src=URL> tag.",
            }
            if save_path and vurl:
                try:
                    resp["saved_paths"] = [save_generated_media(vurl, save_path, 0)]
                except Exception as _se:
                    _log.warning("[media] video save failed: %s", _se)
            return _json.dumps(resp, ensure_ascii=False)

    return resolve_and_run


def build_media_tool_schemas(image_models, video_models):
    """OpenAI function-call 스키마 2개(generate_image, generate_video)를 만들어 반환."""
    img_props = {
        "prompt": {"type": "string", "description": "Detailed description of the image to generate."},
        "size": {"type": "string", "description": "Aspect size: 1024x1024 (1:1), 1792x1024 (16:9), or 1024x1792 (9:16).", "enum": ["1024x1024", "1792x1024", "1024x1792", "512x512"]},
        "n": {"type": "integer", "description": "Number of images (1-4).", "minimum": 1, "maximum": 4},
        "save_path": {"type": "string", "description": "Optional. Save the generated image(s) into the workspace so they can be referenced from files (e.g. an index.html). Give a directory like 'assets/' or 'images/hero/' to auto-name files, or a filename like 'assets/hero.png'. When set, the response includes 'saved_paths' (workspace-relative) suitable for <img src> in HTML/CSS."},
    }
    if image_models:
        img_props["model"] = {"type": "string", "description": "Image model to use.", "enum": image_models}
    else:
        img_props["model"] = {"type": "string", "description": "Image model to use."}
    img_schema = {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text prompt using a registered image model. Call this when the task needs a picture, illustration, hero/background image, or any visual asset. Returns image URLs to embed in your reply; if 'save_path' is given, also saves files to the workspace and returns their relative paths in 'saved_paths'.",
            "parameters": {"type": "object", "properties": img_props, "required": ["prompt"]},
        },
    }

    vid_props = {
        "prompt": {"type": "string", "description": "Detailed description of the video to generate."},
        "size": {"type": "string", "description": "Aspect size: 1024x1024 (1:1), 1792x1024 (16:9), or 1024x1792 (9:16).", "enum": ["1024x1024", "1792x1024", "1024x1792"]},
        "image_url": {"type": "string", "description": "Optional. Reference image URL (http/https) for image-to-video (I2V) models like happyhorse-1.1-i2v. The image becomes the first frame of the generated video. Required when using an I2V model."},
        "save_path": {"type": "string", "description": "Optional. Save the generated video into the workspace (a directory or filename). When set, the response includes 'saved_paths'."},
    }
    if video_models:
        vid_props["model"] = {"type": "string", "description": "Video model to use.", "enum": video_models}
    else:
        vid_props["model"] = {"type": "string", "description": "Video model to use."}
    vid_schema = {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a short video from a text prompt using a registered video model. Call this when the task needs a video or animation. For image-to-video (I2V) models like happyhorse-1.1-i2v, pass 'image_url' (the reference image becomes the first frame). Returns a video URL; if 'save_path' is given, also saves the file to the workspace.",
            "parameters": {"type": "object", "properties": vid_props, "required": ["prompt"]},
        },
    }
    return img_schema, vid_schema


def register_media_generation_tools(registry) -> tuple:
    """registry에 generate_image/generate_video 도구를 멱등 등록하고,
    채팅 에이전트 주입용 OpenAI 스키마 2개를 반환한다.

    채팅(streaming.py)과 다이나믹 하네스(runner.py) 양쪽에서 호출해
    'media-generation' toolset이 항상 registry에 존재하도록 보장한다.
    동일 toolset 재등록은 registry가 허용하므로 여러 번 호출해도 안전하다.

    Returns:
        (img_schema, vid_schema) — OpenAI function-call 스키마 튜플.
    """
    image_models, video_models = _collect_media_models()
    resolver = _make_media_resolver(image_models, video_models)
    img_schema, vid_schema = build_media_tool_schemas(image_models, video_models)

    registry.register(
        name="generate_image",
        toolset="media-generation",
        schema={
            "name": "generate_image",
            "description": img_schema["function"]["description"],
            "parameters": img_schema["function"]["parameters"],
        },
        handler=lambda args, **kw: resolver(args, 'image'),
        check_fn=lambda: True,
        is_async=False,
        description="Generate an image using a registered image model",
    )
    registry.register(
        name="generate_video",
        toolset="media-generation",
        schema={
            "name": "generate_video",
            "description": vid_schema["function"]["description"],
            "parameters": vid_schema["function"]["parameters"],
        },
        handler=lambda args, **kw: resolver(args, 'video'),
        check_fn=lambda: True,
        is_async=False,
        description="Generate a video using a registered video model",
    )
    try:
        registry.register_toolset_alias("media", "media-generation")
    except Exception:
        pass

    _log.info(
        "[media] Registered generate_image (%d models) + generate_video (%d models) into registry.",
        len(image_models), len(video_models),
    )
    return img_schema, vid_schema
