"""harness-worker - 외부 코딩 하네스를 워커로 스폰하는 순수 로직.

Hermes 를 임포트하지 않는다 - 단위 테스트가 가능해야 한다.

여기서 봉인하는 함정들:
  1. 비대화형 실행 시 stdin 을 닫지 않으면 두 CLI 가 입력을 기다리며 멈춘다
     -> subprocess.DEVNULL 로 코드에서 강제한다. 사람이 잊을 수 없다.
  2. Codex 는 git 저장소 밖에서 실행을 거부한다 -> 자동 준비.
  3. OpenRouter 키를 매번 손으로 읽지 않는다 -> 내부에서 해결.
  4. 출력에 warning/tokens used 잡음이 섞인다 -> 정제해서 본문만 반환.
  5. .cmd/.bat 은 shell 없이 직접 실행할 수 없다 -> cmd.exe /c 로 감싼다.

[2026-09-20 프로바이더 선택식 개편]
  6. Codex 0.152+ 는 wire_api="chat" 을 폐지했다 -> "responses" 강제.
  7. Codex 는 ~/.codex/config.toml 을 전역으로 읽어 프로바이더를 못 바꾼다
     -> 프로바이더별 임시 CODEX_HOME 을 만들어 CODEX_HOME env 로 주입한다.
     (사용자의 실제 ~/.codex 는 절대 건드리지 않는다)
  8. Claude Code 는 claude-* 카탈로그 이름만 통과시킨다
     -> LiteLLM 게이트웨이가 프로바이더마다 claude-<alias> 를 만들어 준다.
  9. opencode-go 는 브라우저 User-Agent + x-opencode-session 헤더가 없으면
     403(Cloudflare 1010) / 400(MissingSessionID) 로 죽는다 -> 기본 헤더로 봉인.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

PROVIDER_JSON = Path(
    r"C:\Users\ttl09\AppData\Local\DAON Agent System\data\custom_providers.json"
)

CODEX_EXE_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft/WinGet/Packages/OpenAI.Codex_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "codex-x86_64-pc-windows-msvc.exe",
    Path(os.environ.get("APPDATA", "")) / "npm/codex.cmd",
]

CLAUDE_CMD_CANDIDATES = [
    Path(os.environ.get("APPDATA", ""))
    / "npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
    Path(os.environ.get("APPDATA", "")) / "npm/claude.cmd",
    Path(os.environ.get("APPDATA", "")) / "npm/claude.ps1",
]

LITELLM_CONFIG = Path(r"C:\daon\_harness-ab-test\litellm_config.yaml")
LITELLM_LOG = Path(r"C:\daon\_harness-ab-test\litellm.log")
LITELLM_PORT = 4000
LITELLM_MASTER_KEY = "sk-daon-harness-worker"

# 워커 작업장 루트 - 임시 격리 디렉터리는 여기 아래에 만든다
WORKER_ROOT = Path(r"C:\daon\_worker_runs")

# 프로바이더별 임시 CODEX_HOME 을 모아 두는 곳 (매 실행 시 config.toml 재작성)
CODEX_HOME_ROOT = WORKER_ROOT / "_codex_homes"

# 출력 정제에서 버릴 잡음 접두어
NOISE_PREFIXES = (
    "warning: Model metadata",
    "warning: Skill descriptions",
    "Reading additional input from stdin",
    "Warning: no stdin data received",
)

# Claude Code 가 카탈로그에 없는 alias 를 볼 때 stderr 로 뱉는 잡음.
# 응답 뒤에 붙어 나오므로 이 마커가 나오면 그 뒤는 전부 버린다.
CLAUDE_NOISE_MARKERS = (
    "[claude-code:unrecognized_model]",
    "isn't described by this version's model catalog",
    "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1 restores",
)

TOKENS_RE = re.compile(r"^tokens used\s*$", re.IGNORECASE)
CODEX_HEADER_RE = re.compile(r"^-{3,}\s*$")

# 브라우저형 UA - opencode-go 가 Cloudflare(1010) 로 막는 것을 피한다
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 프로바이더 레지스트리  ← 단일 진실 공급원 (여기만 고치면 된다)
# ---------------------------------------------------------------------------
#  key_in_json : custom_providers.json 의 providers.<name>.api_key 에서 읽는다
#  codex_model : Codex 용 기본 모델명 (프로바이더 네이티브 표기)
#  claude_alias: Claude Code 용 LiteLLM alias (claude- 접두어 필수)
#  headers     : 요청에 반드시 붙여야 하는 헤더

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "codex_model": "deepseek/deepseek-v4.1-flash",
        "claude_alias": "claude-openrouter",
        "headers": {},
    },
    "opencode-go": {
        "label": "opencode-go (zen/go)",
        "base_url": "https://opencode.ai/zen/go/v1",
        "codex_model": "deepseek-v4.1-flash",
        "claude_alias": "claude-oc",
        # 없으면 403(Cloudflare 1010) / 400(MissingSessionID)
        "headers": {"x-opencode-session": "daon-harness-worker"},
    },
    "qwen-token-plan": {
        "label": "Qwen Token Plan",
        "base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "codex_model": "qwen3.8-max",
        "claude_alias": "claude-qwen",
        "headers": {},
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.io/v1",
        "codex_model": "MiniMax-M3",
        "claude_alias": "claude-minimax",
        "headers": {},
    },
    "omniroute": {
        "label": "omniroute (로컬)",
        "base_url": "http://localhost:20128/v1",
        "codex_model": "auto/best-coding",
        "claude_alias": "claude-omni",
        # ★ Claude Code 부적합 (2026-09-20 실측):
        #   auto/* 라우팅 모델은 Claude 의 tool-use 프로토콜을 못 맞춰 154초 뒤
        #   엉뚱한 JSON 을 뱉고, 명시 모델(nvidia/... 등)은 업스트림이 죽어 있다
        #   (410 Gone). 로컬 라우터라 상태 변동이 심해 Claude 워커로는 부적합.
        #   → Claude 에서는 alias 를 만들지 않고 명확히 거부한다. Codex 전용.
        "claude_supported": False,
        "claude_note": (
            "omniroute 는 업스트림 변동이 심해(410/500/502/503) Claude Code 에 "
            "부적합합니다. Codex 전용으로 쓰세요."
        ),
        "headers": {},
    },
}

DEFAULT_PROVIDER = "openrouter"


def resolve_provider(name: Optional[str]) -> str:
    """프로바이더 이름을 정규화한다. 없거나 미지원이면 기본값."""
    key = str(name or "").strip().lower()
    if not key:
        return DEFAULT_PROVIDER
    # 별칭 흡수
    alias = {
        "oc": "opencode-go", "opencode": "opencode-go", "opencode_go": "opencode-go",
        "qwen": "qwen-token-plan", "qwen-token": "qwen-token-plan",
        "mini": "minimax", "mm": "minimax",
        "omni": "omniroute", "or": "openrouter",
    }
    key = alias.get(key, key)
    return key if key in PROVIDERS else DEFAULT_PROVIDER


def provider_spec(provider: Optional[str]) -> Dict[str, Any]:
    return PROVIDERS[resolve_provider(provider)]


# ---------------------------------------------------------------------------
# 자격증명 / 바이너리 탐색
# ---------------------------------------------------------------------------

def _load_providers_json(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else PROVIDER_JSON
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("providers") or {}


def read_provider_key(provider: str = DEFAULT_PROVIDER,
                      path: Optional[Path] = None) -> str:
    """custom_providers.json 에서 해당 프로바이더의 api_key 를 읽는다."""
    entry = _load_providers_json(path).get(resolve_provider(provider)) or {}
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("api_key") or entry.get("access_token") or "").strip()


def read_openrouter_key(path: Optional[Path] = None) -> str:
    """하위 호환 - 기존 호출부가 남아 있어도 동작해야 한다."""
    return read_provider_key("openrouter", path)


def read_provider_models(provider: str, path: Optional[Path] = None) -> List[str]:
    """해당 프로바이더가 custom_providers.json 에 등록한 모델 ID 목록."""
    entry = _load_providers_json(path).get(resolve_provider(provider)) or {}
    out: List[str] = []
    for m in (entry.get("models") or []) if isinstance(entry, dict) else []:
        if isinstance(m, dict):
            mid = m.get("id") or m.get("name")
        else:
            mid = m
        if mid:
            out.append(str(mid))
    return out


def available_providers() -> List[Dict[str, Any]]:
    """키가 있는 프로바이더만 골라 메타와 함께 돌려준다."""
    out: List[Dict[str, Any]] = []
    for name, spec in PROVIDERS.items():
        key = read_provider_key(name)
        out.append({
            "provider": name,
            "label": spec["label"],
            "has_key": bool(key),
            "base_url": spec["base_url"],
            "codex_model": spec["codex_model"],
            "claude_alias": spec["claude_alias"],
            "claude_supported": claude_supports(name),
            "claude_note": spec.get("claude_note", ""),
            "models": read_provider_models(name),
        })
    return out


def find_binary(harness: str, explicit: Optional[str] = None) -> Optional[str]:
    """하네스 실행 파일 경로를 찾는다. .cmd 래퍼도 후보에 포함."""
    if explicit:
        return explicit if Path(explicit).exists() else None

    if harness == "codex":
        for c in CODEX_EXE_CANDIDATES:
            if c and Path(c).exists():
                return str(c)
        found = shutil.which("codex")
        if found:
            return found
        candidates = CODEX_EXE_CANDIDATES
    elif harness in ("claude", "claude-code"):
        for c in CLAUDE_CMD_CANDIDATES:
            if c and Path(c).exists():
                return str(c)
        found = shutil.which("claude")
        if found:
            return found
        candidates = CLAUDE_CMD_CANDIDATES
    else:
        return None

    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


def _wrap_for_windows(exe: str) -> List[str]:
    """Return the argv prefix that can execute *exe* without shell=True.

    .cmd/.bat are not real executables - cmd.exe has to run them.
    """
    lower = exe.lower()
    if lower.endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", exe]
    return [exe]


# ---------------------------------------------------------------------------
# 작업 디렉터리 준비
# ---------------------------------------------------------------------------

def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _rmtree_retry(path: str, attempts: int = 4, delay: float = 0.6) -> bool:
    """Remove a directory tree, retrying past Windows file-lock lag.

    git/python 이 파일 핸들을 놓기 전에 호출하면 PermissionError 가 난다.
    ignore_errors 로 삼키면 "지웠다"고 거짓 보고하게 되므로 재시도 후
    실제 상태를 반환한다.
    """
    p = Path(path)
    for i in range(attempts):
        try:
            shutil.rmtree(p)
        except FileNotFoundError:
            return True
        except Exception:
            # 읽기전용 속성만 풀고 한 번 더 (Windows 대비)
            try:
                for root, dirs, files in os.walk(p):
                    for name in files:
                        try:
                            os.chmod(os.path.join(root, name), 0o777)
                        except OSError:
                            pass
                shutil.rmtree(p)
            except Exception:
                pass
        if not p.exists():
            return True
        time.sleep(delay)
    return not p.exists()


def _git(args: List[str], cwd: Path) -> tuple:
    try:
        p = subprocess.run(
            ["git"] + args, cwd=str(cwd), capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=60, stdin=subprocess.DEVNULL,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def prepare_workdir(workdir: Optional[str] = None, isolate: bool = False,
                    label: str = "") -> Dict[str, Any]:
    """워커가 쓸 git 저장소를 준비한다. Codex 는 repo 밖에서 실행을 거부한다.

    우선순위:
      workdir 지정 + isolate=False  -> 그 경로를 그대로 쓴다
      그 외                         -> WORKER_ROOT 아래 새 디렉터리
    """
    if workdir and not isolate:
        p = Path(workdir)
        if not p.is_absolute():
            p = Path.cwd() / p
        p.mkdir(parents=True, exist_ok=True)
        created_repo = False
        if not _is_git_repo(p):
            rc, _ = _git(["init", "-q"], p)
            created_repo = rc == 0
        return {"path": str(p), "created_repo": created_repo, "isolated": False}

    WORKER_ROOT.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^A-Za-z0-9_-]", "", label or "run") or "run"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    p = Path(tempfile.mkdtemp(prefix=f"{tag}_{stamp}_", dir=str(WORKER_ROOT)))
    rc, _ = _git(["init", "-q"], p)
    # 초기 커밋 - Codex 가 HEAD 없는 repo 를 싫어하는 경우가 있다
    (p / "README.md").write_text(
        "# harness-worker scratch\n", encoding="utf-8"
    )
    _git(["add", "-A"], p)
    _git(["-c", "user.email=worker@daon", "-c", "user.name=worker",
          "commit", "-qm", "init"], p)
    return {"path": str(p), "created_repo": rc == 0, "isolated": True}


# ---------------------------------------------------------------------------
# Codex: 프로바이더별 임시 CODEX_HOME
# ---------------------------------------------------------------------------

def write_codex_home(provider: str) -> Dict[str, Any]:
    """프로바이더 설정이 담긴 config.toml 을 임시 CODEX_HOME 에 쓴다.

    ★ 사용자의 실제 ~/.codex 는 건드리지 않는다.
      CODEX_HOME 환경변수로 이 디렉터리만 바라보게 한다.

    Codex 0.152+ 는 wire_api="chat" 을 폐지했으므로 "responses" 를 쓴다.
    (OpenRouter / Qwen / opencode-go / MiniMax / omniroute 모두 /responses 지원 실측)
    """
    name = resolve_provider(provider)
    spec = PROVIDERS[name]

    home = CODEX_HOME_ROOT / name
    home.mkdir(parents=True, exist_ok=True)

    env_key_name = f"DAON_HARNESS_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_KEY"

    lines = [
        f'model = "{spec["codex_model"]}"',
        f'model_provider = "{name}"',
        "model_context_window = 128000",
        "",
        f"[model_providers.{name}]",
        f'name = "{spec["label"]}"',
        f'base_url = "{spec["base_url"]}"',
        f'env_key = "{env_key_name}"',
        'wire_api = "responses"',
        "request_max_retries = 2",
        "stream_idle_timeout_ms = 60000",
    ]
    headers = spec.get("headers") or {}
    if headers:
        # TOML 인라인 테이블 - opencode-go 는 이게 없으면 403/400 으로 죽는다
        hdr = ", ".join(f'"{k}" = "{v}"' for k, v in headers.items())
        lines.append(f"http_headers = {{ {hdr} }}")

    # MCP 서버 연동 (Context7: 최신 문서, Serena: 시맨틱 심볼 리팩터링)
    lines += [
        "",
        "[mcp_servers.context7]",
        'command = "npx"',
        'args = ["-y", "@upstash/context7-mcp"]',
        "",
        "[mcp_servers.serena]",
        'command = "uvx"',
        'args = ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server", "--project-from-cwd"]',
    ]

    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "home": str(home),
        "config": str(home / "config.toml"),
        "env_key_name": env_key_name,
        "codex_model": spec["codex_model"],
        "base_url": spec["base_url"],
    }


# ---------------------------------------------------------------------------
# Claude: LiteLLM 설정 생성 (프로바이더별 alias 를 한 파일에 모두 정의)
# ---------------------------------------------------------------------------

def write_litellm_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """모든 프로바이더를 claude-* alias 로 노출하는 게이트웨이 설정을 만든다.

    이렇게 하면 프로바이더를 바꿀 때마다 게이트웨이를 재기동할 필요가 없다.
    Claude Code 쪽에서 `--model claude-qwen` 처럼 고르면 된다.

    Claude Code 는 카탈로그 검사 때문에 claude- 접두어만 통과시킨다.
    """
    out_path = Path(path) if path else LITELLM_CONFIG
    out_path.parent.mkdir(parents=True, exist_ok=True)

    entries: List[str] = []
    alias_map: Dict[str, Dict[str, Any]] = {}

    for name, spec in PROVIDERS.items():
        # Claude Code 에 부적합한 프로바이더는 alias 를 만들지 않는다
        if not claude_supports(name):
            continue
        env_var = f"DAON_HARNESS_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_KEY"
        alias = spec["claude_alias"]
        # claude_model 이 따로 있으면 그걸 쓴다 (auto/* 라우팅 모델 회피용)
        model = spec.get("claude_model") or spec["codex_model"]

        block = [
            f"  - model_name: {alias}",
            "    litellm_params:",
            f"      model: openai/{model}",
            f"      api_base: {spec['base_url']}",
            f"      api_key: os.environ/{env_var}",
        ]
        headers = spec.get("headers") or {}
        if headers:
            block.append("      extra_headers:")
            for k, v in headers.items():
                block.append(f"        {k}: {v}")
        entries.append("\n".join(block))

        alias_map[alias] = {
            "provider": name,
            "model": model,
            "base_url": spec["base_url"],
            "env_var": env_var,
        }

    # Claude Code 기본 슬롯(claude-opus-5 등)은 기본 프로바이더를 가리키게 한다
    default_alias = PROVIDERS[DEFAULT_PROVIDER]["claude_alias"]
    for slot in ("claude-opus-5", "claude-sonnet-4-6", "claude-sonnet-4-5",
                 "claude-haiku-4-5"):
        entries.append(
            f"  - model_name: {slot}\n"
            f"    litellm_params:\n"
            f"      model: openai/{PROVIDERS[DEFAULT_PROVIDER]['codex_model']}\n"
            f"      api_base: {PROVIDERS[DEFAULT_PROVIDER]['base_url']}\n"
            f"      api_key: os.environ/"
            f"{alias_map[default_alias]['env_var']}"
        )

    body = (
        "# LiteLLM gateway - DAON harness-worker\n"
        "# 자동 생성 파일. 직접 편집하지 말 것 (worker.write_litellm_config 가 덮어쓴다).\n"
        "# 모든 프로바이더를 claude-* alias 로 노출한다.\n"
        "# Claude Code: claude --model claude-qwen / claude-oc / claude-minimax ...\n"
        "\n"
        "model_list:\n" + "\n\n".join(entries) + "\n"
        "\n"
        "litellm_settings:\n"
        "  drop_params: true\n"
        "  set_verbose: false\n"
        "\n"
        "general_settings:\n"
        f"  master_key: {LITELLM_MASTER_KEY}\n"
    )
    out_path.write_text(body, encoding="utf-8")

    return {"config": str(out_path), "aliases": alias_map}


def claude_supports(provider: str) -> bool:
    """이 프로바이더를 Claude Code 워커로 쓸 수 있는가."""
    spec = PROVIDERS[resolve_provider(provider)]
    return spec.get("claude_supported", True) is not False


def claude_alias_for(provider: str) -> str:
    return PROVIDERS[resolve_provider(provider)]["claude_alias"]


# ---------------------------------------------------------------------------
# 명령 구성 / 환경
# ---------------------------------------------------------------------------

def build_command(harness: str, prompt: str, exe: str,
                  full_auto: bool = True, model: Optional[str] = None,
                  provider: Optional[str] = None) -> List[str]:
    argv = _wrap_for_windows(exe)

    if harness == "codex":
        argv += ["exec"]
        if full_auto:
            # Codex 0.152 dropped --full-auto (measured exit=2); a managed
            # permission_profile also overrides config.toml's sandbox_mode, so
            # -s workspace-write still fell back to read-only (2 live tests).
            # Since stdin is DEVNULL there is no approval path: bypass it.
            argv += [
                "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check",
            ]
        if model:
            argv += ["-m", model]
        # Windows PowerShell heredoc / 32KB argv 한계 우회 지침 주입
        windows_directive = (
            "[SYSTEM DIRECTIVE FOR WINDOWS POWERSHELL]:\n"
            "- You are running autonomously in Windows PowerShell without human interaction.\n"
            "- The Windows command-line buffer has length limits. Do NOT execute massive inline command lines (>8KB) or bash-style multiline heredocs.\n"
            "- When creating or editing files, write files directly using Python (`python -c \"...\"`) or standard PowerShell cmdlets (`Set-Content`, `Out-File` with UTF-8).\n"
            "- Work autonomously and complete the entire task until fully verified.\n\n"
        )
        argv.append(windows_directive + prompt)
        return argv

    # claude / claude-code - 모델은 LiteLLM alias 를 쓴다
    flags: List[str] = []
    if full_auto:
        # -p print mode + stdin=DEVNULL: 승인 프롬프트로 멈추지 않도록 권한 완전 우회
        flags += [
            "--dangerously-skip-permissions",
            "--permission-mode", "bypassPermissions",
        ]
    if model:
        flags += ["--model", model]

    # Claude CLI 규격: claude [options] [command] [prompt]
    # -p 는 print 플래그이며, prompt 는 맨 마지막 위치 인자로 와야 플래그가 정상 적용됨
    return argv + flags + ["-p", prompt]


def build_env(harness: str, key: str, base_env: Optional[Dict[str, str]] = None,
              provider: Optional[str] = None,
              codex_home: Optional[str] = None,
              model: Optional[str] = None) -> Dict[str, str]:
    """워커 프로세스용 환경. 키와 인코딩을 여기서 확정한다."""
    env = dict(base_env if base_env is not None else os.environ)
    name = resolve_provider(provider)
    spec = PROVIDERS[name]

    # 인코딩 - Windows cp949 사고 방지
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    if harness == "codex":
        # 프로바이더별 config.toml 을 담은 임시 홈으로 고정한다.
        # 이게 없으면 ~/.codex/config.toml(전역)이 이겨서 프로바이더 전환이 안 된다.
        if codex_home:
            env["CODEX_HOME"] = codex_home
        env_key_name = f"DAON_HARNESS_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_KEY"
        env[env_key_name] = key
        # 하위 호환: 예전 설정이 OPENROUTER_API_KEY 를 참조할 수 있다
        if name == "openrouter":
            env["OPENROUTER_API_KEY"] = key
    else:
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{LITELLM_PORT}"
        env["ANTHROPIC_AUTH_TOKEN"] = LITELLM_MASTER_KEY
        env["ANTHROPIC_MODEL"] = model or claude_alias_for(name)
        # 세션 제목 생성 슬롯은 카탈로그에 실재하는 이름을 쓴다.
        # (claude-qwen 같은 커스텀 alias 를 넣으면 Claude Code 가
        #  "isn't described by this version's model catalog" 경고를 쏟아낸다)
        env["ANTHROPIC_SMALL_FAST_MODEL"] = "claude-haiku-4-5"
        # 카탈로그에 없는 모델명을 써도 창 크기 강제 대기로 멈추지 않게 한다
        env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    return env


# ---------------------------------------------------------------------------
# 출력 정제
# ---------------------------------------------------------------------------

def clean_output(harness: str, text: str) -> str:
    """warning 잡음과 메타 정보를 걷어내고 실제 응답 본문만 남긴다."""
    if not text:
        return ""

    lines = text.replace("\r\n", "\n").split("\n")

    # Claude Code 카탈로그 경고는 응답 뒤에 붙으므로 나온 지점부터 끝을 버린다
    for i, ln in enumerate(lines):
        if any(m in ln for m in CLAUDE_NOISE_MARKERS):
            lines = lines[:i]
            break

    if harness == "codex":
        # "codex" 단독 줄 이후가 응답, "tokens used" 앞까지
        start = None
        for i, ln in enumerate(lines):
            if ln.strip() == "codex":
                start = i + 1
        end = len(lines)
        for i, ln in enumerate(lines):
            if TOKENS_RE.match(ln.strip()):
                end = i
                break
        if start is not None and start <= end:
            lines = lines[start:end]
        else:
            # 구조를 못 찾으면 앞쪽 헤더 블록만 제거
            lines = [ln for ln in lines if not CODEX_HEADER_RE.match(ln.strip())]

    cleaned: List[str] = []
    for ln in lines:
        s = ln.strip()
        if any(s.startswith(p) for p in NOISE_PREFIXES):
            continue
        cleaned.append(ln)

    out = "\n".join(cleaned).strip()
    # 양 끝의 빈 줄 정리
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def run_worker(
    harness: str,
    prompt: str,
    workdir: Optional[str] = None,
    isolate: bool = False,
    model: Optional[str] = None,
    timeout: int = 300,
    full_auto: bool = True,
    keep: bool = False,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """워커를 한 번 실행하고 정제된 결과를 돌려준다.

    provider: openrouter | opencode-go | qwen-token-plan | minimax | omniroute
              (별칭 oc / qwen / mm / omni 도 허용)

    반환 dict:
      ok, harness, provider, model, exit_code, elapsed, workdir, output,
      raw_tail, error, command
    """
    harness = "codex" if harness == "codex" else "claude"
    prov = resolve_provider(provider)

    result: Dict[str, Any] = {
        "ok": False, "harness": harness, "provider": prov,
        "exit_code": None, "elapsed": 0.0,
        "workdir": None, "output": "", "raw_tail": "", "error": "", "command": "",
    }

    exe = find_binary(harness)
    if not exe:
        result["error"] = (
            f"{harness} 실행 파일을 찾지 못했습니다. "
            + ("winget install -e --id OpenAI.Codex" if harness == "codex"
               else "npm install -g @anthropic-ai/claude-code")
        )
        return result
    result["exe"] = exe

    # 자격증명 - 명시 키 > 해당 프로바이더 키
    key = api_key if api_key is not None else read_provider_key(prov)
    if not key:
        result["error"] = (
            f"[{prov}] API 키를 읽지 못했습니다 "
            f"(custom_providers.json 의 providers.{prov}.api_key). "
            f"사용 가능: {', '.join(p['provider'] for p in available_providers() if p['has_key'])}"
        )
        return result

    # Codex 는 프로바이더별 임시 홈을 쓴다 (~/.codex 는 보존)
    codex_home = None
    if harness == "codex":
        try:
            home = write_codex_home(prov)
            codex_home = home["home"]
            result["codex_home"] = home["config"]
            if not model:
                model = home["codex_model"]
        except Exception as exc:
            result["error"] = f"Codex 홈(config.toml) 생성 실패: {exc}"
            return result
    else:
        # Claude Code 는 카탈로그/프로토콜 제약이 있어 부적합한 프로바이더가 있다
        if not claude_supports(prov):
            spec = PROVIDERS[prov]
            result["error"] = (
                f"[{prov}] 은(는) Claude Code 워커로 쓸 수 없습니다. "
                + str(spec.get("claude_note") or "")
                + " 사용 가능: "
                + ", ".join(n for n in PROVIDERS
                            if claude_supports(n)
                            and read_provider_key(n))
            )
            return result
        # 게이트웨이가 떠 있어야 한다 (모든 alias 가 들어 있는 설정 보장)
        try:
            write_litellm_config()
            st = gateway_status()
            if not st.get("running"):
                gateway_up()
        except Exception:
            pass
        if not model:
            model = claude_alias_for(prov)

    result["model"] = model

    wd = prepare_workdir(workdir=workdir, isolate=isolate, label=harness)
    result["workdir"] = wd["path"]
    result["isolated"] = wd["isolated"]

    argv = build_command(harness, prompt, exe, full_auto=full_auto,
                         model=model, provider=prov)
    env = build_env(harness, key, provider=prov, codex_home=codex_home,
                    model=model)
    result["command"] = " ".join(argv[:3]) + (" ... " if len(argv) > 3 else "")

    t0 = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=wd["path"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # ★ 핵심: 이걸 빼면 두 CLI 가 입력을 기다리며 멈춘다
            stdin=subprocess.DEVNULL,
        )
        result["elapsed"] = round(time.time() - t0, 2)
        result["exit_code"] = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        raw = stdout + stderr
        result["raw_tail"] = "\n".join(raw.strip().split("\n")[-12:])

        # 응답은 stdout 으로, 경고/로그는 stderr 로 나온다.
        # 합쳐서 정제하면 경고가 본문에 섞이므로 stdout 을 우선한다
        # (stdout 이 비어 있을 때만 stderr 로 폴백).
        primary = stdout if stdout.strip() else stderr
        out = clean_output(harness, primary)
        result["output"] = out
        result["ok"] = proc.returncode == 0 and bool(out)

        if not result["ok"] and not result["error"]:
            result["error"] = (
                f"exit={proc.returncode}. 출력이 비었거나 실패했습니다. "
                "raw_tail 을 확인하세요."
            )
    except subprocess.TimeoutExpired:
        result["elapsed"] = round(time.time() - t0, 2)
        result["error"] = f"타임아웃 ({timeout}초). 워커가 응답하지 않았습니다."
    except Exception as exc:
        result["elapsed"] = round(time.time() - t0, 2)
        result["error"] = f"실행 실패: {exc}"
    finally:
        if wd["isolated"] and not keep:
            # 실제로 사라졌는지로 판정한다 (거짓 보고 금지)
            result["cleaned"] = _rmtree_retry(wd["path"])
            if not result["cleaned"]:
                result["cleanup_warning"] = (
                    "격리 디렉터리를 지우지 못했습니다(파일 잠금). "
                    f"수동 삭제: {wd['path']}"
                )

    return result


# ---------------------------------------------------------------------------
# 백그라운드 작업 관리 (Job Registry)
# ---------------------------------------------------------------------------

JOBS_FILE = WORKER_ROOT / "worker_jobs.json"
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_ACTIVE_PROCS: Dict[str, subprocess.Popen] = {}
_ACTIVE_PROCS_LOCK = threading.Lock()


def _ensure_jobs_loaded() -> None:
    global _JOBS
    if _JOBS:
        return
    if JOBS_FILE.exists():
        try:
            with open(JOBS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _JOBS = data
        except Exception:
            pass


def _persist_jobs() -> None:
    try:
        WORKER_ROOT.mkdir(parents=True, exist_ok=True)
        with _JOBS_LOCK:
            safe_jobs = {}
            for jid, item in list(_JOBS.items())[-50:]:
                safe_jobs[jid] = {k: v for k, v in item.items() if k != "proc"}
            with open(JOBS_FILE, "w", encoding="utf-8") as f:
                json.dump(safe_jobs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """특정 job_id의 최신 상태를 반환한다."""
    _ensure_jobs_loaded()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job:
            return dict(job)
    return None


def list_jobs(session_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """작업 목록을 최신 순으로 정렬하여 반환한다."""
    _ensure_jobs_loaded()
    with _JOBS_LOCK:
        all_jobs = list(_JOBS.values())
        if session_id:
            all_jobs = [j for j in all_jobs if j.get("session_id") == session_id]
        all_jobs.sort(key=lambda x: x.get("started_at", 0), reverse=True)
        return [dict(j) for j in all_jobs[:limit]]


def kill_job(job_id: str) -> Dict[str, Any]:
    """실행 중인 워커 프로세스를 종료한다."""
    _ensure_jobs_loaded()
    with _ACTIVE_PROCS_LOCK:
        proc = _ACTIVE_PROCS.get(job_id)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return {"ok": False, "error": f"Job not found: {job_id}"}
        if job.get("status") in ("completed", "failed", "killed", "timeout"):
            return {"ok": True, "message": f"Job {job_id} is already {job.get('status')}.", "job": dict(job)}

    if proc:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                )
            else:
                proc.kill()
        except Exception:
            pass

    with _JOBS_LOCK:
        if job:
            job["status"] = "killed"
            job["finished_at"] = time.time()
            job["elapsed"] = round(job["finished_at"] - job.get("started_at", job["finished_at"]), 2)
            job["error"] = "Job was killed by user request."
    _persist_jobs()
    return {"ok": True, "message": f"Job {job_id} was killed.", "job": get_job(job_id)}


def _send_windows_toast(title: str, message: str, status: str = "completed") -> None:
    """윈도우 우측 하단 바탕화면 토스트 알림 및 알림음 재생."""
    if sys.platform != "win32":
        return

    def _toast_worker():
        # 1. 윈도우 기본 알림음 재생
        try:
            import winsound
            snd = winsound.MB_ICONASTERISK if status == "completed" else winsound.MB_ICONHAND
            winsound.MessageBeep(snd)
        except Exception:
            pass

        # 2. Windows 10/11 WinRT 바탕화면 토스트 배너
        try:
            clean_title = title.replace('"', '""').replace("'", "''")
            clean_msg = message.replace('"', '""').replace("'", "''")
            ps_code = f"""
            try {{
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
                [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
                $xml = '<toast><visual><binding template="ToastGeneric"><text>{clean_title}</text><text>{clean_msg}</text></binding></visual><audio src="ms-winsoundevent:Notification.Default"/></toast>'
                $toastXml = [Windows.Data.Xml.Dom.XmlDocument]::new()
                $toastXml.LoadXml($xml)
                $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
                $appId = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
            }} catch {{ }}
            """
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_code],
                capture_output=True,
                timeout=5,
                creationflags=creationflags,
                stdin=subprocess.DEVNULL
            )
        except Exception:
            pass

    t = threading.Thread(target=_toast_worker, daemon=True)
    t.start()


def start_background_job(
    harness: str,
    prompt: str,
    workdir: Optional[str] = None,
    isolate: bool = False,
    model: Optional[str] = None,
    timeout: int = 600,
    full_auto: bool = True,
    keep: bool = False,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """워커를 독립된 백그라운드 스레드 및 프로세스로 실행하고, 즉시 job_id 를 반환한다."""
    _ensure_jobs_loaded()
    harness = "codex" if harness == "codex" else "claude"
    prov = resolve_provider(provider)

    exe = find_binary(harness)
    if not exe:
        return {
            "ok": False,
            "error": f"{harness} 실행 파일을 찾지 못했습니다.",
            "harness": harness,
        }

    key = api_key if api_key is not None else read_provider_key(prov)
    if not key:
        return {
            "ok": False,
            "error": f"[{prov}] 프로바이더 API 키가 없습니다.",
            "harness": harness,
        }

    codex_home: Optional[str] = None
    if harness == "codex":
        try:
            home = write_codex_home(prov)
            codex_home = home["home"]
            if not model:
                model = home["codex_model"]
        except Exception as exc:
            return {"ok": False, "error": f"Codex 홈 생성 실패: {exc}", "harness": harness}
    else:
        if not claude_supports(prov):
            return {
                "ok": False,
                "error": f"[{prov}] 은(는) Claude Code 워커로 쓸 수 없습니다.",
                "harness": harness,
            }
        try:
            write_litellm_config()
            st = gateway_status()
            if not st.get("running"):
                gateway_up()
        except Exception:
            pass
        if not model:
            model = claude_alias_for(prov)

    wd = prepare_workdir(workdir=workdir, isolate=isolate, label=harness)
    argv = build_command(harness, prompt, exe, full_auto=full_auto,
                         model=model, provider=prov)
    env = build_env(harness, key, provider=prov, codex_home=codex_home,
                    model=model)

    job_id = f"job_{harness}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now = time.time()

    job_info: Dict[str, Any] = {
        "job_id": job_id,
        "harness": harness,
        "provider": prov,
        "model": model,
        "workdir": wd["path"],
        "isolated": wd["isolated"],
        "prompt": prompt,
        "command": " ".join(argv[:3]) + (" ... " if len(argv) > 3 else ""),
        "status": "running",
        "started_at": now,
        "finished_at": None,
        "elapsed": 0.0,
        "pid": None,
        "exit_code": None,
        "output": "",
        "raw_tail": "",
        "error": "",
        "session_id": session_id or "",
    }

    with _JOBS_LOCK:
        _JOBS[job_id] = job_info
    _persist_jobs()

    def _worker_thread_func():
        t0 = time.time()
        proc = None
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            proc = subprocess.Popen(
                argv,
                cwd=wd["path"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )

            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS[job_id] = proc
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["pid"] = proc.pid
            _persist_jobs()

            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
            elapsed = round(time.time() - t0, 2)

            raw = (stdout or "") + (stderr or "")
            primary = stdout if stdout and stdout.strip() else (stderr or "")
            out = clean_output(harness, primary)
            ok = exit_code == 0 and bool(out)

            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["status"] = "completed" if ok else "failed"
                    _JOBS[job_id]["exit_code"] = exit_code
                    _JOBS[job_id]["elapsed"] = elapsed
                    _JOBS[job_id]["finished_at"] = time.time()
                    _JOBS[job_id]["output"] = out
                    _JOBS[job_id]["raw_tail"] = "\n".join(raw.strip().split("\n")[-12:])
                    if not ok:
                        _JOBS[job_id]["error"] = f"exit={exit_code}. 출력이 비었거나 실패했습니다."

        except subprocess.TimeoutExpired:
            elapsed = round(time.time() - t0, 2)
            if proc:
                try:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
                    else:
                        proc.kill()
                except Exception:
                    pass
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["status"] = "timeout"
                    _JOBS[job_id]["elapsed"] = elapsed
                    _JOBS[job_id]["finished_at"] = time.time()
                    _JOBS[job_id]["error"] = f"타임아웃 ({timeout}초). 워커가 응답하지 않았습니다."

        except Exception as exc:
            elapsed = round(time.time() - t0, 2)
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["status"] = "failed"
                    _JOBS[job_id]["elapsed"] = elapsed
                    _JOBS[job_id]["finished_at"] = time.time()
                    _JOBS[job_id]["error"] = f"실행 중 예외: {exc}"

        finally:
            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS.pop(job_id, None)

            if wd["isolated"] and not keep:
                _rmtree_retry(wd["path"])

            _persist_jobs()

            final_st = _JOBS.get(job_id, {})
            st_kor = "완료" if final_st.get("status") == "completed" else "실패"
            icon = "✅" if final_st.get("status") == "completed" else "❌"
            msg = (
                f"{icon} [워커 {st_kor}] {harness.upper()} 작업이 {st_kor}되었습니다. "
                f"(소요: {final_st.get('elapsed')}s, 작업경로: {wd['path']})"
            )

            # 1. 윈도우 바탕화면 알림 (소리 + 우측 하단 배너)
            _send_windows_toast(
                title=f"DAON 워커 {st_kor} ({harness.upper()})",
                message=f"소요 시간: {final_st.get('elapsed')}초\n경로: {wd['path']}",
                status=final_st.get("status", "completed"),
            )

            # 2. SSE 알림 전송 (연결이 활성 상태일 때)
            if session_id:
                try:
                    from api.config import get_stream_queue
                    q = get_stream_queue(session_id)
                    if q:
                        q.put_nowait(('notice', {'message': msg, 'job_id': job_id, 'status': final_st.get('status')}))
                except Exception:
                    pass

    worker_thread = threading.Thread(
        target=_worker_thread_func,
        name=f"daon-worker-{job_id}",
        daemon=True,
    )
    worker_thread.start()

    return {
        "ok": True,
        "status": "running",
        "job_id": job_id,
        "harness": harness,
        "provider": prov,
        "model": model,
        "workdir": wd["path"],
        "isolated": wd["isolated"],
        "message": (
            f"{harness.upper()} 워커가 백엔드에서 비동기로 시작되었습니다 (Job ID: {job_id}). "
            f"라온이는 멈추지 않고 사용자에게 작업 시작을 알리고 다른 대화를 계속할 수 있습니다. "
            f"진행 상태 확인은 worker_job(action='status', job_id='{job_id}') 도구를 사용하세요."
        ),
    }


# ---------------------------------------------------------------------------
# LiteLLM 게이트웨이 관리
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 4.0) -> Optional[int]:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def _find_listener_pid(port: int = LITELLM_PORT) -> Optional[int]:
    """포트를 LISTEN 중인 PID 를 netstat 로 찾는다."""
    try:
        p = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    for line in (p.stdout or "").split("\n"):
        if f":{port}" in line and "LISTENING" in line.upper():
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def gateway_status() -> Dict[str, Any]:
    code = _http_get(f"http://127.0.0.1:{LITELLM_PORT}/health/liveliness")
    pid = _find_listener_pid()
    return {
        "running": code == 200,
        "health_code": code,
        "pid": pid,
        "port": LITELLM_PORT,
        "config": str(LITELLM_CONFIG),
        "config_exists": LITELLM_CONFIG.exists(),
        # 실제 yaml 에 만들어지는 alias 만 (Claude 부적합 프로바이더는 제외)
        "provider_aliases": {p["claude_alias"]: n for n, p in PROVIDERS.items()
                             if claude_supports(n)},
    }


def _provider_env() -> Dict[str, str]:
    """게이트웨이가 참조하는 모든 프로바이더 키를 환경변수로 올린다."""
    out: Dict[str, str] = {}
    for name in PROVIDERS:
        key = read_provider_key(name)
        env_var = f"DAON_HARNESS_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_KEY"
        if key:
            out[env_var] = key
    # 하위 호환
    or_key = read_provider_key("openrouter")
    if or_key:
        out["OPENROUTER_API_KEY"] = or_key
    return out


def gateway_up(wait_seconds: int = 45) -> Dict[str, Any]:
    """LiteLLM 을 백그라운드로 띄운다 (Claude Code 계열에 필요).

    모든 프로바이더를 alias 로 노출하므로, 여기서 한 번만 띄우면
    프로바이더를 바꿔도 재기동할 필요가 없다.
    """
    st = gateway_status()
    if st["running"]:
        return {"ok": True, "already_running": True, **st}

    # 설정을 항상 최신으로 재생성한다 (프로바이더 추가/변경 반영)
    gen = write_litellm_config()

    keys = _provider_env()
    if not keys:
        return {"ok": False, "error": "어떤 프로바이더 키도 읽지 못했습니다 (custom_providers.json)."}

    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(keys)

    litellm = shutil.which("litellm")
    if not litellm:
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python312/Scripts/litellm.exe"
        if cand.exists():
            litellm = str(cand)
    if not litellm:
        return {"ok": False, "error": "litellm 실행 파일을 찾지 못했습니다 (pip install 'litellm[proxy]')."}

    try:
        LITELLM_LOG.parent.mkdir(parents=True, exist_ok=True)
        # ★ 이 프로세스는 '독립'이어야 한다.
        #   Popen(creationflags=DETACHED_PROCESS) 만으로는 부모가 Job Object 에
        #   묶여 있을 때(에이전트 터미널 등) 부모 종료와 함께 회수된다.
        #   실측: 게이트웨이가 뜨고 요청까지 처리한 뒤 부모가 끝나자 함께 사라짐.
        #   → 검증된 우회책인 PowerShell Start-Process 로 띄운다.
        #     (커넥터 daon_connector_hidden.vbs 와 같은 계열의 해법)
        log_out = str(LITELLM_LOG)
        log_err = str(LITELLM_LOG.with_suffix(".err.log"))
        ps_cmd = (
            f"$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; "
            f"Start-Process -FilePath '{litellm}' "
            f"-ArgumentList '--config','{LITELLM_CONFIG}','--port','{LITELLM_PORT}',"
            f"'--host','127.0.0.1' "
            f"-WindowStyle Hidden "
            f"-RedirectStandardOutput '{log_out}' "
            f"-RedirectStandardError '{log_err}'"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"ok": False, "error": f"기동 실패: {exc}"}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(2)
        if _http_get(f"http://127.0.0.1:{LITELLM_PORT}/health/liveliness") == 200:
            return {"ok": True, "launched_pid": proc.pid,
                    "config_written": gen["config"],
                    "loaded_keys": sorted(keys.keys()),
                    **gateway_status()}

    return {"ok": False, "error": f"{wait_seconds}초 안에 뜨지 않았습니다. 로그: {LITELLM_LOG}",
            "launched_pid": proc.pid}


def gateway_down() -> Dict[str, Any]:
    pid = _find_listener_pid()
    if not pid:
        return {"ok": True, "already_stopped": True}
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True, timeout=20,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "pid": pid}
    time.sleep(2)
    alive = _find_listener_pid()
    return {"ok": alive is None, "killed_pid": pid, "still_alive": alive}


# ---------------------------------------------------------------------------
# 상태 조회
# ---------------------------------------------------------------------------

def worker_status() -> Dict[str, Any]:
    gw = gateway_status()
    codex_ok = find_binary("codex")
    claude_ok = find_binary("claude")
    provs = available_providers()

    runs: List[str] = []
    if WORKER_ROOT.exists():
        runs = sorted([p.name for p in WORKER_ROOT.iterdir()
                       if p.is_dir() and not p.name.startswith("_")])[-10:]

    return {
        "default_provider": DEFAULT_PROVIDER,
        "providers": provs,
        "usable_providers": [p["provider"] for p in provs if p["has_key"]],
        # 하위 호환 - 예전 소비자가 이 키를 본다
        "openrouter_key": bool(read_provider_key("openrouter")),
        "codex": {"available": bool(codex_ok), "path": codex_ok,
                  "needs_gateway": False,
                  "note": "provider 인자로 프로바이더 선택 (임시 CODEX_HOME 사용)",
                  "providers": [p["provider"] for p in provs if p["has_key"]]},
        "claude": {"available": bool(claude_ok), "path": claude_ok,
                   "needs_gateway": True,
                   "note": "게이트웨이가 모든 프로바이더 alias 제공 → --model claude-qwen 등",
                   "providers": [p["provider"] for p in provs
                                 if p["has_key"] and p["claude_supported"]],
                   "unsupported": [p["provider"] for p in provs
                                   if p["has_key"] and not p["claude_supported"]]},
        "gateway": gw,
        "worker_root": str(WORKER_ROOT),
        "recent_runs": runs,
    }
