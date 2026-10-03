import os
import shutil
import re
from pathlib import Path

_PROFILE_ID_RE = re.compile(r'^[a-zA-Z0-9\u3131-\u3163\uac00-\ud7a3][a-zA-Z0-9\u3131-\u3163\uac00-\ud7a3()_\-\s]{0,63}$')
_DEFAULT_HERMES_HOME = Path.home() / '.hermes'

def _count_skills(skills_dir: Path) -> int:
    """하위 디렉토리(SKILL.md) 및 루트 md 스킬 개수를 정확히 집계합니다."""
    if not skills_dir.is_dir():
        return 0
    dirs = [d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
    if dirs:
        return len(dirs)
    return len(list(skills_dir.glob('*.md')))

def _extract_profile_model_info(home: Path) -> tuple[str | None, str | None]:
    """프로파일 디렉토리의 config.yaml에서 model과 provider를 파싱합니다."""
    config_path = home / 'config.yaml'
    model = None
    provider = None
    if config_path.exists():
        try:
            content = config_path.read_text(encoding='utf-8')
            lines = content.splitlines()
            model_block = False
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith('model:'):
                    parts = line_stripped.split(':', 1)
                    val = parts[1].strip().strip("'\"")
                    if val:
                        model = val
                        break
                    model_block = True
                elif model_block:
                    if line.startswith(' ') or line.startswith('\t'):
                        if line_stripped.startswith('default:'):
                            model = line_stripped.split(':', 1)[1].strip().strip("'\"")
                            break
                    else:
                        model_block = False
            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith('provider:'):
                    parts = line_stripped.split(':', 1)
                    val = parts[1].strip().strip("'\"")
                    if val:
                        provider = val
                        break
        except Exception:
            pass
    return model, provider

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

def hermes_home_for(profile_name: str = None) -> Path:
    """지정 프로파일의 home 경로를 반환합니다. 없거나 default면 _DEFAULT_HERMES_HOME."""
    if not profile_name or profile_name == 'default':
        return _DEFAULT_HERMES_HOME

    # 1) 정확한 디렉토리 이름 일치 확인 (~/.hermes/profiles/{profile_name})
    p = _DEFAULT_HERMES_HOME / 'profiles' / profile_name
    if p.is_dir():
        return p

    # 2) 별칭 / 부분 키워드 매핑 지원 (예: 'bill' -> '빌(개발)', 'sherlock' -> '셜록(검수)')
    profile_mapping = {
        "prada": "프라다(디자인)", "design": "프라다(디자인)", "프라다": "프라다(디자인)",
        "bill": "빌(개발)", "dev": "빌(개발)", "개발": "빌(개발)", "빌": "빌(개발)",
        "sherlock": "셜록(검수)", "qa": "셜록(검수)", "검수": "셜록(검수)", "셜록": "셜록(검수)",
        "tony": "토니(기획)", "planner": "토니(기획)", "기획": "토니(기획)", "토니": "토니(기획)",
        "raon": "raon", "라온": "raon",
        "daon": "다온(응대)", "다온": "다온(응대)",
    }
    lowered = profile_name.lower().strip()
    if lowered in profile_mapping:
        mapped_dir = _DEFAULT_HERMES_HOME / 'profiles' / profile_mapping[lowered]
        if mapped_dir.is_dir():
            return mapped_dir

    return _DEFAULT_HERMES_HOME


def get_profile_persona(profile_name: str = None) -> str:
    """지정 프로파일의 SOUL.md 및 AGENTS.md를 로드하여 시스템 프롬프트용 텍스트로 결합합니다."""
    home = hermes_home_for(profile_name)
    soul_file = home / 'SOUL.md'
    agents_file = home / 'AGENTS.md'

    parts = []
    eff_name = profile_name or home.name
    if soul_file.exists():
        try:
            soul_text = soul_file.read_text(encoding='utf-8').strip()
            if soul_text:
                parts.append(f"### [Core Persona / SOUL: {eff_name}]\n{soul_text}")
        except Exception:
            pass
    if agents_file.exists():
        try:
            agents_text = agents_file.read_text(encoding='utf-8').strip()
            if agents_text:
                parts.append(f"### [Operating Protocols / AGENTS: {eff_name}]\n{agents_text}")
        except Exception:
            pass
    return "\n\n".join(parts)


def get_active_hermes_home() -> Path:
    return hermes_home_for(get_active_profile_name())

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
    except PermissionError:
        pass  # PyInstaller _MEI* 임시 디렉토리는 읽기 전용 — 무시
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
        proj_env_dir = Path(__file__).parent.parent.parent.resolve()
        _reload_dotenv(proj_env_dir)

def list_profiles_api() -> list:
    active = get_active_profile_name()
    # 'default' 는 ~/.hermes 루트 자체(별도 폴더 아님)이고 실사용되지 않으므로 목록에서 숨긴다.
    # active_profile 이 비어 'default' 로 해석되는 경우에도 raon 을 활성으로 취급한다.
    if active == 'default':
        active = 'raon'
    result = []
    global_has_env = (_DEFAULT_HERMES_HOME / '.env').exists()

    # 1. Default(root) 항목은 UI 목록에 노출하지 않는다 — 전역 루트라 삭제 불가 · 미사용
    #    (과거에 'default' 항목을 append 했으나 대표님 요청으로 2026-10-02 제거)

    # 2. Sub-profiles
    profiles_dir = _DEFAULT_HERMES_HOME / 'profiles'
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.iterdir()):
            if p.is_dir():
                p_model, p_provider = _extract_profile_model_info(p)
                result.append({
                    'name': p.name,
                    'path': str(p),
                    'is_default': False,
                    'is_active': active == p.name,
                    'has_env': (p / '.env').exists(),
                    'has_global_env': global_has_env,
                    'skill_count': _count_skills(p / 'skills'),
                    'model': p_model,
                    'provider': p_provider,
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
        proj_env_dir = Path(__file__).parent.parent.parent.resolve()
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

def create_profile_api(name: str, clone_from: str = None, clone_config: bool = True) -> dict:
    if name == 'default':
        raise ValueError("Cannot create a profile named 'default'.")
    if not _PROFILE_ID_RE.match(name):
        raise ValueError("Invalid profile name format.")
        
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / name
    if profile_dir.exists():
        raise FileExistsError(f"Profile '{name}' already exists.")
        
    profile_dir.mkdir(parents=True, exist_ok=False)
    
    # Create standard directories (including home/ for subprocess isolation, plans, skins, workspace)
    subdirs = ['memories', 'sessions', 'skills', 'logs', 'cron', 'home', 'plans', 'skins', 'workspace']
    for subdir in subdirs:
        (profile_dir / subdir).mkdir(parents=True, exist_ok=True)
        
    # Determine clone source directory (specified profile -> active profile -> default)
    if clone_from:
        src_dir = hermes_home_for(clone_from)
    else:
        src_dir = hermes_home_for(get_active_profile_name())
    if not src_dir.is_dir():
        src_dir = _DEFAULT_HERMES_HOME

    # Clone config files from source (or default fallback)
    if clone_config:
        config_files = ['config.yaml', '.env', 'SOUL.md', 'AGENTS.md']
        for fn in config_files:
            src = src_dir / fn
            # If not in src_dir, check default hermes home for .env/config.yaml
            if not src.exists() and fn in ('.env', 'config.yaml'):
                src = _DEFAULT_HERMES_HOME / fn
            if src.exists():
                shutil.copy2(src, profile_dir / fn)
                
    # Create default soul if not cloned
    soul_path = profile_dir / 'SOUL.md'
    if not soul_path.exists():
        soul_path.write_text(f'# SOUL.md - {name}\n\nYou are a specialized assistant named {name}.\n', encoding='utf-8')
        
    global_has_env = (_DEFAULT_HERMES_HOME / '.env').exists()
    p_model, p_provider = _extract_profile_model_info(profile_dir)
    return {
        'name': name,
        'path': str(profile_dir),
        'is_default': False,
        'is_active': False,
        'has_env': (profile_dir / '.env').exists(),
        'has_global_env': global_has_env,
        'skill_count': _count_skills(profile_dir / 'skills'),
        'model': p_model,
        'provider': p_provider,
    }

def delete_profile_api(name: str) -> dict:
    if name == 'default':
        raise ValueError("Cannot delete the default profile.")
        
    profile_dir = _DEFAULT_HERMES_HOME / 'profiles' / name
    if not profile_dir.is_dir():
        raise ValueError(f"Profile '{name}' does not exist.")
        
    # If active, switch to raon first ('default' 는 목록에서 숨긴 루트 항목이므로 사용하지 않음)
    active = get_active_profile_name()
    if active == name:
        try:
            switch_profile('raon')
        except (ValueError, FileNotFoundError):
            switch_profile('default')
        
    shutil.rmtree(profile_dir)
    return {'ok': True, 'name': name}
