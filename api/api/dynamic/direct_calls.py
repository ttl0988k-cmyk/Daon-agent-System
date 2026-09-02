"""
Direct API call wrappers for MiniMax, DeepSeek, and generic AIAgent routing.

Provides:
- _call_minimax_direct(): MiniMax Anthropic-compatible API with model fallback
- _call_deepseek_direct(): DeepSeek Chat Completions API with model fallback
- _call_direct(): routes meta-agents (Planner/Merger) via AIAgent with robust fallback
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from api.dynamic.auth import _get_minimax_api_key, _get_deepseek_api_key
from api.dynamic.limits import _load_harness_limits
from api.dynamic.dag_utils import _get_model_chain_for_node, _extract_assistant_content
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _registered_models_for(provider: str) -> list[str]:
    """Get registered model IDs for a provider from custom_providers.json (dynamic)."""
    try:
        from api.managers import model_manager
        for g in model_manager.get_available_models():
            if g.get('provider_key') == provider:
                return [m.get('id') if isinstance(m, dict) else str(m) for m in g.get('models', [])]
    except Exception as e:
        _log.info("Failed to load registered models for %s: %s", provider, e)
    return []


def _call_minimax_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: Optional[str] = None) -> str:
    """Call MiniMax Anthropic-compatible API directly, falling back to other
    MiniMax models registered in custom_providers.json if needed.
    Includes robust retry handling for 429 and 503 errors.
    """
    api_key = _get_minimax_api_key()
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not found in environment or auth.json.")

    models_to_try = ([preferred_model] if preferred_model else []) + _registered_models_for("minimax")
    seen = set()
    models_to_try = [x for x in models_to_try if x and not (x in seen or seen.add(x))]
    if not models_to_try:
        raise ValueError("No MiniMax models registered in custom_providers.json.")

    base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/anthropic").rstrip("/")
    url = f"{base_url}/v1/messages"

    last_error = None
    for model in models_to_try:
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

        payload = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}]}

        if system_instruction:
            payload["system"] = system_instruction

        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    res_data = json.loads(response.read().decode("utf-8"))
                    content = res_data.get("content", [])
                    if content and isinstance(content, list):
                        text = content[0].get("text", "")
                        if text:
                            return text
                    return ""
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                if e.code in (429, 503):
                    last_error = RuntimeError(f"MiniMax API HTTP Error {e.code} for model {model}: {err_msg}")
                    if e.code == 429 and _is_quota_exhausted(err_msg):
                        raise last_error  # 사용량 소진 — 남은 모델/재시도 모두 중단(크레딧 보호)
                    if attempt < max_retries - 1:
                        sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                        _log.info(
                            "Model %s returned %d. Retrying in %.2f seconds (attempt %d/%d)...",
                            model, e.code, sleep_sec, attempt + 1, max_retries
                        )
                        time.sleep(sleep_sec)
                        continue
                    else:
                        _log.info("Model %s failed after %d attempts.", model, max_retries)
                else:
                    last_error = RuntimeError(f"MiniMax API HTTP Error {e.code} for model {model}: {err_msg}")
                    break
            except Exception as e:
                last_error = e
                break

    raise last_error


def _call_deepseek_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: Optional[str] = None) -> str:
    """Call DeepSeek API directly, falling back to other DeepSeek models
    registered in custom_providers.json if needed.
    Includes robust retry handling for 429 and 503 errors.
    """
    api_key = _get_deepseek_api_key()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment or auth.json.")

    models_to_try = ([preferred_model] if preferred_model else []) + _registered_models_for("deepseek")
    seen = set()
    models_to_try = [x for x in models_to_try if x and not (x in seen or seen.add(x))]
    if not models_to_try:
        raise ValueError("No DeepSeek models registered in custom_providers.json.")

    base_url = os.getenv("DEEPSEEK_BASE_URL", "")
    if not base_url:
        try:
            from api.managers import model_manager
            base_url = model_manager._get_base_url("deepseek") or ""
        except Exception:
            base_url = ""
    if not base_url:
        raise ValueError("No base_url configured for the 'deepseek' provider.")
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error = None
    for model in models_to_try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages}

        req_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    res_data = json.loads(response.read().decode("utf-8"))
                    choices = res_data.get("choices", [])
                    if choices and isinstance(choices, list):
                        choice = choices[0]
                        message = choice.get("message", {})
                        text = message.get("content", "")
                        if text:
                            return text
                    return ""
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="ignore")
                if e.code in (429, 503):
                    last_error = RuntimeError(f"DeepSeek API HTTP Error {e.code} for model {model}: {err_msg}")
                    if e.code == 429 and _is_quota_exhausted(err_msg):
                        raise last_error  # 사용량 소진 — 남은 모델/재시도 모두 중단(크레딧 보호)
                    if attempt < max_retries - 1:
                        sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                        _log.info(
                            "DeepSeek Model %s returned %d. Retrying in %.2f seconds (attempt %d/%d)...",
                            model, e.code, sleep_sec, attempt + 1, max_retries
                        )
                        time.sleep(sleep_sec)
                        continue
                    else:
                        _log.info("DeepSeek Model %s failed after %d attempts.", model, max_retries)
                else:
                    last_error = RuntimeError(f"DeepSeek API HTTP Error {e.code} for model {model}: {err_msg}")
                    break
            except Exception as e:
                last_error = e
                break

    raise last_error


_QUOTA_MARKERS = (
    "usage limit", "limit reached", "resets in", "quota",
    "monthly usage", "weekly usage", "daily usage",
)


def _is_quota_exhausted(msg: str) -> bool:
    """429 중에서도 "주간/월간 사용량 소진"류(리셋 대기 필요)를 판정한다.

    순간 RPM 제한(초당 과다 호출)은 잠깐 쉬면 풀리므로 재시도가 유효하지만,
    opencode.ai의 "Weekly usage limit reached. Resets in 4 days."처럼
    사용량 소진은 재시도해도 소진만 반복된다. kimi-k3 사고(2026-09-03)에서
    429를 재시도/폴백하며 1,605건의 소진 호출을 반복한 원인.
    """
    low = (msg or "").lower()
    return any(marker in low for marker in _QUOTA_MARKERS)


def _is_permanent_provider_error(e: Exception) -> bool:
    """401/402/403 및 크레딧/인증 오류는 "영구 실패"로 판정한다.

    이런 오류는 같은 프로바이더의 다른 모델을 때려도 해결되지 않으므로,
    3회 재시도나 폴백으로 크레딧을 더 태우지 말고 즉시 중단(circuit-open)해야 한다.
    """
    status = getattr(e, "status_code", None)
    if status in (401, 402, 403):
        return True
    msg = str(e).lower()
    for marker in ("http 401", "http 402", "http 403", "insufficient", "credits",
                   "invalid api key", "unauthorized", "authentication", "payment required"):
        if marker in msg:
            return True
    # 429라도 사용량 소진이면 재시도/폴백으로 크레딧을 더 태우지 않는다.
    if status == 429 and _is_quota_exhausted(msg):
        return True
    if "http 429" in msg and _is_quota_exhausted(msg):
        return True
    return False


def _call_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: Optional[str] = None, stream_callback=None) -> str:
    """Wrapper that dynamically routes meta-agents (Planner/Merger) using AIAgent with robust fallback retry logic."""
    agent_path = str(Path(__file__).resolve().parent.parent.parent.parent / "hermes-agent")
    if agent_path not in sys.path:
        sys.path.append(agent_path)
    from run_agent import AIAgent

    model_configs = _get_model_chain_for_node(preferred_model)
    if not model_configs:
        raise RuntimeError("No available models found for direct call. Check API keys.")

    limits = _load_harness_limits()
    max_retries = limits.get("node", {}).get("max_retries", 3)

    last_error = None
    for cfg in model_configs:
        model_name = cfg["model"]
        provider = cfg["provider"]
        api_key = cfg["api_key"]
        base_url = cfg["base_url"]

        for attempt in range(max_retries):
            try:
                agent = AIAgent(
                    model=model_name,
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    enabled_toolsets=[],  # Prevent meta-agents from bypassing delegation
                    quiet_mode=True,
                )
                res = agent.run_conversation(
                    user_message=prompt,
                    system_message=system_instruction or "You are a helpful AI.",
                    stream_callback=stream_callback
                )
                if res.get("failed"):
                    raise RuntimeError(res.get("error"))
                return _extract_assistant_content(res.get("messages", []))
            except Exception as e:
                last_error = e
                # 영구 실패(401/402/403, 크레딧 부족)는 재시도/폴백 없이 즉시 중단.
                if _is_permanent_provider_error(e):
                    _log.info(
                        "Permanent provider error for '%s' (provider=%s) — circuit-open, no retry/fallback: %s",
                        model_name, provider, e
                    )
                    raise RuntimeError(
                        f"Permanent provider error for '{model_name}' (provider={provider}): {e}"
                    )
                _log.info(
                    "Direct call failed with '%s' (Attempt %d/%d): %s",
                    model_name, attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                    _log.info("Retrying in %.2f seconds...", sleep_sec)
                    time.sleep(sleep_sec)

    raise RuntimeError(f"Direct call failed after trying all fallback models. Last error: {last_error}")
