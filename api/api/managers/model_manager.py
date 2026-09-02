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
                        normalize_opencode_provider(pname): pcfg for pname, pcfg in file_presets.items()
                        if isinstance(pcfg, dict)
                    }
                file_providers = data.get('providers', {})
                if isinstance(file_providers, dict):
                    # OpenCode 이름 변형('opencode go' 등)을 hermes canonical id로
                    # 정규화해 이후 모든 조회(모델 해석/키/base_url)가一致하도록 한다.
                    result['providers'] = {
                        normalize_opencode_provider(pname): pcfg for pname, pcfg in file_providers.items()
                    }
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


# ── OpenCode (Zen / Go) provider normalization ──────────────────────────
# hermes-agent의 canonical provider id는 'opencode-go' / 'opencode-zen' (하이픈).
# 사용자가 UI에 'opencode go'(공백)처럼 등록하거나 CLI 별칭('go', 'zen')을
# 세팅에 남긴 경우에도 동일하게 라우팅되도록 정규화한다.
_OPENCODE_PROVIDER_ALIASES = {
    'go': 'opencode-go',
    'opencode go': 'opencode-go',
    'opencode-go-sub': 'opencode-go',
    'zen': 'opencode-zen',
    'opencode': 'opencode-zen',
    'opencode zen': 'opencode-zen',
}


def normalize_opencode_provider(provider: str) -> str:
    """Map OpenCode name variants (spaces/aliases) to hermes canonical ids."""
    p = (provider or '').strip().lower()
    return _OPENCODE_PROVIDER_ALIASES.get(p, p)


def normalize_opencode_model_id(provider: str, model_id: str) -> str:
    """Strip a leading '<provider>/' namespace from an OpenCode model id."""
    p = normalize_opencode_provider(provider)
    current = str(model_id or '').strip()
    if not current or p not in ('opencode-go', 'opencode-zen'):
        return current
    prefix = f'{p}/'
    if current.lower().startswith(prefix):
        return current[len(prefix):]
    return current


def _env_variants(provider: str) -> List[str]:
    """Env-var candidates for a provider's API key.

    'opencode-go' (hyphenated id) must also match OPENCODE_GO_API_KEY, which is
    the name hermes-agent's auth.py advertises. Returns de-duplicated candidates.
    """
    base = (provider or '').strip().upper()
    if not base:
        return []
    out = [f'{base}_API_KEY']
    alt = f"{base.replace('-', '_')}_API_KEY"
    if alt not in out:
        out.append(alt)
    return out


def resolve_opencode_route(provider: str, model: str,
                           base_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Compute (api_mode, base_url) for OpenCode Zen/Go providers.

    OpenCode 라우터는 모델마다 다른 API 표면을 노출한다 (2026-09 실측):
      - Go: GPT/Grok → /v1/responses (chat/messages는 401 "not supported
        for format" 또는 500), MiniMax → /v1/messages,
        GLM/Kimi/Mimo 등 → /v1/chat/completions
      - Zen: Claude → /v1/messages, GPT → /v1/responses, 그 외 → chat/completions
    다른 프로바이더에는 (None, base_url)를 반환해 호출 측이 그대로
    AIAgent(api_mode=...)에 넘길 수 있게 한다.
    """
    p = normalize_opencode_provider(provider)
    if p not in ('opencode-go', 'opencode-zen'):
        return None, base_url
    # hermes_cli.models.opencode_model_api_mode의 Go 테이블은 구버터(2026-09
    # 라이브 카탈로그에 gpt-5.6-luna/grok-4.x 추가됨)이므로 실측 결과로 보정한다.
    m = normalize_opencode_model_id(p, model or '').lower()
    api_mode = 'chat_completions'
    if p == 'opencode-go':
        if m.startswith('minimax-'):
            api_mode = 'anthropic_messages'
        elif m.startswith('gpt-') or m.startswith('grok-'):
            # oa-compat/anthropic 포맷 미지원 — Responses API 전용
            api_mode = 'codex_responses'
    elif p == 'opencode-zen':
        if m.startswith('claude-'):
            api_mode = 'anthropic_messages'
        elif m.startswith('gpt-'):
            api_mode = 'codex_responses'
    url = (base_url or '').strip().rstrip('/')
    if not url:
        url = ('https://opencode.ai/zen/go/v1' if p == 'opencode-go'
               else 'https://opencode.ai/zen/v1')
    # Anthropic SDK는 base_url에 자체적으로 /v1/messages를 붙인다.
    # trailing /v1을 제거해 .../v1/v1/messages 오경로를 방지한다
    # (hermes runtime_provider.py 동일 로직 미러).
    if api_mode == 'anthropic_messages':
        import re as _re
        url = _re.sub(r'/v1/?$', '', url)
    return api_mode, url


class ModelManager:
    """Manages available models and provider resolution — fully dynamic via custom_providers.json."""

    def __init__(self):
        # No hardcoded models — everything is loaded dynamically from custom_providers.json
        pass

    def _get_all_provider_models(self) -> Dict[str, List[Dict[str, str]]]:
        """Get provider→models mapping from the single source of truth (custom_providers.json).

        Merges: custom providers[].models (first) + presets[].models.

        2026-09-03 kimi-k3 사고 대책: merge 순서를 custom-first로 바꾼다.
        resolve_model_provider의 정확/대소문자 무시 매칭이 이 dict 순서대로
        돌기 때문에, 프리셋(opencode-go 33모델 — deepseek-v4-flash, minimax-m3
        등 동명 모델 포함)이 먼저면 사용자가 등록한 프로바이더를 거치지 않고
        opencode.ai로 라우팅되어 크레딧이 소진되었다.
        """
        data = _load_custom_providers()
        result = {}

        # 1) Models from custom providers (user-added, with API keys) — 최우선
        for pname, pcfg in data.get('providers', {}).items():
            if isinstance(pcfg, dict) and 'models' in pcfg:
                result[pname] = list(pcfg['models'])

        # 2) Models from presets (built-in providers) — 사용자 등록에 없을 때만
        for pname, pcfg in data.get('presets', {}).items():
            if isinstance(pcfg, dict) and 'models' in pcfg:
                result.setdefault(pname, list(pcfg['models']))

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

    def refresh_provider_models(self, name: str) -> dict:
        """기존 프로바이더의 저장된 base_url/api_key로 /models를 다시 호출해 모델 목록을 갱신한다.

        자동 감지 실패(Cloudflare 403 등)로 목록이 비었거나 오래된 경우,
        키를 다시 입력하지 않고도 카탈로그를 최신화할 수 있게 한다.
        """
        name = name.strip().lower()
        data = _load_custom_providers()
        providers = data.get('providers', {})
        if name not in providers:
            raise KeyError(f"Provider '{name}' not found")
        cfg = providers[name]
        base_url = (cfg.get('base_url') or '').strip()
        api_key = (cfg.get('api_key') or '').strip() or self._get_api_key(name)
        if not base_url:
            raise RuntimeError(f"프로바이더 '{name}'에 base_url이 저장되어 있지 않습니다")
        if not api_key:
            raise RuntimeError(f"프로바이더 '{name}'에 저장된 API 키가 없습니다 — 편집에서 키를 입력하세요")
        models = self.fetch_models_from_provider(base_url, api_key)
        if not models:
            raise RuntimeError("제공자가 0개의 모델을 반환했습니다 — 기존 목록을 유지합니다")
        cfg['models'] = models
        _save_custom_providers(providers)
        return {'success': True, 'provider': name, 'models': models, 'count': len(models)}

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
            # Cloudflare error 1010 차단 회피: urllib 기본 UA(Python-urllib/x.y) 금지.
            # opencode.ai는 구글 리버스 프록시 뒤에서 비브라우저 UA를 403으로 막는다.
            'User-Agent': 'DAON-Agent/1.0 (+https://daon.local)',
            'Accept': 'application/json',
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

        # 하이픈이 포함된 프로바이더명(opencode-go 등)은 env 변수로 쓸 수 없으므로
        # 언더스코어 변형도 함께 탐색한다.
        for env_name in _env_variants(provider):
            env_key = os.getenv(env_name, '')
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

        # 2026-09-03 kimi-k3 사고 대책: 매칭 우선순위를 사용자 등록(custom) 최우선으로
        # 바꾼다. 기존엔 프리셋 정확 일치가 custom 대소문자 무시 일치보다 앞서서,
        # opencode-go 프리셋(33모델 — minimax-m3, deepseek-v4-flash 등 동명 모델 포함)이
        # 사용자가 등록한 프로바이더의 동일 모델을 선점해 opencode.ai로 라우팅되었다.
        # 우선순위: custom 정확 > custom 대소문자 무시 > 프리셋 정확 > 프리셋 대소문자 무시
        # When model_id is an exact match in a provider's model list,
        # use the model_id as-is — NEVER strip namespace prefixes.
        # (e.g. NVIDIA NIM needs "z-ai/glm-5.2" in full, OpenRouter needs "tencent/hy3:free")
        data = _load_custom_providers()
        provider_models = self._get_all_provider_models()
        _custom = data.get('providers', {})
        _target = model_id.casefold()

        # 1) Custom provider 정확 일치 — 사용자 등록 최우선
        for pname, cfg in _custom.items():
            for m in cfg.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id:
                    return model_id, pname, cfg.get('base_url')

        # 2) Custom provider 대소문자 무시 일치 — canonical ID로 정규화
        # ('minimax-m3' → 'MiniMax-M3') 프론트/세션에 저장된 대소문자 변형 ID를
        # 등록된 원본 ID로 되돌려 이후 API 호출도 canonical 이름으로 수행한다.
        for pname, cfg in _custom.items():
            for m in cfg.get('models', []):
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid and mid.casefold() == _target:
                    return mid, pname, cfg.get('base_url')

        # 3) Preset provider 정확 일치
        for p, models in provider_models.items():
            for m in models:
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid == model_id:
                    return model_id, p, self._get_base_url(p)

        # 4) Preset provider 대소문자 무시 일치
        for p, models in provider_models.items():
            for m in models:
                mid = m.get('id') if isinstance(m, dict) else str(m)
                if mid and mid.casefold() == _target:
                    return mid, p, self._get_base_url(p)

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
        # UI 표시 순서는 기존대로 프리셋 먼저 유지한다(라우팅 우선순위와 UI 순서는 분리.
        # _get_all_provider_models는 라우팅 안전을 위해 custom-first로 바뀌었다).
        ALLOWED_PRESETS = ([p for p in custom_data.get('presets', {}) if p in provider_models]
                           + [p for p in provider_models if p not in custom_data.get('presets', {})])

        groups = []
        _added_provider_keys = set()  # 중복 방지

        # 1) Preset providers (built-in, from custom_providers.json presets)
        for provider in ALLOWED_PRESETS:
            if provider in provider_models:
                display_name = custom_data.get('presets', {}).get(provider, {}).get('label', provider.capitalize())
                has_key = bool(
                    any(os.environ.get(v) for v in _env_variants(provider)) or
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
