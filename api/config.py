import logging
import os
import sys
import collections
import json
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_logger = logging.getLogger(__name__)

# =============================================================================
# Local project paths
# =============================================================================
HOME = Path.home()
if hasattr(sys, '_MEIPASS'):
    # server.exe is at resources/server.exe; extraResources live at resources/
    BASE_DIR = Path(sys.executable).parent.resolve()
    if (BASE_DIR / 'static').exists():
        RESOURCE_DIR = BASE_DIR
    elif (Path.cwd() / 'static').exists():
        RESOURCE_DIR = Path.cwd()
    else:
        RESOURCE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent.parent.resolve()
    RESOURCE_DIR = BASE_DIR
STATE_DIR = BASE_DIR / 'data'
SESSION_DIR = STATE_DIR / 'sessions'
WORKSPACES_FILE = STATE_DIR / 'workspaces.json'
SETTINGS_FILE = STATE_DIR / 'settings.json'
SESSION_INDEX_FILE = SESSION_DIR / '_index.json'
PROJECTS_FILE = STATE_DIR / 'projects.json'
LAST_WORKSPACE_FILE = STATE_DIR / 'last_workspace.txt'

# Ensure directories exist
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# Add hermes-agent path to sys.path
AGENT_PATHS = [
    str(RESOURCE_DIR / 'hermes-agent'),
    str(BASE_DIR / 'hermes-agent'),
]

for p in AGENT_PATHS:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)
        # Also add its parent dir to path
        parent_dir = str(Path(p).parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

# =============================================================================
# Config YAML loading (project-level config.yaml, then profile config.yaml)
# =============================================================================
_cfg_cache = {}
_cfg_lock = threading.Lock()

def _get_config_path() -> Path:
    """Return the profile-level config.yaml path (e.g. ~/.hermes/config.yaml)."""
    env_override = os.getenv('HERMES_CONFIG_PATH')
    if env_override:
        return Path(env_override).expanduser()
    try:
        from api.profiles import get_active_hermes_home
        return get_active_hermes_home() / 'config.yaml'
    except ImportError:
        _logger.debug("profiles module not available, using default config path")
        return Path.home() / '.hermes' / 'config.yaml'

def _get_project_config_path() -> Path:
    """Return the project-level config.yaml path (next to server.py)."""
    # Check RESOURCE_DIR first (PyInstaller bundle), then BASE_DIR (install root)
    cfg_in_resource = RESOURCE_DIR / 'config.yaml'
    if cfg_in_resource.exists():
        return cfg_in_resource
    return BASE_DIR / 'config.yaml'

def get_config() -> dict:
    if not _cfg_cache:
        reload_config()
    return _cfg_cache

def reload_config() -> None:
    """Reload config from project-level config.yaml, then profile config.yaml."""
    with _cfg_lock:
        _cfg_cache.clear()
        # 1) Project-level config.yaml (BASE_DIR / config.yaml)
        project_cfg = _get_project_config_path()
        try:
            import yaml as _yaml
            if project_cfg.exists():
                loaded = _yaml.safe_load(project_cfg.read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    _cfg_cache.update(loaded)
        except Exception:
            pass
        # 2) Profile-level config.yaml (overrides project-level)
        profile_cfg = _get_config_path()
        try:
            import yaml as _yaml
            if profile_cfg.exists() and profile_cfg != project_cfg:
                loaded = _yaml.safe_load(profile_cfg.read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    _cfg_cache.update(loaded)
        except Exception:
            pass

reload_config()
cfg = _cfg_cache


# =============================================================================
# Config helper: config.yaml → env var → hardcoded default
# =============================================================================
def _load_config_value(key_path, env_var=None, default=None):
    """Look up a value from config.yaml (via dot-separated key_path),
    then from an environment variable, then fall back to `default`."""
    # 1) Try config.yaml
    val = cfg
    for part in key_path.split('.'):
        if isinstance(val, dict):
            val = val.get(part)
        else:
            val = None
            break
    if val is not None:
        return val
    # 2) Try environment variable
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None:
            return env_val
    # 3) Fall back to hardcoded default
    return default


# =============================================================================
# Server host / port
# =============================================================================
PORT = int(_load_config_value('server.port', 'PORT', 9090))
HOST = _load_config_value('server.host', None, '0.0.0.0')


# =============================================================================
# Global in-memory locks and queues
# =============================================================================
LOCK = threading.Lock()
STREAMS = {}  # stream_id -> queue.Queue
STREAMS_LOCK = threading.Lock()
CANCEL_FLAGS = {}  # stream_id -> threading.Event
SERVER_START_TIME = time.time()
CHAT_LOCK = threading.Lock()

# Thread-local env context
_thread_ctx = threading.local()
def _set_thread_env(**kwargs):
    _thread_ctx.env = kwargs
def _clear_thread_env():
    _thread_ctx.env = {}

# Per-session agent locks
SESSION_AGENT_LOCKS = {}
SESSION_AGENT_LOCKS_LOCK = threading.Lock()
def _get_session_agent_lock(session_id: str) -> threading.Lock:
    with SESSION_AGENT_LOCKS_LOCK:
        if session_id not in SESSION_AGENT_LOCKS:
            SESSION_AGENT_LOCKS[session_id] = threading.Lock()
        return SESSION_AGENT_LOCKS[session_id]


# =============================================================================
# Model constants
# =============================================================================
DEFAULT_MODEL = _load_config_value('model.default', None, "minimax-m3") # fallback default


# =============================================================================
# Limits
# =============================================================================
MAX_FILE_BYTES = int(_load_config_value('limits.max_file_bytes', None, 200_000))
MAX_UPLOAD_BYTES = int(_load_config_value('limits.max_upload_bytes', None, 20 * 1024 * 1024))
SESSIONS_MAX = int(_load_config_value('limits.sessions_cache_max', None, 100))


# =============================================================================
# File type maps
# =============================================================================
def _load_ext_set(key_path, default_set):
    """Load a set of extensions from config.yaml, falling back to default_set."""
    raw = _load_config_value(key_path, None, None)
    if isinstance(raw, list):
        return {s.strip().lower() for s in raw if isinstance(s, str)}
    return default_set

IMAGE_EXTS = _load_ext_set('extensions.image', {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp'})
MD_EXTS = _load_ext_set('extensions.markdown', {'.md', '.markdown', '.mdown'})
CODE_EXTS = _load_ext_set('extensions.code', {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.css', '.html', '.json',
    '.yaml', '.yml', '.toml', '.sh', '.bash', '.txt', '.log', '.env',
    '.csv', '.xml', '.sql', '.rs', '.go', '.java', '.c', '.cpp', '.h',
})


# =============================================================================
# MIME type map
# =============================================================================
_DEFAULT_MIME_MAP = {
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.svg': 'image/svg+xml', '.webp': 'image/webp',
    '.ico': 'image/x-icon', '.bmp': 'image/bmp',
    '.pdf': 'application/pdf', '.json': 'application/json',
    '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
}

def _load_mime_map(default_map):
    """Load MIME map from config.yaml, falling back to default_map."""
    raw = _load_config_value('mime_map', None, None)
    if isinstance(raw, dict):
        return {k.lower(): v for k, v in raw.items()}
    return dict(default_map)

MIME_MAP = _load_mime_map(_DEFAULT_MIME_MAP)


# =============================================================================
# Toolsets
# =============================================================================
_DEFAULT_TOOLSETS = _load_config_value('toolsets.default', None, [
    'browser', 'clarify', 'code_execution', 'cronjob', 'delegation', 'file',
    'image_gen', 'memory', 'session_search', 'skills', 'terminal', 'todo',
    'web', 'webhook',
])
CLI_TOOLSETS = _DEFAULT_TOOLSETS  # simple fallback


# =============================================================================
# Fallback models
# =============================================================================
_FALLBACK_MODELS = _load_config_value('fallback_models', None, [
    {'provider': 'OpenRouter', 'id': 'nvidia/nemotron-3-ultra-550b-a55b:free', 'label': 'Nemotron 3 Ultra (Free)'},
    {'provider': 'OpenRouter', 'id': 'openai/gpt-oss-120b:free', 'label': 'GPT-OSS 120B'},
    {'provider': 'OpenRouter', 'id': 'tencent/hy3:free', 'label': 'Tencent Hy3 (free)'},
])


# =============================================================================
# Provider display names
# =============================================================================
_PROVIDER_DISPLAY = _load_config_value('provider_display', None, {
    'openai': 'OpenAI', 'openai-codex': 'OpenAI Codex', 'anthropic': 'Anthropic',
    'openrouter': 'OpenRouter', 'xai': 'xAI', 'zai': 'ZhipuAI', 'kimi-coding': 'Kimi',
    'deepseek': 'DeepSeek', 'nous': 'Nous',
    'minimax': 'MiniMax', 'nvidia': 'NVIDIA NIM', 'meta-llama': 'Meta Llama',
    'huggingface': 'HuggingFace', 'alibaba': 'Alibaba',
    'ollama': 'Ollama', 'lmstudio': 'LM Studio',
})


# =============================================================================
# Provider model lists — loaded dynamically from data/custom_providers.json.
# This is just a config.yaml override passthrough; the single source of truth
# is the JSON file managed by model_manager.
# =============================================================================
_PROVIDER_MODELS = _load_config_value('provider_models', None, {})


# =============================================================================
# Static file path (PyInstaller-compatible)
# =============================================================================
if hasattr(sys, '_MEIPASS'):
    _INDEX_HTML_PATH = Path(sys._MEIPASS) / 'index.html'
else:
    _INDEX_HTML_PATH = BASE_DIR / 'index.html'

# Default workspace discovery
def _discover_default_workspace() -> Path:
    if os.getenv('HERMES_WEBUI_DEFAULT_WORKSPACE'):
        return Path(os.getenv('HERMES_WEBUI_DEFAULT_WORKSPACE')).expanduser().resolve()
    common = Path.home() / 'workspace'
    if common.exists():
        return common.resolve()
    return (STATE_DIR / 'workspace').resolve()

DEFAULT_WORKSPACE = _discover_default_workspace()

def resolve_model_provider(model_id: str) -> tuple:
    from api.managers.model_manager import model_manager
    return model_manager.resolve_model_provider(model_id)

def get_available_models() -> dict:
    from api.managers.model_manager import model_manager
    active_provider = None
    default_model = DEFAULT_MODEL
    
    cfg_default = ''
    model_cfg = cfg.get('model', {})
    if isinstance(model_cfg, str):
        default_model = model_cfg
    elif isinstance(model_cfg, dict):
        active_provider = model_cfg.get('provider')
        cfg_default = model_cfg.get('default', '')
        if cfg_default:
            default_model = cfg_default
            
    env_model = os.getenv('HERMES_MODEL') or os.getenv('OPENAI_MODEL') or os.getenv('LLM_MODEL')
    if env_model:
        default_model = env_model.strip()
        
    groups = model_manager.get_available_models()
    
    if default_model:
        all_ids = {m['id'] for g in groups for m in g.get('models', [])}
        if default_model not in all_ids:
            label = default_model.split('/')[-1] if '/' in default_model else default_model
            injected = False
            for g in groups:
                if active_provider and active_provider.lower() in g.get('provider', '').lower():
                    g['models'].insert(0, {'id': default_model, 'label': label})
                    injected = True
                    break
            if not injected and groups:
                groups[0]['models'].insert(0, {'id': default_model, 'label': label})
            elif not groups:
                groups.append({'provider': active_provider or 'Default', 'models': [{'id': default_model, 'label': label}]})
                
    return {
        'active_provider': active_provider,
        'default_model': default_model,
        'groups': groups,
    }

# Settings persistence
_SETTINGS_DEFAULTS = {
    'default_model': DEFAULT_MODEL,
    'default_workspace': str(DEFAULT_WORKSPACE),
    'send_key': 'enter',
    'show_token_usage': False,
    'show_cli_sessions': False,
    'sync_to_insights': False,
    'theme': 'cherry-blossom',
    'bot_name': 'Hermes',
    'password_hash': None,
}

def load_settings() -> dict:
    settings = dict(_SETTINGS_DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            if isinstance(stored, dict):
                settings.update(stored)
        except Exception:
            _logger.warning("Failed to load settings from %s", SETTINGS_FILE, exc_info=True)
    return settings

_SETTINGS_ALLOWED_KEYS = set(_SETTINGS_DEFAULTS.keys()) - {'password_hash'}
_SETTINGS_ENUM_VALUES = {
    'send_key': {'enter', 'ctrl+enter'},
}
_SETTINGS_BOOL_KEYS = {'show_token_usage', 'show_cli_sessions', 'sync_to_insights'}

def save_settings(settings: dict) -> dict:
    import hashlib as _hl
    current = load_settings()
    raw_pw = settings.pop('_set_password', None)
    if raw_pw and isinstance(raw_pw, str) and raw_pw.strip():
        salt = str(STATE_DIR).encode()
        current['password_hash'] = _hl.sha256(salt + raw_pw.strip().encode()).hexdigest()
    if settings.pop('_clear_password', False):
        current['password_hash'] = None
    for k, v in settings.items():
        if k in _SETTINGS_ALLOWED_KEYS:
            if k in _SETTINGS_ENUM_VALUES and v not in _SETTINGS_ENUM_VALUES[k]:
                continue
            if k in _SETTINGS_BOOL_KEYS:
                v = bool(v)
            current[k] = v
            
    SETTINGS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    
    global DEFAULT_MODEL, DEFAULT_WORKSPACE
    if 'default_model' in current:
        DEFAULT_MODEL = current['default_model']
    if 'default_workspace' in current:
        DEFAULT_WORKSPACE = Path(current['default_workspace']).expanduser().resolve()
    return current

# Apply saved settings on startup
_startup_settings = load_settings()
if SETTINGS_FILE.exists():
    if _startup_settings.get('default_model'):
        DEFAULT_MODEL = _startup_settings['default_model']
    if _startup_settings.get('default_workspace'):
        DEFAULT_WORKSPACE = Path(_startup_settings['default_workspace']).expanduser().resolve()

# SESSIONS in-memory cache
SESSIONS = collections.OrderedDict()
SESSIONS_MAX = 100

# Try to initialize profile state on startup
try:
    from api.profiles import init_profile_state
    init_profile_state()
except ImportError:
    pass
