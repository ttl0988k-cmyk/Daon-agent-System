# -*- coding: utf-8 -*-
"""DAON 플러그인 스토어 생성기 (1회성/재생성용 변환기).

소재 2종:
  A. wshobson 마켓플레이스: %LOCALAPPDATA%/Daon/plugins/agents/plugins/*  (plugin.json + skills/)
  B. anthropics/skills 클론: %LOCALAPPDATA%/Daon/plugin_store_src/anthropics-skills/skills/*

산출물:
  1. %LOCALAPPDATA%/Daon/plugin_store/<name>/  — 다온 플러그인 스키마(plugin.yaml + skills/)로
     변환된 "설치 가능 원본 폴더". /api/plugins/import source_type=folder 의 identifier로 쓰인다.
     ※ import API의 target(%LOCALAPPDATA%/Daon/plugins)과 다른 경로 → self-copy 루프 함정 회피(메모리 규칙).
  2. static/store/plugins.json — 프론트 카탈로그(검색/카테고지 필터용). sync_to_installed.ps1가 설치본으로 옮긴다.
"""
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
W_SHOBSON = LOCAL / "Daon" / "plugins" / "agents" / "plugins"
ANTHROPICS = LOCAL / "Daon" / "plugin_store_src" / "anthropics-skills" / "skills"
STORE_ROOT = LOCAL / "Daon" / "plugin_store"
APP_SRC = Path(r"C:/daon/Daon agent System")
CATALOG_OUT = APP_SRC / "static" / "store" / "plugins.json"

COLORS = ["#6c8cff", "#4ecdc4", "#ff8a5c", "#b39ddb", "#f06292",
          "#aed581", "#4dd0e1", "#ffb74d", "#e57373", "#9575cd"]

# 카테고리 한국어 라벨 + 키워드 규칙 (첫 매칭 우선)
CATEGORY_RULES = [
    ("문서",        ["docx", "xlsx", "pptx", "pdf", "document", "deck", "file-conversion", "coauthoring"]),
    ("디자인",      ["design", "brand", "canvas", "theme", "art", "landing", "gif"]),
    ("개발",        ["python", "javascript", "typescript", "backend", "frontend", "full-stack", "shell",
                     "jvm", "julia", "dotnet", "systems-programming", "framework", "api-scaffolding",
                     "code-refactoring", "codebase", "developer", "mcp-builder", "claude-api", "web-artifacts",
                     "code-documentation", "documentation", "blockchain", "quantitative", "game", "arm-cortex",
                     "embedded", "mobile", "multi-platform", "functional"]),
    ("테스트/품질",  ["testing", "accessibility", "tdd", "review", "validation", "compliance", "wcag",
                     "screen-reader", "quality"]),
    ("LLM/AI",      ["llm", "rag", "prompt", "agent", "skill-creator", "machine-learning", "finetuning",
                     "context", "orchestration", "conductor"]),
    ("데이터",      ["database", "postgresql", "data-engineering", "migration", "cloud-optimization"]),
    ("인프라/운영",  ["kubernetes", "cloud-infrastructure", "cicd", "deployment", "observability",
                     "monitoring", "incident", "performance", "dependency", "dgx"]),
    ("보안",        ["security", "sast", "threat", "stride", "attack", "audit", "protect", "ship-mate",
                     "reverse-engineering", "payment"]),
    ("SEO/콘텐츠",  ["seo", "content", "social", "marketing", "communications", "internal-comms",
                     "academy"]),
    ("비즈니스",    ["business", "analytics", "hr-", "customer", "sales", "operating", "startup", "team"]),
]


def categorize(name: str, desc: str) -> str:
    hay = (name + " " + desc).lower()
    for cat, keys in CATEGORY_RULES:
        for k in keys:
            if k in hay:
                return cat
    return "기타"


def color_for(name: str) -> str:
    return COLORS[sum(name.encode("utf-8")) % len(COLORS)]


def read_frontmatter_desc(skill_md: Path) -> str:
    """SKILL.md frontmatter에서 description 한 줄 추출 (없으면 빈 문자열)."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    block = text[3:end]
    for line in block.splitlines():
        s = line.strip()
        if s.startswith("description:"):
            v = s[len("description:"):].strip().strip("\"'")
            return v
    return ""


def yaml_quote(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit_plugin_yaml(dst: Path, name: str, version: str, desc: str, author: str,
                     skills: list) -> None:
    lines = [
        "name: " + yaml_quote(name),
        "version: " + yaml_quote(version),
        "description: " + yaml_quote(desc),
        "author: " + yaml_quote(author),
        "skills:",
    ]
    for sn, rel in skills:
        lines.append("  - name: " + yaml_quote(sn))
        lines.append("    path: " + yaml_quote(rel))
    lines.append("mcp: []")
    lines.append("tools: []")
    lines.append("hooks: []")
    lines.append("secrets: []")
    with open(dst / "plugin.yaml", "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def collect_wshobson(entries: list) -> int:
    n_ok = 0
    for src in sorted(W_SHOBSON.iterdir()):
        if not src.is_dir():
            continue
        pj = src / ".claude-plugin" / "plugin.json"
        skills_dir = src / "skills"
        if not pj.exists() or not skills_dir.is_dir():
            continue
        skill_names = sorted(
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists()
        )
        if not skill_names:
            continue
        try:
            meta = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = meta.get("name") or src.name
        desc = meta.get("description") or ""
        author = ""
        a = meta.get("author")
        if isinstance(a, dict):
            author = a.get("name") or ""
        elif isinstance(a, str):
            author = a
        dst = STORE_ROOT / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".claude-plugin", ".codex-plugin",
                                                                ".cursor-plugin", ".opencode", ".git"))
        emit_plugin_yaml(dst, name, str(meta.get("version") or "1.0.0"), desc, author or "Seth Hobson",
                         [(sn, "skills/" + sn + "/SKILL.md") for sn in skill_names])
        entries.append({
            "name": name,
            "desc": desc,
            "category": categorize(name, desc),
            "skills": len(skill_names),
            "path": str(dst),
            "src": "wshobson",
            "color": color_for(name),
        })
        n_ok += 1
    return n_ok


def collect_anthropics(entries: list) -> int:
    n_ok = 0
    for src in sorted(ANTHROPICS.iterdir()):
        if not src.is_dir() or not (src / "SKILL.md").exists():
            continue
        name = "anthropic-" + src.name
        desc = read_frontmatter_desc(src / "SKILL.md")
        dst = STORE_ROOT / name
        if dst.exists():
            shutil.rmtree(dst)
        (dst / "skills" / src.name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst / "skills" / src.name, ignore=shutil.ignore_patterns(".git"))
        emit_plugin_yaml(dst, name, "1.0.0", desc or ("Anthropic official skill: " + src.name),
                         "Anthropic", [(src.name, "skills/" + src.name + "/SKILL.md")])
        entries.append({
            "name": name,
            "desc": desc,
            "category": categorize(src.name, desc),
            "skills": 1,
            "path": str(dst),
            "src": "anthropics",
            "color": color_for(name),
        })
        n_ok += 1
    return n_ok


def main() -> int:
    if not W_SHOBSON.is_dir():
        print("NO wshobson material:", W_SHOBSON, file=sys.stderr)
        return 1
    STORE_ROOT.mkdir(parents=True, exist_ok=True)
    entries: list = []
    a = collect_wshobson(entries) if W_SHOBSON.is_dir() else 0
    b = collect_anthropics(entries) if ANTHROPICS.is_dir() else 0
    entries.sort(key=lambda e: (e["category"], e["name"]))
    catalog = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "categories": sorted({e["category"] for e in entries}),
        "plugins": entries,
    }
    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_OUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wshobson:", a, "anthropics:", b, "total:", len(entries))
    print("catalog:", CATALOG_OUT)
    print("store:", STORE_ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
