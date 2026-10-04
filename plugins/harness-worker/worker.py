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

# ChatGPT 구독(OAuth) 자격증명 - `codex login` 이 만드는 파일.
# 구독 프로바이더는 이 파일을 임시 CODEX_HOME 으로 복사해 인증을 물려준다.
# ★ 읽기만 한다. 전역 ~/.codex 는 절대 수정하지 않는다.
CODEX_AUTH_JSON = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".codex" / "auth.json"

# Codex CLI 탐색 순서 — ★순서가 중요하다. [2026-09-25 실측]
#  1) npm @openai/codex 의 **네이티브 .exe** 를 최우선.
#     · 0.156.1+ 카탈로그에 gpt-6-luna 가 있다 (WinGet 0.152.0 에는 없음)
#     · .cmd shim / codex.js 래퍼를 쓰면 cmd.exe 가 인자를 재파싱해
#       프롬프트 본문(줄바꿈·따옴표)이 유실된다
#       (실측: 모델이 "SYSTEM DIRECTIVE 헤더는 보이는데 지시문 텍스트가 없다"고 답함)
#  2) .cmd shim — 위 .exe 가 없을 때만
#  3) WinGet 설치본 — ChatGPT 앱이 파일을 잠그고 있어 자체 업데이트 불가(0x80070020)
_NPM_CODEX_ROOT = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex"
_NPM_CODEX_NATIVE = (
    _NPM_CODEX_ROOT
    / "node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
)

CODEX_EXE_CANDIDATES = [
    _NPM_CODEX_NATIVE,
    Path(os.environ.get("APPDATA", "")) / "npm/codex.cmd",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft/WinGet/Packages/OpenAI.Codex_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "codex-x86_64-pc-windows-msvc.exe",
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
        # [2026-09-24 대표님 지시] Codex 기본 모델 = GLM 5.3 Flash
        "codex_model": "z-ai/glm-5.3-flash",
        # ★ context_length(모델 최대 스펙 1,310,720)가 아니라
        #   top_provider.context_length(실제 서빙 한계 1,048,576)를 쓴다.
        #   스펙값으로 잡으면 그만큼 채우다 컨텍스트 초과로 죽는다.
        "context_window": 1048576,
        # claude_model 을 따로 고정하지 않으면 codex_model 을 따라가 Claude Code
        # 워커까지 GLM 으로 끌려간다. Codex 만 교체가 지시사항이므로 분리한다.
        "claude_model": "deepseek/deepseek-v4.1-flash",
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
    # [2026-09-25 대표님 지시] 대표님 ChatGPT 계정 구독 경로.
    #   base_url/env_key 를 두지 않는다 = Codex CLI 기본(OpenAI/ChatGPT OAuth) 인증을 쓴다.
    #   임시 CODEX_HOME 에 ~/.codex/auth.json 을 복사해 넣어 구독을 그대로 물려준다.
    # 실측(2026-09-25, CLI 0.156.1 + 대표님 ChatGPT 계정):
    #   gpt-6-luna      → 정상 (기본값. 대표님 메인 모델)
    #   gpt-5.6-luna    → 정상
    #   gpt-5.6-terra   → 정상
    #   gpt-5.5         → 404 미지원
    "chatgpt": {
        "label": "ChatGPT 구독 (OAuth)",
        "mode": "subscription",
        "codex_model": "gpt-6-luna",
        "alt_models": ["gpt-6-luna", "gpt-5.6-luna", "gpt-5.6-terra"],
        "known_blocked": ["gpt-5.5"],
        "claude_supported": False,
        "claude_note": (
            "ChatGPT 구독은 Codex 전용입니다. Claude Code 는 Anthropic 자격증명이 "
            "필요하므로 이 프로바이더로는 쓸 수 없습니다."
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
        "chatgpt": "chatgpt", "gpt": "chatgpt", "openai": "chatgpt",
        "sub": "chatgpt", "subscription": "chatgpt",
    }
    key = alias.get(key, key)
    return key if key in PROVIDERS else DEFAULT_PROVIDER


def is_subscription(provider: Optional[str]) -> bool:
    """구독(OAuth) 프로바이더인가. API 키 대신 CLI 자체 자격증명을 쓴다."""
    return PROVIDERS[resolve_provider(provider)].get("mode") == "subscription"


def resolve_models_override(models: Optional[Dict[str, str]],
                            provider: str) -> str:
    """models 맵에서 해당 프로바이더용 모델명을 꺼낸다 (별칭 흡수).

    예: models={'chatgpt':'gpt-5.6-terra','oc':'deepseek-v4.1-flash'}
        resolve_models_override(models, 'opencode-go') -> 'deepseek-v4.1-flash'
    """
    if not models:
        return ""
    want = resolve_provider(provider)
    for k, v in models.items():
        if v and resolve_provider(k) == want:
            return str(v)
    return ""


# ---------------------------------------------------------------------------
# 할당량 소진 감지 / 프로바이더 자동 폴백 (두뇌 스왑)
# ---------------------------------------------------------------------------
# [2026-09-25 대표님 지시] ChatGPT 구독(Free)처럼 할당량이 작은 두뇌가 소진되면
#   다음 두뇌로 자동 교체해 워커를 계속 돌린다. (영상의 '모델 라우팅' 개념)
#
# 마커 출처 = Codex 바이너리 내부 오류 분류 코드 실측 추출:
#   connection_error | network_error | http_401 | http_403 | http_429 |
#   http_4xx | http_5xx | stream_error | context_window_exceeded |
#   quota_exceeded | usage_not_included | retryable_api_error | rate_limit
#
# ★ 숫자 단독("429") 매칭은 금지 — "tokens used 11,429" 같은 토큰 수에 오탐한다.
#   반드시 컨텍스트가 붙은 형태만 쓴다.
QUOTA_MARKERS = (
    "quota_exceeded", "quota exceeded",
    "usage_not_included", "usage limit", "usage_limit",
    "rate_limit", "rate limit", "too many requests",
    "http_429", "status 429", "status: 429", "error 429", "code 429",
    "insufficient_quota", "insufficient credits", "insufficient_credits",
    "exceeded your current quota", "credit balance is too low",
    "you've reached your", "limit reached", "exceeded your usage",
)

# 소진된 프로바이더를 기억해 두는 파일 (임시 상태 → WORKER_ROOT 하위)
QUOTA_STORE_FILE = WORKER_ROOT / "_provider_quota.json"
# 리셋 시각을 못 읽었을 때의 기본 쿨다운 (ChatGPT Free 리셋 주기 기준)
QUOTA_DEFAULT_COOLDOWN_SEC = 5 * 3600

# 자동 폴백 기본 순서: 구독(한도 작음) 우선 → API 키 프로바이더
FALLBACK_CHAIN = ("chatgpt", "openrouter", "opencode-go", "minimax")
AUTO_PROVIDER_ALIASES = ("auto", "chain", "fallback")

_QUOTA_LOCK = threading.Lock()


def detect_quota_exhausted(text: str) -> str:
    """본문에서 할당량 소진 신호를 찾는다. 찾으면 그 마커를, 없으면 빈 문자열."""
    if not text:
        return ""
    low = text.lower()
    for m in QUOTA_MARKERS:
        if m in low:
            return m
    return ""


def parse_reset_hint(text: str) -> Optional[float]:
    """응답에서 할당량 리셋 시각 힌트(epoch)를 읽는다. 못 읽으면 None.

    Codex 응답에 resetsAt / resets_at + ISO-8601 이 실려 오는 경우가 있다.
    ('Try again at 3:45 PM' 형태는 시간대가 불명확하므로 일부러 무시한다.)
    """
    if not text:
        return None
    m = re.search(
        r"(?:resets?_?at|reset_at)['\"]?\s*[:=]\s*['\"]?"
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    raw = m.group(1).strip().replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        return ts if ts > 0 else None
    except Exception:
        return None


def _load_quota_store() -> Dict[str, Any]:
    try:
        if QUOTA_STORE_FILE.exists():
            data = json.loads(QUOTA_STORE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_quota_store(data: Dict[str, Any]) -> None:
    try:
        QUOTA_STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUOTA_STORE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def get_quota_cooldown(provider: str) -> Dict[str, Any]:
    """쿨다운 중이면 상세를, 아니면 빈 dict 를 돌려준다."""
    name = resolve_provider(provider)
    with _QUOTA_LOCK:
        ent = (_load_quota_store().get(name) or {})
    try:
        until = float(ent.get("until") or 0)
    except Exception:
        until = 0.0
    if until > time.time():
        return {
            "provider": name,
            "until": until,
            "seconds_left": int(until - time.time()),
            "marker": ent.get("marker", ""),
            "reason": ent.get("reason", ""),
            "reset_hint": ent.get("reset_hint", ""),
        }
    return {}


def set_quota_cooldown(provider: str, marker: str = "", reason: str = "",
                       seconds: Optional[int] = None,
                       reset_hint: str = "") -> Dict[str, Any]:
    """프로바이더를 할당량 소진 상태로 표시한다 (기본 5시간)."""
    name = resolve_provider(provider)
    secs = int(seconds) if seconds else QUOTA_DEFAULT_COOLDOWN_SEC
    now = time.time()
    ent = {
        "until": now + secs,
        "marked_at": now,
        "seconds": secs,
        "marker": marker,
        "reason": reason,
        "reset_hint": reset_hint,
    }
    with _QUOTA_LOCK:
        d = _load_quota_store()
        d[name] = ent
        _save_quota_store(d)
    return {"provider": name, **ent}


def clear_quota_cooldown(provider: str) -> bool:
    """쿨다운을 해제한다 (수동 복구 / 할당량 회복 확인 시)."""
    name = resolve_provider(provider)
    with _QUOTA_LOCK:
        d = _load_quota_store()
        if name in d:
            d.pop(name, None)
            _save_quota_store(d)
            return True
    return False


def list_quota_cooldowns() -> List[Dict[str, Any]]:
    """현재 쿨다운 중인 프로바이더 목록."""
    out: List[Dict[str, Any]] = []
    with _QUOTA_LOCK:
        names = list(_load_quota_store().keys())
    for n in names:
        info = get_quota_cooldown(n)
        if info:
            out.append(info)
    return out


def build_fallback_chain(harness: str = "codex",
                         explicit: Optional[List[str]] = None,
                         include_cooling: bool = False) -> List[str]:
    """실행할 프로바이더 순서를 만든다.

    - harness 에 못 쓰는 프로바이더는 제외한다 (예: Claude Code 에 chatgpt 불가)
    - include_cooling=False 면 쿨다운(할당량 소진) 중인 것은 건너뛴다
    - 전부 쿨다운이면 어차피 시도해야 하므로 원래 순서를 그대로 돌려준다
    """
    harness = "codex" if harness == "codex" else "claude"
    names = list(explicit) if explicit else list(FALLBACK_CHAIN)
    ordered: List[str] = []
    for n in names:
        nm = resolve_provider(n)
        if nm in ordered:
            continue
        if harness == "claude" and not claude_supports(nm):
            continue
        ordered.append(nm)
    if not ordered:
        ordered = [DEFAULT_PROVIDER]
    if include_cooling:
        return ordered
    warm = [n for n in ordered if not get_quota_cooldown(n)]
    return warm or ordered


def is_auto_provider(provider: Optional[str]) -> bool:
    """provider='auto' 계열인가 (자동 폴백 체인 요청)."""
    return str(provider or "").strip().lower() in AUTO_PROVIDER_ALIASES


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


def subscription_logged_in() -> Dict[str, Any]:
    """`codex login` 으로 만든 ChatGPT 구독 자격증명이 유효한지 본다 (읽기 전용).

    반환: {"ok": bool, "mode": str, "account_id": str, "reason": str}
    """
    p = CODEX_AUTH_JSON
    if not p.exists():
        return {
            "ok": False, "mode": "", "account_id": "",
            "reason": f"구독 로그인 파일이 없습니다: {p} (먼저 `codex login` 실행)",
        }
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "mode": "", "account_id": "",
                "reason": f"auth.json 파싱 실패: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "mode": "", "account_id": "", "reason": "auth.json 형식 오류"}

    mode = str(data.get("auth_mode") or "")
    tokens = data.get("tokens") or {}
    access = str((tokens or {}).get("access_token") or "")
    account = str((tokens or {}).get("account_id") or "")
    if mode.lower() == "chatgpt" and access:
        return {"ok": True, "mode": mode, "account_id": account, "reason": ""}
    return {
        "ok": False, "mode": mode, "account_id": account,
        "reason": (
            f"구독 자격증명이 아닙니다 (auth_mode={mode!r}). "
            "`codex login` 으로 ChatGPT 계정 로그인을 완료하세요."
        ),
    }


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
        sub = spec.get("mode") == "subscription"
        key = read_provider_key(name)
        has_key = bool(key)
        if sub:
            has_key = bool(subscription_logged_in().get("ok"))
        out.append({
            "provider": name,
            "label": spec["label"],
            "has_key": has_key,
            "base_url": spec.get("base_url", ""),
            "codex_model": spec["codex_model"],
            "claude_alias": spec.get("claude_alias", ""),
            "claude_supported": claude_supports(name),
            "claude_note": spec.get("claude_note", ""),
            "models": read_provider_models(name),
            "subscription": sub,
            "cooldown": get_quota_cooldown(name) or None,
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

def write_codex_home(provider: str, with_mcp: bool = False, allowed_mcps: Optional[List[str]] = None) -> Dict[str, Any]:
    """프로바이더 설정이 담긴 config.toml 을 임시 CODEX_HOME 에 쓴다.

    ★ 사용자의 실제 ~/.codex 는 건드리지 않는다.
      CODEX_HOME 환경변수로 이 디렉터리만 바라보게 한다.

    Codex 0.152+ 는 wire_api="chat" 을 폐지했으므로 "responses" 를 쓴다.
    (OpenRouter / Qwen / opencode-go / MiniMax / omniroute 모두 /responses 지원 실측)

    with_mcp: True 인 경우에만 MCP 서버를 주입한다.
              allowed_mcps 가 지정되면 Laya가 선별한 해당 MCP 서버만 핀포인트로 주입한다.
    """
    name = resolve_provider(provider)
    spec = PROVIDERS[name]
    subscription = spec.get("mode") == "subscription"

    home = CODEX_HOME_ROOT / name
    home.mkdir(parents=True, exist_ok=True)

    env_key_name = f"DAON_HARNESS_{re.sub(r'[^A-Za-z0-9]', '_', name).upper()}_KEY"

    lines: List[str] = []
    if subscription:
        # ── 구독(OAuth) 경로 ──────────────────────────────────────────
        # model_provider / base_url / env_key 를 일절 쓰지 않는다.
        # → Codex CLI 기본(OpenAI) + auth.json 의 ChatGPT 자격증명을 쓴다.
        # auth.json 은 '복사'만 한다. 전역 ~/.codex 는 읽기 전용 취급.
        if spec.get("codex_model") not in (None, "", "auto"):
            lines.append(f'model = "{spec["codex_model"]}"')
        _src = CODEX_AUTH_JSON
        if _src.exists():
            try:
                shutil.copyfile(_src, home / "auth.json")
            except Exception:
                pass
        lines.append("")
    else:
        lines = [
            f'model = "{spec["codex_model"]}"',
            f'model_provider = "{name}"',
            f'model_context_window = {spec.get("context_window", 128000)}',
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

    if with_mcp:
        # Laya 스마트 선별 목록이 제공되면 해당 목록만, 아니면 전체 허용
        targets = set(allowed_mcps) if allowed_mcps is not None else {"context7", "serena", "daon-design", "figma", "stitch"}

        if "context7" in targets:
            lines += [
                "",
                "[mcp_servers.context7]",
                'command = "npx"',
                'args = ["-y", "@upstash/context7-mcp"]',
            ]
        if "serena" in targets:
            lines += [
                "",
                "[mcp_servers.serena]",
                'command = "uvx"',
                # [2026-09-25 병합] 소스 사본에만 있던 대시보드 비활성 플래그를 이식.
                # serena 는 uvx+git clone 으로 뜨므로 불필요한 웹/GUI 창을 꺼 시작 비용을 줄인다.
                'args = ["--from", "git+https://github.com/oraios/serena", "serena", "start-mcp-server", "--project", "C:/daon/Daon agent System", "--enable-web-dashboard", "false", "--open-web-dashboard", "false", "--enable-gui-log-window", "false"]',
            ]
        if "daon-design" in targets:
            lines += [
                "",
                '[mcp_servers."daon-design"]',
                'command = "python"',
                'args = ["-u", "c:/daon/Daon agent System/api/api/mcp/daon_design_mcp.py"]',
            ]

        # Figma & Stitch: mcp_servers.json 에서 키를 동적으로 읽어 주입 (하드코딩 방지)
        try:
            _mcp_json = Path(os.environ.get("LOCALAPPDATA", "")) / "DAON Agent System/data/mcp_servers.json"
            if _mcp_json.exists():
                with open(_mcp_json, "r", encoding="utf-8") as _f:
                    for _srv in json.load(_f):
                        _sid = _srv.get("server_id")
                        if _sid == "figma" and "figma" in targets:
                            _fkey = _srv.get("env", {}).get("FIGMA_API_KEY", "")
                            if _fkey:
                                lines += [
                                    "",
                                    "[mcp_servers.figma]",
                                    'command = "figma-mcp"',
                                    'args = []',
                                    f'env = {{ FIGMA_API_KEY = "{_fkey}" }}',
                                ]
                        elif _sid == "stitch" and "stitch" in targets:
                            _skey = _srv.get("env", {}).get("STITCH_API_KEY", "")
                            _gcred = _srv.get("env", {}).get("GOOGLE_APPLICATION_CREDENTIALS", "").replace("\\", "\\\\")
                            if _skey:
                                lines += [
                                    "",
                                    "[mcp_servers.stitch]",
                                    'command = "npx"',
                                    'args = ["-y", "@_davideast/stitch-mcp", "proxy", "--transport", "stdio"]',
                                    f'env = {{ STITCH_API_KEY = "{_skey}", GOOGLE_APPLICATION_CREDENTIALS = "{_gcred}" }}',
                                ]
        except Exception:
            pass

    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if subscription:
        return {
            "home": str(home),
            "config": str(home / "config.toml"),
            "env_key_name": "",
            "codex_model": spec["codex_model"],
            "base_url": "",
            "subscription": True,
            "auth_copied": (home / "auth.json").exists(),
        }

    return {
        "home": str(home),
        "config": str(home / "config.toml"),
        "env_key_name": env_key_name,
        "codex_model": spec["codex_model"],
        "base_url": spec["base_url"],
        "subscription": False,
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
        actual_key = read_provider_key(name)
        key_val = actual_key if actual_key else f"os.environ/{env_var}"

        block = [
            f"  - model_name: {alias}",
            "    litellm_params:",
            f"      model: openai/{model}",
            f"      api_base: {spec['base_url']}",
            f"      api_key: {key_val}",
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
    default_model = alias_map[default_alias]["model"]
    default_key = read_provider_key(DEFAULT_PROVIDER)
    default_key_val = default_key if default_key else f"os.environ/{alias_map[default_alias]['env_var']}"
    for slot in ("claude-opus-5", "claude-sonnet-4-6", "claude-sonnet-4-5",
                 "claude-haiku-4-5"):
        entries.append(
            f"  - model_name: {slot}\n"
            f"    litellm_params:\n"
            f"      model: openai/{default_model}\n"
            f"      api_base: {PROVIDERS[DEFAULT_PROVIDER]['base_url']}\n"
            f"      api_key: {default_key_val}"
        )

    body = (
        "# LiteLLM gateway - DAON harness-worker\n"
        "# Auto-generated configuration. Do not edit directly.\n"
        "# Exposes registered providers as claude-* aliases.\n"
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
    # 구독 전용 프로바이더(chatgpt 등)는 claude_alias 가 없다 → 빈 문자열
    return PROVIDERS[resolve_provider(provider)].get("claude_alias", "")


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
        if model and model != "auto":
            argv += ["-m", model]
        # Windows PowerShell heredoc / 32KB argv 한계 우회 지침 주입
        windows_directive = (
            "[SYSTEM DIRECTIVE FOR WINDOWS POWERSHELL]:\n"
            "- You are running autonomously in Windows PowerShell without human interaction.\n"
            "- The Windows command-line buffer has length limits. Do NOT execute massive inline command lines (>8KB) or bash-style multiline heredocs.\n"
            "- When creating or editing files, write files directly using Python (`python -c \"...\"`) or standard PowerShell cmdlets (`Set-Content`, `Out-File` with UTF-8).\n"
            "- Work autonomously and complete the entire task until fully verified.\n"
            "- Once the required files, edits, and verification are finished, output your final response immediately. Do NOT run cleanup commands or attempt to delete build artifacts (like __pycache__ or temporary files).\n\n"
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
        if is_subscription(name):
            # ── 구독(OAuth) 경로 ──────────────────────────────────────
            # API 키를 주입하지 않는다. 임시 CODEX_HOME 의 auth.json(OAuth)을 쓴다.
            # 주변 환경에 키가 남아 있으면 인증 방식이 API 키로 뒤집히므로 제거한다.
            for _k in ("OPENAI_API_KEY", "OPENROUTER_API_KEY"):
                env.pop(_k, None)
        else:
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

def _run_single(
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
    with_mcp: bool = False,
    _wd_path: Optional[str] = None,
    _job_id: Optional[str] = None,
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """워커를 **한 번** 실행하고 정제된 결과를 돌려준다 (단일 프로바이더).

    provider: openrouter | chatgpt | opencode-go | qwen-token-plan | minimax | omniroute
              (별칭 oc / qwen / mm / omni / gpt / openai / sub 도 허용)

    자동 폴백이 필요하면 run_worker(provider='auto') 를 쓴다.

    models  : 프로바이더별 모델 지정. {'chatgpt':'gpt-5.6-terra',
              'openrouter':'deepseek/deepseek-v4.1-flash'} 처럼 주면
              각 두뇌가 자기에게 맞는 모델로 돈다 (전역 model 보다 우선).

    _wd_path: 이미 준비된 작업 디렉터리 경로. 폴백 재시도 시 같은 폴더를
              이어 쓰기 위해 내부에서만 넘긴다 (이미 만든 파일을 살린다).
    _job_id : 백그라운드 잡이면 PID 를 _ACTIVE_PROCS 에 등록해 kill 이 동작하게 한다.

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

    # 자격증명 - 구독(OAuth) 프로바이더는 키가 없는 게 정상이다
    subscription = is_subscription(prov)
    key = api_key if api_key is not None else read_provider_key(prov)
    if subscription:
        st = subscription_logged_in()
        if not st.get("ok"):
            result["error"] = f"[{prov}] {st.get('reason')}"
            return result
    elif not key:
        result["error"] = (
            f"[{prov}] API 키를 읽지 못했습니다 "
            f"(custom_providers.json 의 providers.{prov}.api_key). "
            f"사용 가능: {', '.join(p['provider'] for p in available_providers() if p['has_key'])}"
        )
        return result

    codex_home = None
    if harness == "codex":
        try:
            allowed_mcps = None
            if with_mcp:
                try:
                    _api_dir = r"c:\daon\Daon agent System\api"
                    if _api_dir not in sys.path:
                        sys.path.insert(0, _api_dir)
                    from api.laya_client import laya_client
                    allowed_mcps = laya_client.prune_mcp(prompt, ["context7", "serena", "daon-design", "figma", "stitch"])
                except Exception:
                    allowed_mcps = None
            home = write_codex_home(prov, with_mcp=with_mcp, allowed_mcps=allowed_mcps)
            codex_home = home["home"]
            result["codex_home"] = home["config"]
            _per = resolve_models_override(models, prov)
            if _per:
                model = _per
            elif not model:
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
        _per = resolve_models_override(models, prov)
        if _per:
            # Claude Code 는 LiteLLM alias 를 받는 게 안전하다 (--model 값)
            model = claude_alias_for(prov) if _per.lower().startswith("claude-") else _per

    result["model"] = model

    if _wd_path:
        # 폴백 재시도 — 앞선 두뇌가 만들어 둔 작업 폴더를 그대로 이어 쓴다.
        # (새로 만들면 이미 생성된 파일이 사라져 처음부터 다시 하게 된다)
        wd = {"path": _wd_path, "created_repo": False, "isolated": bool(isolate)}
    else:
        wd = prepare_workdir(workdir=workdir, isolate=isolate, label=harness)
    result["workdir"] = wd["path"]
    result["isolated"] = wd["isolated"]

    argv = build_command(harness, prompt, exe, full_auto=full_auto,
                         model=model, provider=prov)
    env = build_env(harness, key, provider=prov, codex_home=codex_home,
                    model=model)
    result["command"] = " ".join(argv[:3]) + (" ... " if len(argv) > 3 else "")

    t0 = time.time()
    out_file = None
    err_file = None
    proc = None
    try:
        # PIPE 대신 TemporaryFile 사용 — 자식 프로세스가 파이프 핸들을 상속해 EOF 미도착 hang 방지
        out_file = tempfile.TemporaryFile(mode="w+b")
        err_file = tempfile.TemporaryFile(mode="w+b")

        proc = subprocess.Popen(
            argv,
            cwd=wd["path"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_file,
            stderr=err_file,
        )

        # 백그라운드 잡이면 PID 를 등록해 kill_job 이 실제로 종료시킬 수 있게 한다
        if _job_id:
            try:
                with _ACTIVE_PROCS_LOCK:
                    _ACTIVE_PROCS[_job_id] = proc
            except Exception:
                pass

        deadline = t0 + timeout
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                break
            time.sleep(0.3)
        else:
            if proc:
                try:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
                    else:
                        proc.kill()
                except Exception:
                    pass
            result["elapsed"] = round(time.time() - t0, 2)
            result["error"] = f"타임아웃 ({timeout}초). 워커가 응답하지 않았습니다."
            return result

        result["elapsed"] = round(time.time() - t0, 2)
        result["exit_code"] = proc.returncode

        # 비동기로 남은 자식 프로세스 정리 (좀비 방지)
        if proc and sys.platform == "win32":
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=3, stdin=subprocess.DEVNULL)
            except Exception:
                pass

        out_file.seek(0)
        err_file.seek(0)
        stdout = out_file.read().decode("utf-8", errors="replace")
        stderr = err_file.read().decode("utf-8", errors="replace")
        raw = stdout + stderr
        result["raw_tail"] = "\n".join(raw.strip().split("\n")[-12:])

        # 응답은 stdout 으로, 경고/로그는 stderr 로 나온다.
        primary = stdout if stdout.strip() else stderr
        out = clean_output(harness, primary)
        result["output"] = out
        result["ok"] = proc.returncode == 0 and bool(out)

        if not result["ok"] and not result["error"]:
            result["error"] = (
                f"exit={proc.returncode}. 출력이 비었거나 실패했습니다. "
                "raw_tail 을 확인하세요."
            )
    except Exception as exc:
        result["elapsed"] = round(time.time() - t0, 2)
        result["error"] = f"실행 실패: {exc}"
    finally:
        if _job_id:
            try:
                with _ACTIVE_PROCS_LOCK:
                    _ACTIVE_PROCS.pop(_job_id, None)
            except Exception:
                pass
        if out_file:
            try:
                out_file.close()
            except Exception:
                pass
        if err_file:
            try:
                err_file.close()
            except Exception:
                pass
        if wd["isolated"] and not keep:
            # 실제로 사라졌는지로 판정한다 (거짓 보고 금지)
            result["cleaned"] = _rmtree_retry(wd["path"])
            if not result["cleaned"]:
                result["cleanup_warning"] = (
                    "격리 디렉터리를 지우지 못했습니다(파일 잠금). "
                    f"수동 삭제: {wd['path']}"
                )

    return result


def _cleanup_isolated(res: Dict[str, Any], isolate: bool, keep: bool,
                      wd_path: Optional[str]) -> Dict[str, Any]:
    """폴백 래퍼용 정리. 최종 결과에만 적용한다 (중간 시도 폴더는 보존)."""
    if isolate and not keep and wd_path:
        cleaned = _rmtree_retry(wd_path)
        res["cleaned"] = cleaned
        if not cleaned:
            res["cleanup_warning"] = (
                "격리 디렉터리를 지우지 못했습니다(파일 잠금). "
                f"수동 삭제: {wd_path}"
            )
    return res


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
    with_mcp: bool = False,
    chain: Optional[List[str]] = None,
    _job_id: Optional[str] = None,
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """워커를 실행한다.

    provider 에 단일 값을 주면 그 프로바이더로 **한 번** 실행한다 (기존 동작).

    provider='auto' (또는 'chain' / 'fallback') 이면 **자동 폴백 체인**으로 돈다:
      chatgpt(구독) → openrouter → opencode-go → minimax
    앞의 두뇌가 할당량을 소진하면 즉시 다음 두뇌로 갈아타고,
    소진된 프로바이더는 쿨다운으로 기록해 다음 호출부터 건너뛴다.
    같은 작업 폴더를 이어 쓰므로 이미 만든 파일은 유지된다.

    chain 인자로 순서를 직접 지정할 수 있다 (예: ["openrouter", "chatgpt"]).

    반환 dict 는 _run_single 과 같고, 폴백 시 추가 키:
      attempts[]          — 시도한 프로바이더별 결과·소진 마커
      fallback_used       — 첫 두뇌가 아닌 다른 두뇌로 성공했는지
      requested_provider  — 'auto'
      chain               — 실제로 시도한 순서
    """
    harness = "codex" if harness == "codex" else "claude"

    if not is_auto_provider(provider):
        return _run_single(
            harness, prompt, workdir=workdir, isolate=isolate, model=model,
            timeout=timeout, full_auto=full_auto, keep=keep, api_key=api_key,
            provider=provider, with_mcp=with_mcp, _job_id=_job_id,
            models=models,
        )

    # 쿨다운으로 건너뛴 두뇌를 따로 기록한다 — 사전 제외도 '교체'이므로
    # fallback_used 에 반영해야 대표님께 정확히 보고된다.
    requested_chain = build_fallback_chain(harness, explicit=chain, include_cooling=True)
    names = build_fallback_chain(harness, explicit=chain)
    skipped_cooling = [n for n in requested_chain if n not in names]
    attempts: List[Dict[str, Any]] = []
    wd_path: Optional[str] = None
    last: Dict[str, Any] = {}

    for idx, prov in enumerate(names):
        res = _run_single(
            harness, prompt, workdir=workdir, isolate=isolate, model=model,
            timeout=timeout, full_auto=full_auto,
            keep=True,  # 최종 정리는 이 래퍼가 한다 (재시도 폴더 보존)
            api_key=api_key, provider=prov, with_mcp=with_mcp,
            _wd_path=wd_path, _job_id=_job_id, models=models,
        )
        if not wd_path:
            wd_path = res.get("workdir")

        blob = "\n".join([str(res.get("error") or ""), str(res.get("raw_tail") or "")])
        marker = detect_quota_exhausted(blob)
        hint = parse_reset_hint(blob) if marker else None

        attempts.append({
            "provider": prov,
            "ok": bool(res.get("ok")),
            "exit_code": res.get("exit_code"),
            "elapsed": res.get("elapsed"),
            "model": res.get("model"),
            "quota_marker": marker,
            "error": (str(res.get("error") or ""))[:300],
        })

        if res.get("ok"):
            if marker:
                # 응답은 받았지만 할당량 경고가 섞여 있으면 미리 표시해 둔다
                secs = int(hint - time.time()) + 60 if hint and hint > time.time() else None
                set_quota_cooldown(prov, marker, "응답에 할당량 경고 포함",
                                   seconds=secs, reset_hint=str(hint or ""))
            res.update({
                "attempts": attempts,
                "fallback_used": idx > 0 or bool(skipped_cooling),
                "skipped_cooling": skipped_cooling,
                "requested_provider": "auto",
                "chain": list(names),
            })
            return _cleanup_isolated(res, isolate, keep, wd_path)

        if marker:
            secs = int(hint - time.time()) + 60 if hint and hint > time.time() else None
            set_quota_cooldown(prov, marker, "할당량 소진",
                               seconds=secs, reset_hint=str(hint or ""))
        last = res

        # kill 요청이면 다음 두뇌로 넘어가지 않는다 (불필요한 재시도 방지)
        if _job_id:
            try:
                with _JOBS_LOCK:
                    if _JOBS.get(_job_id, {}).get("status") == "killed":
                        break
            except Exception:
                pass

    out: Dict[str, Any] = dict(last) if last else {
        "ok": False, "harness": harness, "provider": "auto",
        "error": "실행 가능한 프로바이더가 없습니다.",
    }
    out["ok"] = False
    out["attempts"] = attempts
    out["fallback_used"] = len(attempts) > 1 or bool(skipped_cooling)
    out["skipped_cooling"] = skipped_cooling
    out["requested_provider"] = "auto"
    out["chain"] = list(names)
    out["error"] = (str(out.get("error") or "")).strip() + \
        f" | 폴백 체인 {len(attempts)}개 모두 실패"
    return _cleanup_isolated(out, isolate, keep, wd_path)


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

        # 먼저 상태를 killed 로 설정하여 워커 스레드가 인지하도록 함
        job["status"] = "killed"
        job["finished_at"] = time.time()
        job["elapsed"] = round(job["finished_at"] - job.get("started_at", job["finished_at"]), 2)
        job["error"] = "Job was killed by user request."
    _persist_jobs()

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
    with_mcp: bool = False,
    chain: Optional[List[str]] = None,
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """워커를 독립된 백그라운드 스레드 및 프로세스로 실행하고, 즉시 job_id 를 반환한다."""
    _ensure_jobs_loaded()
    harness = "codex" if harness == "codex" else "claude"

    # ── 자동 폴백 체인 (provider='auto') ─────────────────────────────────
    #   앞 두뇌가 할당량을 소진하면 다음 두뇌로 갈아탄다.
    #   스레드가 run_worker(폴백 래퍼)를 통째로 돌리고 잡 상태만 갱신한다.
    if is_auto_provider(provider):
        names = build_fallback_chain(harness, explicit=chain)
        auto_job_id = f"job_{harness}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        auto_info: Dict[str, Any] = {
            "job_id": auto_job_id,
            "harness": harness,
            "provider": "auto",
            "chain": names,
            "model": model or "",
            "workdir": "",
            "isolated": bool(isolate),
            "prompt": prompt,
            "command": "auto-fallback: " + " -> ".join(names),
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "elapsed": 0.0,
            "pid": None,
            "exit_code": None,
            "output": "",
            "raw_tail": "",
            "error": "",
            "session_id": session_id or "",
            "attempts": [],
            "fallback_used": False,
        }
        with _JOBS_LOCK:
            _JOBS[auto_job_id] = auto_info
        _persist_jobs()

        def _auto_thread_func() -> None:
            t0 = time.time()
            try:
                res = run_worker(
                    harness, prompt, workdir=workdir, isolate=isolate, model=model,
                    timeout=timeout, full_auto=full_auto, keep=keep,
                    api_key=api_key, provider="auto", with_mcp=with_mcp,
                    chain=chain, _job_id=auto_job_id, models=models,
                )
            except Exception as exc:
                res = {"ok": False, "error": f"실행 실패: {exc}"}

            elapsed = round(time.time() - t0, 2)
            with _JOBS_LOCK:
                j = _JOBS.get(auto_job_id)
                if j is None:
                    return
                if j.get("status") != "killed":
                    j["status"] = "completed" if res.get("ok") else "failed"
                    j["exit_code"] = res.get("exit_code")
                    j["output"] = res.get("output") or ""
                    j["raw_tail"] = res.get("raw_tail") or ""
                    j["error"] = res.get("error") or ""
                    j["provider"] = res.get("provider") or "auto"
                    j["model"] = res.get("model") or j.get("model") or ""
                    j["workdir"] = res.get("workdir") or j.get("workdir") or ""
                    j["attempts"] = res.get("attempts") or []
                    j["fallback_used"] = bool(res.get("fallback_used"))
                j["elapsed"] = elapsed
                j["finished_at"] = time.time()
                final_st = dict(j)
            _persist_jobs()

            st_raw = final_st.get("status")
            st_kor = ("완료" if st_raw == "completed"
                      else ("중단" if st_raw == "killed" else "실패"))
            icon = "✅" if st_raw == "completed" else "❌"
            used = final_st.get("attempts") or []
            chain_txt = " → ".join(
                f"{a.get('provider')}{'' if a.get('ok') else '(실패)'}" for a in used
            ) or " → ".join(names)
            msg = (
                f"{icon} [워커 {st_kor}] {harness.upper()} 자동 폴백 작업이 {st_kor}되었습니다. "
                f"(소요: {elapsed}s, 두뇌: {chain_txt})"
            )
            try:
                _send_windows_toast(
                    title=f"DAON 워커 {st_kor} ({harness.upper()} auto)",
                    message=f"소요 시간: {elapsed}초\n두뇌: {chain_txt}",
                    status=st_raw or "completed",
                )
            except Exception:
                pass
            if session_id:
                try:
                    from api.config import get_stream_queue
                    q = get_stream_queue(session_id)
                    if q:
                        q.put_nowait(('notice', {
                            'message': msg, 'job_id': auto_job_id,
                            'status': st_raw,
                        }))
                except Exception:
                    pass

        threading.Thread(
            target=_auto_thread_func,
            name=f"daon-worker-auto-{auto_job_id}",
            daemon=True,
        ).start()

        return {
            "ok": True,
            "status": "running",
            "job_id": auto_job_id,
            "harness": harness,
            "provider": "auto",
            "chain": names,
            "model": model or "",
            "isolated": bool(isolate),
            "attempts": [],
            "message": (
                f"{harness.upper()} 워커가 백그라운드에서 자동 폴백 체인으로 시작되었습니다 "
                f"(Job ID: {auto_job_id}, 순서: {' → '.join(names)}). "
                f"앞 두뇌가 할당량을 소진하면 자동으로 다음 두뇌로 갈아탑니다. "
                f"진행 확인은 worker_job(action='status', job_id='{auto_job_id}')."
            ),
        }

    prov = resolve_provider(provider)

    exe = find_binary(harness)
    if not exe:
        return {
            "ok": False,
            "error": f"{harness} 실행 파일을 찾지 못했습니다.",
            "harness": harness,
        }

    # 자격증명 - 구독(OAuth) 프로바이더는 API 키가 없는 게 정상이다.
    # [2026-09-25] 워커 본체(run_worker)와 동일한 구독 분기를 여기에도 적용.
    #   이게 없으면 dispatch_worker(백그라운드) 경로만 "API 키가 없습니다"로 거부된다.
    subscription = is_subscription(prov)
    key = api_key if api_key is not None else read_provider_key(prov)
    if subscription:
        st = subscription_logged_in()
        if not st.get("ok"):
            return {
                "ok": False,
                "error": f"[{prov}] {st.get('reason')}",
                "harness": harness,
            }
    elif not key:
        return {
            "ok": False,
            "error": f"[{prov}] 프로바이더 API 키가 없습니다.",
            "harness": harness,
        }

    codex_home: Optional[str] = None
    if harness == "codex":
        try:
            allowed_mcps = None
            if with_mcp:
                try:
                    _api_dir = r"c:\daon\Daon agent System\api"
                    if _api_dir not in sys.path:
                        sys.path.insert(0, _api_dir)
                    from api.laya_client import laya_client
                    allowed_mcps = laya_client.prune_mcp(prompt, ["context7", "serena", "daon-design", "figma", "stitch"])
                except Exception:
                    allowed_mcps = None
            home = write_codex_home(prov, with_mcp=with_mcp, allowed_mcps=allowed_mcps)
            codex_home = home["home"]
            _per = resolve_models_override(models, prov)
            if _per:
                model = _per
            elif not model:
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
        _per = resolve_models_override(models, prov)
        if _per:
            model = claude_alias_for(prov) if _per.lower().startswith("claude-") else _per

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
        out_file = None
        err_file = None
        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            # PIPE 대신 TemporaryFile 사용 — 자식 프로세스가 파이프 핸들을 상속해 EOF 미도착 hang 방지
            out_file = tempfile.TemporaryFile(mode="w+b")
            err_file = tempfile.TemporaryFile(mode="w+b")

            proc = subprocess.Popen(
                argv,
                cwd=wd["path"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_file,
                stderr=err_file,
                creationflags=creationflags,
            )

            with _ACTIVE_PROCS_LOCK:
                _ACTIVE_PROCS[job_id] = proc
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["pid"] = proc.pid
            _persist_jobs()

            deadline = t0 + timeout
            exit_code = None
            is_timeout = False

            # poll 루프로 부모 exit 감시 (communicate 블로킹 회피)
            while True:
                with _JOBS_LOCK:
                    curr_st = _JOBS.get(job_id, {}).get("status")
                if curr_st == "killed":
                    break

                rc = proc.poll()
                if rc is not None:
                    exit_code = rc
                    break

                if time.time() >= deadline:
                    is_timeout = True
                    break

                time.sleep(0.3)

            elapsed = round(time.time() - t0, 2)

            if is_timeout:
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
                    if job_id in _JOBS and _JOBS[job_id].get("status") not in ("killed", "completed"):
                        _JOBS[job_id]["status"] = "timeout"
                        _JOBS[job_id]["elapsed"] = elapsed
                        _JOBS[job_id]["finished_at"] = time.time()
                        _JOBS[job_id]["error"] = f"타임아웃 ({timeout}초). 워커가 응답하지 않았습니다."

            elif _JOBS.get(job_id, {}).get("status") != "killed":
                # 남은 자식 프로세스 비동기 정리
                if proc and sys.platform == "win32":
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, timeout=3, stdin=subprocess.DEVNULL)
                    except Exception:
                        pass

                out_file.seek(0)
                err_file.seek(0)
                stdout = out_file.read().decode("utf-8", errors="replace")
                stderr = err_file.read().decode("utf-8", errors="replace")

                raw = (stdout or "") + (stderr or "")
                primary = stdout if stdout and stdout.strip() else (stderr or "")
                out = clean_output(harness, primary)
                ok = exit_code == 0 and bool(out)

                with _JOBS_LOCK:
                    if job_id in _JOBS and _JOBS[job_id].get("status") != "killed":
                        _JOBS[job_id]["status"] = "completed" if ok else "failed"
                        _JOBS[job_id]["exit_code"] = exit_code
                        _JOBS[job_id]["elapsed"] = elapsed
                        _JOBS[job_id]["finished_at"] = time.time()
                        _JOBS[job_id]["output"] = out
                        _JOBS[job_id]["raw_tail"] = "\n".join(raw.strip().split("\n")[-12:])
                        if not ok:
                            _JOBS[job_id]["error"] = f"exit={exit_code}. 출력이 비었거나 실패했습니다."

        except Exception as exc:
            elapsed = round(time.time() - t0, 2)
            with _JOBS_LOCK:
                if job_id in _JOBS and _JOBS[job_id].get("status") not in ("killed", "completed"):
                    _JOBS[job_id]["status"] = "failed"
                    _JOBS[job_id]["elapsed"] = elapsed
                    _JOBS[job_id]["finished_at"] = time.time()
                    _JOBS[job_id]["error"] = f"실행 중 예외: {exc}"

        finally:
            if out_file:
                try:
                    out_file.close()
                except Exception:
                    pass
            if err_file:
                try:
                    err_file.close()
                except Exception:
                    pass
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
        log_out = str(LITELLM_LOG)
        log_err = str(LITELLM_LOG.with_suffix(".err.log"))
        
        # Windows CP949 환경에서 LiteLLM 배너의 특수문자(█) 출력 시 UnicodeEncodeError 방지를 위해
        # python.exe에 -X utf8 플래그를 주고 WScript.Shell(독립 데몬)로 기동합니다.
        py_exe = sys.executable or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python312/python.exe")
        vbs_path = Path(r"C:\daon\_harness-ab-test\start_litellm.vbs")
        vbs_content = (
            f'Set WshShell = CreateObject("WScript.Shell")\r\n'
            f'WshShell.Run "{py_exe} -X utf8 -c ""import litellm.proxy.proxy_cli, sys; '
            f'sys.argv = [\'litellm\', \'--config\', \'{LITELLM_CONFIG}\', \'--port\', \'{LITELLM_PORT}\', \'--host\', \'127.0.0.1\']; '
            f'litellm.proxy.proxy_cli.run_server()""", 0, False\r\n'
        )
        vbs_path.write_text(vbs_content, encoding="utf-8")
        
        proc = subprocess.Popen(["wscript.exe", str(vbs_path)], env=env)
    except Exception as exc:
        return {"ok": False, "error": f"기동 실패: {exc}"}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(1.5)
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
        "auto_fallback": {
            "default_chain": list(FALLBACK_CHAIN),
            "codex_chain": build_fallback_chain("codex"),
            "claude_chain": build_fallback_chain("claude"),
            "cooldowns": list_quota_cooldowns(),
            "note": ("provider='auto' 로 호출하면 이 순서로 자동 교체한다. "
                     "할당량을 소진한 두뇌는 쿨다운으로 건너뛴다."),
        },
        "worker_root": str(WORKER_ROOT),
        "recent_runs": runs,
    }
