"""
Community Skills Auto-Recommendation Engine for Daon Agent System.

Scans workspace file patterns and searches community skill hubs (GitHub, ClawHub,
skills.sh, etc.) for relevant skills based on detected technologies and frameworks.

Pattern: same as api/mcp_recommender.py — detect → query → recommend.
"""

import os
import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


# ── Detection helpers (reuse pattern from mcp_recommender.py) ──────────────────


def _all_files(structure: dict) -> list:
    """Flatten all file paths from structure categories."""
    cats = structure.get("categories", {})
    all_f = []
    for files in cats.values():
        all_f.extend(files)
    return all_f


def _has_tech(structure: dict, keyword: str, check_deps: bool = True) -> bool:
    """Check if a technology keyword appears in dependency files."""
    ws_root = structure.get("workspace_root", "")
    # Check package.json
    pkg_json = os.path.join(ws_root, "package.json")
    if check_deps and os.path.isfile(pkg_json):
        try:
            import json
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for dep in all_deps:
                if keyword.lower() in dep.lower():
                    return True
        except Exception:
            pass
    # Check requirements.txt
    req_txt = os.path.join(ws_root, "requirements.txt")
    if check_deps and os.path.isfile(req_txt):
        try:
            with open(req_txt, "r", encoding="utf-8") as f:
                content = f.read().lower()
            if keyword.lower() in content:
                return True
        except Exception:
            pass
    # Check file extensions
    all_files = _all_files(structure)
    for f in all_files:
        if keyword.lower() in f.lower():
            return True
    return False


def _has_docker(structure: dict) -> bool:
    all_files = _all_files(structure)
    for f in all_files:
        fn = f.lower()
        if fn in ("dockerfile", "docker-compose.yml", "docker-compose.yaml"):
            return True
        if fn.startswith("dockerfile.") or fn.startswith("docker-compose."):
            return True
    return False


def _has_kubernetes(structure: dict) -> bool:
    all_files = _all_files(structure)
    for f in all_files:
        fn = f.lower()
        if fn.endswith((".yaml", ".yml")):
            if any(kw in fn for kw in ("deployment", "service", "ingress", "kustomization")):
                return True
    # Check for helm charts
    ws_root = structure.get("workspace_root", "")
    for check_dir in ("charts", "helm"):
        chart_path = os.path.join(ws_root, check_dir)
        if os.path.isdir(chart_path):
            return True
    return False


def _has_frontend_framework(structure: dict) -> Optional[str]:
    """Detect frontend framework and return its name."""
    ws_root = structure.get("workspace_root", "")
    pkg_json = os.path.join(ws_root, "package.json")
    if os.path.isfile(pkg_json):
        try:
            import json
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            dep_names = {d.lower() for d in all_deps}
            frameworks = {
                ("next",): "nextjs",
                ("react",): "react",
                ("vue",): "vue",
                ("svelte", "sveltekit"): "svelte",
                ("angular", "@angular/core"): "angular",
                ("astro",): "astro",
                ("nuxt",): "nuxt",
                ("solid-js",): "solidjs",
            }
            for keys, name in frameworks.items():
                if any(k in dep_names for k in keys):
                    return name
        except Exception:
            pass
    return None


def _has_python_framework(structure: dict) -> Optional[str]:
    """Detect Python web framework and return its name."""
    ws_root = structure.get("workspace_root", "")
    req_txt = os.path.join(ws_root, "requirements.txt")
    if os.path.isfile(req_txt):
        try:
            with open(req_txt, "r", encoding="utf-8") as f:
                content = f.read().lower()
            frameworks = {
                "fastapi": "fastapi",
                "flask": "flask",
                "django": "django",
                "starlette": "starlette",
                "aiohttp": "aiohttp",
                "sanic": "sanic",
            }
            for fw, name in frameworks.items():
                if fw in content:
                    return name
        except Exception:
            pass
    return None


def _has_github_actions(structure: dict) -> bool:
    ws_root = structure.get("workspace_root", "")
    workflows_dir = os.path.join(ws_root, ".github", "workflows")
    return os.path.isdir(workflows_dir)


def _has_tests(structure: dict) -> bool:
    """Check if project has test files."""
    all_files = _all_files(structure)
    test_patterns = ("test_", "_test", "tests/", "__tests__/", ".spec.", ".test.")
    for f in all_files:
        fl = f.lower()
        if any(p in fl for p in test_patterns):
            return True
    return False


def _has_database(structure: dict) -> Optional[str]:
    """Detect database usage."""
    ws_root = structure.get("workspace_root", "")
    req_txt = os.path.join(ws_root, "requirements.txt")
    if os.path.isfile(req_txt):
        try:
            with open(req_txt, "r", encoding="utf-8") as f:
                content = f.read().lower()
            dbs = ["sqlalchemy", "psycopg2", "asyncpg", "pymongo", "redis", "sqlite3",
                   "prisma", "tortoise-orm", "peewee", "pony"]
            for db in dbs:
                if db in content:
                    return db
        except Exception:
            pass
    all_files = _all_files(structure)
    for f in all_files:
        ext = os.path.splitext(f)[1].lower()
        if ext in (".db", ".sqlite", ".sqlite3"):
            return "sqlite"
    return None


def _has_ci_cd(structure: dict) -> bool:
    """Detect CI/CD config files."""
    all_files = _all_files(structure)
    ci_files = (".github/workflows", ".gitlab-ci.yml", "jenkinsfile",
                ".circleci", ".travis.yml", "azure-pipelines.yml")
    for f in all_files:
        fl = f.lower()
        if any(c in fl for c in ci_files):
            return True
    return False


def _python_has_typing(structure: dict) -> bool:
    """Check if Python project uses type hints extensively."""
    ws_root = structure.get("workspace_root", "")
    py_files = structure.get("categories", {}).get("python", [])
    # Quick check: look for pyproject.toml or setup.cfg with mypy/pyright config
    for cfg in ("pyproject.toml", "setup.cfg", "mypy.ini"):
        cfg_path = os.path.join(ws_root, cfg)
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                if any(t in content for t in ("mypy", "pyright", "pytype", "type: ignore")):
                    return True
            except Exception:
                pass
    return False


# ── Skill Recommendation Query Table ──────────────────────────────────────────
# Each rule: (check_fn, search_query, label, reason, confidence, source_filter)
# search_query is the query sent to skills hub
# source_filter limits which hubs to search (None = all)

SKILL_DETECTION_RULES = [
    # ── Docker / Container ──
    (_has_docker, "docker container", "🐳 Docker 도구",
     "Dockerfile이 감지되었습니다 — 컨테이너 관리 스킬을 추천합니다",
     "high", None),

    # ── Kubernetes ──
    (_has_kubernetes, "kubernetes helm k8s", "☸️ Kubernetes 도구",
     "Kubernetes 매니페스트가 감지되었습니다 — K8s 관리 스킬을 추천합니다",
     "high", None),

    # ── Frontend frameworks ──
    (lambda s: _has_frontend_framework(s) == "react",
     "react component", "⚛️ React 도구",
     "React 프로젝트가 감지되었습니다 — React 컴포넌트/훅 스킬을 추천합니다",
     "high", None),

    (lambda s: _has_frontend_framework(s) == "nextjs",
     "nextjs vercel", "▲ Next.js 도구",
     "Next.js 프로젝트가 감지되었습니다 — Next.js 최적화 스킬을 추천합니다",
     "high", None),

    (lambda s: _has_frontend_framework(s) == "vue",
     "vue composable", "💚 Vue 도구",
     "Vue.js 프로젝트가 감지되었습니다 — Vue 컴포저블/컴포넌트 스킬을 추천합니다",
     "high", None),

    (lambda s: _has_frontend_framework(s) in ("svelte", "angular", "astro", "nuxt", "solidjs"),
     "frontend web component", "🎨 프론트엔드 도구",
     "프론트엔드 프레임워크가 감지되었습니다 — 웹 개발 스킬을 추천합니다",
     "medium", None),

    # ── Python frameworks ──
    (lambda s: _has_python_framework(s) == "fastapi",
     "fastapi api server", "🚀 FastAPI 도구",
     "FastAPI 프로젝트가 감지되었습니다 — API 개발 스킬을 추천합니다",
     "high", None),

    (lambda s: _has_python_framework(s) == "django",
     "django orm admin", "🎸 Django 도구",
     "Django 프로젝트가 감지되었습니다 — Django ORM/Admin 스킬을 추천합니다",
     "high", None),

    (lambda s: _has_python_framework(s) == "flask",
     "flask web app", "🍶 Flask 도구",
     "Flask 프로젝트가 감지되었습니다 — Flask 웹앱 스킬을 추천합니다",
     "high", None),

    # ── Python general ──
    (lambda s: bool(s.get("categories", {}).get("python")) and _has_tests(s),
     "python testing pytest", "🧪 Python 테스트 도구",
     "Python 테스트 파일이 감지되었습니다 — pytest/unittest 스킬을 추천합니다",
     "medium", None),

    (_python_has_typing, "python type checking mypy", "🔍 Python 타입 체킹",
     "mypy/pyright 설정이 감지되었습니다 — 타입 체킹 스킬을 추천합니다",
     "medium", None),

    (lambda s: bool(s.get("categories", {}).get("python")),
     "python development tool", "🐍 Python 개발 도구",
     "Python 프로젝트입니다 — Python 개발 생산성 스킬을 추천합니다",
     "low", None),

    # ── Database ──
    (lambda s: _has_database(s) is not None,
     "database sql orm", "🗄️ 데이터베이스 도구",
     "데이터베이스 사용이 감지되었습니다 — DB 관리/ORM 스킬을 추천합니다",
     "high", None),

    # ── CI/CD ──
    (_has_ci_cd, "ci cd pipeline automation", "🔄 CI/CD 자동화",
     "CI/CD 파이프라인이 감지되었습니다 — 자동화 스킬을 추천합니다",
     "medium", None),

    (_has_github_actions, "github actions workflow", "🐙 GitHub Actions",
     "GitHub Actions 워크플로우가 감지되었습니다 — Actions 스킬을 추천합니다",
     "high", None),

    # ── General (always recommended) ──
    (lambda s: True, "documentation code review", "📝 코드 품질 도구",
     "모든 프로젝트에 유용한 문서화/코드리뷰 스킬을 추천합니다",
     "low", None),
]


def _discover_for_skills(workspace: Path) -> dict:
    """Lightweight project structure discovery (same pattern as mcp_recommender)."""
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
            if total_files > 5000:
                break
        if total_files > 5000:
            break

    return {
        "total_files": total_files,
        "categories": {k: v for k, v in categories.items() if v},
        "workspace_root": str(workspace),
    }


def _deduplicate_by_name(recommendations: list[dict]) -> list[dict]:
    """Remove duplicate skill recommendations by name, keeping highest confidence."""
    seen = {}
    for rec in recommendations:
        name = rec.get("name", "")
        if name in seen:
            confidence_order = {"high": 3, "medium": 2, "low": 1}
            if confidence_order.get(rec.get("confidence", "low"), 0) > confidence_order.get(seen[name].get("confidence", "low"), 0):
                seen[name] = rec
        else:
            seen[name] = rec
    return list(seen.values())


def recommend_skills(workspace_path: str, max_per_query: int = 5) -> dict:
    """
    Scan workspace and recommend community skills based on detected technologies.

    Args:
        workspace_path: Path to the project workspace
        max_per_query: Max results per search query

    Returns:
        dict with:
            recommendations: [{name, identifier, description, source, trust_level,
                               tags, score, reason, search_query, confidence}]
            workspace_summary: {total_files, categories_summary}
            queries_made: [list of search queries used]
    """
    ws = Path(workspace_path).resolve()

    if not ws.exists() or not ws.is_dir():
        return {"error": f"Workspace not found: {workspace_path}", "recommendations": []}

    # Discover project structure
    structure = _discover_for_skills(ws)

    # Lazy-import skills hub
    try:
        hub_path = Path(__file__).parent.parent / "hermes-agent" / "tools"
        import sys
        if str(hub_path) not in sys.path:
            sys.path.insert(0, str(hub_path))
        from skills_hub import SkillHub
        hub = SkillHub()
    except ImportError as e:
        _logger.warning("Skills hub not available for auto-recommend: %s", e)
        return {
            "recommendations": [],
            "workspace_summary": {},
            "queries_made": [],
            "error": "Skills hub module not available",
        }

    # Collect unique queries from matching rules
    queries_to_run = {}  # query -> {"label": ..., "reason": ..., "confidence": ...}
    for check_fn, search_query, label, reason, confidence, source_filter in SKILL_DETECTION_RULES:
        try:
            if check_fn(structure):
                if search_query not in queries_to_run:
                    queries_to_run[search_query] = []
                queries_to_run[search_query].append({
                    "label": label,
                    "reason": reason,
                    "confidence": confidence,
                    "source_filter": source_filter,
                })
        except Exception as e:
            _logger.debug("Skill detection rule failed: %s", e)
            continue

    # Execute searches and collect results
    all_recommendations = []
    for query, meta_list in queries_to_run.items():
        try:
            results = hub.search(query, limit=max_per_query)
            for r in results:
                # Extract skill data (handle both SkillMeta objects and dicts)
                name = r.name if hasattr(r, "name") else r.get("name", "")
                identifier = r.identifier if hasattr(r, "identifier") else r.get("identifier", "")
                description = r.description if hasattr(r, "description") else r.get("description", "")
                source = r.source if hasattr(r, "source") else r.get("source", "")
                trust_level = r.trust_level if hasattr(r, "trust_level") else r.get("trust_level", "community")
                tags = r.tags if hasattr(r, "tags") else r.get("tags", [])
                score = getattr(r, "score", 0) if hasattr(r, "score") else r.get("score", 0)
                author = getattr(r, "author", "") if hasattr(r, "author") else r.get("author", "")
                version = getattr(r, "version", "") if hasattr(r, "version") else r.get("version", "")

                for meta in meta_list:
                    all_recommendations.append({
                        "name": name,
                        "identifier": identifier,
                        "description": description,
                        "source": source,
                        "trust_level": trust_level,
                        "tags": tags,
                        "score": score,
                        "author": author,
                        "version": version,
                        "label": meta["label"],
                        "reason": meta["reason"],
                        "search_query": query,
                        "confidence": meta["confidence"],
                    })
        except Exception as e:
            _logger.debug("Skills hub search failed for query '%s': %s", query, e)

    # Deduplicate by name
    recommendations = _deduplicate_by_name(all_recommendations)

    # Sort by confidence (high → medium → low), then by score (descending)
    conf_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: (conf_order.get(r["confidence"], 9), -r.get("score", 0)))

    # Limit total recommendations
    recommendations = recommendations[:20]

    # Build workspace summary
    cats = structure.get("categories", {})
    summary = {
        "total_files": structure.get("total_files", 0),
        "categories": {k: len(v) for k, v in cats.items()},
    }

    return {
        "recommendations": recommendations,
        "workspace_summary": summary,
        "queries_made": list(queries_to_run.keys()),
        "total": len(recommendations),
    }
