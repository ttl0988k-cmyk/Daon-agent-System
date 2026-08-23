from typing import List, Dict, Any, Tuple, Optional
import os
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path

# ── Known provider presets ──
# No hardcoded provider presets in code. Provider presets (add-provider
# suggestions in Settings → Providers) live in custom_providers.json under the
# 'presets' key — the SINGLE SOURCE OF TRUTH. Presets carry only name/base_url/
# label (no models, no api keys), so they never inject models into the selector.
_PROVIDER_PRESETS = {}

# ── File path for custom providers ──
def _get_custom_providers_path() -> Path:
    """Get path to custom_providers.json relative to the data directory."""
    try:
        from api.config import STATE_DIR
        return STATE_DIR / 'custom_providers.json'
    except ImportError:
        return Path(__file__).parent.parent.parent.parent / 'data' / 'custom_providers.json'


def _load_custom_providers() -> dict:
    """Load custom providers from JSON file — the SINGLE SOURCE OF TRUTH.

    Returns dict with keys: 'presets', 'providers'
    - presets: provider_name → {base_url, label, models?}
    - providers: provider_name → {api_key, base_url, models, label}
    """
    path = _get_custom_providers_path()
    # Seed from hardcoded presets ONLY on first run (no file yet).
    result = {'presets': dict(_PROVIDER_PRESETS), 'providers': {}}
    if path.exists():
        try:
            # utf-8-sig: tolerate a UTF-8 BOM if an external tool (e.g. PowerShell)
            # rewrote the file with one — a stray BOM makes json.loads() fail and
            # silently drops the user's registered providers (providers={} fallback).
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(data, dict):
                # Once the file exists it is authoritative. Do NOT merge the
                # hardcoded presets back in — a provider deleted via the UI must
                # stay deleted. (Merging was the root cause of ollama/local
                # reappearing after the user removed them.)
                file_presets = data.get('presets', {})
                if isinstance(file_presets, dict):
                    result['presets'] = {
                        pname: pcfg for pname, pcfg in file_presets.items()
                        if isinstance(pcfg, dict)
                    }
                file_providers = data.get('providers', {})
                if isinstance(file_providers, dict):
                    result['providers'] = file_providers
        except Exception as e:
            print(f"[ModelManager] Warning: failed to load custom_providers.json: {e}")
    return result


def _save_custom_providers(providers: dict) -> None:
    """Save custom providers to JSON file, preserving presets."""
    path = _get_custom_providers_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        print(f"[ModelManager] Warning: Cannot create directory {path.parent}: {e}")
        raise RuntimeError(f"Cannot create data directory: {e}")
    try:
        current = _load_custom_providers()
        current['providers'] = providers
        # Write back: presets (from file if exists) + providers
        out = {
            'presets': current.get('presets', {}),
            'providers': providers,
        }
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    except (PermissionError, OSError, IOError) as e:
        print(f"[ModelManager] Warning: Cannot write custom_providers.json: {e}")
        raise RuntimeError(f"Cannot save provider data: {e}")


# ── Hidden models ───────────────────────────────────────────────────────
# No hardcoded hidden-model list. If a provider's /models endpoint omits
# models, add them manually via the provider UI — they are persisted in
# custom_providers.json (the single source of truth) and survive re-fetches.


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

        def _norm_url(u: str) -> str:
            return (u or '').strip().rstrip('/').lower()

        # ── 동일 base_url 병합: 같은 URL의 기존 프로바이더가 있으면 새 항목을
        # 만들지 않고 그 프로바이더에 모델을 합친다. 그렇지 않으면 모델 선택 창에
        # 같은 프로바이더가 이름만 달라 여러 개 노출된다.
        if name not in providers:
            merge_target = None
            for pname, pcfg in providers.items():
                if isinstance(pcfg, dict) and _norm_url(pcfg.get('base_url')) == _norm_url(base_url):
                    merge_target = pname
                    break
            if merge_target is not None:
                target = providers[merge_target]
                final_key = api_key if api_key else target.get('api_key', '')
                existing_models = target.get('models', []) or []
                existing_ids = {}
                for m in existing_models:
                    mid = m.get('id') if isinstance(m, dict) else str(m)
                    if mid:
                        existing_ids[mid] = m
                merged = list(existing_models)
                added_count = 0
                for m in (models or []):
                    mid = m.get('id') if isinstance(m, dict) else str(m)
                    if mid and mid not in existing_ids:
                        merged.append(m)
                        existing_ids[mid] = m
                        added_count += 1
                target['api_key'] = final_key
                target['models'] = merged
                try:
                    _save_custom_providers(providers)
                except RuntimeError as e:
                    raise ValueError(str(e))
                return {'success': True, 'provider': merge_target, 'merged_into': merge_target,
                        'added_count': added_count, 'models': merged}

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
        try:
            _save_custom_providers(providers)
        except RuntimeError as e:
            raise ValueError(str(e))
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
        # ── DEBUG: diagnose auto-detect failures (401 / latin-1 / timeout) ──
        try:
            _mask = (api_key[:6] + '...' + api_key[-4:]) if len(api_key) > 10 else ('<empty>' if not api_key else '<short:' + str(len(api_key)) + '>')
            print(f"[ModelManager] fetch_models_from_provider url={url} api_key_mask={_mask} has_emoji={'❌' in api_key or any(ord(c) > 255 for c in api_key)} key_len={len(api_key)}", flush=True)
            # Check whether the key can even be latin-1 encoded (pre-empt header error)
            try:
                api_key.encode('latin-1')
                _enc_ok = True
            except UnicodeEncodeError:
                _enc_ok = False
            print(f"[ModelManager] fetch_models_from_provider key_latin1_encodable={_enc_ok}", flush=True)
        except Exception as _de:
            print(f"[ModelManager] fetch_models_from_provider DEBUG log failed: {_de}", flush=True)
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        req = urllib.request.Request(url, headers=headers, method='GET')
        ctx = ssl.create_default_context()

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                body = json.loads(resp.read().decode('utf-8'))
            print(f"[ModelManager] fetch_models_from_provider OK url={url} status={getattr(resp, 'status', '?')} models={len(body.get('data', [])) if isinstance(body, dict) else 0}", flush=True)
        except urllib.error.HTTPError as e:
            body = None
            try:
                body = json.loads(e.read().decode('utf-8'))
            except Exception:
                pass
            print(f"[ModelManager] fetch_models_from_provider HTTPError code={e.code} url={url} body={str(body)[:300]}", flush=True)
            raise RuntimeError(f"HTTP {e.code}: {body.get('error', {}).get('message', str(e)) if isinstance(body, dict) else str(e)}")
        except urllib.error.URLError as e:
            print(f"[ModelManager] fetch_models_from_provider URLError url={url} reason={e.reason}", flush=True)
            raise RuntimeError(f"Connection failed: {e.reason}")
        except Exception as e:
            print(f"[ModelManager] fetch_models_from_provider EXC type={type(e).__name__} msg={e} url={url}", flush=True)
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
                    'type': self._infer_model_type(model_id),
                })

        if not models and isinstance(body, dict):
            if 'models' in body:
                for item in body.get('models', []):
                    if isinstance(item, dict) and 'id' in item:
                        mid = item['id']
                        models.append({'id': mid, 'label': item.get('display_name', mid), 'type': self._infer_model_type(mid)})

        return models

    # ── Model Type (3-tier fallback) ────────────────────────────────────

    @staticmethod
    def _infer_model_type(model_id: str) -> str:
        """Infer model type from name (tier 3: name-based guess)."""
        try:
            from api.media_generation import detect_model_type
            return detect_model_type(model_id)
        except ImportError:
            return 'chat'

    def get_model_type(self, model_id: str) -> str:
        """3-tier fallback: registry type > provider metadata > name-based guess.

        1) Check custom_providers.json model dict for explicit 'type' field.
        2) (Provider metadata is already stored as 'type' at fetch time.)
        3) Fall back to name-based detection.
        """
        model_id = (model_id or '').strip()
        if not model_id:
            return 'chat'

        # Tier 1+2: Registry lookup (type stored at fetch/add time)
        data = _load_custom_providers()
        for pname, cfg in data.get('providers', {}).items():
            for m in cfg.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id and isinstance(m, dict) and m.get('type'):
                    return m['type']

        # Also check preset provider models
        provider_models = self._get_all_provider_models()
        for p, models in provider_models.items():
            for m in models:
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id and isinstance(m, dict) and m.get('type'):
                    return m['type']

        # Tier 3: Name-based guess
        return self._infer_model_type(model_id)

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
        return None

    def _get_api_key(self, provider: str) -> str:
        """Get API key for a provider (checks custom providers → env → auth.json).

        auth.json은 활성 프로필 인식 경로(get_active_hermes_home)에서 읽는다.
        비-default 프로필의 자격증명은 ~/.hermes/profiles/<name>/auth.json에 저장되므로
        하드코딩된 ~/.hermes/auth.json만 보면 프로필 키를 놓쳐 프로바이더가 누락된다.
        """
        data = _load_custom_providers()
        providers = data.get('providers', {})

        if provider in providers:
            key = providers[provider].get('api_key', '')
            if key:
                return key

        env_key = os.getenv(f'{provider.upper()}_API_KEY', '')
        if env_key:
            return env_key

        # 프로필 인식 auth.json 탐색 (활성 프로필 → 기본 ~/.hermes 순)
        candidate_homes = []
        try:
            from api.profiles import get_active_hermes_home
            candidate_homes.append(get_active_hermes_home())
        except Exception:
            pass
        candidate_homes.append(Path.home() / '.hermes')
        for home in candidate_homes:
            try:
                auth_path = home / 'auth.json'
                if auth_path.exists():
                    auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                    pool = auth_data.get('credential_pool', {})
                    if provider in pool and pool[provider]:
                        token = pool[provider][0].get('access_token', '')
                        if token:
                            return token
            except Exception:
                continue

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
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id:
                    return model_id, p, self._get_base_url(p)

        # 2) Check custom provider models
        data = _load_custom_providers()
        for pname, cfg in data.get('providers', {}).items():
            for m in cfg.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id:
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
        # 프로필 인식 auth.json 탐색: 활성 프로필의 자격증명 풀을 먼저 읽고,
        # 기본 ~/.hermes/auth.json도 병합한다. 하드코딩 경로만 보면 비-default
        # 프로필의 키를 놓쳐 해당 프로바이더가 AVAILABLE MODELS에서 누락된다.
        auth_keys = {}
        candidate_homes = []
        try:
            from api.profiles import get_active_hermes_home
            candidate_homes.append(get_active_hermes_home())
        except Exception:
            pass
        candidate_homes.append(Path.home() / '.hermes')
        for home in candidate_homes:
            try:
                auth_path = home / 'auth.json'
                if auth_path.exists():
                    auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                    pool = auth_data.get('credential_pool', {})
                    for provider_name, credentials in pool.items():
                        if credentials and credentials[0].get('access_token'):
                            # 활성 프로필의 키를 우선시 (먼저 추가된 것 유지)
                            auth_keys.setdefault(provider_name, credentials[0].get('access_token'))
            except Exception as e:
                print(f"[ModelManager] Warning: failed to load auth.json ({home}): {e}")

        # Load all provider models dynamically
        provider_models = self._get_all_provider_models()
        custom_data = _load_custom_providers()
        custom_providers = custom_data.get('providers', {})

        # Show ALL presets that have models defined — API key presence is checked at call time
        ALLOWED_PRESETS = list(provider_models.keys())

        groups = []
        _added_provider_keys = set()  # 중복 방지

        # 1) Preset providers (built-in, from custom_providers.json presets)
        for provider in ALLOWED_PRESETS:
            if provider in provider_models:
                display_name = custom_data.get('presets', {}).get(provider, {}).get('label', provider.capitalize())
                env_key = f"{provider.upper()}_API_KEY"
                has_key = bool(
                    os.environ.get(env_key) or
                    auth_keys.get(provider) or
                    custom_data.get('providers', {}).get(provider, {}).get('api_key')
                )
                # Only include providers that HAVE an API key
                if has_key:
                    groups.append({
                        'provider': display_name,
                        'provider_key': provider,
                        'is_custom': False,
                        'models': list(provider_models[provider]),
                        'has_api_key': True,
                    })
                    _added_provider_keys.add(provider)

        # 2) Custom providers (from JSON providers section) — skip already-added
        for pname, cfg in custom_providers.items():
            if pname in _added_provider_keys:
                continue
            if cfg.get('api_key') and cfg.get('models'):
                display_name = cfg.get('label', pname.title())
                groups.append({
                    'provider': display_name,
                    'provider_key': pname,
                    'is_custom': True,
                    'base_url': cfg.get('base_url', ''),
                    'models': list(cfg.get('models', []))
                })
                _added_provider_keys.add(pname)

        # Inject model type so the frontend can show the media-option panel
        # (aspect ratio / count) when an image or video model is selected.
        for g in groups:
            new_models = []
            for m in g.get('models', []):
                if isinstance(m, dict):
                    mc = dict(m)
                    if not mc.get('type'):
                        mc['type'] = self.get_model_type(mc.get('id', ''))
                    new_models.append(mc)
                else:
                    mid = str(m)
                    new_models.append({'id': mid, 'label': mid, 'type': self.get_model_type(mid)})
            g['models'] = new_models

        return groups

    def get_presets(self) -> dict:
        """Return known provider presets (name → {base_url, label, models?})."""
        data = _load_custom_providers()
        return data.get('presets', dict(_PROVIDER_PRESETS))


model_manager = ModelManager()
