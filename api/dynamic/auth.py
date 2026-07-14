"""
API key resolution for MiniMax and DeepSeek providers.

Retrieves keys from: custom_providers.json → environment variables → ~/.hermes/auth.json credential_pool entries.
"""

import json
import os
from pathlib import Path

from api.profiles import get_active_hermes_home
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _resolve_key_from_custom_providers(provider: str) -> str:
    """Extract an API key from custom_providers.json if the provider is registered there."""
    try:
        cp_path = Path(__file__).parent.parent.parent / 'data' / 'custom_providers.json'
        if cp_path.exists():
            data = json.loads(cp_path.read_text(encoding='utf-8'))
            providers = data.get('providers', {})
            if provider in providers:
                key = providers[provider].get('api_key', '')
                if key:
                    return key
    except Exception as e:
        _log.warning("Failed to read custom_providers.json: %s", e)
    return ""


def _resolve_key_from_pool(provider: str) -> str:
    """Extract an API key for *provider* from custom_providers.json → environment → auth.json."""
    # 1) Custom providers JSON
    key = _resolve_key_from_custom_providers(provider)
    if key:
        return key

    # 2) Environment variable
    env_var = f"{provider.upper()}_API_KEY"
    if os.getenv(env_var):
        return os.getenv(env_var)

    # 3) auth.json credential pool
    try:
        auth_path = get_active_hermes_home() / "auth.json"
        if auth_path.exists():
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            pool = data.get("credential_pool", {})
            if provider in pool and pool[provider]:
                return pool[provider][0].get("access_token", "")
    except Exception as e:
        _log.warning("Failed to read credential pool from auth.json: %s", e)
    return ""


def _get_minimax_api_key() -> str:
    """Retrieve MINIMAX_API_KEY from custom_providers → environment → auth.json."""
    return _resolve_key_from_pool("minimax")


def _get_deepseek_api_key() -> str:
    """Retrieve DEEPSEEK_API_KEY from custom_providers → environment → auth.json."""
    return _resolve_key_from_pool("deepseek")
