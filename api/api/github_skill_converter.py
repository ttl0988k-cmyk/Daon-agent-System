"""
GitHub URL → Skill Converter

GitHub 저장소/파일 URL을 입력하면 README + 코드를 분석하여
Daon 스킬 형식(SKILL.md + skill.yaml)으로 변환·설치합니다.

지원 URL 형식:
  - https://github.com/{owner}/{repo}
  - https://github.com/{owner}/{repo}/tree/{branch}/{path}
  - https://github.com/{owner}/{repo}/blob/{branch}/{path}
  - https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}
"""

import re
import json
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Skills installation base directory
# [refactor] api/api/github_skill_converter.py → root/skills (단일 정본)
SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# GitHub API base
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# Max file size to fetch (512KB)
MAX_FILE_SIZE = 512 * 1024

# Category detection keywords
CATEGORY_KEYWORDS = {
    "Coding": ["code", "coding", "develop", "programming", "algorithm", "api", "function",
               "class", "module", "library", "framework", "compiler", "debug", "test"],
    "Design": ["design", "ui", "ux", "css", "style", "layout", "color", "font", "figma",
               "sketch", "prototype", "wireframe", "animation"],
    "Content": ["write", "writing", "document", "blog", "article", "content", "copy",
                "markdown", "readme", "tutorial", "guide"],
    "AI": ["ai", "ml", "machine-learning", "neural", "llm", "gpt", "prompt", "agent",
           "model", "training", "inference", "embedding"],
    "Automation": ["automate", "automation", "workflow", "pipeline", "cron", "schedule",
                   "bot", "script", "ci", "cd", "deploy"],
    "System": ["security", "infra", "server", "network", "docker", "kubernetes", "linux",
               "shell", "system", "monitor", "log"],
    "Business": ["business", "market", "sales", "crm", "erp", "finance", "contract",
                 "project", "manage", "plan", "strategy"],
}


def parse_github_url(url: str) -> Optional[dict]:
    """Parse a GitHub URL into components.

    Returns:
        {
            "owner": str,
            "repo": str,
            "branch": str (default: main),
            "path": str (default: ""),
            "type": "repo" | "tree" | "blob" | "raw"
        }
        or None if not a valid GitHub URL.
    """
    url = url.strip().rstrip("/")

    # raw.githubusercontent.com
    raw_match = re.match(
        r"https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.*)",
        url
    )
    if raw_match:
        return {
            "owner": raw_match.group(1),
            "repo": raw_match.group(2),
            "branch": raw_match.group(3),
            "path": raw_match.group(4),
            "type": "raw",
        }

    # github.com patterns
    gh_match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/(tree|blob)/([^/]+)/?(.*))?$",
        url
    )
    if not gh_match:
        return None

    owner = gh_match.group(1)
    repo = gh_match.group(2)
    view_type = gh_match.group(3)  # tree, blob, or None
    branch = gh_match.group(4) or "main"
    path = gh_match.group(5) or ""

    if view_type == "tree":
        url_type = "tree"
    elif view_type == "blob":
        url_type = "blob"
    else:
        url_type = "repo"

    return {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "path": path,
        "type": url_type,
    }


def _fetch_url(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch URL content as text. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "DaonAgent/1.0",
            "Accept": "application/vnd.github.v3+json, text/plain, */*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_FILE_SIZE)
            return data.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        logger.debug("Fetch failed for %s: %s", url, e)
        return None


def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    """Fetch URL as JSON."""
    text = _fetch_url(url, timeout)
    if text:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return None


def fetch_repo_info(owner: str, repo: str) -> dict:
    """Fetch repository metadata from GitHub API."""
    data = _fetch_json(f"{GITHUB_API}/repos/{owner}/{repo}")
    if not data:
        return {}
    return {
        "description": data.get("description", ""),
        "language": data.get("language", ""),
        "topics": data.get("topics", []),
        "stars": data.get("stargazers_count", 0),
        "default_branch": data.get("default_branch", "main"),
        "license": (data.get("license") or {}).get("spdx_id", ""),
    }


def fetch_readme(owner: str, repo: str, branch: str = "main") -> str:
    """Fetch README.md content."""
    # Try common README filenames
    for filename in ["README.md", "readme.md", "README.rst", "README.txt", "README"]:
        url = f"{GITHUB_RAW}/{owner}/{repo}/{branch}/{filename}"
        content = _fetch_url(url)
        if content:
            return content
    return ""


def fetch_file_content(owner: str, repo: str, branch: str, path: str) -> str:
    """Fetch a specific file's content."""
    url = f"{GITHUB_RAW}/{owner}/{repo}/{branch}/{path}"
    return _fetch_url(url) or ""


def fetch_tree_files(owner: str, repo: str, branch: str, path: str) -> list[dict]:
    """Fetch directory listing from GitHub API (tree)."""
    api_url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    data = _fetch_json(api_url)
    if not data or not isinstance(data, list):
        return []
    files = []
    for item in data[:20]:  # Limit to 20 files
        if item.get("type") == "file" and item.get("size", 0) < MAX_FILE_SIZE:
            files.append({
                "name": item.get("name", ""),
                "path": item.get("path", ""),
                "size": item.get("size", 0),
                "download_url": item.get("download_url", ""),
            })
    return files


def detect_category(text: str, topics: list[str] = None) -> str:
    """Detect skill category from content keywords."""
    combined = (text + " " + " ".join(topics or [])).lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined)
        scores[cat] = score

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "Coding"  # Default
    return best


def generate_skill_name(repo: str, path: str = "") -> str:
    """Generate a skill ID from repo/path name."""
    if path:
        # Use last path component without extension
        name = Path(path).stem
    else:
        name = repo

    # Sanitize: lowercase, hyphens only
    name = re.sub(r"[^a-z0-9-]", "-", name.lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "github-skill"


def generate_skill_yaml(name: str, description: str, category: str,
                        topics: list[str], language: str, url: str) -> str:
    """Generate skill.yaml content."""
    tags = list(set(topics[:8])) if topics else []
    if language and language.lower() not in [t.lower() for t in tags]:
        tags.append(language.lower())

    lines = [
        f"name: {name}",
        f"category: {category}",
        "version: '1.0'",
        f"description: {description[:200]}",
        "priority: medium",
        "capabilities:",
        f"- {category.lower()}",
    ]

    if tags:
        lines.append("tags:")
        for tag in tags[:10]:
            lines.append(f"- {tag}")

    lines.extend([
        f"source_url: {url}",
        "source_type: github",
        f"when_to_use: {description[:100]}",
        "when_not_to_use: ''",
    ])

    return "\n".join(lines) + "\n"


def generate_skill_md(name: str, description: str, readme: str,
                      code_snippets: list[str], url: str, repo_info: dict) -> str:
    """Generate SKILL.md content from README + code."""
    sections = []

    # Title
    display_name = name.replace("-", " ").title()
    sections.append(f"# {display_name}\n")

    # Description
    if description:
        sections.append(f"> {description}\n")

    # Source
    sections.append(f"**Source:** [{url}]({url})\n")
    if repo_info.get("stars"):
        sections.append(f"**Stars:** {repo_info['stars']} | **Language:** {repo_info.get('language', 'N/A')}\n")

    # README content (trimmed)
    if readme:
        # Remove first heading (we already have our own title)
        readme_trimmed = re.sub(r"^#[^#\n]*\n", "", readme, count=1).strip()
        # Limit to ~3000 chars
        if len(readme_trimmed) > 3000:
            readme_trimmed = readme_trimmed[:3000] + "\n\n... (truncated)"
        sections.append("## Documentation\n")
        sections.append(readme_trimmed + "\n")

    # Code snippets
    if code_snippets:
        sections.append("## Key Code\n")
        for snippet in code_snippets[:3]:  # Max 3 snippets
            sections.append(snippet + "\n")

    # Usage instruction
    sections.append("## Usage\n")
    sections.append(f"This skill was auto-generated from [{url}]({url}).\n")
    sections.append("The agent will use the documentation and code above as context when this skill is active.\n")

    return "\n".join(sections)


def convert_github_url(url: str, custom_name: str = None, custom_category: str = None) -> dict:
    """Main entry: Convert a GitHub URL to an installed skill.

    Args:
        url: GitHub URL (repo, tree, blob, or raw)
        custom_name: Optional custom skill name override
        custom_category: Optional category override

    Returns:
        {
            "ok": True,
            "skill_name": str,
            "installed_to": str,
            "category": str,
            "files": [str],
            "description": str,
        }
        or {"ok": False, "error": str}
    """
    parsed = parse_github_url(url)
    if not parsed:
        return {"ok": False, "error": f"Invalid GitHub URL: {url}"}

    owner = parsed["owner"]
    repo = parsed["repo"]
    branch = parsed["branch"]
    path = parsed["path"]
    url_type = parsed["type"]

    # Fetch repo info
    repo_info = fetch_repo_info(owner, repo)
    if not branch or branch == "main":
        branch = repo_info.get("default_branch", "main")

    # Fetch content based on URL type
    readme = ""
    code_snippets = []
    description = repo_info.get("description", "")
    topics = repo_info.get("topics", [])
    language = repo_info.get("language", "")

    if url_type == "repo":
        # Fetch README
        readme = fetch_readme(owner, repo, branch)
        if not description and readme:
            # Extract first paragraph as description
            first_para = re.search(r"^(?!#)(.+)$", readme, re.MULTILINE)
            if first_para:
                description = first_para.group(1).strip()[:200]

    elif url_type == "blob" or url_type == "raw":
        # Fetch specific file
        content = fetch_file_content(owner, repo, branch, path)
        if not content:
            return {"ok": False, "error": f"Could not fetch file: {path}"}

        ext = Path(path).suffix.lower()
        if ext in (".md", ".rst", ".txt"):
            readme = content
        else:
            # Code file — wrap in fenced block
            lang = ext.lstrip(".") or "text"
            code_snippets.append(f"```{lang}\n{content[:5000]}\n```")
            if not description:
                description = f"Code from {owner}/{repo}/{path}"

    elif url_type == "tree":
        # Directory — fetch listing + key files
        files = fetch_tree_files(owner, repo, branch, path)
        for f in files:
            fname = f["name"].lower()
            if fname in ("readme.md", "readme.rst", "readme.txt"):
                readme = _fetch_url(f["download_url"]) or ""
            elif f["size"] < 50000 and Path(fname).suffix in (
                ".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".sh", ".yaml", ".yml", ".json"
            ):
                content = _fetch_url(f["download_url"])
                if content:
                    lang = Path(fname).suffix.lstrip(".")
                    code_snippets.append(f"```{lang}\n# {f['name']}\n{content[:3000]}\n```")

        if not readme:
            readme = fetch_readme(owner, repo, branch)

    # Generate skill name
    skill_name = custom_name or generate_skill_name(repo, path)

    # Detect category
    all_text = f"{readme} {description} {' '.join(topics)} {' '.join(code_snippets[:1])}"
    category = custom_category or detect_category(all_text, topics)

    if not description:
        description = f"Skill generated from {owner}/{repo}"

    # Create skill directory
    skill_dir = SKILLS_DIR / category / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    for subdir in ["examples", "prompts", "templates"]:
        (skill_dir / subdir).mkdir(exist_ok=True)
        gitkeep = skill_dir / subdir / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")

    # Generate files
    yaml_content = generate_skill_yaml(
        skill_name, description, category, topics, language, url
    )
    md_content = generate_skill_md(
        skill_name, description, readme, code_snippets, url, repo_info
    )

    # Write files
    installed_files = []

    yaml_path = skill_dir / "skill.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    installed_files.append(str(yaml_path.relative_to(SKILLS_DIR.parent)))

    md_path = skill_dir / "SKILL.md"
    md_path.write_text(md_content, encoding="utf-8")
    installed_files.append(str(md_path.relative_to(SKILLS_DIR.parent)))

    logger.info("GitHub skill installed: %s/%s (%s)", category, skill_name, url)

    return {
        "ok": True,
        "skill_name": skill_name,
        "installed_to": f"skills/{category}/{skill_name}",
        "category": category,
        "files": installed_files,
        "description": description,
        "source_url": url,
        "repo_info": {
            "stars": repo_info.get("stars", 0),
            "language": language,
            "topics": topics[:5],
        },
    }
