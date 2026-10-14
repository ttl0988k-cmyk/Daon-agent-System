from __future__ import annotations


# Default request timeout (seconds) applied when neither the provider nor the
# model declares one.  Without this an unconfigured provider falls through to
# timeout=None, which the HTTP client treats as "wait forever" — that froze the
# first Dynamic Harness node for 15+ minutes on 2026-10-14.  A non-positive
# config value is treated as "unset" by _coerce_timeout and also falls back to
# this default, so a request can never block indefinitely.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600.0


def _coerce_timeout(raw: object) -> float | None:
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return timeout


def get_provider_request_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return the provider request timeout in seconds.

    Always returns a positive float: when neither the model nor the provider
    declares a timeout, DEFAULT_REQUEST_TIMEOUT_SECONDS is used so an
    unconfigured provider can never block forever.
    """
    if not provider_id:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS

    try:
        from hermes_cli.config import load_config
    except ImportError:
        return DEFAULT_REQUEST_TIMEOUT_SECONDS

    config = load_config()
    providers = config.get("providers", {}) if isinstance(config, dict) else {}
    provider_config = (
        providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    )
    if not isinstance(provider_config, dict):
        return DEFAULT_REQUEST_TIMEOUT_SECONDS

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        timeout = _coerce_timeout(model_config.get("timeout_seconds"))
        if timeout is not None:
            return timeout

    configured = _coerce_timeout(provider_config.get("request_timeout_seconds"))
    if configured is not None:
        return configured

    return DEFAULT_REQUEST_TIMEOUT_SECONDS


def get_provider_stale_timeout(
    provider_id: str, model: str | None = None
) -> float | None:
    """Return a configured non-stream stale timeout in seconds, if any."""
    if not provider_id:
        return None

    try:
        from hermes_cli.config import load_config
    except ImportError:
        return None

    config = load_config()
    providers = config.get("providers", {}) if isinstance(config, dict) else {}
    provider_config = (
        providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    )
    if not isinstance(provider_config, dict):
        return None

    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        timeout = _coerce_timeout(model_config.get("stale_timeout_seconds"))
        if timeout is not None:
            return timeout

    return _coerce_timeout(provider_config.get("stale_timeout_seconds"))


def _get_model_config(
    provider_config: dict[str, object], model: str | None
) -> dict[str, object] | None:
    if not model:
        return None

    models = provider_config.get("models", {})
    model_config = models.get(model, {}) if isinstance(models, dict) else {}
    if isinstance(model_config, dict):
        return model_config
    return None
