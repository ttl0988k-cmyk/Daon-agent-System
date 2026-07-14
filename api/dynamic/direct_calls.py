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


def _call_minimax_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: str = "MiniMax-M3") -> str:
    """Call MiniMax Anthropic-compatible API directly, falling back to MiniMax-M2.7 and MiniMax-M2.5 if needed.
    Includes robust retry handling for 429 and 503 errors.
    """
    api_key = _get_minimax_api_key()
    if not api_key:
        raise ValueError("MINIMAX_API_KEY not found in environment or auth.json.")

    models_to_try = [preferred_model, "MiniMax-M2.7", "MiniMax-M2.5", "MiniMax-M2.1"]
    seen = set()
    models_to_try = [x for x in models_to_try if not (x in seen or seen.add(x))]

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


def _call_deepseek_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: str = "deepseek-chat") -> str:
    """Call DeepSeek API directly, falling back to other deepseek models if needed.
    Includes robust retry handling for 429 and 503 errors.
    """
    api_key = _get_deepseek_api_key()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not found in environment or auth.json.")

    models_to_try = [preferred_model, "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]
    seen = set()
    models_to_try = [x for x in models_to_try if not (x in seen or seen.add(x))]

    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    url = f"{base_url}/chat/completions"

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


def _call_direct(prompt: str, system_instruction: Optional[str] = None, preferred_model: Optional[str] = None, stream_callback=None) -> str:
    """Wrapper that dynamically routes meta-agents (Planner/Merger) using AIAgent with robust fallback retry logic."""
    agent_path = str(Path(__file__).resolve().parent.parent.parent / "hermes-agent")
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
                _log.info(
                    "Direct call failed with '%s' (Attempt %d/%d): %s",
                    model_name, attempt + 1, max_retries, e
                )
                if attempt < max_retries - 1:
                    sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                    _log.info("Retrying in %.2f seconds...", sleep_sec)
                    time.sleep(sleep_sec)

    raise RuntimeError(f"Direct call failed after trying all fallback models. Last error: {last_error}")
