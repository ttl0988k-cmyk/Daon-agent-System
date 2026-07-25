"""
AI Setup Generator — auto-generates AI coding tool configuration files
from workspace project structure analysis.

Generated files:
  1. AGENTS.md           — Hermes, Cursor, Copilot project overview + conventions
  2. CLAUDE.md           — Claude Code specific instructions
  3. .cursor/rules        — Cursor rule file
  4. .github/copilot-instructions.md — GitHub Copilot instructions

Reuses _discover_project_structure() from api/routes/docs_routes.py for
workspace scanning.
"""

import logging
import os
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project structure discovery (adapted from docs_routes.py)
# ---------------------------------------------------------------------------

def _discover_project_structure(workspace: Path) -> dict:
    """Walk the workspace and categorize files by type."""
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
                 '.next', '.nuxt', '__MACOSX'}

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

    return {
        "total_files": total_files,
        "categories": {k: v for k, v in categories.items() if v},
        "workspace_root": str(workspace),
    }


# ---------------------------------------------------------------------------
# Tech stack detection
# ---------------------------------------------------------------------------

def _detect_tech_stack(workspace: Path, structure: dict) -> dict:
    """Detect language, framework, package manager and build tools."""
    result = {
        "primary_language": "unknown",
        "languages": [],
        "framework": None,
        "package_manager": None,
        "test_command": None,
        "build_command": None,
        "lint_command": None,
        "config_files": [],
        "has_docker": False,
        "has_git": False,
        "has_github_actions": False,
    }

    cats = structure.get("categories", {})

    # Primary language detection
    py_count = len(cats.get("python", []))
    js_count = len(cats.get("javascript", [])) + len(cats.get("typescript", []))
    ts_count = len(cats.get("typescript", []))

    if py_count > js_count:
        result["primary_language"] = "Python"
        result["languages"].append("Python")
        if js_count > 0:
            result["languages"].append("JavaScript/TypeScript")
    elif js_count > py_count:
        if ts_count > len(cats.get("javascript", [])) // 2:
            result["primary_language"] = "TypeScript"
        else:
            result["primary_language"] = "JavaScript"
        result["languages"].append("JavaScript/TypeScript")
        if py_count > 0:
            result["languages"].append("Python")
    else:
        # Check for other languages
        for go_file in cats.get("other", []):
            if go_file.endswith(".go"):
                result["primary_language"] = "Go"
                result["languages"].append("Go")
                break

    if not result["languages"]:
        result["languages"].append("Generic")

    # Framework detection
    py_files_lower = " ".join(cats.get("python", [])).lower()
    js_files_lower = " ".join(cats.get("javascript", []) + cats.get("typescript", [])).lower()

    frameworks = []

    # Python frameworks
    if "fastapi" in py_files_lower or "starlette" in py_files_lower:
        frameworks.append("FastAPI")
        result["test_command"] = "pytest"
    elif "flask" in py_files_lower:
        frameworks.append("Flask")
        result["test_command"] = "pytest"
    elif "django" in py_files_lower:
        frameworks.append("Django")
        result["test_command"] = "python manage.py test"

    # JS frameworks
    if "next" in js_files_lower or (workspace / "next.config.js").exists() or (workspace / "next.config.ts").exists():
        frameworks.append("Next.js")
    elif "react" in js_files_lower:
        frameworks.append("React")
    elif "vue" in js_files_lower:
        frameworks.append("Vue.js")
    elif "svelte" in js_files_lower:
        frameworks.append("Svelte")
    elif "angular" in js_files_lower:
        frameworks.append("Angular")

    if frameworks:
        result["framework"] = " + ".join(frameworks)
    elif "electron" in js_files_lower:
        result["framework"] = "Electron"

    # Package manager
    if (workspace / "package.json").exists():
        # Check lock files
        if (workspace / "pnpm-lock.yaml").exists():
            result["package_manager"] = "pnpm"
        elif (workspace / "yarn.lock").exists():
            result["package_manager"] = "yarn"
        elif (workspace / "bun.lockb").exists() or (workspace / "bun.lock").exists():
            result["package_manager"] = "bun"
        else:
            result["package_manager"] = "npm"
        result["build_command"] = f"{result['package_manager']} run build"
        if not result["test_command"]:
            result["test_command"] = f"{result['package_manager']} test"
        if not result["lint_command"]:
            result["lint_command"] = f"{result['package_manager']} run lint"

    if (workspace / "requirements.txt").exists() or (workspace / "pyproject.toml").exists():
        if not result["package_manager"]:
            if (workspace / "pyproject.toml").exists():
                result["package_manager"] = "pip (pyproject.toml)"
            else:
                result["package_manager"] = "pip"
        if not result["test_command"]:
            result["test_command"] = "pytest"
        if not result["build_command"]:
            # check for PyInstaller
            if (workspace / "server.spec").exists():
                result["build_command"] = "pyinstaller server.spec"

    # Config files
    for cfg in ["config.yaml", "config.yml", ".env.example", ".env",
                "package.json", "pyproject.toml", "tsconfig.json",
                "server.spec", "Makefile", "docker-compose.yml",
                "Dockerfile", ".editorconfig"]:
        if (workspace / cfg).exists():
            result["config_files"].append(cfg)

    # Docker
    if (workspace / "Dockerfile").exists() or (workspace / "docker-compose.yml").exists():
        result["has_docker"] = True

    # Git
    if (workspace / ".git").is_dir():
        result["has_git"] = True

    # GitHub Actions
    github_actions_dir = workspace / ".github" / "workflows"
    if github_actions_dir.is_dir() and list(github_actions_dir.glob("*.yml")):
        result["has_github_actions"] = True

    return result


# ---------------------------------------------------------------------------
# Directory tree generator
# ---------------------------------------------------------------------------

def _generate_tree(workspace: Path, max_depth: int = 3, max_items: int = 80) -> str:
    """Generate a simple directory tree."""
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv',
                 'build', 'dist', 'dist_new', '.idea', '.vscode', 'data',
                 '.next', '.nuxt', '__MACOSX', '.hermes', 'skills'}

    lines = [f"```\n{workspace.name}/"]

    def walk(dir_path: Path, prefix: str, depth: int):
        if depth > max_depth or len(lines) > max_items:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in skip_dirs]
        files = [e for e in entries if e.is_file() and not e.name.startswith('.')]

        total = dirs + files
        for i, entry in enumerate(total):
            is_last = (i == len(total) - 1)
            connector = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                walk(entry, next_prefix, depth + 1)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

            if len(lines) > max_items:
                lines.append(f"{next_prefix}...")
                return

    walk(workspace, "", 1)
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Coding conventions by language
# ---------------------------------------------------------------------------

LANGUAGE_CONVENTIONS = {
    "Python": [
        "PEP 8 스타일 가이드를 따릅니다 (4-space indentation)",
        "Type hints를 적극적으로 사용합니다 (`from typing import ...`)",
        "Docstring은 Google 또는 NumPy 스타일로 작성합니다",
        "`black`, `ruff`, `mypy`를 코드 품질 도구로 사용합니다",
        "환경변수는 `.env` 파일에서 관리하고 `python-dotenv`로 로드합니다",
    ],
    "JavaScript": [
        "ESLint + Prettier로 코드 스타일을 관리합니다",
        "async/await를 사용하고 callback 중첩을 피합니다",
        "ESM 모듈 시스템 (`import`/`export`)을 사용합니다",
        "변수명은 camelCase, 상수는 UPPER_SNAKE_CASE를 사용합니다",
    ],
    "TypeScript": [
        "Strict mode를 활성화합니다 (`tsconfig.json`에서 `strict: true`)",
        "`interface`보다 `type`을 우선 사용합니다 (필요시 interface)",
        "any 타입 사용을 지양하고 제네릭을 활용합니다",
        "ESLint + Prettier + `tsc --noEmit`으로 검증합니다",
        "Barrel exports (`index.ts`)로 모듈을 정리합니다",
    ],
    "Go": [
        "`gofmt`로 코드를 자동 포맷팅합니다",
        "에러는 항상 처리하고, panic은 복구 불가능할 때만 사용합니다",
        "인터페이스는 작게 유지합니다 (단일 책임 원칙)",
        "패키지명은 간결하고 의미있게 짓습니다",
    ],
    "Generic": [
        "일관된 코드 스타일을 유지합니다",
        "의미 있는 변수명과 함수명을 사용합니다",
        "복잡한 로직에는 주석을 추가합니다",
        "DRY(Don't Repeat Yourself) 원칙을 따릅니다",
    ],
}


def _get_conventions(tech_stack: dict) -> list[str]:
    """Get coding conventions based on detected languages."""
    conventions = []
    for lang in tech_stack.get("languages", ["Generic"]):
        if lang in LANGUAGE_CONVENTIONS:
            conventions.extend(LANGUAGE_CONVENTIONS[lang])
    if not conventions:
        conventions = LANGUAGE_CONVENTIONS["Generic"]
    return conventions


# ---------------------------------------------------------------------------
# File generators
# ---------------------------------------------------------------------------

def _generate_agents_md(name: str, tech_stack: dict, structure: dict,
                        tree: str, ws_path: Path) -> str:
    """Generate AGENTS.md content."""
    langs = ", ".join(tech_stack.get("languages", ["Unknown"]))
    framework = tech_stack.get("framework") or "N/A"
    pkg_manager = tech_stack.get("package_manager") or "N/A"
    conventions = _get_conventions(tech_stack)

    lines = [
        f"# Project: {name}",
        "",
        "> 🤖 **AI Agent Operating Guide** — 이 프로젝트에서 작업하는 AI 에이전트를 위한 운영 지침입니다.",
        "",
        "---",
        "",
        "## 📋 프로젝트 개요",
        "",
        f"- **주 언어**: {langs}",
        f"- **프레임워크**: {framework}",
        f"- **패키지 매니저**: {pkg_manager}",
    ]

    if tech_stack.get("has_docker"):
        lines.append("- **Docker**: 사용")
    if tech_stack.get("has_git"):
        lines.append("- **버전 관리**: Git")
    if tech_stack.get("has_github_actions"):
        lines.append("- **CI/CD**: GitHub Actions")

    lines.extend([
        "",
        "## 📁 프로젝트 구조",
        "",
        tree,
        "",
        "## 🔑 주요 설정 파일",
        "",
    ])

    config_files = tech_stack.get("config_files", [])
    if config_files:
        for cf in config_files[:10]:
            lines.append(f"- `{cf}`")
    else:
        lines.append("- 설정 파일을 찾을 수 없습니다")

    lines.extend([
        "",
        "## 📐 코딩 컨벤션",
        "",
    ])

    for i, conv in enumerate(conventions, 1):
        lines.append(f"{i}. {conv}")

    lines.extend([
        "",
        "## 🔧 명령어",
        "",
        "| 명령 | 실행 |",
        "|------|------|",
    ])

    if tech_stack.get("build_command"):
        lines.append(f"| 빌드 | `{tech_stack['build_command']}` |")
    if tech_stack.get("test_command"):
        lines.append(f"| 테스트 | `{tech_stack['test_command']}` |")
    if tech_stack.get("lint_command"):
        lines.append(f"| 린트 | `{tech_stack['lint_command']}` |")

    agent_rules = [
        "",
        "## 🤖 에이전트 운영 규칙",
        "",
        "### 작업 시작 전",
    ]

    cfg_list = "`, `".join(config_files[:5]) if config_files else "설정 파일"
    agent_rules.append(f"1. `{cfg_list}` 파일을 먼저 확인하여 프로젝트 설정을 이해합니다")
    if tech_stack.get("has_git"):
        agent_rules.append("2. `git status`로 현재 변경사항을 확인합니다")
    agent_rules.append("3. 기존 코드 스타일을 분석하고 일관성 있게 작성합니다")

    agent_rules.extend([
        "",
        "### 코드 작성 시",
        "1. 위의 코딩 컨벤션을 준수합니다",
        "2. 새로운 기능은 기존 패턴을 따릅니다",
        "3. 복잡한 로직에는 주석을 추가합니다",
        "4. 하드코딩된 값 대신 상수/설정을 사용합니다",
        "",
        "### 작업 완료 후",
    ])

    if tech_stack.get("test_command"):
        agent_rules.append(f"1. `{tech_stack['test_command']}`로 테스트를 실행합니다")
    if tech_stack.get("lint_command"):
        agent_rules.append(f"2. `{tech_stack['lint_command']}`로 코드 품질을 확인합니다")
    agent_rules.append("3. 변경사항을 요약하여 보고합니다")

    lines.extend(agent_rules)

    # Add generated timestamp
    lines.extend([
        "",
        "---",
        "",
        f"*이 파일은 Daon Agent System에 의해 자동 생성되었습니다.*",
        f"*마지막 업데이트: {_now()}*",
    ])

    return "\n".join(lines)


def _generate_claude_md(name: str, tech_stack: dict, structure: dict,
                        tree: str, ws_path: Path) -> str:
    """Generate CLAUDE.md content (Claude Code specific, reuses AGENTS.md base)."""
    base = _generate_agents_md(name, tech_stack, structure, tree, ws_path)

    claude_specific = [
        "",
        "---",
        "",
        "## 🧠 Claude Code 특화 지침",
        "",
        "### 파일 읽기 우선순위",
        "1. 이 파일 (`CLAUDE.md`)을 가장 먼저 읽습니다",
        "2. 프로젝트 설정 파일을 확인합니다",
        "3. 관련 소스 코드를 분석한 후 수정을 시작합니다",
        "",
        "### 도구 사용 지침",
        "- `list_files`로 프로젝트 구조를 파악한 후 작업합니다",
        "- `search_files`로 기존 구현을 검색하여 중복을 피합니다",
        "- `read_file`로 파일을 먼저 읽고, `apply_diff`로 수정합니다",
        "- 한 번에 여러 독립적인 파일을 수정할 때는 병렬 도구 호출을 활용합니다",
        "",
        "### 커밋 컨벤션",
        "- Conventional Commits 형식: `type(scope): description`",
        "- Type: feat, fix, docs, refactor, test, chore, style",
        "- 커밋 메시지는 영어로 작성합니다",
        "",
        "### 출력 형식",
        "- 마크다운 응답은 파일 경로를 클릭 가능한 링크로 표시합니다",
        "- 코드 블록에는 항상 언어를 지정합니다",
        "- 변경사항은 before/after 형식으로 명확히 제시합니다",
        "",
        f"*Daon Agent System에 의해 생성됨 — {_now()}*",
    ]

    return base + "\n".join(claude_specific)


def _generate_cursor_rules(name: str, tech_stack: dict, structure: dict,
                           tree: str, ws_path: Path) -> str:
    """Generate .cursor/rules content."""
    langs = ", ".join(tech_stack.get("languages", ["Unknown"]))
    conventions = _get_conventions(tech_stack)
    config_files = tech_stack.get("config_files", [])

    lines = [
        f"# Cursor Rules for {name}",
        "",
        f"## Project Context",
        f"- Language: {langs}",
        f"- Framework: {tech_stack.get('framework') or 'N/A'}",
        f"- Package Manager: {tech_stack.get('package_manager') or 'N/A'}",
        "",
        "## Coding Standards",
    ]

    for conv in conventions:
        lines.append(f"- {conv}")

    lines.extend([
        "",
        "## Commands",
    ])

    if tech_stack.get("build_command"):
        lines.append(f"- Build: `{tech_stack['build_command']}`")
    if tech_stack.get("test_command"):
        lines.append(f"- Test: `{tech_stack['test_command']}`")
    if tech_stack.get("lint_command"):
        lines.append(f"- Lint: `{tech_stack['lint_command']}`")

    lines.extend([
        "",
        "## File Patterns",
        "- Always read config files first before making changes",
    ])

    if config_files:
        lines.append(f"- Key config files: {', '.join('`' + f + '`' for f in config_files[:6])}")

    # Add path-specific rules
    lines.extend([
        "",
        "## Cursor-Specific Rules",
    ])

    if tech_stack["primary_language"] == "Python":
        lines.extend([
            "- Use `ruff` for linting and formatting",
            "- Type hints are required for all function signatures",
        ])
    elif tech_stack["primary_language"] in ("TypeScript", "JavaScript"):
        lines.extend([
            "- Use ESLint and Prettier configurations",
            "- Prefer functional components with hooks",
        ])

    lines.extend([
        "",
        "## Agent Behavior",
        "- Read existing code patterns before implementing new features",
        "- Follow the established file structure",
        "- Add meaningful comments for complex logic",
        "- Keep pull requests focused and small",
    ])

    return "\n".join(lines)


def _generate_copilot_instructions(name: str, tech_stack: dict,
                                    structure: dict, tree: str,
                                    ws_path: Path) -> str:
    """Generate .github/copilot-instructions.md content."""
    langs = ", ".join(tech_stack.get("languages", ["Unknown"]))
    conventions = _get_conventions(tech_stack)

    lines = [
        f"# GitHub Copilot Instructions for {name}",
        "",
        "## Project Context",
        "",
        f"This is a **{langs}** project" +
        (f" using **{tech_stack['framework']}**" if tech_stack.get("framework") else "") + ".",
        "",
        "## Code Style Preferences",
        "",
    ]

    for conv in conventions:
        lines.append(f"- {conv}")

    lines.extend([
        "",
        "## Preferred Patterns",
        "",
    ])

    if tech_stack["primary_language"] == "Python":
        lines.extend([
            "- Use dataclasses or Pydantic models for data structures",
            "- Prefer `pathlib.Path` over `os.path`",
            "- Use context managers (`with` statements) for resource management",
            "- Log with `logging` module, not `print`",
        ])
    elif tech_stack["primary_language"] in ("TypeScript", "JavaScript"):
        lines.extend([
            "- Use async/await over Promise chains",
            "- Prefer arrow functions for callbacks",
            "- Use template literals over string concatenation",
            "- Destructure objects and arrays where appropriate",
        ])

    lines.extend([
        "",
        "## Testing",
    ])

    if tech_stack.get("test_command"):
        lines.append(f"- Run tests with: `{tech_stack['test_command']}`")
    else:
        lines.append("- Write unit tests for all new functions")

    lines.extend([
        "",
        "## File Organization",
    ])

    lines.append("- Follow the existing directory structure")
    specific_cats = {k: v for k, v in structure.get("categories", {}).items()
                     if k in ("python", "javascript", "typescript") and len(v) > 0}
    for cat, files in specific_cats.items():
        dirs = set()
        for f in files[:20]:
            parts = Path(f).parts
            if len(parts) > 1:
                dirs.add(parts[0])
        if dirs:
            lines.append(f"- {cat.title()} code lives in: {', '.join(sorted(dirs))}")

    lines.extend([
        "",
        "## Pull Request Guidelines",
        "- Keep PRs focused on a single concern",
        "- Include a clear description of changes",
        "- Reference related issues",
        "- Ensure all tests pass before requesting review",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


GENERATORS = {
    "agents.md": (_generate_agents_md, "AGENTS.md", "AGENTS.md"),
    "claude.md": (_generate_claude_md, "CLAUDE.md", "CLAUDE.md"),
    "cursor_rules": (_generate_cursor_rules, ".cursor/rules", ".cursor/rules.txt"),
    "copilot_instructions": (_generate_copilot_instructions,
                             ".github/copilot-instructions.md",
                             ".github/copilot-instructions.md"),
}

FILE_TYPE_LABELS = {
    "agents.md": "AGENTS.md",
    "claude.md": "CLAUDE.md",
    "cursor_rules": ".cursor/rules",
    "copilot_instructions": ".github/copilot-instructions.md",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preview_setup_file(workspace_path: str, file_type: str) -> dict:
    """Preview a single setup file without writing to disk.

    Args:
        workspace_path: Absolute path to the workspace root
        file_type: One of "agents.md", "claude.md", "cursor_rules", "copilot_instructions"

    Returns:
        {"content": str, "filename": str, "will_overwrite": bool, "error": Optional[str]}
    """
    ws_path = Path(workspace_path).resolve()

    if not ws_path.is_dir():
        return {"content": "", "filename": "", "will_overwrite": False,
                "error": f"워크스페이스를 찾을 수 없습니다: {workspace_path}"}

    if file_type not in GENERATORS:
        return {"content": "", "filename": "", "will_overwrite": False,
                "error": f"알 수 없는 파일 타입: {file_type}"}

    try:
        gen_func, rel_path, _ = GENERATORS[file_type]
        structure = _discover_project_structure(ws_path)
        tech_stack = _detect_tech_stack(ws_path, structure)
        tree = _generate_tree(ws_path)
        name = ws_path.name

        content = gen_func(name, tech_stack, structure, tree, ws_path)
        target_path = ws_path / rel_path
        will_overwrite = target_path.exists()

        return {
            "content": content,
            "filename": rel_path,
            "will_overwrite": will_overwrite,
            "error": None,
        }
    except Exception as e:
        _logger.error("Preview failed: %s", e, exc_info=True)
        return {"content": "", "filename": "", "will_overwrite": False,
                "error": str(e)}


def generate_setup_files(workspace_path: str, file_types: list[str],
                         overwrite: bool = False) -> dict:
    """Generate AI setup files in the workspace.

    Args:
        workspace_path: Absolute path to the workspace root
        file_types: List of file types to generate
        overwrite: If True, overwrite existing files

    Returns:
        {"generated": ["AGENTS.md", ...], "skipped": ["CLAUDE.md", ...],
         "errors": [{"file": "...", "error": "..."}], "workspace": str,
         "tech_stack": {...}}
    """
    ws_path = Path(workspace_path).resolve()

    if not ws_path.is_dir():
        return {"generated": [], "skipped": [], "errors": [
            {"file": "ALL", "error": f"워크스페이스를 찾을 수 없습니다: {workspace_path}"}
        ], "workspace": str(ws_path), "tech_stack": None}

    try:
        structure = _discover_project_structure(ws_path)
        tech_stack = _detect_tech_stack(ws_path, structure)
    except Exception as e:
        return {"generated": [], "skipped": [], "errors": [
            {"file": "ALL", "error": f"프로젝트 분석 실패: {e}"}
        ], "workspace": str(ws_path), "tech_stack": None}

    tree = _generate_tree(ws_path)
    name = ws_path.name

    generated = []
    skipped = []
    errors = []

    for ft in file_types:
        if ft not in GENERATORS:
            errors.append({"file": ft, "error": f"알 수 없는 파일 타입: {ft}"})
            continue

        gen_func, rel_path, _ = GENERATORS[ft]
        target_path = ws_path / rel_path

        if target_path.exists() and not overwrite:
            skipped.append(ft)
            continue

        try:
            content = gen_func(name, tech_stack, structure, tree, ws_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            generated.append(ft)
        except Exception as e:
            _logger.error("Failed to generate %s: %s", ft, e)
            errors.append({"file": ft, "error": str(e)})

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "workspace": str(ws_path),
        "tech_stack": tech_stack,
    }


# ---------------------------------------------------------------------------
# CLI test entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python setup_generator.py <workspace_path> [--preview file_type]")
        sys.exit(1)

    ws = sys.argv[1]

    if "--preview" in sys.argv:
        idx = sys.argv.index("--preview")
        ft = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "agents.md"
        result = preview_setup_file(ws, ft)
        if result["error"]:
            print(f"Error: {result['error']}")
        else:
            print(f"--- Preview: {result['filename']} ---")
            print(f"Will overwrite: {result['will_overwrite']}")
            print(result["content"][:2000])
    else:
        result = generate_setup_files(ws, ["agents.md", "claude.md", "cursor_rules", "copilot_instructions"])
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
