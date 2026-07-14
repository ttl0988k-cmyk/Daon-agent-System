# agent/fallback.py
#
# Fallback / credential recovery — extracted from AIAgent in run_agent.py.
# All functions take an ``agent`` parameter (the AIAgent instance) instead of ``self``.

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from agent.openai_client_lifecycle import (
    close_openai_client,
    create_openai_client,
    replace_primary_openai_client,
    client_log_context,
)

logger = logging.getLogger(__name__)


# ── Credential refresh ───────────────────────────────────────────────────────


def try_refresh_codex_client_credentials(agent: Any, *, force: bool = True) -> bool:
    """Refresh Codex runtime credentials (api_key + base_url).

    Returns True if credentials were updated and the primary client was
    replaced successfully.
    """
    if agent.api_mode != "codex_responses" or agent.provider != "openai-codex":
        return False

    try:
        from hermes_cli.auth import resolve_codex_runtime_credentials

        creds = resolve_codex_runtime_credentials(force_refresh=force)
    except Exception as exc:
        logger.debug("Codex credential refresh failed: %s", exc)
        return False

    api_key = creds.get("api_key")
    base_url = creds.get("base_url")
    if not isinstance(api_key, str) or not api_key.strip():
        return False
    if not isinstance(base_url, str) or not base_url.strip():
        return False

    agent.api_key = api_key.strip()
    agent.base_url = base_url.strip().rstrip("/")
    agent._client_kwargs["api_key"] = agent.api_key
    agent._client_kwargs["base_url"] = agent.base_url

    if not replace_primary_openai_client(agent, reason="codex_credential_refresh"):
        return False

    return True


def try_refresh_nous_client_credentials(agent: Any, *, force: bool = True) -> bool:
    """Refresh Nous runtime credentials (api_key + base_url).

    Returns True if credentials were updated and the primary client was
    replaced successfully.
    """
    if agent.api_mode != "chat_completions" or agent.provider != "nous":
        return False

    try:
        from hermes_cli.auth import resolve_nous_runtime_credentials

        creds = resolve_nous_runtime_credentials(
            min_key_ttl_seconds=max(60, int(os.getenv("HERMES_NOUS_MIN_KEY_TTL_SECONDS", "1800"))),
            timeout_seconds=float(os.getenv("HERMES_NOUS_TIMEOUT_SECONDS", "15")),
            force_mint=force,
        )
    except Exception as exc:
        logger.debug("Nous credential refresh failed: %s", exc)
        return False

    api_key = creds.get("api_key")
    base_url = creds.get("base_url")
    if not isinstance(api_key, str) or not api_key.strip():
        return False
    if not isinstance(base_url, str) or not base_url.strip():
        return False

    agent.api_key = api_key.strip()
    agent.base_url = base_url.strip().rstrip("/")
    agent._client_kwargs["api_key"] = agent.api_key
    agent._client_kwargs["base_url"] = agent.base_url
    # Nous requests should not inherit OpenRouter-only attribution headers.
    agent._client_kwargs.pop("default_headers", None)

    if not replace_primary_openai_client(agent, reason="nous_credential_refresh"):
        return False

    return True


def try_refresh_anthropic_client_credentials(agent: Any) -> bool:
    """Refresh Anthropic client credentials.

    Only applies to the native Anthropic provider (not third-party
    Anthropic-protocol endpoints).  Returns True if the client was
    rebuilt with a new token.
    """
    if agent.api_mode != "anthropic_messages" or not hasattr(agent, "_anthropic_api_key"):
        return False
    if agent.provider != "anthropic":
        return False

    try:
        from agent.anthropic_adapter import resolve_anthropic_token, build_anthropic_client

        new_token = resolve_anthropic_token()
    except Exception as exc:
        logger.debug("Anthropic credential refresh failed: %s", exc)
        return False

    if not isinstance(new_token, str) or not new_token.strip():
        return False
    new_token = new_token.strip()
    if new_token == agent._anthropic_api_key:
        return False

    try:
        agent._anthropic_client.close()
    except Exception:
        pass

    try:
        from hermes_cli.timeouts import get_provider_request_timeout
        agent._anthropic_client = build_anthropic_client(
            new_token,
            getattr(agent, "_anthropic_base_url", None),
            timeout=get_provider_request_timeout(agent.provider, agent.model),
        )
    except Exception as exc:
        logger.warning("Failed to rebuild Anthropic client after credential refresh: %s", exc)
        return False

    agent._anthropic_api_key = new_token
    from agent.anthropic_adapter import _is_oauth_token
    agent._is_anthropic_oauth = _is_oauth_token(new_token) if agent.provider == "anthropic" else False
    return True


# ── Client header management ─────────────────────────────────────────────────


def apply_client_headers_for_base_url(agent: Any, base_url: str) -> None:
    """Set provider-specific default headers on ``_client_kwargs``."""
    from agent.auxiliary_client import _OR_HEADERS

    normalized = (base_url or "").lower()
    if "openrouter" in normalized:
        agent._client_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif "api.githubcopilot.com" in normalized:
        from hermes_cli.models import copilot_default_headers
        agent._client_kwargs["default_headers"] = copilot_default_headers()
    elif "api.kimi.com" in normalized:
        agent._client_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.30.0"}
    elif "portal.qwen.ai" in normalized:
        from run_agent import _qwen_portal_headers
        agent._client_kwargs["default_headers"] = _qwen_portal_headers()
    elif "chatgpt.com" in normalized:
        from agent.auxiliary_client import _codex_cloudflare_headers
        agent._client_kwargs["default_headers"] = _codex_cloudflare_headers(
            agent._client_kwargs.get("api_key", ""),
        )
    else:
        agent._client_kwargs.pop("default_headers", None)


# ── Credential pool rotation ─────────────────────────────────────────────────


def swap_credential(agent: Any, entry: Any) -> None:
    """Swap the active credential to a pool entry.

    Handles both OpenAI-compatible and Anthropic providers.
    """
    runtime_key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    runtime_base = getattr(entry, "runtime_base_url", None) or getattr(entry, "base_url", None) or agent.base_url

    if agent.api_mode == "anthropic_messages":
        from agent.anthropic_adapter import build_anthropic_client, _is_oauth_token

        try:
            agent._anthropic_client.close()
        except Exception:
            pass

        agent._anthropic_api_key = runtime_key
        agent._anthropic_base_url = runtime_base
        from hermes_cli.timeouts import get_provider_request_timeout
        agent._anthropic_client = build_anthropic_client(
            runtime_key, runtime_base,
            timeout=get_provider_request_timeout(agent.provider, agent.model),
        )
        agent._is_anthropic_oauth = _is_oauth_token(runtime_key) if agent.provider == "anthropic" else False
        agent.api_key = runtime_key
        agent.base_url = runtime_base
        return

    agent.api_key = runtime_key
    agent.base_url = runtime_base.rstrip("/") if isinstance(runtime_base, str) else runtime_base
    agent._client_kwargs["api_key"] = agent.api_key
    agent._client_kwargs["base_url"] = agent.base_url
    apply_client_headers_for_base_url(agent, agent.base_url)
    replace_primary_openai_client(agent, reason="credential_rotation")


def recover_with_credential_pool(
    agent: Any,
    *,
    status_code: Optional[int],
    has_retried_429: bool,
    classified_reason: Optional[Any] = None,
    error_context: Optional[Dict[str, Any]] = None,
) -> tuple[bool, bool]:
    """Attempt credential recovery via pool rotation.

    Returns (recovered, has_retried_429).
    On rate limits: first occurrence retries same credential (sets flag True).
                    second consecutive failure rotates to next credential.
    On billing exhaustion: immediately rotates.
    On auth failures: attempts token refresh before rotating.

    `classified_reason` lets the recovery path honor the structured error
    classifier instead of relying only on raw HTTP codes. This matters for
    providers that surface billing/rate-limit/auth conditions under a
    different status code, such as Anthropic returning HTTP 400 for
    "out of extra usage".
    """
    from agent.error_classifier import FailoverReason

    pool = getattr(agent, "_credential_pool", None)
    if pool is None:
        return False, has_retried_429

    effective_reason = classified_reason
    if effective_reason is None:
        if status_code == 402:
            effective_reason = FailoverReason.billing
        elif status_code == 429:
            effective_reason = FailoverReason.rate_limit
        elif status_code == 401:
            effective_reason = FailoverReason.auth

    if effective_reason is not None:
        reason_name = effective_reason.name if hasattr(effective_reason, "name") else str(effective_reason)
    else:
        reason_name = "unknown"

    if effective_reason == FailoverReason.billing:
        rotate_status = status_code if status_code is not None else 402
        next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
        if next_entry is not None:
            logger.info(
                "Credential %s (billing) — rotated to pool entry %s",
                rotate_status,
                getattr(next_entry, "id", "?"),
            )
            swap_credential(agent, next_entry)
            return True, False
        return False, has_retried_429

    if effective_reason == FailoverReason.rate_limit:
        if not has_retried_429:
            return False, True
        rotate_status = status_code if status_code is not None else 429
        next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
        if next_entry is not None:
            logger.info(
                "Credential %s (rate limit) — rotated to pool entry %s",
                rotate_status,
                getattr(next_entry, "id", "?"),
            )
            swap_credential(agent, next_entry)
            return True, False
        return False, True

    if effective_reason == FailoverReason.auth:
        refreshed = pool.try_refresh_current()
        if refreshed is not None:
            logger.info(f"Credential auth failure — refreshed pool entry {getattr(refreshed, 'id', '?')}")
            swap_credential(agent, refreshed)
            return True, has_retried_429
        # Refresh failed — rotate to next credential instead of giving up.
        # The failed entry is already marked exhausted by try_refresh_current().
        rotate_status = status_code if status_code is not None else 401
        next_entry = pool.mark_exhausted_and_rotate(status_code=rotate_status, error_context=error_context)
        if next_entry is not None:
            logger.info(
                "Credential %s (auth refresh failed) — rotated to pool entry %s",
                rotate_status,
                getattr(next_entry, "id", "?"),
            )
            swap_credential(agent, next_entry)
            return True, False

    return False, has_retried_429


# ── Fallback chain ───────────────────────────────────────────────────────────


def try_activate_fallback(agent: Any) -> bool:
    """Switch to the next fallback model/provider in the chain.

    Advances through ``_fallback_chain`` on each call; returns False
    when exhausted.
    """
    if agent._fallback_index >= len(agent._fallback_chain):
        return False

    fb = agent._fallback_chain[agent._fallback_index]
    agent._fallback_index += 1
    fb_provider = (fb.get("provider") or "").strip().lower()
    fb_model = (fb.get("model") or "").strip()
    if not fb_provider or not fb_model:
        return try_activate_fallback(agent)  # skip invalid, try next

    try:
        from agent.auxiliary_client import resolve_provider_client
        fb_base_url_hint = (fb.get("base_url") or "").strip() or None
        fb_api_key_hint = (fb.get("api_key") or "").strip() or None
        if fb_base_url_hint and "ollama.com" in fb_base_url_hint.lower() and not fb_api_key_hint:
            fb_api_key_hint = os.getenv("OLLAMA_API_KEY") or None
        fb_client, _resolved_fb_model = resolve_provider_client(
            fb_provider, model=fb_model, raw_codex=True,
            explicit_base_url=fb_base_url_hint,
            explicit_api_key=fb_api_key_hint)
        if fb_client is None:
            logging.warning(
                "Fallback to %s failed: provider not configured",
                fb_provider)
            return try_activate_fallback(agent)

        try:
            from hermes_cli.model_normalize import normalize_model_for_provider
            fb_model = normalize_model_for_provider(fb_model, fb_provider)
        except Exception:
            pass

        fb_api_mode = "chat_completions"
        fb_base_url = str(fb_client.base_url)
        if fb_provider == "openai-codex":
            fb_api_mode = "codex_responses"
        elif fb_provider == "anthropic" or fb_base_url.rstrip("/").lower().endswith("/anthropic"):
            fb_api_mode = "anthropic_messages"
        elif agent._is_direct_openai_url(fb_base_url):
            fb_api_mode = "codex_responses"
        elif agent._provider_model_requires_responses_api(
            fb_model,
            provider=fb_provider,
        ):
            fb_api_mode = "codex_responses"
        elif fb_provider == "bedrock" or "bedrock-runtime" in fb_base_url.lower():
            fb_api_mode = "bedrock_converse"

        old_model = agent.model
        agent.model = fb_model
        agent.provider = fb_provider
        agent.base_url = fb_base_url
        agent.api_mode = fb_api_mode
        agent._fallback_activated = True

        from hermes_cli.timeouts import get_provider_request_timeout
        _fb_timeout = get_provider_request_timeout(fb_provider, fb_model)

        if fb_api_mode == "anthropic_messages":
            from agent.anthropic_adapter import build_anthropic_client, resolve_anthropic_token, _is_oauth_token
            effective_key = (fb_client.api_key or resolve_anthropic_token() or "") if fb_provider == "anthropic" else (fb_client.api_key or "")
            agent.api_key = effective_key
            agent._anthropic_api_key = effective_key
            agent._anthropic_base_url = fb_base_url
            agent._anthropic_client = build_anthropic_client(
                effective_key, agent._anthropic_base_url, timeout=_fb_timeout,
            )
            agent._is_anthropic_oauth = _is_oauth_token(effective_key) if fb_provider == "anthropic" else False
            agent.client = None
            agent._client_kwargs = {}
        else:
            agent.api_key = fb_client.api_key
            agent.client = fb_client
            fb_headers = getattr(fb_client, "_custom_headers", None)
            if not fb_headers:
                fb_headers = getattr(fb_client, "default_headers", None)
            agent._client_kwargs = {
                "api_key": fb_client.api_key,
                "base_url": fb_base_url,
                **({"default_headers": dict(fb_headers)} if fb_headers else {}),
            }
            if _fb_timeout is not None:
                agent._client_kwargs["timeout"] = _fb_timeout
                replace_primary_openai_client(agent, reason="fallback_timeout_apply")

        agent._use_prompt_caching, agent._use_native_cache_layout = (
            agent._anthropic_prompt_cache_policy(
                provider=fb_provider,
                base_url=fb_base_url,
                api_mode=fb_api_mode,
                model=fb_model,
            )
        )

        if hasattr(agent, 'context_compressor') and agent.context_compressor:
            from agent.model_metadata import get_model_context_length
            fb_context_length = get_model_context_length(
                agent.model, base_url=agent.base_url,
                api_key=agent.api_key, provider=agent.provider,
            )
            agent.context_compressor.update_model(
                model=agent.model,
                context_length=fb_context_length,
                base_url=agent.base_url,
                api_key=getattr(agent, "api_key", ""),
                provider=agent.provider,
            )

        agent._emit_status(
            f"🔄 Primary model failed — switching to fallback: "
            f"{fb_model} via {fb_provider}"
        )
        logging.info(
            "Fallback activated: %s → %s (%s)",
            old_model, fb_model, fb_provider,
        )
        return True
    except Exception as e:
        logging.error("Failed to activate fallback %s: %s", fb_model, e)
        return try_activate_fallback(agent)


def restore_primary_runtime(agent: Any) -> bool:
    """Restore the primary runtime at the start of a new turn.

    Returns True if restoration was performed.
    """
    if not agent._fallback_activated:
        return False

    rt = agent._primary_runtime
    try:
        agent.model = rt["model"]
        agent.provider = rt["provider"]
        agent.base_url = rt["base_url"]
        agent.api_mode = rt["api_mode"]
        agent.api_key = rt["api_key"]
        agent._client_kwargs = dict(rt["client_kwargs"])
        agent._use_prompt_caching = rt["use_prompt_caching"]
        agent._use_native_cache_layout = rt.get(
            "use_native_cache_layout",
            agent.api_mode == "anthropic_messages" and agent.provider == "anthropic",
        )

        if agent.api_mode == "anthropic_messages":
            from agent.anthropic_adapter import build_anthropic_client
            agent._anthropic_api_key = rt["anthropic_api_key"]
            agent._anthropic_base_url = rt["anthropic_base_url"]
            from hermes_cli.timeouts import get_provider_request_timeout
            agent._anthropic_client = build_anthropic_client(
                rt["anthropic_api_key"], rt["anthropic_base_url"],
                timeout=get_provider_request_timeout(agent.provider, agent.model),
            )
            agent._is_anthropic_oauth = rt["is_anthropic_oauth"]
            agent.client = None
        else:
            agent.client = create_openai_client(
                agent,
                dict(rt["client_kwargs"]),
                reason="restore_primary",
                shared=True,
            )

        cc = agent.context_compressor
        cc.update_model(
            model=rt["compressor_model"],
            context_length=rt["compressor_context_length"],
            base_url=rt["compressor_base_url"],
            api_key=rt["compressor_api_key"],
            provider=rt["compressor_provider"],
        )

        agent._fallback_activated = False
        agent._fallback_index = 0

        logging.info(
            "Primary runtime restored for new turn: %s (%s)",
            agent.model, agent.provider,
        )
        return True
    except Exception as e:
        logging.warning("Failed to restore primary runtime: %s", e)
        return False


# ── Transport recovery ───────────────────────────────────────────────────────


# Which error types indicate a transient transport failure worth
# one more attempt with a rebuilt client / connection pool.
_TRANSIENT_TRANSPORT_ERRORS = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "APIConnectionError", "APITimeoutError",
})


def try_recover_primary_transport(
    agent: Any,
    api_error: Exception,
    *,
    retry_count: int,
    max_retries: int,
) -> bool:
    """Attempt one extra primary-provider recovery cycle for transient transport failures.

    Returns True if recovery was attempted.
    """
    if agent._fallback_activated:
        return False

    error_type = type(api_error).__name__
    if error_type not in _TRANSIENT_TRANSPORT_ERRORS:
        return False

    if agent._is_openrouter_url():
        return False
    provider_lower = (agent.provider or "").strip().lower()
    if provider_lower in ("nous", "nous-research"):
        return False

    try:
        if getattr(agent, "client", None) is not None:
            try:
                close_openai_client(
                    agent, agent.client, reason="primary_recovery", shared=True,
                )
            except Exception:
                pass

        rt = agent._primary_runtime
        agent._client_kwargs = dict(rt["client_kwargs"])
        agent.model = rt["model"]
        agent.provider = rt["provider"]
        agent.base_url = rt["base_url"]
        agent.api_mode = rt["api_mode"]
        agent.api_key = rt["api_key"]

        if agent.api_mode == "anthropic_messages":
            from agent.anthropic_adapter import build_anthropic_client
            agent._anthropic_api_key = rt["anthropic_api_key"]
            agent._anthropic_base_url = rt["anthropic_base_url"]
            from hermes_cli.timeouts import get_provider_request_timeout
            agent._anthropic_client = build_anthropic_client(
                rt["anthropic_api_key"], rt["anthropic_base_url"],
                timeout=get_provider_request_timeout(agent.provider, agent.model),
            )
            agent._is_anthropic_oauth = rt["is_anthropic_oauth"]
            agent.client = None
        else:
            agent.client = create_openai_client(
                agent,
                dict(rt["client_kwargs"]),
                reason="primary_recovery",
                shared=True,
            )

        wait_time = min(3 + retry_count, 8)
        agent._vprint(
            f"{agent.log_prefix}🔁 Transient {error_type} on {agent.provider} — "
            f"rebuilt client, waiting {wait_time}s before one last primary attempt.",
            force=True,
        )
        time.sleep(wait_time)
        return True
    except Exception as e:
        logging.warning("Primary transport recovery failed: %s", e)
        return False
