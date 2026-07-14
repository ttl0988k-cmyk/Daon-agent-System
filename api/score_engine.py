"""
Config Score Engine — evaluates Daon+Hermes configuration completeness on a 100-point scale.

Eight weighted categories:
  1. API Key     (25 pts) — registered providers, key validity
  2. Model Setup (15 pts) — default model, fallback, model diversity
  3. Profile     (15 pts) — active profile, SOUL/AGENTS.md, profile count
  4. MCP Servers (15 pts) — registered servers, connection status
  5. Workspace   (10 pts) — registered workspaces, valid paths
  6. Integration (10 pts) — Slack/Notion enabled
  7. Skills      ( 5 pts) — installed skills, custom skills
  8. Security    ( 5 pts) — password set, API key masking

Returns a dict compatible with the GET /api/score/evaluate response schema.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring weights (must sum to 100)
# ---------------------------------------------------------------------------
WEIGHTS = {
    "api_key": 25,
    "model_setup": 15,
    "profile": 15,
    "mcp_servers": 15,
    "workspace": 10,
    "integration": 10,
    "skills": 5,
    "security": 5,
}

GRADES = [
    (90, "S", "⭐"),
    (75, "A", "✅"),
    (60, "B", "👍"),
    (40, "C", "⚠️"),
    (0, "D", "🔴"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    """Resolve the data/ directory next to this project root."""
    # score_engine.py lives in api/ — go up one level
    return Path(__file__).resolve().parent.parent / "data"


def _load_json(path: Path) -> dict | list:
    """Load a JSON file, returning {} or [] on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _logger.debug("score_engine: could not load %s", path)
        return {} if path.suffix == ".json" else []


# ---------------------------------------------------------------------------
# Category scorers — each returns (score: int, max: int, detail: str)
# ---------------------------------------------------------------------------

def _score_api_key(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate registered providers and API key presence."""
    providers = _load_json(data_dir / "custom_providers.json")

    configured: list[str] = list(providers.get("providers", {}).keys()) if isinstance(providers, dict) else []
    preset_count = len(providers.get("presets", {})) if isinstance(providers, dict) else 0

    # Count providers that have an actual API key set (not masked)
    with_key = 0
    for name, cfg in providers.get("providers", {}).items():
        key = cfg.get("api_key", "")
        if key and not key.startswith("••••"):
            with_key += 1

    # Also check environment for common keys
    env_keys = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
        "TOGETHER_API_KEY", "MINIMAX_API_KEY",
    ]
    env_count = sum(1 for k in env_keys if os.environ.get(k))

    max_pts = WEIGHTS["api_key"]
    recommendations: list[str] = []

    if with_key == 0 and env_count == 0:
        score = 0
        detail = "API 키가 하나도 설정되지 않았습니다"
        recommendations.append("OpenAI 또는 MiniMax API 키를 등록하면 AI 모델을 사용할 수 있습니다")
    elif with_key >= 2:
        score = max_pts
        detail = f"{with_key}개 프로바이더에 API 키 등록됨"
    elif with_key == 1:
        score = 20
        detail = f"1개 프로바이더 API 키 등록됨 (총 {len(configured) + preset_count}개 프리셋 중)"
    elif env_count >= 1:
        score = 18
        detail = f"환경변수로 {env_count}개 API 키 감지됨 (직접 등록 권장)"
    else:
        score = 5
        detail = f"{preset_count}개 프리셋 있으나 API 키 미등록"
        recommendations.append("Settings에서 프로바이더 API 키를 등록하세요")

    return score, max_pts, detail, recommendations


def _score_model_setup(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate model configuration."""
    settings = _load_json(data_dir / "settings.json")
    providers = _load_json(data_dir / "custom_providers.json")

    max_pts = WEIGHTS["model_setup"]
    score = 0
    parts: list[str] = []
    recommendations: list[str] = []

    if not isinstance(settings, dict):
        return 0, max_pts, "모델 설정을 불러올 수 없음", []

    model = settings.get("default_model")

    if model:
        score += 8
        parts.append(f"기본 모델: {model}")
    else:
        parts.append("기본 모델 미지정")
        recommendations.append("Settings에서 기본 모델을 지정하세요")

    # Check for model diversity (custom provider models)
    provider_models = 0
    for cfg in providers.get("providers", {}).values() if isinstance(providers, dict) else []:
        models = cfg.get("models", [])
        provider_models += len(models)

    if provider_models >= 5:
        score += 4
    elif provider_models >= 2:
        score += 2

    # Check for fallback / multiple options (via env or other signals)
    if len(providers.get("providers", {})) >= 2:
        score += 3
        parts.append("멀티 프로바이더 구성 완료")
    else:
        parts.append("프로바이더 다양성 부족")

    return min(score, max_pts), max_pts, "; ".join(parts), recommendations


def _score_profile(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate Hermes profiles."""
    max_pts = WEIGHTS["profile"]
    hermes_home = Path.home() / ".hermes"
    profiles_dir = hermes_home / "profiles"

    score = 0
    parts: list[str] = []
    recommendations: list[str] = []

    # Check active profile
    active_file = hermes_home / "active_profile"
    active = "default"
    if active_file.exists():
        try:
            active = active_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    parts.append(f"활성 프로필: {active}")

    # Count profiles
    if profiles_dir.is_dir():
        profile_names = [d.name for d in profiles_dir.iterdir() if d.is_dir()]
        if len(profile_names) >= 3:
            score += 5
            parts.append(f"{len(profile_names)}개 프로필")
        elif len(profile_names) >= 1:
            score += 3
            parts.append(f"{len(profile_names)}개 프로필")
        else:
            recommendations.append("프로필을 생성하여 역할별 설정을 분리하세요")
    else:
        recommendations.append("프로필을 생성하여 역할별 설정을 분리하세요")

    # Check SOUL.md / AGENTS.md for active profile
    profile_home = profiles_dir / active if active != "default" and (profiles_dir / active).is_dir() else hermes_home
    has_soul = (profile_home / "SOUL.md").exists()
    has_agents = (profile_home / "AGENTS.md").exists()

    if has_soul:
        score += 5
        parts.append("SOUL.md 있음")
    else:
        recommendations.append("SOUL.md를 작성하여 에이전트 페르소나를 정의하세요")

    if has_agents:
        score += 5
        parts.append("AGENTS.md 있음")
    else:
        recommendations.append("AGENTS.md를 작성하여 에이전트 운영 규칙을 정의하세요")

    if not has_soul and not has_agents:
        score = 0
        parts.append("페르소나 파일 없음")

    return min(score, max_pts), max_pts, "; ".join(parts), recommendations


def _score_mcp_servers(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate MCP server configuration."""
    max_pts = WEIGHTS["mcp_servers"]
    mcp = _load_json(data_dir / "mcp_servers.json")

    if not isinstance(mcp, list):
        return 0, max_pts, "MCP 설정을 불러올 수 없음", ["MCP 서버를 추가하여 도구를 확장하세요"]

    count = len(mcp)
    score = min(count * 5, max_pts)  # 5 pts per server, capped at 15

    types: list[str] = []
    for s in mcp:
        if isinstance(s, dict):
            label = s.get("label", s.get("server_id", "unknown"))
            types.append(label)

    if count >= 3:
        detail = f"{count}개 MCP 서버 등록됨 ({', '.join(types[:3])}{'...' if len(types) > 3 else ''})"
    elif count >= 1:
        detail = f"{count}개 MCP 서버 등록됨 ({', '.join(types)})"
    else:
        detail = "MCP 서버 없음"

    recommendations: list[str] = []
    if count < 2:
        recommendations.append("Tools 확장을 위해 추가 MCP 서버를 등록하세요 (예: filesystem, playwright)")

    return score, max_pts, detail, recommendations


def _score_workspace(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate workspace configuration."""
    max_pts = WEIGHTS["workspace"]
    workspaces = _load_json(data_dir / "workspaces.json")

    if not isinstance(workspaces, list):
        return 0, max_pts, "워크스페이스 설정을 불러올 수 없음", []

    valid = 0
    invalid = 0
    for ws in workspaces:
        if isinstance(ws, dict):
            path = ws.get("path", "")
            if path and Path(path).is_dir():
                valid += 1
            else:
                invalid += 1

    score = min(valid * 5, max_pts)  # 5 pts per valid workspace, capped at 10

    parts = [f"{valid}개 유효한 워크스페이스"]
    if invalid:
        parts.append(f"{invalid}개 무효")
        invalid_detail = f"，{invalid}개 경로가 유효하지 않음"
    else:
        invalid_detail = ""

    detail = f"{valid}개 워크스페이스 등록됨{invalid_detail}"

    recommendations: list[str] = []
    if valid == 0:
        recommendations.append("작업할 워크스페이스를 등록하세요")

    return score, max_pts, detail, recommendations


def _score_integration(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate Slack/Notion integration status."""
    max_pts = WEIGHTS["integration"]
    integrations = _load_json(data_dir / "integration_config.json")

    if not isinstance(integrations, dict):
        return 0, max_pts, "통합 설정을 불러올 수 없음", []

    score = 0
    parts: list[str] = []

    slack = integrations.get("slack", {})
    if isinstance(slack, dict) and slack.get("enabled"):
        score += 5
        parts.append("Slack 연동 활성화")
    else:
        parts.append("Slack 미연동")

    notion = integrations.get("notion", {})
    if isinstance(notion, dict) and notion.get("enabled"):
        score += 5
        parts.append("Notion 연동 활성화")
    else:
        parts.append("Notion 미연동")

    detail = "; ".join(parts)
    recommendations: list[str] = []
    if score < max_pts:
        recommendations.append("Slack/Notion 연동을 설정하면 알림과 문서 연동이 가능합니다")

    return score, max_pts, detail, recommendations


def _score_skills(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate installed skills."""
    max_pts = WEIGHTS["skills"]
    skills_dir = data_dir.parent / "skills"

    if not skills_dir.is_dir():
        return 0, max_pts, "스킬 디렉터리를 찾을 수 없음", []

    # Count .md files (excluding roles/ subdirectory)
    root_skills = list(skills_dir.glob("*.md"))
    role_skills = list((skills_dir / "roles").glob("*.md")) if (skills_dir / "roles").is_dir() else []

    total = len(root_skills) + len(role_skills)
    # Check for custom skills (non-role, non-standard names)
    standard = {"taste", "taste_v1.1", "sherlock-qa", "self-reflection", "security",
                "bill-dev", "auto-documenter", "contract-validator", "html-anything",
                "notification-relay"}
    custom = [s.stem for s in root_skills if s.stem not in standard]

    score = min(total * 1, max_pts)  # 1 pt per skill, capped at 5
    if custom:
        score = min(score + 1, max_pts)

    detail = f"{total}개 스킬 설치됨"
    if custom:
        detail += f" (커스텀: {', '.join(custom[:3])})"

    recommendations: list[str] = []
    return score, max_pts, detail, recommendations


def _score_security(data_dir: Path) -> tuple[int, int, str, list[str]]:
    """Evaluate security settings."""
    max_pts = WEIGHTS["security"]
    settings = _load_json(data_dir / "settings.json")

    score = 0
    parts: list[str] = []
    recommendations: list[str] = []

    if not isinstance(settings, dict):
        return 0, max_pts, "보안 설정을 불러올 수 없음", []

    # Password / auth
    if settings.get("password_hash"):
        score += 3
        parts.append("비밀번호 설정됨")
    else:
        parts.append("비밀번호 미설정")
        recommendations.append("Settings에서 비밀번호를 설정하여 앱을 보호하세요")

    # API key masking — checked indirectly via providers
    providers = _load_json(data_dir / "custom_providers.json")
    masked_keys = 0
    unmasked_keys = 0
    for cfg in providers.get("providers", {}).values() if isinstance(providers, dict) else []:
        key = cfg.get("api_key", "")
        if key.startswith("••••"):
            masked_keys += 1
        elif key:
            unmasked_keys += 1

    if unmasked_keys == 0 and masked_keys > 0:
        score += 2
        parts.append("API 키 마스킹 양호")
    elif unmasked_keys > 0:
        parts.append(f"⚠ {unmasked_keys}개 API 키가 마스킹되지 않음")
    else:
        parts.append("API 키 없음")

    detail = "; ".join(parts)
    return score, max_pts, detail, recommendations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_config_score(data_dir: Optional[Path] = None) -> dict:
    """Run the full configuration evaluation.

    Args:
        data_dir: Path to the data/ directory.  Auto-detected if None.

    Returns:
        Dict with: total_score, grade, grade_emoji, categories, recommendations
    """
    if data_dir is None:
        data_dir = _resolve_data_dir()

    scorers = [
        ("api_key", _score_api_key),
        ("model_setup", _score_model_setup),
        ("profile", _score_profile),
        ("mcp_servers", _score_mcp_servers),
        ("workspace", _score_workspace),
        ("integration", _score_integration),
        ("skills", _score_skills),
        ("security", _score_security),
    ]

    categories: list[dict] = []
    total_score = 0
    all_recommendations: list[str] = []

    category_labels = {
        "api_key": "API 키",
        "model_setup": "모델 설정",
        "profile": "프로필",
        "mcp_servers": "MCP 서버",
        "workspace": "워크스페이스",
        "integration": "통합",
        "skills": "스킬",
        "security": "보안",
    }

    for key, scorer in scorers:
        score, max_pts, detail, recs = scorer(data_dir)
        total_score += score

        # Determine per-category status
        pct = score / max_pts if max_pts > 0 else 0
        if pct >= 0.8:
            status = "good"
        elif pct >= 0.4:
            status = "warning"
        else:
            status = "danger"

        categories.append({
            "key": key,
            "name": category_labels.get(key, key),
            "score": score,
            "max": max_pts,
            "percentage": round(pct * 100),
            "status": status,
            "detail": detail,
        })
        all_recommendations.extend(recs)

    # Determine grade
    grade = "D"
    grade_emoji = "🔴"
    for threshold, g, emoji in GRADES:
        if total_score >= threshold:
            grade = g
            grade_emoji = emoji
            break

    return {
        "total_score": total_score,
        "max_score": 100,
        "grade": grade,
        "grade_emoji": grade_emoji,
        "categories": categories,
        "recommendations": all_recommendations[:6],  # top 6 only
        "recommendation_count": len(all_recommendations),
    }


# ---------------------------------------------------------------------------
# CLI test entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    result = evaluate_config_score()
    print(json.dumps(result, ensure_ascii=False, indent=2))
