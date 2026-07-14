from typing import List, Dict, Any, Tuple, Optional
import os
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path

# ── Known provider presets with base URLs ──
# These are the FALLBACK defaults — the single source of truth is custom_providers.json
_PROVIDER_PRESETS = {
    'openai':      {'base_url': 'https://api.openai.com/v1',           'label': 'OpenAI'},
    'deepseek':    {'base_url': 'https://api.deepseek.com/v1',         'label': 'DeepSeek'},
    'minimax':     {'base_url': 'https://api.minimax.io/anthropic',    'label': 'MiniMax'},
    'anthropic':   {'base_url': 'https://api.anthropic.com/v1',        'label': 'Anthropic'},
    'google':      {'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'label': 'Google'},
    'openrouter':  {'base_url': 'https://openrouter.ai/api/v1',       'label': 'OpenRouter'},
    'together':    {'base_url': 'https://api.together.xyz/v1',        'label': 'Together AI'},
    'groq':        {'base_url': 'https://api.groq.com/openai/v1',     'label': 'Groq'},
    'nvidia':      {'base_url': 'https://integrate.api.nvidia.com/v1', 'label': 'NVIDIA NIM'},
    'ollama':      {'base_url': 'http://localhost:11434/v1',           'label': 'Ollama (Local)'},
    'lmstudio':    {'base_url': 'http://localhost:1234/v1',            'label': 'LM Studio (Local)'},
    'xai':         {'base_url': 'https://api.x.ai/v1',                'label': 'xAI'},
    'zhipu':       {'base_url': 'https://open.bigmodel.cn/api/paas/v4', 'label': 'ZhipuAI'},
    'local':       {'base_url': 'http://localhost:11434/v1',           'label': 'Local Models'},
}

# ── File path for custom providers ──
def _get_custom_providers_path() -> Path:
    """Get path to custom_providers.json relative to the data directory."""
    try:
        from api.config import STATE_DIR
        return STATE_DIR / 'custom_providers.json'
    except ImportError:
        return Path(__file__).parent.parent.parent / 'data' / 'custom_providers.json'


def _load_custom_providers() -> dict:
    """Load custom providers from JSON file — the SINGLE SOURCE OF TRUTH.
    
    Returns dict with keys: 'presets', 'providers'
    - presets: provider_name → {base_url, label, models?}
    - providers: provider_name → {api_key, base_url, models, label}
    """
    path = _get_custom_providers_path()
    result = {'presets': dict(_PROVIDER_PRESETS), 'providers': {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                # Merge file presets over hardcoded fallback
                file_presets = data.get('presets', {})
                if isinstance(file_presets, dict):
                    for pname, pcfg in file_presets.items():
                        if isinstance(pcfg, dict):
                            merged = dict(_PROVIDER_PRESETS.get(pname, {}))
                            merged.update(pcfg)
                            result['presets'][pname] = merged
                file_providers = data.get('providers', {})
                if isinstance(file_providers, dict):
                    result['providers'] = file_providers
        except Exception as e:
            print(f"[ModelManager] Warning: failed to load custom_providers.json: {e}")
    return result


def _save_custom_providers(providers: dict) -> None:
    """Save custom providers to JSON file, preserving presets."""
    path = _get_custom_providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _load_custom_providers()
    current['providers'] = providers
    # Write back: presets (from file if exists) + providers
    out = {
        'presets': current.get('presets', {}),
        'providers': providers,
    }
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')


class ModelManager:
    """Manages available models and provider resolution — fully dynamic via custom_providers.json."""

    def __init__(self):
        # No hardcoded models — everything is loaded dynamically from custom_providers.json
        pass

    def _get_all_provider_models(self) -> Dict[str, List[Dict[str, str]]]:
        """Get provider→models mapping from the single source of truth (custom_providers.json).
        
        Merges: presets[].models + custom providers[].models
        """
        data = _load_custom_providers()
        result = {}

        # 1) Models from presets (built-in providers)
        for pname, pcfg in data.get('presets', {}).items():
            if isinstance(pcfg, dict) and 'models' in pcfg:
                result[pname] = list(pcfg['models'])

        # 2) Models from custom providers (user-added, with API keys)
        for pname, pcfg in data.get('providers', {}).items():
            if isinstance(pcfg, dict) and 'models' in pcfg:
                result[pname] = list(pcfg['models'])

        return result

    # ── Custom Provider CRUD ────────────────────────────────────────────

    def get_custom_providers(self) -> dict:
        """Return all custom providers with their models and configs (API keys masked)."""
        data = _load_custom_providers()
        providers = {}
        for name, cfg in data.get('providers', {}).items():
            masked_cfg = dict(cfg)
            if 'api_key' in masked_cfg and masked_cfg['api_key']:
                key = masked_cfg['api_key']
                if len(key) > 8:
                    masked_cfg['api_key'] = key[:4] + '•' * (len(key) - 8) + key[-4:]
                else:
                    masked_cfg['api_key'] = '•' * len(key)
            providers[name] = masked_cfg
        return providers

    def add_custom_provider(self, name: str, api_key: str, base_url: str,
                            models: List[Dict[str, str]] = None) -> dict:
        """Add or update a custom provider. Auto-fetches models if api_key is provided."""
        name = name.strip().lower()
        if not name:
            raise ValueError("Provider name is required")
        if not base_url:
            raise ValueError("Base URL is required")

        data = _load_custom_providers()
        providers = data.get('providers', {})

        # Preserve existing api_key if empty string sent (update without changing key)
        existing_key = ''
        if name in providers:
            existing_key = providers[name].get('api_key', '')

        final_key = api_key if api_key else existing_key

        # Auto-fetch models if API key is provided and models not explicitly passed
        if final_key and models is None:
            try:
                fetched = self.fetch_models_from_provider(base_url, final_key)
                if fetched:
                    models = fetched
            except Exception:
                pass

        providers[name] = {
            'api_key': final_key,
            'base_url': base_url.rstrip('/'),
            'models': models or [],
            'label': data.get('presets', {}).get(name, {}).get('label', name.title()),
        }
        _save_custom_providers(providers)
        return {'success': True, 'provider': name, 'models': models or []}

    def delete_custom_provider(self, name: str) -> dict:
        """Delete a custom provider."""
        name = name.strip().lower()
        data = _load_custom_providers()
        providers = data.get('providers', {})
        if name in providers:
            del providers[name]
            _save_custom_providers(providers)
            return {'success': True, 'provider': name}
        raise KeyError(f"Provider '{name}' not found")

    def update_custom_provider_models(self, name: str, models: List[Dict[str, str]]) -> dict:
        """Update models list for a custom provider."""
        name = name.strip().lower()
        data = _load_custom_providers()
        providers = data.get('providers', {})
        if name not in providers:
            raise KeyError(f"Provider '{name}' not found")
        providers[name]['models'] = models
        _save_custom_providers(providers)
        return {'ok': True, 'provider': name, 'model_count': len(models)}

    # ── Auto-fetch models from provider API ─────────────────────────────

    def fetch_models_from_provider(self, base_url: str, api_key: str) -> List[Dict[str, str]]:
        """Try to fetch available models from the provider's /models endpoint (OpenAI-compatible)."""
        url = base_url.rstrip('/') + '/models'
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        req = urllib.request.Request(url, headers=headers, method='GET')
        ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                body = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = None
            try:
                body = json.loads(e.read().decode('utf-8'))
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code}: {body.get('error', {}).get('message', str(e)) if isinstance(body, dict) else str(e)}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch models: {e}")

        models = []
        data_list = body.get('data', []) if isinstance(body, dict) else []

        for item in data_list:
            if isinstance(item, dict) and 'id' in item:
                model_id = item['id']
                models.append({
                    'id': model_id,
                    'label': model_id,
                    'owned_by': item.get('owned_by', ''),
                })

        if not models and isinstance(body, dict):
            if 'models' in body:
                for item in body.get('models', []):
                    if isinstance(item, dict) and 'id' in item:
                        models.append({'id': item['id'], 'label': item.get('display_name', item['id'])})

        return models

    # ── Resolution ──────────────────────────────────────────────────────

    def _get_base_url(self, provider: str) -> Optional[str]:
        """Get base_url for a provider (checks presets + custom providers)."""
        data = _load_custom_providers()
        presets = data.get('presets', {})
        providers = data.get('providers', {})

        if provider in providers:
            return providers[provider].get('base_url')
        if provider in presets:
            return presets[provider].get('base_url')
        if provider == 'local':
            return os.getenv('OLLAMA_BASE_URL') or 'http://localhost:11434/v1'
        return None

    def _get_api_key(self, provider: str) -> str:
        """Get API key for a provider (checks custom providers → env → auth.json)."""
        data = _load_custom_providers()
        providers = data.get('providers', {})

        if provider in providers:
            key = providers[provider].get('api_key', '')
            if key:
                return key

        env_key = os.getenv(f'{provider.upper()}_API_KEY', '')
        if env_key:
            return env_key

        try:
            auth_path = Path.home() / '.hermes' / 'auth.json'
            if auth_path.exists():
                auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                pool = auth_data.get('credential_pool', {})
                if provider in pool and pool[provider]:
                    return pool[provider][0].get('access_token', '')
        except Exception:
            pass

        return ''

    def resolve_model_provider(self, model_id: str) -> Tuple[str, str, Optional[str]]:
        """Resolve bare model name → (model_id, provider, base_url)."""
        model_id = (model_id or '').strip()
        if not model_id:
            return model_id, 'custom', None

        # 1) Check preset provider models (from custom_providers.json)
        # When model_id is an exact match in a provider's model list,
        # use the model_id as-is — NEVER strip namespace prefixes.
        # (e.g. NVIDIA NIM needs "z-ai/glm-5.2" in full, OpenRouter needs "tencent/hy3:free")
        provider_models = self._get_all_provider_models()
        for p, models in provider_models.items():
            for m in models:
                if m.get('id') == model_id:
                    return model_id, p, self._get_base_url(p)

        # 2) Check custom provider models
        data = _load_custom_providers()
        for pname, cfg in data.get('providers', {}).items():
            for m in cfg.get('models', []):
                if m.get('id') == model_id:
                    return model_id, pname, cfg.get('base_url')

        # 3) Check if model_id has a provider/ prefix
        if '/' in model_id:
            provider, bare_model = model_id.split('/', 1)
            return bare_model, provider, self._get_base_url(provider)

        # 4) Check if model_id matches a known provider as prefix
        for pname in list(provider_models.keys()) + list(data.get('providers', {}).keys()):
            if model_id.startswith(pname + '/'):
                return model_id[len(pname)+1:], pname, self._get_base_url(pname)

        return model_id, 'custom', None

    # ── Available Models ────────────────────────────────────────────────

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Return structured model list dynamically from custom_providers.json."""
        auth_keys = {}
        try:
            auth_path = Path.home() / '.hermes' / 'auth.json'
            if auth_path.exists():
                auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                pool = auth_data.get('credential_pool', {})
                for provider_name, credentials in pool.items():
                    if credentials and credentials[0].get('access_token'):
                        auth_keys[provider_name] = credentials[0].get('access_token')
        except Exception as e:
            print(f"[ModelManager] Warning: failed to load auth.json: {e}")

        # Load all provider models dynamically
        provider_models = self._get_all_provider_models()
        custom_data = _load_custom_providers()
        custom_providers = custom_data.get('providers', {})

        # Show ALL presets that have models defined — API key presence is checked at call time
        ALLOWED_PRESETS = list(provider_models.keys())

        groups = []

        # 1) Preset providers (built-in, from custom_providers.json presets)
        for provider in ALLOWED_PRESETS:
            if provider in provider_models:
                display_name = custom_data.get('presets', {}).get(provider, {}).get('label', provider.capitalize())
                env_key = f"{provider.upper()}_API_KEY"
                has_key = (
                    provider == 'local' or
                    os.environ.get(env_key) or
                    auth_keys.get(provider)
                )
                if has_key:
                    groups.append({
                        'provider': display_name,
                        'provider_key': provider,
                        'is_custom': False,
                        'models': list(provider_models[provider]),
                        'has_api_key': True,
                    })

        # 2) Custom providers (from JSON providers section)
        for pname, cfg in custom_providers.items():
            if cfg.get('api_key') and cfg.get('models'):
                display_name = cfg.get('label', pname.title())
                groups.append({
                    'provider': display_name,
                    'provider_key': pname,
                    'is_custom': True,
                    'base_url': cfg.get('base_url', ''),
                    'models': list(cfg.get('models', []))
                })

        return groups

    def get_presets(self) -> dict:
        """Return known provider presets (name → {base_url, label, models?})."""
        data = _load_custom_providers()
        return data.get('presets', dict(_PROVIDER_PRESETS))


model_manager = ModelManager()
