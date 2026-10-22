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

    Returns dict with keys: 'presets', 'providers', 'disabled'
    - presets: provider_name → {base_url, label} (add-templates only.
      presets NEVER contribute models — see _get_all_provider_models.)
    - providers: provider_name → {api_key, base_url, models, label}
    - disabled: [provider_name...] user-deleted blocklist. env 키만으로
      부활하는 preset provider(opencode-zen 등)를 영구 차단한다.
      (대표님 지시 2026-10-17: 설정창 삭제가 전체 목록에 연동.)
    """
    path = _get_custom_providers_path()
    # Seed from hardcoded presets ONLY on first run (no file yet).
    result = {'presets': dict(_PROVIDER_PRESETS), 'providers': {}, 'disabled': []}
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
                file_disabled = data.get('disabled', [])
                if isinstance(file_disabled, list):
                    result['disabled'] = sorted({
                        normalize_opencode_provider(str(x))
                        for x in file_disabled if str(x).strip()
                    })
        except Exception as e:
            print(f"[ModelManager] Warning: failed to load custom_providers.json: {e}")
    return result


def _save_custom_providers(providers: dict) -> None:
    """Save custom providers to JSON file, preserving presets + disabled."""
    path = _get_custom_providers_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        print(f"[ModelManager] Warning: Cannot create directory {path.parent}: {e}")
        raise RuntimeError(f"Cannot create data directory: {e}")
    try:
        current = _load_custom_providers()
        current['providers'] = providers
        # Write back: presets + providers + disabled (all from file if exists)
        out = {
            'presets': current.get('presets', {}),
            'providers': providers,
            'disabled': current.get('disabled', []),
        }
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    except (PermissionError, OSError, IOError) as e:
        print(f"[ModelManager] Warning: Cannot write custom_providers.json: {e}")
        raise RuntimeError(f"Cannot save provider data: {e}")


def _set_provider_disabled(name: str, disabled: bool) -> list:
    """disabled 차단목록에 추가/제거하고 파일에 영속화. 정규화된 목록 반환."""
    cname = normalize_opencode_provider((name or '').strip().lower())
    if not cname:
        return []
    path = _get_custom_providers_path()
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    cur = raw.get('disabled', [])
    if not isinstance(cur, list):
        cur = []
    normed = sorted({normalize_opencode_provider(str(x)) for x in cur if str(x).strip()})
    if disabled:
        if cname not in normed:
            normed.append(cname)
            normed.sort()
    else:
        normed = [x for x in normed if x != cname]
    raw['disabled'] = normed
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
    except (PermissionError, OSError, IOError) as e:
        print(f"[ModelManager] Warning: Cannot persist disabled list: {e}")
    return normed


def _scrub_preset_models(name: str) -> bool:
    """해당 preset 엔트리에서 models를 제거(추가용 템플릿만 남김). 변경 시 True."""
    cname = normalize_opencode_provider((name or '').strip().lower())
    if not cname:
        return False
    path = _get_custom_providers_path()
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(raw, dict):
            return False
    except Exception:
        return False
    presets = raw.get('presets', {})
    if not isinstance(presets, dict):
        return False
    changed = False
    for pname, pcfg in list(presets.items()):
        if normalize_opencode_provider(pname) == cname and isinstance(pcfg, dict) and 'models' in pcfg:
            del pcfg['models']
            changed = True
    if changed:
        try:
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
        except (PermissionError, OSError, IOError) as e:
            print(f"[ModelManager] Warning: Cannot scrub preset models: {e}")
    return changed


# ── Hidden models ──
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
        elif m.startswith('gpt-') or m.startswith('grok-') or m.startswith('muse-spark-'):
            # oa-compat/anthropic 포맷 미지원 — Responses API 전용
            api_mode = 'codex_responses'
    elif p == 'opencode-zen':
        if m.startswith('claude-'):
            api_mode = 'anthropic_messages'
        elif m.startswith('gpt-') or m.startswith('muse-spark-'):
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

        단일 진실 원천 (대표님 지시 2026-10-17): 오직 'providers' 섹션
        (사용자가 UI에서 명시 등록)만 모델 출처다. presets는 추가용
        템플릿(base_url/label)일 뿐 모델을 기여하지 않는다.
        실측 결함: presets에 models가 박혀 있으면(구 파일 잔재: opencode-zen
        11종) env 키 존재만으로 /api/models·하네스·확장에 미등록 모델이
        노출돼 잔액 0 호출 → 전멸했다. disabled 차단목록도 여기서 제외.
        """
        data = _load_custom_providers()
        result = {}
        disabled = {normalize_opencode_provider(str(x)) for x in (data.get('disabled') or []) if str(x).strip()}

        # Models from custom providers (user-added) ONLY — presets 제외
        for pname, pcfg in data.get('providers', {}).items():
            if not isinstance(pcfg, dict) or 'models' not in pcfg:
                continue
            try:
                ckey = normalize_opencode_provider(pname)
            except Exception:
                ckey = str(pname).strip().lower()
            if ckey in disabled:
                continue
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
                existing_target_key = target.get('api_key', '')
                if not api_key or '•' in api_key or '*' in api_key:
                    final_key = existing_target_key
                else:
                    final_key = api_key
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
                _set_provider_disabled(merge_target, False)
                _set_provider_disabled(name, False)
                return {'success': True, 'provider': merge_target, 'merged_into': merge_target,
                        'added_count': added_count, 'models': merged}

        # Preserve existing api_key if empty string or masked key sent (update without changing key)
        existing_key = ''
        if name in providers:
            existing_key = providers[name].get('api_key', '')

        if not api_key or '•' in api_key or '*' in api_key:
            final_key = existing_key
        else:
            final_key = api_key

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
        _set_provider_disabled(name, False)
        return {'success': True, 'provider': name, 'models': models or []}

    def delete_custom_provider(self, name: str) -> dict:
        """Delete a custom provider — 설정창 삭제가 전체 목록에 연동된다.

        대표님 지시 (2026-10-17): providers 삭제 + disabled 차단목록 등록 +
        preset models 스크럽을 한 번에 수행한다. env 키만으로 부활하던
        preset provider(opencode-zen 등)도 presets-only 이름으로 삭제하면
        차단+스크럽돼 /api/models·하네스·확장 등 모든 목록에서 사라진다.
        """
        raw = (name or '').strip().lower()
        cname = normalize_opencode_provider(raw)
        if not raw:
            raise ValueError('Provider name is required')
        data = _load_custom_providers()
        providers = data.get('providers', {})
        presets = data.get('presets', {}) or {}
        removed = False
        for key in {raw, cname}:
            if key in providers:
                del providers[key]
                removed = True
        preset_hit = any(
            normalize_opencode_provider(pn) == cname for pn in presets
        )
        if not removed and not preset_hit:
            raise KeyError(f"Provider '{name}' not found")
        if removed:
            _save_custom_providers(providers)
        _set_provider_disabled(cname, True)
        _scrub_preset_models(cname)
        try:
            from api.dynamic.model_selector import get_model_selector
            _sel = get_model_selector()
            if _sel is not None:
                _sel.refresh_profiles()
        except Exception:
            pass
        return {'success': True, 'provider': cname, 'removed_entry': removed,
                'preset_scrubbed': preset_hit, 'disabled': True}

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
        # Guard: model_id must be str. A dict (e.g. {'default': ...}) reaching
        # .strip() raises AttributeError and crashes the agent stream.
        if not isinstance(model_id, str):
            if isinstance(model_id, dict):
                model_id = model_id.get('default') or model_id.get('id') or model_id.get('model') or ''
            else:
                model_id = str(model_id or '')
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

    def get_model_reasoning_effort(self, model_id: str, provider: str = '') -> Optional[str]:
        """3-tier fallback for model reasoning effort: custom provider -> presets -> None.

        1) Check custom_providers.json model dict for explicit 'reasoning_effort' field.
        2) Check preset provider models for 'reasoning_effort'.
        3) Fallback to None (let session/global default take over).
        """
        # Guard: model_id must be str. A dict (e.g. {'default': ...}) reaching
        # .strip() raises AttributeError and crashes the agent stream.
        if not isinstance(model_id, str):
            if isinstance(model_id, dict):
                model_id = model_id.get('default') or model_id.get('id') or model_id.get('model') or ''
            else:
                model_id = str(model_id or '')
        model_id = (model_id or '').strip()
        if not model_id:
            return None

        data = _load_custom_providers()
        # 1) Custom providers
        for pname, cfg in data.get('providers', {}).items():
            if provider and pname != provider:
                continue
            for m in cfg.get('models', []):
                if isinstance(m, dict):
                    mid = m.get('id', '')
                    if mid == model_id and m.get('reasoning_effort'):
                        return m['reasoning_effort']

        # 2) Presets
        for pname, pcfg in data.get('presets', {}).items():
            if provider and pname != provider:
                continue
            for m in pcfg.get('models', []):
                if isinstance(m, dict):
                    mid = m.get('id', '')
                    if mid == model_id and m.get('reasoning_effort'):
                        return m['reasoning_effort']

        return None

    @staticmethod
    def supports_reasoning(model_id: str, provider: str = '') -> bool:
        """Smart detection: returns True if model supports reasoning/thinking effort.

        Guards against sending reasoning_effort to non-reasoning models (gpt-4o, claude-3-5, etc.)
        which causes API 400 Bad Request errors.
        """
        if not model_id:
            return True
        mid = str(model_id).strip().lower()
        pname = str(provider).strip().lower()

        # Explicit non-reasoning exclusions
        non_reasoning_prefixes = (
            'gpt-4o', 'gpt-4-turbo', 'gpt-4.1', 'gpt-4', 'gpt-3.5',
            'claude-3-5', 'claude-3.5', 'claude-3-opus', 'claude-3-haiku',
            'gemini-1.5', 'gemini-1.0',
            'llama', 'mistral', 'codestral', 'command-r'
        )
        if any(mid.startswith(p) or ('/' + p) in mid for p in non_reasoning_prefixes):
            if 'thinking' not in mid and 'reason' not in mid and 'r1' not in mid:
                return False

        reasoning_patterns = (
            r'\bo[1-4](?:-mini|-preview)?\b',
            r'\bclaude-3[-.]7\b',
            r'\bclaude-4\b',
            r'thinking',
            r'reason',
            r'\br1\b',
            r'\bqwq\b',
            r'minimax',
            r'deepseek',
            r'glm',
            r'qwen',
            r'mimo',
            r'auto',
            r'gpt-oss',
        )
        import re
        if any(re.search(pat, mid) for pat in reasoning_patterns):
            return True
        if pname in ('minimax', 'deepseek', 'opencode-go', 'opencode-zen', 'omniroute'):
            return True
        return False

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
        # Guard: model_id must be str. A dict (e.g. {'default': ...}) reaching
        # .strip() raises AttributeError and crashes the agent stream.
        if not isinstance(model_id, str):
            if isinstance(model_id, dict):
                model_id = model_id.get('default') or model_id.get('id') or model_id.get('model') or ''
            else:
                model_id = str(model_id or '')
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

        # 5) Check if model_id has a provider/ prefix (e.g. 'deepseek/deepseek-v4.1-flash' or 'opencode-go/deepseek-v4.1-flash')
        if '/' in model_id:
            raw_prefix, bare_model = model_id.split('/', 1)
            bare_target = bare_model.casefold()

            # 5-a) 접두사가 등록된 custom provider인 경우 (e.g. 'opencode-go', 'opencode-zen')
            for pname, cfg in _custom.items():
                if pname.casefold() == raw_prefix.casefold():
                    return bare_model, pname, cfg.get('base_url')

            # 5-b) [OpenCode Go 최우선 방어 가드]
            # 'deepseek/deepseek-v4.1-flash' 처럼 OpenRouter식 네임스페이스로 요청이 들어왔더라도,
            # bare_model이 사용자가 등록한 custom provider(특히 OpenCode Go / Zen)의 모델 목록에 존재하면
            # OpenRouter나 외부 유료 직통으로 새어나가지 않고 등록된 custom provider로 우선 매핑한다!
            for pname, cfg in _custom.items():
                for m in cfg.get('models', []):
                    mid = m.get('id') if isinstance(m, dict) else str(m)
                    if mid and (mid == bare_model or mid.casefold() == bare_target):
                        return mid, pname, cfg.get('base_url')

            # 5-c) Preset provider 모델 중 bare_model이 일치하는 경우
            for p, models in provider_models.items():
                if p.casefold() == raw_prefix.casefold():
                    return bare_model, p, self._get_base_url(p)

            # 5-d) 일반 prefix 분기 fallback
            return bare_model, raw_prefix, self._get_base_url(raw_prefix)

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
        # 순서는 custom-first 병합 순서(_get_all_provider_models)를 그대로 따른다:
        # 사용자 등록 프로바이더가 UI에도 먼저 노출되고, dag_utils의 custom-first
        # 앵커 정렬(기억 큐 등 미지정 호출 보호)과 그룹 순서가 일치한다.
        ALLOWED_PRESETS = list(provider_models.keys())

        groups = []
        _added_provider_keys = set()  # 중복 방지
        # is_custom 판정은 "어느 단계에서 추가됐는지"가 아니라
        # "사용자가 직접 등록한 프로바이더인지" 기준이어야 한다.
        # (커스텀 병합 순서 변경으로 커스텀 그룹이 1단계에서 추가될 수 있고,
        #  이 플래그가 틀리면 dag_utils의 custom-first 앵커 정렬이 무력화된다.)
        _custom_names = set(custom_providers.keys())

        # 1) Preset providers (built-in, from custom_providers.json presets)
        # ── 설정창 연동 (대표님 지시 2026-10-17) ──
        # env 키만으로 preset이 모델 목록에 부활하던 구멍을 막는다.
        # preset 그룹은 사용자가 명시 등록한 경우(providers 섹션에 키+모델 보유,
        # 또는 auth.json 크리덴셜 풀에 토큰)에만 노출한다. 둘 다 없으면
        # 설정창에 없는 provider이므로 목록에서 제외한다.
        _explicitly_registered = set(_custom_names) | {
            str(_ak).strip().lower() for _ak, _av in (auth_keys or {}).items() if _av
        }
        for provider in ALLOWED_PRESETS:
            if provider in provider_models:
                if str(provider).strip().lower() not in _explicitly_registered:
                    continue
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
                        'is_custom': provider in _custom_names,
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

        # Inject model type and supports_reasoning so the frontend can show the media-option panel
        # and reasoning effort selector when applicable.
        for g in groups:
            new_models = []
            for m in g.get('models', []):
                if isinstance(m, dict):
                    mc = dict(m)
                    if not mc.get('type'):
                        mc['type'] = self.get_model_type(mc.get('id', ''))
                    mc['supports_reasoning'] = self.supports_reasoning(mc.get('id', ''), g.get('provider', ''))
                    new_models.append(mc)
                else:
                    mid = str(m)
                    new_models.append({
                        'id': mid,
                        'label': mid,
                        'type': self.get_model_type(mid),
                        'supports_reasoning': self.supports_reasoning(mid, g.get('provider', ''))
                    })
            g['models'] = new_models

        return groups

    def get_presets(self) -> dict:
        """Return known provider presets (name → {base_url, label, models?})."""
        data = _load_custom_providers()
        return data.get('presets', dict(_PROVIDER_PRESETS))

    def get_harness_allowed_providers(self) -> List[str]:
        """하네스용 허용 provider_key 목록 — 사용자가 명시 등록한 것만.

        대표님 지시 (2026-10-17): 하네스는 '앱에 설정된 모델'만 쓴다.
        실측 결함: get_available_models()는 환경변수 키 존재만으로 preset
        provider(opencode-zen 등)를 available에 올린다. 서버 프로세스에
        OPENCODE_ZEN_API_KEY가 주입돼 있으면 Zen 전체(opus-5-5·sonnet-5-5)가
        후보에 들어 잔액 0인데도 호출 시도 → 7노드 전멸·복구 루프.
        이 헬퍼는 custom_providers.json 'providers' 섹션(api_key+models 보유,
        = 사용자가 UI에서 명시 등록) + auth.json credential_pool 토큰 보유
        provider만 반환한다. presets 단독(env 키만)은 제외.
        provider_key 기준 정렬 리스트. 실패 시 빈 리스트(호출부가 None 폴백).
        disabled 차단목록은 auth 풀에 토큰이 있어도 제외한다.
        """
        allowed: set = set()
        try:
            data = _load_custom_providers()
            _dis = {normalize_opencode_provider(str(x)) for x in (data.get('disabled') or []) if str(x).strip()}
        except Exception:
            _dis = set()
        try:
            data = _load_custom_providers()
            for pname, pcfg in (data.get('providers') or {}).items():
                if not isinstance(pcfg, dict):
                    continue
                if pcfg.get('api_key') and pcfg.get('models'):
                    try:
                        allowed.add(normalize_opencode_provider(pname))
                    except Exception:
                        allowed.add(str(pname).strip().lower())
        except Exception:
            pass
        try:
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
                    if not auth_path.exists():
                        continue
                    auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                    pool = auth_data.get('credential_pool', {})
                    for provider_name, credentials in pool.items():
                        if credentials and credentials[0].get('access_token'):
                            try:
                                _ck = normalize_opencode_provider(provider_name)
                            except Exception:
                                _ck = str(provider_name).strip().lower()
                            if _ck not in _dis:
                                allowed.add(_ck)
                except Exception:
                    continue
        except Exception:
            pass
        return sorted(allowed)


model_manager = ModelManager()
