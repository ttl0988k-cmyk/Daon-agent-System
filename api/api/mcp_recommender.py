"""
MCP Auto-Recommendation Engine for Daon Agent System.

Scans workspace file patterns and recommends MCP servers based on
detected technologies, frameworks, and project structure.
"""
import os
import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ── Detection rule: (check_fn, mcp_preset_id, label, reason_template, confidence) ──
# check_fn receives the structure dict and returns bool
# If mcp_preset_id is None, it's a custom suggestion (not in built-in presets)


def _has_github_workflows(structure: dict) -> bool:
    """Check if .github/workflows directory exists."""
    ws_root = structure.get("workspace_root", "")
    workflows_dir = os.path.join(ws_root, ".github", "workflows")
    return os.path.isdir(workflows_dir)


def _has_docker(structure: dict) -> bool:
    """Check for Dockerfile or docker-compose files."""
    all_files = _all_files(structure)
    for f in all_files:
        if f.lower() in ("dockerfile", "docker-compose.yml", "docker-compose.yaml"):
            return True
        if f.lower().startswith("dockerfile.") or f.lower().startswith("docker-compose."):
            return True
    return False


def _has_db_files(structure: dict) -> bool:
    """Check for SQLite/database files."""
    all_files = _all_files(structure)
    for f in all_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in (".db", ".sqlite", ".sqlite3"):
            return True
    return False


def _has_web_framework(structure: dict) -> bool:
    """Check for web frontend frameworks (React/Vue/Next/Nuxt/Svelte)."""
    # Check package.json content
    ws_root = structure.get("workspace_root", "")
    pkg_json = os.path.join(ws_root, "package.json")
    if os.path.isfile(pkg_json):
        try:
            import json
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            web_frameworks = {"react", "vue", "next", "nuxt", "svelte", "sveltekit",
                              "angular", "@angular/core", "solid-js", "preact", "astro"}
            if web_frameworks & set(dep.lower() for dep in deps):
                return True
            # Check for Playwright itself
            if "playwright" in str(deps).lower() or "@playwright/test" in str(deps):
                return True
        except Exception:
            pass
    return False


def _has_many_md(structure: dict) -> bool:
    """Check if there are many markdown files (knowledge-base pattern)."""
    cats = structure.get("categories", {})
    md_files = cats.get("markdown", [])
    return len(md_files) >= 10


def _has_complex_structure(structure: dict) -> bool:
    """Check if the project has many source files (complex project)."""
    cats = structure.get("categories", {})
    total = structure.get("total_files", 0)
    # Complex if >50 source files (py, js, ts combined)
    source_count = (len(cats.get("python", [])) +
                    len(cats.get("javascript", [])) +
                    len(cats.get("typescript", [])))
    return total > 100 or source_count > 50


def _has_python_web(structure: dict) -> bool:
    """Check for Python web frameworks (FastAPI/Flask/Django)."""
    ws_root = structure.get("workspace_root", "")
    req_txt = os.path.join(ws_root, "requirements.txt")
    if os.path.isfile(req_txt):
        try:
            with open(req_txt, "r", encoding="utf-8") as f:
                content = f.read().lower()
            py_web = {"fastapi", "flask", "django", "starlette", "aiohttp", "sanic"}
            for fw in py_web:
                if fw in content:
                    return True
        except Exception:
            pass
    return False


def _has_proto_files(structure: dict) -> bool:
    """Check for protobuf files."""
    all_files = _all_files(structure)
    for f in all_files:
        if f.endswith(".proto"):
            return True
    return False


def _has_postgres_ref(structure: dict) -> bool:
    """Check for PostgreSQL references."""
    all_files = _all_files(structure)
    for f in all_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in (".env", ".yml", ".yaml", ".toml", ".py", ".js", ".ts", ".json", ".sql", ".md"):
            # Only check certain files to avoid scanning everything
            pass
    # Check docker-compose for postgres
    ws_root = structure.get("workspace_root", "")
    for dc_name in ("docker-compose.yml", "docker-compose.yaml"):
        dc_path = os.path.join(ws_root, dc_name)
        if os.path.isfile(dc_path):
            try:
                with open(dc_path, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                if "postgres" in content or "postgresql" in content:
                    return True
            except Exception:
                pass
    # Check requirements.txt for psycopg2/asyncpg
    req_txt = os.path.join(ws_root, "requirements.txt")
    if os.path.isfile(req_txt):
        try:
            with open(req_txt, "r", encoding="utf-8") as f:
                content = f.read().lower()
            if "psycopg2" in content or "asyncpg" in content:
                return True
        except Exception:
            pass
    return False


def _all_files(structure: dict) -> list:
    """Flatten all file paths from structure categories."""
    cats = structure.get("categories", {})
    all_f = []
    for files in cats.values():
        all_f.extend(files)
    return all_f


# ── Detection Rule Table ──────────────────────────────────────────────────────
# Each rule: (check_function, mcp_preset_id_or_None, label, reason, confidence)
# confidence: "high" | "medium" | "low"

DETECTION_RULES = [
    # GitHub MCP
    (_has_github_workflows, "github",
     "🐙 깃허브(GitHub) MCP",
     "GitHub Actions 워크플로우(.github/workflows/)가 감지되었습니다",
     "high"),

    # Playwright MCP
    (_has_web_framework, "playwright",
     "🎭 플레이라이트(Playwright) MCP",
     "웹 프론트엔드 프레임워크(React/Vue/Next 등)가 감지되었습니다",
     "high"),

    # Memory MCP
    (_has_many_md, "memory",
     "🧠 메모리(Memory) MCP",
     "마크다운 문서가 10개 이상 발견되었습니다 — 지식 그래프 기반 메모리 저장소 추천",
     "medium"),

    # Sequential Thinking MCP
    (_has_complex_structure, "sequential_thinking",
     "🤔 순차적 사고(Sequential Thinking) MCP",
     "복잡한 프로젝트 구조 (대규모 소스 파일) — 단계적 추론 도구 추천",
     "medium"),

    # Filesystem MCP (baseline — always useful for any project)
    (lambda s: True, "filesystem",
     "📁 파일 시스템 MCP",
     "모든 프로젝트에 유용한 파일 시스템 접근 도구",
     "low"),

    # ── Custom suggestions (not in built-in presets) ──

    # Docker
    (_has_docker, None,
     "🐳 Docker MCP",
     "Dockerfile 또는 docker-compose.yml이 감지되었습니다",
     "high"),

    # SQLite
    (_has_db_files, None,
     "🗄️ SQLite MCP",
     "SQLite 데이터베이스 파일(.db/.sqlite)이 감지되었습니다",
     "high"),

    # PostgreSQL (via docker-compose)
    (_has_postgres_ref, None,
     "🐘 PostgreSQL MCP",
     "docker-compose에서 PostgreSQL 참조가 감지되었습니다",
     "medium"),

    # Python web (FastAPI/Flask) → Playwright for testing
    (_has_python_web, "playwright",
     "🎭 플레이라이트(Playwright) MCP",
     "Python 웹 프레임워크(FastAPI/Flask/Django)가 감지되었습니다 — E2E 테스트 자동화 추천",
     "medium"),

    # Protobuf → GitHub for API management
    (_has_proto_files, "github",
     "🐙 깃허브(GitHub) MCP",
     "Protocol Buffer(.proto) 파일이 감지되었습니다 — API 관리에 GitHub MCP 추천",
     "low"),
]


def _deduplicate(recommendations: list[dict]) -> list[dict]:
    """Remove duplicate recommendations, keeping the highest confidence one."""
    seen = {}
    for rec in recommendations:
        key = rec["mcp_id"]
        if key in seen:
            # Keep the higher confidence
            confidence_order = {"high": 3, "medium": 2, "low": 1}
            if confidence_order.get(rec["confidence"], 0) > confidence_order.get(seen[key]["confidence"], 0):
                seen[key]["confidence"] = rec["confidence"]
                seen[key]["reason"] = rec["reason"]  # Use the more confident reason
        else:
            seen[key] = rec
    return list(seen.values())


def recommend_mcp_servers(workspace_path: str, existing_server_ids: list[str] | None = None) -> dict:
    """
    Scan a workspace and recommend MCP servers.

    Args:
        workspace_path: Path to the project workspace
        existing_server_ids: List of already-installed MCP server IDs

    Returns:
        dict with:
            recommendations: [{mcp_id, label, reason, confidence, preset, already_installed}]
            workspace_summary: {total_files, categories_summary}
    """
    from pathlib import Path as _Path
    ws = _Path(workspace_path).resolve()

    if not ws.exists() or not ws.is_dir():
        return {"error": f"Workspace not found: {workspace_path}", "recommendations": []}

    existing = set(existing_server_ids or [])

    # Discover project structure (reuse pattern from setup_generator)
    structure = _discover_for_recommend(ws)

    # Extract preset configurations from MCP_PRESETS
    try:
        from api.mcp_client import MCP_PRESETS
    except ImportError:
        MCP_PRESETS = {}

    recommendations = []

    for check_fn, preset_id, label, reason_template, confidence in DETECTION_RULES:
        try:
            if check_fn(structure):
                rec = {
                    "mcp_id": preset_id if preset_id else _label_to_id(label),
                    "label": label,
                    "reason": reason_template,
                    "confidence": confidence,
                    "already_installed": (preset_id in existing) if preset_id else False,
                }
                # Attach preset config if available
                if preset_id and preset_id in MCP_PRESETS:
                    rec["preset"] = {
                        "command": MCP_PRESETS[preset_id]["command"],
                        "args": MCP_PRESETS[preset_id]["args"],
                        "description": MCP_PRESETS[preset_id].get("description", ""),
                    }
                elif preset_id:
                    rec["preset"] = None  # Known ID but no preset loaded
                else:
                    # Custom suggestion — provide install hint
                    rec["preset"] = None
                    rec["install_hint"] = _get_install_hint(rec["mcp_id"])

                recommendations.append(rec)
        except Exception as e:
            _logger.debug("MCP detection rule failed: %s", e)
            continue

    # Deduplicate
    recommendations = _deduplicate(recommendations)

    # Sort by confidence (high → medium → low), then already_installed last
    conf_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: (conf_order.get(r["confidence"], 9), r["already_installed"]))

    # Build workspace summary
    cats = structure.get("categories", {})
    summary = {
        "total_files": structure.get("total_files", 0),
        "categories": {k: len(v) for k, v in cats.items()},
    }

    return {
        "recommendations": recommendations,
        "workspace_summary": summary,
    }


def _discover_for_recommend(workspace: Path) -> dict:
    """Lightweight project structure discovery for MCP recommendation."""
    categories = {
        "python": [], "javascript": [], "typescript": [], "config": [],
        "markdown": [], "html": [], "css": [], "data": [], "other": [],
    }
    ext_map = {
        ".py": "python", ".pyx": "python", ".pxd": "python",
        ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
        ".yaml": "config", ".yml": "config", ".toml": "config",
        ".json": "config", ".env": "config", ".cfg": "config",
        ".md": "markdown", ".mdx": "markdown", ".rst": "markdown",
        ".html": "html", ".htm": "html",
        ".css": "css", ".scss": "css", ".less": "css",
        ".csv": "data", ".sqlite": "data", ".db": "data",
        ".txt": "data", ".log": "data",
        ".go": "other", ".rs": "other", ".java": "other",
        ".c": "other", ".cpp": "other", ".h": "other",
    }
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv',
                 'build', 'dist', 'dist_new', '.idea', '.vscode', 'data',
                 '.next', '.nuxt', '__MACOSX', 'build_temp'}

    total_files = 0
    for root, dirs, files in os.walk(str(workspace)):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith('.')]
        rel_root = Path(root).relative_to(workspace)
        for fname in files:
            total_files += 1
            ext = Path(fname).suffix.lower()
            cat = ext_map.get(ext, "other")
            rel_path = str(rel_root / fname) if str(rel_root) != '.' else fname
            categories[cat].append(rel_path)

            # Cap at reasonable limit to avoid memory issues on huge projects
            if total_files > 5000:
                break
        if total_files > 5000:
            break

    return {
        "total_files": total_files,
        "categories": {k: v for k, v in categories.items() if v},
        "workspace_root": str(workspace),
    }


def _label_to_id(label: str) -> str:
    """Convert a label like '🐳 Docker MCP' to an ID like 'docker'."""
    # Extract the text after the emoji
    import re
    # Remove emoji and trailing " MCP"
    cleaned = re.sub(r'[^\w\s]', '', label.split()[-1] if ' ' in label else label)
    if cleaned.upper() == 'MCP':
        # Take the word before MCP
        parts = label.split()
        for i, p in enumerate(parts):
            if p.upper() == 'MCP' and i > 0:
                cleaned = parts[i - 1]
                break
    # Remove non-alpha prefix (emoji parts)
    cleaned = re.sub(r'^[^a-zA-Z]+', '', cleaned)
    return cleaned.lower().strip()


def _get_install_hint(mcp_id: str) -> dict:
    """Get install hint for custom (non-preset) MCP servers."""
    hints = {
        "docker": {
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-docker"],
            "note": "npm registry에서 @anthropic/mcp-server-docker를 설치합니다",
        },
        "sqlite": {
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-sqlite", "data/"],
            "note": "SQLite MCP 서버 — data/ 디렉토리를 지정하세요",
        },
        "postgresql": {
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-postgres", "postgresql://localhost:5432/mydb"],
            "note": "PostgreSQL 연결 문자열을 환경에 맞게 수정하세요",
        },
    }
    return hints.get(mcp_id)
