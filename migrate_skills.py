#!/usr/bin/env python3
"""
Skill Migration Script — DAON Agent System
==========================================
Converts flat skills/*.md files into the new categorized folder structure:

    skills/
    ├── AI/
    │   └── self-reflection/
    │       ├── skill.yaml
    │       └── SKILL.md
    ├── Coding/
    │   └── bill-dev/
    │       ├── skill.yaml
    │       └── SKILL.md
    ├── Design/
    │   └── premium-ui/
    │       ├── skill.yaml
    │       ├── SKILL.md
    │       ├── prompts/
    │       ├── examples/
    │       ├── data/
    │       └── templates/
    ├── Automation/
    ├── Content/
    ├── Business/
    ├── Data/
    ├── System/
    └── Archive/

Usage:
    python migrate_skills.py [--dry-run]
"""
import os
import re
import sys
import shutil
import yaml
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────

SKILLS_ROOT = Path(__file__).parent / "skills"
API_SKILLS_ROOT = Path(__file__).parent / "api" / "skills"

# Category mapping: frontmatter category → new folder name
CATEGORY_MAP = {
    "design": "Design",
    "ui": "Design",
    "ux": "Design",
    "security": "System",
    "system": "System",
    "devops": "System",
    "media": "Content",
    "content": "Content",
    "writing": "Content",
    "documentation": "Content",
    "coding": "Coding",
    "development": "Coding",
    "programming": "Coding",
    "quality-assurance": "Coding",
    "testing": "Coding",
    "automation": "Automation",
    "ai": "AI",
    "agent": "AI",
    "business": "Business",
    "data": "Data",
    "general": "Coding",  # default fallback
}

# Manual overrides for specific skills (skill_name → Category)
SKILL_CATEGORY_OVERRIDE = {
    "premium-ui": "Design",
    "brutalist-ui": "Design",
    "minimalist-ui": "Design",
    "taste": "Design",
    "taste_v1.1": "Design",
    "taste-design": "Design",
    "ui-ux-pro": "Design",
    "creative-director": "Design",
    "redesign-audit": "Design",
    "html-anything": "Coding",
    "security": "System",
    "ffmpeg-video-editor": "Content",
    "auto-documenter": "Content",
    "full-output": "Coding",
    "bill-dev": "Coding",
    "self-reflection": "AI",
    "sherlock-qa": "AI",
    "contract-validator": "Business",
    "notification-relay": "Automation",
    # roles
    "debugger": "Coding",
    "documenter": "Content",
    "explainer": "Content",
    "refactorer": "Coding",
    "reviewer": "Coding",
    "tester": "Coding",
    "writer": "Content",
}

# Trigger keywords for skill router (skill_name → trigger list)
SKILL_TRIGGERS = {
    "premium-ui": ["premium design", "luxury website", "agency quality", "awwwards", "high-end ui"],
    "brutalist-ui": ["brutalist", "raw design", "anti-design", "brutalism"],
    "minimalist-ui": ["minimalist", "clean design", "simple ui", "minimal"],
    "taste": ["design taste", "aesthetic", "visual quality"],
    "taste_v1.1": ["design taste", "aesthetic", "visual quality"],
    "taste-design": ["design system", "design language", "visual consistency"],
    "ui-ux-pro": ["ui design", "ux design", "user interface", "user experience", "web design", "landing page", "dashboard"],
    "creative-director": ["creative direction", "brand identity", "visual concept"],
    "redesign-audit": ["redesign", "design audit", "ui review", "design critique"],
    "html-anything": ["html", "web page", "markup", "static site"],
    "security": ["security", "vulnerability", "xss", "injection", "auth", "보안"],
    "ffmpeg-video-editor": ["video", "ffmpeg", "encoding", "subtitle", "자막", "영상 편집"],
    "auto-documenter": ["documentation", "readme", "docs", "문서화", "주석"],
    "full-output": ["full output", "complete code", "전체 코드", "완성본"],
    "bill-dev": ["billing", "payment", "invoice", "결제"],
    "self-reflection": ["self reflection", "self review", "자기 검토", "반성"],
    "sherlock-qa": ["debug", "investigate", "root cause", "디버그", "원인 분석"],
    "contract-validator": ["contract", "agreement", "terms", "계약"],
    "notification-relay": ["notification", "alert", "slack", "telegram", "알림"],
    "debugger": ["debug", "fix bug", "error", "디버그", "버그"],
    "documenter": ["document", "write docs", "문서 작성"],
    "explainer": ["explain", "tutorial", "설명", "가이드"],
    "refactorer": ["refactor", "restructure", "리팩토링", "코드 정리"],
    "reviewer": ["code review", "pr review", "코드 리뷰", "검토"],
    "tester": ["test", "unit test", "integration test", "테스트"],
    "writer": ["write", "blog", "article", "글쓰기", "블로그"],
}

# Capabilities for skill router (skill_name → capabilities list)
SKILL_CAPABILITIES = {
    "premium-ui": ["ui_generation", "design_review", "component_selection"],
    "brutalist-ui": ["ui_generation", "design_review"],
    "minimalist-ui": ["ui_generation", "design_review", "component_selection"],
    "taste": ["design_review", "aesthetic_judgment"],
    "taste_v1.1": ["design_review", "aesthetic_judgment"],
    "taste-design": ["design_system", "consistency_check"],
    "ui-ux-pro": ["ui_generation", "ux_analysis", "component_selection", "design_review"],
    "creative-director": ["creative_direction", "brand_strategy"],
    "redesign-audit": ["design_audit", "ui_review"],
    "html-anything": ["html_generation", "web_development"],
    "security": ["security_audit", "vulnerability_scan", "input_validation"],
    "ffmpeg-video-editor": ["video_editing", "audio_processing", "encoding"],
    "auto-documenter": ["documentation", "code_analysis"],
    "full-output": ["complete_implementation", "code_generation"],
    "bill-dev": ["billing_system", "payment_integration"],
    "self-reflection": ["self_evaluation", "error_analysis"],
    "sherlock-qa": ["debugging", "root_cause_analysis", "testing"],
    "contract-validator": ["contract_analysis", "legal_review"],
    "notification-relay": ["notification_routing", "messaging"],
    "debugger": ["debugging", "error_analysis"],
    "documenter": ["documentation", "technical_writing"],
    "explainer": ["explanation", "tutorial_creation"],
    "refactorer": ["code_refactoring", "code_quality"],
    "reviewer": ["code_review", "quality_assurance"],
    "tester": ["test_generation", "quality_assurance"],
    "writer": ["content_writing", "copywriting"],
}

ALL_CATEGORIES = ["AI", "Coding", "Design", "Automation", "Content", "Business", "Data", "System", "Archive"]


# ── Helpers ────────────────────────────────────────────────────────────────

def parse_frontmatter(content: str) -> tuple:
    """Parse YAML frontmatter from markdown content.
    Returns (metadata_dict, body_text).
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    body = parts[2].strip()
    return meta, body


def get_category_for_skill(skill_name: str, frontmatter_category: str) -> str:
    """Determine the target category folder for a skill."""
    # Check manual override first
    if skill_name in SKILL_CATEGORY_OVERRIDE:
        return SKILL_CATEGORY_OVERRIDE[skill_name]

    # Map from frontmatter category
    cat_lower = (frontmatter_category or "general").lower().strip()
    return CATEGORY_MAP.get(cat_lower, "Coding")


def build_skill_yaml(meta: dict, skill_name: str, category: str) -> dict:
    """Build the skill.yaml content from frontmatter metadata + new fields."""
    skill_yaml = {
        "name": meta.get("name", skill_name),
        "category": category,
        "version": str(meta.get("version", "1.0")),
        "description": meta.get("purpose", meta.get("description", "")),
        "priority": meta.get("priority", "medium"),
    }

    # Capabilities
    caps = SKILL_CAPABILITIES.get(skill_name, [])
    if caps:
        skill_yaml["capabilities"] = caps

    # Knowledge references (subfolders that exist)
    knowledge = []
    for folder in ["prompts", "examples", "data", "templates"]:
        knowledge.append(folder)
    skill_yaml["knowledge"] = knowledge

    # Trigger keywords for skill router
    triggers = SKILL_TRIGGERS.get(skill_name, [])
    if triggers:
        skill_yaml["trigger"] = triggers

    # Tags
    tags = meta.get("tags", [])
    if tags:
        skill_yaml["tags"] = tags

    # Graph relationships
    if meta.get("graph_requires"):
        skill_yaml["graph_requires"] = meta["graph_requires"]
    if meta.get("graph_compatible"):
        skill_yaml["graph_compatible"] = meta["graph_compatible"]
    if meta.get("graph_conflicts"):
        skill_yaml["graph_conflicts"] = meta["graph_conflicts"]
    if meta.get("conflicts_with"):
        skill_yaml["conflicts_with"] = meta["conflicts_with"]

    # Extended metadata
    if meta.get("when_to_use"):
        skill_yaml["when_to_use"] = meta["when_to_use"]
    if meta.get("when_not_to_use"):
        skill_yaml["when_not_to_use"] = meta["when_not_to_use"]
    if meta.get("inputs"):
        skill_yaml["inputs"] = meta["inputs"]
    if meta.get("outputs"):
        skill_yaml["outputs"] = meta["outputs"]
    if meta.get("constraints"):
        skill_yaml["constraints"] = meta["constraints"]
    if meta.get("success_criteria"):
        skill_yaml["success_criteria"] = meta["success_criteria"]

    return skill_yaml


def migrate_skill_file(md_path: Path, skills_root: Path, dry_run: bool = False) -> str:
    """Migrate a single .md skill file to the new structure.
    Returns a status message.
    """
    content = md_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)

    # Determine skill name (from frontmatter) and folder name (from file stem)
    skill_name = meta.get("name", md_path.stem)
    # Use file stem for folder name to avoid collisions (e.g. taste.md vs taste_v1.1.md)
    folder_name = md_path.stem.lower().replace(" ", "-").replace("_", "-").replace(".", "-")

    # Determine category
    fm_category = meta.get("category", "general")
    category = get_category_for_skill(md_path.stem, fm_category)

    # Target directory
    target_dir = skills_root / category / folder_name
    target_skill_yaml = target_dir / "skill.yaml"
    target_skill_md = target_dir / "SKILL.md"

    if dry_run:
        return f"  [DRY-RUN] {md_path.name} → {category}/{folder_name}/"

    # Create directory structure
    target_dir.mkdir(parents=True, exist_ok=True)
    for subfolder in ["prompts", "examples", "data", "templates"]:
        (target_dir / subfolder).mkdir(exist_ok=True)
        # Add .gitkeep to empty folders
        gitkeep = target_dir / subfolder / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("")

    # Write skill.yaml
    skill_yaml_data = build_skill_yaml(meta, skill_name, category)
    with open(target_skill_yaml, "w", encoding="utf-8") as f:
        yaml.dump(skill_yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Write SKILL.md (body only, with a title header)
    title = meta.get("name", skill_name)
    skill_md_content = f"# {title}\n\n{body}\n"
    target_skill_md.write_text(skill_md_content, encoding="utf-8")

    return f"  ✓ {md_path.name} → {category}/{folder_name}/"


def migrate_skills_directory(skills_root: Path, dry_run: bool = False):
    """Migrate all skills in a directory to the new structure."""
    print(f"\n{'='*60}")
    print(f"Migrating: {skills_root}")
    print(f"{'='*60}")

    if not skills_root.exists():
        print(f"  ⚠ Directory not found: {skills_root}")
        return

    # Collect all .md files (flat + roles/)
    md_files = []
    for f in sorted(skills_root.iterdir()):
        if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"):
            md_files.append(f)
        elif f.is_dir() and f.name == "roles":
            for rf in sorted(f.iterdir()):
                if rf.is_file() and rf.suffix == ".md":
                    md_files.append(rf)

    if not md_files:
        print("  No .md skill files found.")
        return

    print(f"  Found {len(md_files)} skill files to migrate.\n")

    # Create category folders
    if not dry_run:
        for cat in ALL_CATEGORIES:
            (skills_root / cat).mkdir(exist_ok=True)

    # Migrate each file
    for md_file in md_files:
        status = migrate_skill_file(md_file, skills_root, dry_run)
        print(status)

    # Remove old flat files and roles/ folder
    if not dry_run:
        print("\n  Cleaning up old files...")
        for md_file in md_files:
            md_file.unlink()
            print(f"    ✗ Removed: {md_file.relative_to(skills_root)}")

        # Remove roles/ if empty
        roles_dir = skills_root / "roles"
        if roles_dir.exists() and not any(roles_dir.iterdir()):
            roles_dir.rmdir()
            print(f"    ✗ Removed: roles/")

    print(f"\n  ✅ Migration complete for {skills_root}")


def verify_structure(skills_root: Path):
    """Print the resulting directory tree."""
    print(f"\n{'='*60}")
    print(f"Resulting structure: {skills_root}")
    print(f"{'='*60}")

    for cat_dir in sorted(skills_root.iterdir()):
        if not cat_dir.is_dir():
            continue
        skill_dirs = [d for d in sorted(cat_dir.iterdir()) if d.is_dir()]
        print(f"\n  {cat_dir.name}/ ({len(skill_dirs)} skills)")
        for skill_dir in skill_dirs:
            files = [f.name for f in sorted(skill_dir.iterdir()) if f.is_file() and f.name != ".gitkeep"]
            print(f"    └── {skill_dir.name}/  [{', '.join(files)}]")


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("[DRY RUN MODE] no files will be modified.\n")

    # Migrate project root skills/
    migrate_skills_directory(SKILLS_ROOT, dry_run)

    # Migrate api/skills/
    migrate_skills_directory(API_SKILLS_ROOT, dry_run)

    # Verify
    if not dry_run:
        verify_structure(SKILLS_ROOT)
        verify_structure(API_SKILLS_ROOT)

    print("\n\nDone! Next steps:")
    print("  1. Update api/api/skill_registry.py to scan new structure")
    print("  2. Update api/api/skill_router.py to read trigger from skill.yaml")
    print("  3. Test the app")
