#!/usr/bin/env python3
"""
Video Generation Tools Module — Multi-Provider

Provides image-to-video generation with start/end frame conditioning
(frame-locking) from multiple providers:

1. **MiniMax** (Hailuo) — direct API, subscription-based, first+last frame
2. **DashScope** (Qwen/Wan) — direct API, subscription-based, first+last frame
3. **FAL.ai** — credit-based, multiple models (Kling, Luma, Seedance, etc.)

Provider selection priority:
  MINIMAX_API_KEY → DASHSCOPE_API_KEY → FAL_KEY / managed gateway

This enables seamless camera-flight chains where consecutive clips share
identical boundary frames — the core requirement for scroll-scrubbed
"fly through the world" landing pages.

Architecture:
- ``DIRECT_VIDEO_PROVIDERS`` catalogs MiniMax and DashScope direct API models.
- ``FAL_VIDEO_MODELS`` catalogs FAL.ai models (existing).
- ``_resolve_video_provider()`` picks the best available provider.
- Each provider has its own submit+poll function (async task pattern).
- Reuses FAL credential/gateway infrastructure from image_generation_tool.
"""

import json
import logging
import os
import time
import datetime
import uuid
import urllib.request
import urllib.error
import ssl
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FAL video model catalog
# ---------------------------------------------------------------------------
FAL_VIDEO_MODELS: Dict[str, Dict[str, Any]] = {
    "fal-ai/kling-video/v2.5-turbo/pro/image-to-video": {
        "display": "Kling 2.5 Turbo Pro",
        "speed": "~60-120s",
        "strengths": "Best frame-lock fidelity, cinematic motion",
        "price": "$0.35-0.70/clip",
        "frame_lock": True,
        "durations": [5, 10],
        "defaults": {
            "duration": 5,
            "cfg_scale": 0.5,
            "negative_prompt": "blurry, distorted, low quality, watermark",
        },
        "supports": {
            "prompt", "image_url", "end_image_url", "duration",
            "cfg_scale", "negative_prompt",
        },
    },
    "fal-ai/luma-dream-machine/image-to-video": {
        "display": "Luma Dream Machine",
        "speed": "~60-90s",
        "strengths": "Start+end frame, smooth camera moves",
        "price": "$0.25/clip",
        "frame_lock": True,
        "durations": [5],
        "defaults": {
            "loop": False,
        },
        "supports": {
            "prompt", "image_url", "end_image_url", "loop",
        },
    },
    "fal-ai/wan/v2.1/image-to-video": {
        "display": "Wan 2.1 I2V",
        "speed": "~90-180s",
        "strengths": "Open-source, good motion quality",
        "price": "$0.15/clip",
        "frame_lock": False,
        "durations": [5],
        "defaults": {
            "num_frames": 81,
            "resolution": "480p",
        },
        "supports": {
            "prompt", "image_url", "num_frames", "resolution",
            "negative_prompt", "seed",
        },
    },
    "fal-ai/minimax/video/image-to-video": {
        "display": "MiniMax Hailuo (via FAL)",
        "speed": "~45-90s",
        "strengths": "Fast, natural motion, first+last frame",
        "price": "$0.20/clip",
        "frame_lock": True,
        "durations": [6],
        "defaults": {},
        "supports": {
            "prompt", "image_url", "end_image_url",
        },
    },
    "fal-ai/seedance/v2/image-to-video": {
        "display": "Seedance 2.0",
        "speed": "~60-120s",
        "strengths": "Start+end frame, high fidelity",
        "price": "$0.30/clip",
        "frame_lock": True,
        "durations": [5, 10],
        "defaults": {
            "duration": 5,
            "resolution": "720p",
        },
        "supports": {
            "prompt", "image_url", "end_image_url", "duration",
            "resolution", "negative_prompt",
        },
    },
}

# Default FAL model: best frame-lock fidelity for scroll-world use cases
DEFAULT_FAL_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"


# ---------------------------------------------------------------------------
# Direct API provider catalog (subscription-based, no FAL credits needed)
# ---------------------------------------------------------------------------
DIRECT_VIDEO_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "minimax": {
        "display": "MiniMax (Hailuo)",
        "env_key": "MINIMAX_API_KEY",
        "api_base": "https://api.minimax.chat/v1",
        "models": {
            "I2V-01-Director": {
                "display": "MiniMax I2V Director",
                "speed": "~45-90s",
                "strengths": "Director-mode camera control, first+last frame",
                "frame_lock": True,
                "durations": [6],
            },
            "I2V-01": {
                "display": "MiniMax I2V",
                "speed": "~45-90s",
                "strengths": "Fast, natural motion, first+last frame",
                "frame_lock": True,
                "durations": [6],
            },
        },
        "default_model": "I2V-01-Director",
    },
    "dashscope": {
        "display": "DashScope (Qwen/Wan)",
        "env_key": "DASHSCOPE_API_KEY",
        "api_base": "https://dashscope.aliyuncs.com/api/v1",
        "models": {
            "wanx2.7-i2v-turbo": {
                "display": "Wan 2.7 I2V Turbo",
                "speed": "~60-120s",
                "strengths": "Latest Wan, first+last frame, high quality",
                "frame_lock": True,
                "durations": [5],
            },
            "wanx2.1-i2v-turbo": {
                "display": "Wan 2.1 I2V Turbo",
                "speed": "~90-180s",
                "strengths": "Open-source, stable",
                "frame_lock": False,
                "durations": [5],
            },
            "wanx2.1-i2v-plus": {
                "display": "Wan 2.1 I2V Plus",
                "speed": "~120-240s",
                "strengths": "Higher quality, slower",
                "frame_lock": False,
                "durations": [5],
            },
        },
        "default_model": "wanx2.7-i2v-turbo",
    },
}

# Provider selection priority (first available wins)
PROVIDER_PRIORITY = ["minimax", "dashscope", "fal"]

# Polling configuration
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600  # 10 minutes max


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------
def _resolve_video_provider(
    model_override: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Resolve the best available video provider and model.

    Returns (provider_name, model_id, model_metadata).

    Priority: MINIMAX_API_KEY → DASHSCOPE_API_KEY → FAL_KEY / managed gateway.
    If model_override is given, it can be:
      - A direct provider model ID (e.g. "I2V-01-Director", "wanx2.7-i2v-turbo")
      - A FAL model ID (e.g. "fal-ai/kling-video/...")
      - A provider:model pair (e.g. "minimax:I2V-01")
    """
    # Handle explicit provider:model override
    if model_override and ":" in model_override:
        prov_name, model_id = model_override.split(":", 1)
        if prov_name in DIRECT_VIDEO_PROVIDERS:
            prov = DIRECT_VIDEO_PROVIDERS[prov_name]
            if model_id in prov["models"]:
                return prov_name, model_id, prov["models"][model_id]
        if prov_name == "fal" and model_override in FAL_VIDEO_MODELS:
            return "fal", model_override, FAL_VIDEO_MODELS[model_override]

    # Handle FAL model override directly
    if model_override and model_override in FAL_VIDEO_MODELS:
        return "fal", model_override, FAL_VIDEO_MODELS[model_override]

    # Handle direct provider model override (search all providers)
    if model_override:
        for prov_name, prov in DIRECT_VIDEO_PROVIDERS.items():
            if model_override in prov["models"]:
                if os.getenv(prov["env_key"]):
                    return prov_name, model_override, prov["models"][model_override]

    # Auto-select: try providers in priority order
    for prov_name in PROVIDER_PRIORITY:
        if prov_name == "fal":
            if os.getenv("FAL_KEY") or _check_managed_gateway():
                # Resolve FAL model from config or default
                fal_model = _resolve_fal_model()
                return "fal", fal_model, FAL_VIDEO_MODELS[fal_model]
        else:
            prov = DIRECT_VIDEO_PROVIDERS[prov_name]
            if os.getenv(prov["env_key"]):
                default_model = prov["default_model"]
                return prov_name, default_model, prov["models"][default_model]

    # Nothing available — return FAL default (will fail at credential check)
    return "fal", DEFAULT_FAL_MODEL, FAL_VIDEO_MODELS[DEFAULT_FAL_MODEL]


def _resolve_fal_model() -> str:
    """Resolve the active FAL video model from config or default."""
    model_id = DEFAULT_FAL_MODEL

    try:
        from hermes_state import load_config
        cfg = load_config()
        vid_cfg = cfg.get("video_gen") if isinstance(cfg, dict) else None
        if isinstance(vid_cfg, dict):
            configured = vid_cfg.get("model", "")
            if configured and configured in FAL_VIDEO_MODELS:
                model_id = configured
    except Exception:
        pass

    env_model = os.getenv("VIDEO_GEN_MODEL", "")
    if env_model and env_model in FAL_VIDEO_MODELS:
        model_id = env_model

    return model_id


# ---------------------------------------------------------------------------
# FAL payload builder
# ---------------------------------------------------------------------------
def _build_video_payload(
    model_id: str,
    prompt: str,
    image_url: str,
    end_image_url: Optional[str] = None,
    duration: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the FAL API payload for a video generation request."""
    meta = FAL_VIDEO_MODELS[model_id]
    supports = meta["supports"]
    defaults = dict(meta.get("defaults", {}))

    payload: Dict[str, Any] = {}
    payload["prompt"] = prompt
    payload["image_url"] = image_url

    if end_image_url and "end_image_url" in supports:
        payload["end_image_url"] = end_image_url

    if duration is not None and "duration" in supports:
        valid_durations = meta.get("durations", [5])
        payload["duration"] = duration if duration in valid_durations else valid_durations[0]

    if negative_prompt and "negative_prompt" in supports:
        payload["negative_prompt"] = negative_prompt
    elif "negative_prompt" in defaults and "negative_prompt" in supports:
        payload["negative_prompt"] = defaults["negative_prompt"]

    for key, value in defaults.items():
        if key in supports and key not in payload:
            payload[key] = value

    payload = {k: v for k, v in payload.items() if k in supports}
    return payload


# ---------------------------------------------------------------------------
# HTTP helper (shared by MiniMax and DashScope)
# ---------------------------------------------------------------------------
def _http_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[dict] = None,
    timeout: int = 30,
) -> dict:
    """Make an HTTP request and return parsed JSON."""
    ctx = ssl.create_default_context()
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# MiniMax direct API (subscription — no FAL credits)
# ---------------------------------------------------------------------------
def _submit_minimax_video(
    model: str,
    prompt: str,
    image_url: str,
    end_image_url: Optional[str] = None,
    **kwargs,
) -> str:
    """Submit a MiniMax video generation request and poll until complete.

    API: POST /v1/video_generation → task_id → poll /v1/query/video_generation
    → file_id → /v1/files/retrieve → download_url

    Returns the video download URL.
    """
    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        raise ValueError("MINIMAX_API_KEY environment variable not set")

    api_base = DIRECT_VIDEO_PROVIDERS["minimax"]["api_base"]
    auth_headers = {"Authorization": f"Bearer {api_key}"}

    # Build request body
    body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "first_frame_image": image_url,
    }
    if end_image_url:
        body["last_frame_image"] = end_image_url

    logger.info("MiniMax submit: model=%s, prompt=%s", model, prompt[:80])

    # Submit task
    result = _http_json(
        f"{api_base}/video_generation",
        method="POST",
        headers=auth_headers,
        body=body,
        timeout=60,
    )

    # Check for immediate errors
    base_resp = result.get("base_resp", {})
    if base_resp.get("status_code", 0) != 0:
        raise ValueError(
            f"MiniMax API error: {base_resp.get('status_msg', 'unknown')} "
            f"(code {base_resp.get('status_code')})"
        )

    task_id = result.get("task_id")
    if not task_id:
        raise ValueError(f"No task_id in MiniMax response: {json.dumps(result)[:300]}")

    logger.info("MiniMax task submitted: %s", task_id)

    # Poll for completion
    video_url = _poll_minimax_task(api_base, auth_headers, task_id)
    return video_url


def _poll_minimax_task(
    api_base: str,
    auth_headers: Dict[str, str],
    task_id: str,
) -> str:
    """Poll MiniMax task until Success, then retrieve video URL via file_id."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)

        result = _http_json(
            f"{api_base}/query/video_generation?task_id={task_id}",
            method="GET",
            headers=auth_headers,
            timeout=30,
        )

        status = result.get("status", "")
        logger.info("MiniMax task %s: status=%s", task_id, status)

        if status == "Success":
            file_id = result.get("file_id")
            if not file_id:
                raise ValueError(f"MiniMax task succeeded but no file_id: {json.dumps(result)[:300]}")

            # Retrieve download URL
            file_result = _http_json(
                f"{api_base}/files/retrieve?file_id={file_id}",
                method="GET",
                headers=auth_headers,
                timeout=30,
            )
            download_url = (
                file_result.get("file", {}).get("download_url")
                or file_result.get("download_url")
            )
            if not download_url:
                raise ValueError(f"No download_url in MiniMax file response: {json.dumps(file_result)[:300]}")
            return download_url

        if status in ("Fail", "Failed"):
            error_msg = result.get("error", result.get("message", "unknown error"))
            raise ValueError(f"MiniMax task failed: {error_msg}")

        # Still processing — continue polling

    raise TimeoutError(f"MiniMax task {task_id} timed out after {POLL_TIMEOUT_SECONDS}s")


# ---------------------------------------------------------------------------
# DashScope direct API (Qwen subscription — no FAL credits)
# ---------------------------------------------------------------------------
def _submit_dashscope_video(
    model: str,
    prompt: str,
    image_url: str,
    end_image_url: Optional[str] = None,
    duration: Optional[int] = None,
    **kwargs,
) -> str:
    """Submit a DashScope video generation request and poll until complete.

    API: POST /services/aigc/video-generation/generation (async)
    → task_id → poll /tasks/{task_id} → video_url

    Returns the video download URL.
    """
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY environment variable not set")

    api_base = DIRECT_VIDEO_PROVIDERS["dashscope"]["api_base"]
    auth_headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
    }

    # Build request body
    input_data: Dict[str, Any] = {
        "prompt": prompt,
        "img_url": image_url,
    }
    if end_image_url:
        input_data["end_img_url"] = end_image_url

    parameters: Dict[str, Any] = {}
    if duration:
        parameters["duration"] = duration

    body: Dict[str, Any] = {
        "model": model,
        "input": input_data,
    }
    if parameters:
        body["parameters"] = parameters

    logger.info("DashScope submit: model=%s, prompt=%s", model, prompt[:80])

    # Submit task
    result = _http_json(
        f"{api_base}/services/aigc/video-generation/generation",
        method="POST",
        headers=auth_headers,
        body=body,
        timeout=60,
    )

    # Extract task_id
    output = result.get("output", {})
    task_id = output.get("task_id")
    if not task_id:
        # Check for error response
        code = result.get("code", "")
        message = result.get("message", "")
        if code or message:
            raise ValueError(f"DashScope API error: {code} — {message}")
        raise ValueError(f"No task_id in DashScope response: {json.dumps(result)[:300]}")

    logger.info("DashScope task submitted: %s", task_id)

    # Poll for completion
    video_url = _poll_dashscope_task(api_base, auth_headers, task_id)
    return video_url


def _poll_dashscope_task(
    api_base: str,
    auth_headers: Dict[str, str],
    task_id: str,
) -> str:
    """Poll DashScope task until SUCCEEDED, return video URL."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    # Remove async header for polling (not needed for GET)
    poll_headers = {k: v for k, v in auth_headers.items() if k != "X-DashScope-Async"}

    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)

        result = _http_json(
            f"{api_base}/tasks/{task_id}",
            method="GET",
            headers=poll_headers,
            timeout=30,
        )

        output = result.get("output", {})
        task_status = output.get("task_status", "")
        logger.info("DashScope task %s: status=%s", task_id, task_status)

        if task_status == "SUCCEEDED":
            video_url = output.get("video_url")
            if not video_url:
                # Try nested result structure
                results = output.get("results", [])
                if results and isinstance(results, list):
                    video_url = results[0].get("url") or results[0].get("video_url")
            if not video_url:
                raise ValueError(f"DashScope task succeeded but no video_url: {json.dumps(output)[:300]}")
            return video_url

        if task_status in ("FAILED", "CANCELED"):
            error_msg = output.get("message", output.get("code", "unknown error"))
            raise ValueError(f"DashScope task failed: {error_msg}")

        # PENDING / RUNNING — continue polling

    raise TimeoutError(f"DashScope task {task_id} timed out after {POLL_TIMEOUT_SECONDS}s")


# ---------------------------------------------------------------------------
# FAL submission (reuses image tool's credential infrastructure)
# ---------------------------------------------------------------------------
def _submit_fal_video(model: str, arguments: Dict[str, Any]):
    """Submit a FAL video generation request.

    Reuses the same credential resolution as image_generation_tool:
    direct FAL_KEY or managed Nous gateway.
    """
    import fal_client

    managed_gateway = None
    try:
        from tools.managed_tool_gateway import resolve_managed_tool_gateway
        from tools.tool_backend_helpers import prefers_gateway
        if not (os.getenv("FAL_KEY") and not prefers_gateway("video_gen")):
            managed_gateway = resolve_managed_tool_gateway("fal-queue")
    except Exception:
        pass

    request_headers = {"x-idempotency-key": str(uuid.uuid4())}

    if managed_gateway is None:
        return fal_client.submit(model, arguments=arguments, headers=request_headers)

    try:
        from tools.image_generation_tool import _get_managed_fal_client
        managed_client = _get_managed_fal_client(managed_gateway)
        return managed_client.submit(model, arguments=arguments, headers=request_headers)
    except Exception as exc:
        if os.getenv("FAL_KEY"):
            logger.warning("Managed gateway failed (%s), falling back to direct FAL", exc)
            return fal_client.submit(model, arguments=arguments, headers=request_headers)
        raise


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------
def video_generate_tool(
    prompt: str,
    image_url: str,
    end_image_url: Optional[str] = None,
    duration: Optional[int] = None,
    negative_prompt: Optional[str] = None,
    model_override: Optional[str] = None,
) -> str:
    """Generate a video clip from a start image (+ optional end image).

    Automatically selects the best available provider:
    MiniMax (subscription) → DashScope (subscription) → FAL.ai (credits).

    Returns a JSON string with:
    {
        "success": bool,
        "video_url": str | None,
        "duration": float,
        "provider": str,
        "model": str,
        "frame_locked": bool,
        "error": str | None
    }
    """
    # Resolve provider and model
    provider, model_id, meta = _resolve_video_provider(model_override)

    start_time = datetime.datetime.now()
    frame_locked = False

    try:
        # Validate inputs
        if not prompt or not isinstance(prompt, str) or len(prompt.strip()) == 0:
            raise ValueError("prompt is required and must be a non-empty string")
        if not image_url or not isinstance(image_url, str):
            raise ValueError("image_url (start frame) is required")

        # Determine frame-lock capability
        if end_image_url:
            if meta.get("frame_lock"):
                frame_locked = True
            else:
                logger.warning(
                    "Model %s does not support end-frame conditioning. "
                    "The clip will start from image_url but end freely.",
                    model_id,
                )
                end_image_url = None

        display_name = meta.get("display", model_id)
        logger.info(
            "Generating video via %s / %s — prompt: %s | frame_lock=%s",
            provider, display_name, prompt[:80], frame_locked,
        )

        # Route to provider
        if provider == "minimax":
            video_url = _submit_minimax_video(
                model=model_id,
                prompt=prompt,
                image_url=image_url,
                end_image_url=end_image_url,
            )

        elif provider == "dashscope":
            video_url = _submit_dashscope_video(
                model=model_id,
                prompt=prompt,
                image_url=image_url,
                end_image_url=end_image_url,
                duration=duration,
            )

        elif provider == "fal":
            # Check FAL credentials
            if not (os.getenv("FAL_KEY") or _check_managed_gateway()):
                raise ValueError(
                    "No video provider credentials found. Set one of: "
                    "MINIMAX_API_KEY, DASHSCOPE_API_KEY, or FAL_KEY."
                )
            arguments = _build_video_payload(
                model_id=model_id,
                prompt=prompt,
                image_url=image_url,
                end_image_url=end_image_url,
                duration=duration,
                negative_prompt=negative_prompt,
            )
            handler = _submit_fal_video(model_id, arguments=arguments)
            result = handler.get()

            if not result:
                raise ValueError("No result returned from FAL.ai video API")

            video_url = None
            if isinstance(result, dict):
                if "video" in result and isinstance(result["video"], dict):
                    video_url = result["video"].get("url")
                elif "video_url" in result:
                    video_url = result["video_url"]
                elif "output" in result and isinstance(result["output"], dict):
                    video_url = result["output"].get("url")
                elif "url" in result:
                    video_url = result["url"]

            if not video_url:
                raise ValueError(
                    f"No video URL in FAL response. Keys: "
                    f"{list(result.keys()) if isinstance(result, dict) else type(result)}"
                )
        else:
            raise ValueError(f"Unknown provider: {provider}")

        generation_time = (datetime.datetime.now() - start_time).total_seconds()

        logger.info(
            "Video generated in %.1fs via %s/%s (frame_lock=%s): %s",
            generation_time, provider, model_id, frame_locked,
            video_url[:100] if video_url else "None",
        )

        response_data = {
            "success": True,
            "video_url": video_url,
            "duration": generation_time,
            "provider": provider,
            "model": model_id,
            "model_display": meta.get("display", model_id),
            "frame_locked": frame_locked,
            "error": None,
        }
        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        generation_time = (datetime.datetime.now() - start_time).total_seconds()
        error_msg = f"Error generating video: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)

        response_data = {
            "success": False,
            "video_url": None,
            "duration": generation_time,
            "provider": provider,
            "model": model_id,
            "model_display": meta.get("display", model_id),
            "frame_locked": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
        return json.dumps(response_data, indent=2, ensure_ascii=False)


def _check_managed_gateway() -> bool:
    """Check if managed FAL gateway is available."""
    try:
        from tools.managed_tool_gateway import resolve_managed_tool_gateway
        gw = resolve_managed_tool_gateway("fal-queue")
        return gw is not None
    except Exception:
        return False


def check_video_generation_requirements() -> bool:
    """True if ANY video provider credentials are available."""
    # Check direct API providers first (subscription-based)
    for prov_name, prov in DIRECT_VIDEO_PROVIDERS.items():
        if os.getenv(prov["env_key"]):
            return True
    # Check FAL
    if os.getenv("FAL_KEY"):
        try:
            import fal_client  # noqa: F401
            return True
        except ImportError:
            pass
    # Check managed gateway
    return _check_managed_gateway()


def list_video_providers() -> str:
    """List all available video providers and their status."""
    providers = []
    for prov_name in PROVIDER_PRIORITY:
        if prov_name == "fal":
            available = bool(os.getenv("FAL_KEY")) or _check_managed_gateway()
            providers.append({
                "provider": "fal",
                "display": "FAL.ai",
                "available": available,
                "auth": "FAL_KEY" if os.getenv("FAL_KEY") else ("managed-gateway" if _check_managed_gateway() else "none"),
                "models": list(FAL_VIDEO_MODELS.keys()),
            })
        else:
            prov = DIRECT_VIDEO_PROVIDERS[prov_name]
            available = bool(os.getenv(prov["env_key"]))
            providers.append({
                "provider": prov_name,
                "display": prov["display"],
                "available": available,
                "auth": prov["env_key"] if available else "none",
                "models": list(prov["models"].keys()),
            })
    return json.dumps(providers, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Frame extraction utility (for scroll-world pipeline)
# ---------------------------------------------------------------------------
def extract_frames_tool(
    video_url: str,
    fps: float = 30.0,
    output_dir: Optional[str] = None,
) -> str:
    """Extract frames from a video URL using ffmpeg.

    Used in the scroll-world pipeline to get the exact last frame of a clip
    for use as the start frame of the next connector clip (frame-identical seams).

    Returns JSON with frame file paths.
    """
    import subprocess
    import tempfile

    try:
        if not video_url:
            raise ValueError("video_url is required")

        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "daon_frames", str(uuid.uuid4())[:8])
        os.makedirs(output_dir, exist_ok=True)

        video_path = os.path.join(output_dir, "input.mp4")
        ctx = ssl.create_default_context()
        urllib.request.urlretrieve(video_url, video_path)

        frame_pattern = os.path.join(output_dir, "frame_%04d.png")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"fps={fps}",
            frame_pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

        frames = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("frame_") and f.endswith(".png")
        ])

        first_frame = frames[0] if frames else None
        last_frame = frames[-1] if frames else None

        response_data = {
            "success": True,
            "frame_count": len(frames),
            "first_frame": first_frame,
            "last_frame": last_frame,
            "output_dir": output_dir,
            "error": None,
        }
        return json.dumps(response_data, indent=2, ensure_ascii=False)

    except Exception as e:
        response_data = {
            "success": False,
            "frame_count": 0,
            "first_frame": None,
            "last_frame": None,
            "output_dir": output_dir if output_dir else None,
            "error": str(e),
        }
        return json.dumps(response_data, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry, tool_error

VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": (
        "Generate a video clip from a start image using image-to-video AI models. "
        "Supports multiple providers: MiniMax (subscription), DashScope/Qwen (subscription), "
        "and FAL.ai (credits). Auto-selects the best available provider. "
        "Optionally provide an end image for frame-locked generation (seamless chains). "
        "Returns a video URL. Use for scroll-world camera flights, animations, "
        "or any image-to-video task. "
        "Set model_override to force a specific provider:model "
        "(e.g. 'minimax:I2V-01-Director', 'dashscope:wanx2.7-i2v-turbo', "
        "or a FAL model ID)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Motion/camera description. E.g. 'camera slowly pushes forward into the scene, gentle parallax, cinematic lighting'.",
            },
            "image_url": {
                "type": "string",
                "description": "URL of the start frame image (the first frame of the generated video will match this).",
            },
            "end_image_url": {
                "type": "string",
                "description": "Optional URL of the end frame image. If provided and the model supports frame-locking, the last frame will match this. Critical for seamless scene transitions.",
            },
            "duration": {
                "type": "integer",
                "enum": [5, 6, 10],
                "description": "Video duration in seconds. Default varies by model. Not all models support all durations.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "What to avoid in the video. E.g. 'blurry, distorted, watermark, text overlay'. (FAL models only)",
            },
            "model_override": {
                "type": "string",
                "description": "Force a specific provider:model. E.g. 'minimax:I2V-01-Director', 'dashscope:wanx2.7-i2v-turbo', or a FAL model ID like 'fal-ai/kling-video/v2.5-turbo/pro/image-to-video'.",
            },
        },
        "required": ["prompt", "image_url"],
    },
}

EXTRACT_FRAMES_SCHEMA = {
    "name": "video_extract_frames",
    "description": (
        "Extract frames from a video file/URL using ffmpeg. "
        "Returns paths to the first and last frames — used to get exact boundary "
        "frames for seamless scroll-world chains. Requires ffmpeg on PATH."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "video_url": {
                "type": "string",
                "description": "URL or local path of the video to extract frames from.",
            },
            "fps": {
                "type": "number",
                "description": "Frames per second to extract. Default 30. Use 1 for sparse sampling.",
                "default": 30,
            },
            "output_dir": {
                "type": "string",
                "description": "Optional output directory for frame files. Auto-created if not specified.",
            },
        },
        "required": ["video_url"],
    },
}


def _handle_video_generate(args, **kw):
    prompt = args.get("prompt", "")
    image_url = args.get("image_url", "")
    if not prompt:
        return tool_error("prompt is required for video generation")
    if not image_url:
        return tool_error("image_url (start frame) is required for video generation")
    return video_generate_tool(
        prompt=prompt,
        image_url=image_url,
        end_image_url=args.get("end_image_url"),
        duration=args.get("duration"),
        negative_prompt=args.get("negative_prompt"),
        model_override=args.get("model_override"),
    )


def _handle_extract_frames(args, **kw):
    video_url = args.get("video_url", "")
    if not video_url:
        return tool_error("video_url is required for frame extraction")
    return extract_frames_tool(
        video_url=video_url,
        fps=args.get("fps", 30.0),
        output_dir=args.get("output_dir"),
    )


registry.register(
    name="video_generate",
    toolset="video_gen",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=_handle_video_generate,
    check_fn=check_video_generation_requirements,
    requires_env=[],
    is_async=False,
    emoji="🎬",
)

registry.register(
    name="video_extract_frames",
    toolset="video_gen",
    schema=EXTRACT_FRAMES_SCHEMA,
    handler=_handle_extract_frames,
    check_fn=lambda: True,  # Only needs ffmpeg, checked at runtime
    requires_env=[],
    is_async=False,
    emoji="🖼️",
)
