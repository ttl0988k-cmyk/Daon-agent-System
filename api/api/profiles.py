import os
import shutil
import re
from pathlib import Path

_PROFILE_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')
_DEFAULT_HERMES_HOME = Path.home() / '.hermes'

def get_active_profile_name() -> str:
    ap_file = _DEFAULT_HERMES_HOME / 'active_profile'
    if ap_file.exists():
        try:
            name = ap_file.read_text(encoding='utf-8').strip()
            if name:
                return name
        except Exception:
            pass
    return 'default'

def get_active_hermes_home() -> Path:
    active = get_active_profile_name()
    if active == 'default':
        return _DEFAULT_HERMES_HOME
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / active
    if profile_dir.is_dir():
        return profile_dir
    return _DEFAULT_HERMES_HOME

def _reload_dotenv(home: Path):
    env_path = home / '.env'
    if not env_path.exists():
        return
    try:
        # Clear existing keys from .env if we are reloading?
        # Typically dotenv only overrides or adds new ones.
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    os.environ[k] = v
    except Exception as e:
        print(f"Error loading profile .env: {e}")

def init_profile_state() -> None:
    home = get_active_hermes_home()
    os.environ['HERMES_HOME'] = str(home)
    
    # 1) Load active profile's .env
    _reload_dotenv(home)
    
    # 2) Load global ~/.hermes/.env
    global_env_dir = Path.home() / '.hermes'
    if global_env_dir.is_dir():
        _reload_dotenv(global_env_dir)
        
    # 3) Load project root .env (bundled .env first, then install root)
    import sys
    if hasattr(sys, '_MEIPASS'):
        # Check PyInstaller bundle first (server.spec bundles .env)
        bundle_env_dir = Path(sys._MEIPASS)
        _reload_dotenv(bundle_env_dir)
        # Also check the directory where server.exe lives (extraResources)
        exe_dir = Path(sys.executable).parent.resolve()
        if exe_dir != bundle_env_dir:
            _reload_dotenv(exe_dir)
    else:
        proj_env_dir = Path(__file__).parent.parent.resolve()
        _reload_dotenv(proj_env_dir)

def list_profiles_api() -> list:
    active = get_active_profile_name()
    result = []
    
    # 1. Default Profile
    result.append({
        'name': 'default',
        'path': str(_DEFAULT_HERMES_HOME),
        'is_default': True,
        'is_active': active == 'default',
        'has_env': (_DEFAULT_HERMES_HOME / '.env').exists(),
        'skill_count': len(list((_DEFAULT_HERMES_HOME / 'skills').glob('*.md'))) if (_DEFAULT_HERMES_HOME / 'skills').is_dir() else 0
    })
    
    # 2. Sub-profiles
    profiles_dir = _DEFAULT_HERMES_HOME / 'profiles'
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.iterdir()):
            if p.is_dir():
                result.append({
                    'name': p.name,
                    'path': str(p),
                    'is_default': False,
                    'is_active': active == p.name,
                    'has_env': (p / '.env').exists(),
                    'skill_count': len(list((p / 'skills').glob('*.md'))) if (p / 'skills').is_dir() else 0
                })
    return result

def switch_profile(name: str) -> dict:
    if name == 'default':
        home = _DEFAULT_HERMES_HOME
    else:
        home = _DEFAULT_HERMES_HOME / 'profiles' / name
        if not home.is_dir():
            raise ValueError(f"Profile '{name}' does not exist.")
            
    # Write active_profile file
    ap_file = _DEFAULT_HERMES_HOME / 'active_profile'
    try:
        ap_file.write_text(name if name != 'default' else '', encoding='utf-8')
    except Exception as e:
        print(f"Error writing active_profile: {e}")
        
    os.environ['HERMES_HOME'] = str(home)
    # Load profile, global, and project env files to keep API keys up to date
    _reload_dotenv(home)
    
    global_env_dir = Path.home() / '.hermes'
    if global_env_dir.is_dir():
        _reload_dotenv(global_env_dir)
        
    import sys
    if hasattr(sys, '_MEIPASS'):
        # Check PyInstaller bundle first
        bundle_env_dir = Path(sys._MEIPASS)
        _reload_dotenv(bundle_env_dir)
        # Also check the directory where server.exe lives (extraResources)
        exe_dir = Path(sys.executable).parent.resolve()
        if exe_dir != bundle_env_dir:
            _reload_dotenv(exe_dir)
    else:
        proj_env_dir = Path(__file__).parent.parent.resolve()
        _reload_dotenv(proj_env_dir)
    
    # Patch module level caches in hermes-agent if available
    try:
        import tools.skills_tool as _sk
        _sk.HERMES_HOME = home
        _sk.SKILLS_DIR = home / 'skills'
    except (ImportError, AttributeError):
        pass

    try:
        import cron.jobs as _cj
        _cj.HERMES_DIR = home
        _cj.CRON_DIR = home / 'cron'
        _cj.JOBS_FILE = _cj.CRON_DIR / 'jobs.json'
        _cj.OUTPUT_DIR = _cj.CRON_DIR / 'output'
    except (ImportError, AttributeError):
        pass

    # Read configuration defaults if exists
    default_model = None
    config_path = home / 'config.yaml'
    if config_path.exists():
        try:
            content = config_path.read_text(encoding='utf-8')
            lines = content.splitlines()
            model_block = False
            for idx, line in enumerate(lines):
                line_stripped = line.strip()
                if line_stripped.startswith('model:'):
                    parts = line_stripped.split(':', 1)
                    val = parts[1].strip()
                    if val:
                        default_model = val.strip("'\"")
                        break
                    model_block = True
                elif model_block:
                    if line.startswith(' ') or line.startswith('\t'):
                        if 'default:' in line_stripped:
                            default_model = line_stripped.split(':', 1)[1].strip().strip("'\"")
                            break
                    else:
                        model_block = False
        except Exception:
            pass

    return {
        'profiles': list_profiles_api(),
        'active': name,
        'default_model': default_model
    }

def create_profile_api(name: str, clone_config: bool = False) -> dict:
    if name == 'default':
        raise ValueError("Cannot create a profile named 'default'.")
    if not _PROFILE_ID_RE.match(name):
        raise ValueError("Invalid profile name format.")
        
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / name
    if profile_dir.exists():
        raise FileExistsError(f"Profile '{name}' already exists.")
        
    profile_dir.mkdir(parents=True, exist_ok=False)
    
    # Create standard directories
    subdirs = ['memories', 'sessions', 'skills', 'logs', 'cron']
    for subdir in subdirs:
        (profile_dir / subdir).mkdir(parents=True, exist_ok=True)
        
    # Clone config files from default if requested
    if clone_config:
        config_files = ['config.yaml', '.env', 'SOUL.md', 'AGENTS.md']
        for fn in config_files:
            src = _DEFAULT_HERMES_HOME / fn
            if src.exists():
                shutil.copy2(src, profile_dir / fn)
                
    # Create default soul if not cloned
    soul_path = profile_dir / 'SOUL.md'
    if not soul_path.exists():
        soul_path.write_text('# SOUL.md\n\nYou are a helpful assistant.\n', encoding='utf-8')
        
    return {
        'name': name,
        'path': str(profile_dir),
        'is_default': False,
        'is_active': False,
        'has_env': (profile_dir / '.env').exists(),
        'skill_count': 0
    }

def delete_profile_api(name: str) -> dict:
    if name == 'default':
        raise ValueError("Cannot delete the default profile.")
        
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / name
    if not profile_dir.is_dir():
        raise ValueError(f"Profile '{name}' does not exist.")
        
    # If active, switch to default first
    active = get_active_profile_name()
    if active == name:
        switch_profile('default')
        
    shutil.rmtree(profile_dir)
    return {'ok': True, 'name': name}
