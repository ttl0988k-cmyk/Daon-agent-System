#!/usr/bin/env python3
"""
Video Generation Tools Module

Provides image-to-video generation via FAL.ai with start/end frame conditioning
(frame-locking). This enables seamless camera-flight chains where consecutive
clips share identical boundary frames — the core requirement for scroll-scrubbed
"fly through the world" landing pages.

Architecture:
- ``FAL_VIDEO_MODELS`` is a catalog of supported image-to-video models with
  per-model metadata (duration support, frame-locking capability, params).
- ``_build_video_payload()`` translates unified inputs into model-specific
  payloads, filtering to the ``supports`` whitelist.
- Reuses the same FAL credential/gateway infrastructure as image_generation_tool
  (FAL_KEY env var or managed Nous gateway).
- Video generation is inherently slow (30s–8min); the tool submits and polls
  synchronously with progress logging.

Supported models (all support start-frame conditioning):
- Kling 2.5 Turbo Pro — best frame-lock fidelity, 5/10s
- Luma Dream Machine — start+end frame, 5s
- Wan 2.1 — open-source, 5s
- MiniMax Hailuo — fast, 6s
- Seedance 2.0 — start+end frame, 5/10s (via FAL)
"""

import json
import logging
import os
import time
import datetime
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# FAL video model catalog
# ---------------------------------------------------------------------------
# Each entry declares:
#   display       — human-readable name
#   speed         — typical generation time
#   strengths     — what it's best at
#   price         — approximate cost per generation
#   frame_lock    — whether end-frame conditioning is supported (critical for seams)
#   durations     — allowed duration values in seconds
#   defaults      — default parameters sent to FAL
#   supports      — whitelist of allowed payload keys

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
        "display": "MiniMax Hailuo",
        "speed": "~45-90s",
        "strengths": "Fast, natural motion",
        "price": "$0.20/clip",
        "frame_lock": False,
        "durations": [6],
        "defaults": {},
        "supports": {
            "prompt", "image_url",
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

# Default: best frame-lock fidelity for scroll-world use cases
DEFAULT_VIDEO_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"

# Polling configuration
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 600  # 10 minutes max


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------
def _resolve_video_model() -> tuple:
    """Resolve the active video model from config or default.

    Returns (model_id, metadata_dict).
    """
    model_id = DEFAULT_VIDEO_MODEL

    # Try loading from config.yaml (video_gen.model)
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

    # Environment override
    env_model = os.getenv("VIDEO_GEN_MODEL", "")
    if env_model and env_model in FAL_VIDEO_MODELS:
        model_id = env_model

    return model_id, FAL_VIDEO_MODELS[model_id]


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------
def _build_video_payload(
    model_id: str,
    prompt: str,
    image_url: str,
    end_image_url: Optional[str] = None,
    duration: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the FAL API payload for a video generation request.

    Filters to the model's ``supports`` whitelist so unsupported keys are
    never sent (each FAL model rejects unknown keys differently).
    """
    meta = FAL_VIDEO_MODELS[model_id]
    supports = meta["supports"]
    defaults = dict(meta.get("defaults", {}))

    payload: Dict[str, Any] = {}

    # Core params
    payload["prompt"] = prompt
    payload["image_url"] = image_url

    # End frame (frame-locking) — only if model supports it
    if end_image_url and "end_image_url" in supports:
        payload["end_image_url"] = end_image_url

    # Duration
    if duration is not None and "duration" in supports:
        valid_durations = meta.get("durations", [5])
        payload["duration"] = duration if duration in valid_durations else valid_durations[0]

    # Negative prompt
    if negative_prompt and "negative_prompt" in supports:
        payload["negative_prompt"] = negative_prompt
    elif "negative_prompt" in defaults and "negative_prompt" in supports:
        payload["negative_prompt"] = defaults["negative_prompt"]

    # Apply remaining defaults (only keys in supports)
    for key, value in defaults.items():
        if key in supports and key not in payload:
            payload[key] = value

    # Final filter: only keep keys in supports
    payload = {k: v for k, v in payload.items() if k in supports}

    return payload


# ---------------------------------------------------------------------------
# FAL submission (reuses image tool's credential infrastructure)
# ---------------------------------------------------------------------------
def _submit_video_request(model: str, arguments: Dict[str, Any]):
    """Submit a FAL video generation request.

    Reuses the same credential resolution as image_generation_tool:
    direct FAL_KEY or managed Nous gateway.
    """
    import fal_client

    # Try managed gateway first (same logic as image tool)
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
        # Direct FAL.ai credentials
        return fal_client.submit(model, arguments=arguments, headers=request_headers)

    # Managed gateway — use image tool's client infrastructure
    try:
        from tools.image_generation_tool import _get_managed_fal_client
        managed_client = _get_managed_fal_client(managed_gateway)
        return managed_client.submit(model, arguments=arguments, headers=request_headers)
    except Exception as exc:
        # Fallback to direct if gateway fails
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
    """Generate a video clip from a start image (+ optional end image) using FAL.ai.

    This is the core tool for scroll-world pipelines: given a scene still and
    optionally the next scene's still, it produces a camera-flight clip whose
    first frame matches the start image and (if supported) last frame matches
    the end image — enabling seamless scroll-scrubbed chains.

    Returns a JSON string with:
    {
        "success": bool,
        "video_url": str | None,
        "duration": float,
        "model": str,
        "frame_locked": bool,
        "error": str | None
    }
    """
    # Resolve model
    if model_override and model_override in FAL_VIDEO_MODELS:
        model_id = model_override
    else:
        model_id, _ = _resolve_video_model()
    meta = FAL_VIDEO_MODELS[model_id]

    start_time = datetime.datetime.now()

    try:
        # Validate inputs
        if not prompt or not isinstance(prompt, str) or len(prompt.strip()) == 0:
            raise ValueError("prompt is required and must be a non-empty string")
        if not image_url or not isinstance(image_url, str):
            raise ValueError("image_url (start frame) is required")

        # Check credentials
        if not (os.getenv("FAL_KEY") or _check_managed_gateway()):
            raise ValueError(
                "FAL_KEY environment variable not set and managed gateway unavailable. "
                "Set FAL_KEY or configure the Nous Subscription gateway."
            )

        # Warn if end frame requested but model doesn't support it
        frame_locked = False
        if end_image_url:
            if meta.get("frame_lock"):
                frame_locked = True
            else:
                logger.warning(
                    "Model %s does not support end-frame conditioning. "
                    "The clip will start from image_url but end freely. "
                    "For seamless chains, use a frame-lock model (Kling, Luma, Seedance).",
                    model_id,
                )
                end_image_url = None  # Strip unsupported param

        # Build payload
        arguments = _build_video_payload(
            model_id=model_id,
            prompt=prompt,
            image_url=image_url,
            end_image_url=end_image_url,
            duration=duration,
            negative_prompt=negative_prompt,
        )

        logger.info(
            "Generating video with %s (%s) — prompt: %s | frame_lock=%s",
            meta.get("display", model_id), model_id, prompt[:80], frame_locked,
        )

        # Submit and poll
        handler = _submit_video_request(model_id, arguments=arguments)
        result = handler.get()  # fal_client polls internally

        generation_time = (datetime.datetime.now() - start_time).total_seconds()

        if not result:
            raise ValueError("No result returned from FAL.ai video API")

        # Extract video URL from response
        video_url = None
        if isinstance(result, dict):
            # Common response shapes across FAL video models
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
                f"No video URL in FAL response. Keys: {list(result.keys()) if isinstance(result, dict) else type(result)}"
            )

        logger.info(
            "Video generated in %.1fs via %s (frame_lock=%s): %s",
            generation_time, model_id, frame_locked, video_url[:100],
        )

        response_data = {
            "success": True,
            "video_url": video_url,
            "duration": generation_time,
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
    """True if FAL credentials and fal_client SDK are both available."""
    try:
        if not (os.getenv("FAL_KEY") or _check_managed_gateway()):
            return False
        import fal_client  # noqa: F401
        return True
    except ImportError:
        return False


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

        # Determine output directory
        if not output_dir:
            output_dir = os.path.join(tempfile.gettempdir(), "daon_frames", str(uuid.uuid4())[:8])
        os.makedirs(output_dir, exist_ok=True)

        # Download video to temp file
        video_path = os.path.join(output_dir, "input.mp4")
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        urllib.request.urlretrieve(video_url, video_path)

        # Extract frames with ffmpeg
        frame_pattern = os.path.join(output_dir, "frame_%04d.png")
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"fps={fps}",
            frame_pattern,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr[:500]}")

        # Collect frame paths
        frames = sorted([
            os.path.join(output_dir, f)
            for f in os.listdir(output_dir)
            if f.startswith("frame_") and f.endswith(".png")
        ])

        # Also get first and last frame explicitly
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
        "Generate a video clip from a start image using FAL.ai image-to-video models. "
        "Optionally provide an end image for frame-locked generation (seamless chains). "
        "The model is user-configured (default: Kling 2.5 Turbo Pro). "
        "Returns a video URL. Use for scroll-world camera flights, animations, "
        "or any image-to-video task."
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
                "enum": [5, 10],
                "description": "Video duration in seconds. Default 5. Not all models support 10s.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "What to avoid in the video. E.g. 'blurry, distorted, watermark, text overlay'.",
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
