"""
Skill Registry — Curated and Auto-distilled Skill Loader.

Scans the skills/ directory for .md skill files, indexes them by name,
and provides lookup APIs for the CEO planner and AgentCompiler.

Architecture:
  CEO selects skill NAMES only  →  Harness reads .md files  →  Injects into agent system_prompt
"""
import logging
import os
import sys
import json
import time
import yaml
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


# Skill Lifecycle States
SKILL_DRAFT = "draft"          # Freshly auto-distilled, untrusted
SKILL_REVIEW = "review"        # Under evaluation
SKILL_APPROVED = "approved"    # Verified, appears in default CEO catalog
SKILL_REJECTED = "rejected"    # Failed review, excluded from catalog

_MANIFEST_FILE = "_skill_manifest.json"


def _resolve_skills_dir() -> Path:
    """Resolve the skills directory relative to the project root.
    단일 정본: 프로젝트 루트 skills/ (dev) 또는 _MEIPASS/skills/ (PyInstaller 번들).
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller: spec이 ('dist_new/skills', 'skills')로 번들 → _MEIPASS/skills/
        return Path(sys._MEIPASS) / "skills"
    else:
        base = Path(__file__).resolve().parent.parent.parent  # api/api/ → api/ → project root
        return base / "skills"


def _resolve_auto_skills_dir() -> Path:
    """Resolve the auto-distilled skills directory (~/.hermes/skills/auto/)."""
    return Path.home() / ".hermes" / "skills" / "auto"


def _resolve_profile_auto_skills_dir() -> Optional[Path]:
    """Resolve the profile-specific auto skills directory if a non-default profile is active.
    Returns None if the default profile is active (global dir is sufficient).
    """
    try:
        from api.profiles import get_active_profile_name
        active = get_active_profile_name()
        if active and active != "default":
            profile_auto = Path.home() / ".hermes" / "profiles" / active / "skills" / "auto"
            if profile_auto.exists():
                return profile_auto
    except Exception:
        pass
    return None


def _get_all_auto_skills_dirs() -> list[Path]:
    """Return all auto-skills directories to scan (global + profile-specific)."""
    dirs = [_resolve_auto_skills_dir()]
    profile_dir = _resolve_profile_auto_skills_dir()
    if profile_dir:
        dirs.append(profile_dir)
    return dirs


def _resolve_hermes_user_skills_dir() -> Path:
    """Resolve ~/.hermes/skills/ — bundled + user-installed skills."""
    return Path.home() / ".hermes" / "skills"


def _resolve_engine_skills_dir() -> Path:
    """Resolve hermes-agent/skills/ — engine-bundled skill categories."""
    if hasattr(sys, '_MEIPASS'):
        base = Path(sys.executable).parent.resolve()
    else:
        base = Path(__file__).resolve().parent.parent.parent
    return base / "hermes-agent" / "skills"


class SkillEntry:
    """Represents a single indexed skill with YAML metadata."""
    __slots__ = (
        "name", "label", "path", "title", "source", "content", "lifecycle",
        "version", "category", "priority", "tags", "conflicts_with",
        "purpose", "when_to_use", "when_not_to_use",
        "inputs", "outputs", "examples", "constraints", "success_criteria",
        "graph_requires", "graph_compatible", "graph_conflicts",
        "style_card_refs",
        "trigger", "capabilities", "enabled",
        "mcp_servers",
    )

    def __init__(
        self, name: str, path: Path, title: str, source: str, content: str,
        label: str = "",
        lifecycle: str = "approved", version: str = "1.0", category: str = "general",
        priority: str = "medium", tags: list = None, conflicts_with: list = None,
        purpose: str = "", when_to_use: str = "", when_not_to_use: str = "",
        inputs: str = "", outputs: str = "", examples: str = "",
        constraints: str = "", success_criteria: str = "",
        graph_requires: list = None, graph_compatible: list = None,
        graph_conflicts: list = None,
        style_card_refs: list = None,
        trigger: list = None, capabilities: list = None,
        enabled: bool = True,
        mcp_servers: list = None,
    ):
        self.name = name
        self.label = label
        self.path = path
        self.title = title
        self.source = source
        self.content = content
        self.lifecycle = lifecycle
        self.version = version
        self.category = category
        self.priority = priority
        self.tags = tags or []
        self.conflicts_with = conflicts_with or []
        self.purpose = purpose
        self.when_to_use = when_to_use
        self.when_not_to_use = when_not_to_use
        self.inputs = inputs
        self.outputs = outputs
        self.examples = examples
        self.constraints = constraints
        self.success_criteria = success_criteria
        self.graph_requires = graph_requires or []
        self.graph_compatible = graph_compatible or []
        self.graph_conflicts = graph_conflicts or []
        self.style_card_refs = style_card_refs or []
        self.trigger = trigger or []
        self.capabilities = capabilities or []
        self.enabled = enabled
        self.mcp_servers = mcp_servers or []

    def to_catalog_line(self) -> str:
        """Return a one-line summary for the CEO skill catalog."""
        tags_str = ", ".join(self.tags[:3]) if self.tags else ""
        requires_str = f" requires:[{', '.join(self.graph_requires[:3])}]" if self.graph_requires else ""
        compat_str = f" compatible:[{', '.join(self.graph_compatible[:3])}]" if self.graph_compatible else ""
        return f"- {self.name} v{self.version} [{self.category}] ({self.priority}): {self.title}  tags:[{tags_str}]{requires_str}{compat_str}"


class SkillRegistry:
    """
    Central registry that indexes and serves skill files.

    Usage:
        registry = SkillRegistry()
        catalog = registry.get_catalog_text()       # For CEO prompt
        content = registry.load_skills(["taste", "security"])  # For agent injection
    """

    def __init__(self):
        self._skills: dict[str, SkillEntry] = {}
        self._all_entries: list[SkillEntry] = []
        self._scan()

    def _scan(self) -> None:
        """Scan curated and auto-distilled skill directories."""
        self._skills.clear()
        self._all_entries.clear()

        # 1. Curated skills (project root skills/) — single source of truth
        curated_dir = _resolve_skills_dir()
        if curated_dir.exists():
            self._scan_directory(curated_dir, source="curated")

        # 2. Auto-distilled skills (global + profile-specific) — lifecycle-managed
        for auto_dir in _get_all_auto_skills_dirs():
            if auto_dir.exists():
                manifest = self._load_manifest(auto_dir)
                self._scan_directory(auto_dir, source="auto", manifest=manifest)

        # 3. Bundled skills (~/.hermes/skills/) — category directories with SKILL.md
        hermes_user_dir = _resolve_hermes_user_skills_dir()
        if hermes_user_dir.exists():
            self._scan_directory(hermes_user_dir, source="bundled")

        # 4. Engine skills (hermes-agent/skills/) — engine-bundled categories
        engine_dir = _resolve_engine_skills_dir()
        if engine_dir.exists():
            self._scan_directory(engine_dir, source="bundled")

        # 5. Plugin skills (DAON plugins/) — qualified as "plugin:skill".
        #    세션에서 ON 된 플러그인의 SKILL.md 를 소스 "plugin" 으로 인덱싱하여
        #    Dynamic Harness 컴파일러(load_skills)와 CEO 카탈로그가 인식하게 한다.
        #    (plugin_gateway 는 plugin_state 만 의존하므로 순환 import 없음.)
        try:
            from api.plugin_gateway import list_installed_plugins
            for _plugin in list_installed_plugins():
                for _s in (_plugin.get("skills") or []):
                    _skill_path = Path(_s.get("path") or "")
                    if not _skill_path.exists():
                        continue
                    try:
                        self._load_skill_entry(
                            _skill_path, _s.get("qualified") or _s["name"],
                            "plugin", None,
                        )
                    except Exception:
                        _logger.warning(
                            "Failed to index plugin skill %s", _s.get("qualified"), exc_info=True
                        )
        except Exception:
            _logger.warning("Failed to scan plugin skills for skill registry", exc_info=True)

        # Build version-aware indexing
        # Group entries by normalized name
        from collections import defaultdict
        grouped = defaultdict(list)
        for entry in self._all_entries:
            key = entry.name.strip().lower().replace(" ", "-")
            grouped[key].append(entry)

        # Helper to parse semantic versioning (or basic float representation) for sorting
        def parse_version(v_str):
            try:
                return [int(x) for x in v_str.split(".")]
            except ValueError:
                try:
                    return [float(v_str)]
                except ValueError:
                    return [0]

        # Register entries with name@version and make the latest version the default
        for base_name, entries in grouped.items():
            # Sort entries by version descending (highest version first)
            entries.sort(key=lambda e: parse_version(e.version), reverse=True)

            # Default key (e.g. "taste") maps to the latest version
            self._skills[base_name] = entries[0]

            # Specific version keys (e.g. "taste@1.0")
            for entry in entries:
                self._skills[f"{base_name}@{entry.version}"] = entry

            # Also register the folder name and filename stem to support historical naming fallback
            for entry in entries:
                folder_key = entry.path.parent.name.strip().lower().replace(" ", "-")
                if folder_key not in self._skills:
                    self._skills[folder_key] = entry
                stem_key = entry.path.stem.strip().lower().replace(" ", "-")
                if stem_key not in self._skills:
                    self._skills[stem_key] = entry

    # Directories to skip during scanning (deactivated skills)
    _SKIP_DIRS = {"Archive", "archive", "_archive"}

    def _scan_directory(self, directory: Path, source: str, manifest: dict = None) -> None:
        """Scan a single directory for skill files and collect them into _all_entries.

        Supports two structures:
          - New categorized: Category/skill-name/SKILL.md + skill.yaml
          - Legacy flat: *.md files directly in directory
          - Auto-distilled: subdirectories with SKILL.md files

        Skips Archive/ directories (deactivated skills).
        """
        if source == "auto":
            # Auto-distilled: scan subdirectories for SKILL.md
            file_iter = sorted(directory.rglob("SKILL.md"))
            for md_file in file_iter:
                # Skip Archive directories
                if any(part in self._SKIP_DIRS for part in md_file.relative_to(directory).parts):
                    continue
                if md_file.name.startswith("_"):
                    continue
                self._load_skill_entry(md_file, md_file.parent.name, source, manifest)
        else:
            # Curated / Bundled: detect new categorized structure
            # New structure: Category/skill-name/SKILL.md
            skill_mds = sorted(directory.rglob("SKILL.md"))
            loaded_dirs = set()

            for md_file in skill_mds:
                # Skip Archive directories
                rel_parts = md_file.relative_to(directory).parts
                if any(part in self._SKIP_DIRS for part in rel_parts):
                    continue
                if md_file.name.startswith("_"):
                    continue
                skill_dir = md_file.parent
                loaded_dirs.add(skill_dir.resolve())
                name = skill_dir.name
                self._load_skill_entry(md_file, name, source, manifest)

            # Fallback: also scan flat .md files (legacy structure)
            # Only pick up .md files NOT already covered by SKILL.md structure
            for md_file in sorted(directory.rglob("*.md")):
                if md_file.name == "SKILL.md":
                    continue  # Already handled above
                if md_file.name.startswith("_"):
                    continue
                # Skip Archive directories
                if any(part in self._SKIP_DIRS for part in md_file.relative_to(directory).parts):
                    continue
                # Skip if this .md is inside a skill folder that was already loaded
                if md_file.parent.resolve() in loaded_dirs:
                    continue
                name = md_file.stem
                self._load_skill_entry(md_file, name, source, manifest)

    def _load_skill_entry(self, md_file: Path, name: str, source: str, manifest: dict = None) -> None:
        """Load a single skill from a .md file, optionally merging skill.yaml metadata."""
        try:
            raw_content = md_file.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(raw_content)

            # Try to load skill.yaml from the same directory (new structure)
            yaml_meta = self._load_skill_yaml(md_file.parent)
            if yaml_meta:
                # skill.yaml takes precedence for structured fields
                for key in ("name", "label", "category", "version", "priority", "description",
                            "capabilities", "trigger", "tags", "knowledge", "enabled",
                            "when_to_use", "when_not_to_use", "inputs", "outputs",
                            "constraints", "success_criteria",
                            "graph_requires", "graph_compatible", "graph_conflicts",
                            "mcp_servers", "mcp"):
                    if key in yaml_meta and yaml_meta[key]:
                        meta[key] = yaml_meta[key]

            title = self._extract_title(body, meta.get("name", name))

            # Check enabled flag (default: True)
            enabled = meta.get("enabled", True)
            if enabled is False or str(enabled).lower() in ("false", "no", "0"):
                return  # Skip disabled skills entirely

            # Determine lifecycle status
            # Plugin skills are explicitly installed via DAON plugins/, so they
            # are treated as approved (available) like curated/bundled skills.
            if source in ("curated", "bundled", "plugin"):
                lifecycle = SKILL_APPROVED
            elif manifest and name in manifest:
                lifecycle = manifest[name].get("status", SKILL_DRAFT)
            else:
                lifecycle = SKILL_DRAFT

            # Infer category from folder structure if not in metadata
            category = meta.get("category", "general")
            if category == "general" and md_file.parent.parent != md_file.parent:
                # e.g. skills/Design/ui-ux-pro/SKILL.md → category = "Design"
                parent_name = md_file.parent.parent.name
                if parent_name and parent_name not in self._SKIP_DIRS:
                    category = parent_name
            # Normalize category so 'creative' == 'Creative' (first letter uppercase)
            if category and category != "general":
                category = category[0].upper() + category[1:]

            # Plugin skills are indexed under their qualified "plugin:skill" name
            # so forced_skills/load_skills lookups resolve deterministically
            # regardless of the SKILL.md frontmatter's own name field.
            entry_name = name if source == "plugin" else meta.get("name", name)

            mcp_srv = meta.get("mcp_servers") or meta.get("mcp") or []
            if isinstance(mcp_srv, str):
                mcp_srv = [s.strip() for s in mcp_srv.split(",") if s.strip()]

            entry = SkillEntry(
                name=entry_name,
                label=meta.get("label", ""),
                path=md_file,
                title=title,
                source=source,
                content=body,
                lifecycle=lifecycle,
                version=str(meta.get("version", "1.0")),
                category=category,
                priority=meta.get("priority", "medium"),
                tags=meta.get("tags", []),
                conflicts_with=meta.get("conflicts_with", []),
                purpose=meta.get("purpose", meta.get("description", "")),
                when_to_use=meta.get("when_to_use", ""),
                when_not_to_use=meta.get("when_not_to_use", ""),
                inputs=meta.get("inputs", meta.get("input", "")),
                outputs=meta.get("outputs", meta.get("output", "")),
                examples=meta.get("examples", ""),
                constraints=meta.get("constraints", ""),
                success_criteria=meta.get("success_criteria", ""),
                graph_requires=meta.get("graph_requires", []),
                graph_compatible=meta.get("graph_compatible", []),
                graph_conflicts=meta.get("graph_conflicts", []),
                style_card_refs=meta.get("style_card_refs", []),
                trigger=meta.get("trigger", []),
                capabilities=meta.get("capabilities", []),
                enabled=True,
                mcp_servers=mcp_srv,
            )
            self._all_entries.append(entry)
        except Exception as e:
            print(f"[SkillRegistry] Warning: Failed to load skill: {e}")

    @staticmethod
    def _load_skill_yaml(skill_dir: Path) -> dict:
        """Load skill.yaml from a skill directory. Returns empty dict if not found."""
        yaml_path = skill_dir / "skill.yaml"
        if not yaml_path.exists():
            return {}
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            _logger.warning("[SkillRegistry] Failed to parse skill.yaml at %s: %s", yaml_path, e)
            return {}

    def detect_conflicts(self, skill_names: list[str]) -> list[str]:
        """Detect and return conflict warnings between the selected skills.
        
        Checks both 'conflicts_with' (legacy) and 'graph_conflicts' (new) fields.
        """
        conflicts = []
        normalized_names = set()
        name_to_original = {}
        for name in skill_names:
            base_name = name.partition("@")[0].partition(":")[0].strip().lower().replace(" ", "-")
            normalized_names.add(base_name)
            name_to_original[base_name] = name

        for name in normalized_names:
            entry = self.get_skill(name)
            if not entry:
                continue

            # Check legacy conflicts_with
            for target_conflict in entry.conflicts_with:
                target_normalized = target_conflict.strip().lower().replace(" ", "-")
                if target_normalized in normalized_names:
                    pair = sorted([entry.name, target_normalized])
                    conflict_msg = f"Conflict detected: Skill '{pair[0]}' is incompatible with skill '{pair[1]}'."
                    if conflict_msg not in conflicts:
                        conflicts.append(conflict_msg)

            # Check graph_conflicts (new skill graph)
            for target_conflict in entry.graph_conflicts:
                target_normalized = target_conflict.strip().lower().replace(" ", "-")
                if target_normalized in normalized_names:
                    pair = sorted([entry.name, target_normalized])
                    conflict_msg = f"Conflict (graph): Skill '{pair[0]}' conflicts with skill '{pair[1]}'."
                    if conflict_msg not in conflicts:
                        conflicts.append(conflict_msg)

        return conflicts

    def get_skill_graph_context(self, skill_names: list[str]) -> str:
        """Build a Skill Graph context block for the CEO prompt.
        
        For each selected skill, lists:
        - requires: skills that MUST be also selected
        - compatible: skills that are recommended to pair with
        - conflicts: skills that must NOT be selected together
        
        Also performs transitive closure: if skill A requires B, and B requires C,
        C is also reported as a transitive requirement of A.
        
        Returns a markdown-formatted string.
        """
        if not skill_names:
            return ""

        lines = ["[SKILL GRAPH — Relationship Constraints]"]

        # Normalize skill names
        normalized = set()
        original_map = {}
        for name in skill_names:
            base = name.partition("@")[0].partition(":")[0].strip().lower().replace(" ", "-")
            normalized.add(base)
            original_map[base] = name

        # Collect all graph relationships
        requires_map: dict[str, list[str]] = {}
        compat_map: dict[str, list[str]] = {}
        conflict_map: dict[str, list[str]] = {}

        for name in normalized:
            entry = self.get_skill(name)
            if not entry:
                continue
            if entry.graph_requires:
                requires_map[name] = [r.strip().lower().replace(" ", "-") for r in entry.graph_requires]
            if entry.graph_compatible:
                compat_map[name] = [c.strip().lower().replace(" ", "-") for c in entry.graph_compatible]
            if entry.graph_conflicts:
                conflict_map[name] = [c.strip().lower().replace(" ", "-") for c in entry.graph_conflicts]

        # Transitive closure for requires (depth-limited to 3 hops)
        def transitive_requires(skill, depth=0, visited=None):
            if visited is None:
                visited = set()
            if depth > 3 or skill in visited:
                return []
            visited.add(skill)
            result = []
            for req in requires_map.get(skill, []):
                if req not in visited:
                    result.append(req)
                    result.extend(transitive_requires(req, depth + 1, visited))
            return result

        # Report requires
        if requires_map:
            lines.append("\n### Hard Requirements (must include)")
            for name in requires_map:
                direct = requires_map[name]
                trans = transitive_requires(name)
                all_req = list(dict.fromkeys(direct + trans))  # dedup preserve order
                trans_only = [r for r in trans if r not in direct]
                parts = []
                if direct:
                    parts.append(f"direct: {', '.join(direct)}")
                if trans_only:
                    parts.append(f"transitive: {', '.join(trans_only)}")
                lines.append(f"- **{name}** → {', '.join(parts)}")

        # Report compatible (recommendations)
        if compat_map:
            lines.append("\n### Compatibility Recommendations (auto-recommend pairing)")
            for name in compat_map:
                compat_list = compat_map[name]
                matched = [c for c in compat_list if c in normalized]
                suggested = [c for c in compat_list if c not in normalized]
                parts = []
                if matched:
                    parts.append(f"already selected: {', '.join(matched)}")
                if suggested:
                    parts.append(f"suggest adding: {', '.join(suggested)}")
                lines.append(f"- **{name}** → compatible with {', '.join(parts)}")

        # Report conflicts
        if conflict_map:
            lines.append("\n### Hard Conflicts (must NOT pair)")
            for name in conflict_map:
                conflict_list = conflict_map[name]
                active = [c for c in conflict_list if c in normalized]
                if active:
                    lines.append(f"- **{name}** ⚠️ conflicts with: {', '.join(active)}")

        if not requires_map and not compat_map and not conflict_map:
            lines.append("\n(No graph relationships defined for selected skills)")

        return "\n".join(lines)

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from skill file content.
        
        Lightweight parser that handles basic YAML without PyYAML dependency.
        Returns (metadata_dict, body_content).
        """
        if not content.startswith("---"):
            return {}, content

        lines = content.split("\n")
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_idx = i
                break

        if end_idx == -1:
            return {}, content

        yaml_lines = lines[1:end_idx]
        body = "\n".join(lines[end_idx + 1:]).strip()
        meta = {}
        current_key = None
        current_list = None

        for line in yaml_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # List item (  - value)
            if stripped.startswith("- ") and current_key:
                if current_list is None:
                    current_list = []
                current_list.append(stripped[2:].strip().strip('"').strip("'"))
                meta[current_key] = current_list
                continue

            # Key: value pair
            if ":" in stripped:
                # Save previous list if any
                current_list = None
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                current_key = key

                if val == "[]":
                    meta[key] = []
                    current_list = None
                elif val:
                    meta[key] = val
                # If val is empty, next lines might be a list

        return meta, body

    @staticmethod
    def _extract_title(content: str, fallback: str) -> str:
        """Extract the first H1 heading as the skill title."""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                title = stripped[2:].strip()
                # Remove trailing markdown emphasis
                for ch in ("—", "–", "-"):
                    if ch in title:
                        title = title.split(ch)[0].strip()
                return title
        return fallback

    def get_catalog_text(self) -> str:
        """
        Return a formatted catalog of all available skills for the CEO prompt.
        CEO uses this to select which skills to apply.
        Only curated and APPROVED auto-skills appear in the default catalog.
        Draft skills are listed separately as candidates for promotion.
        """
        if not self._skills:
            return "(No skills available)"

        lines = ["AVAILABLE SKILLS CATALOG:"]
        curated = [s for s in self._skills.values() if s.source == "curated"]
        bundled = [s for s in self._skills.values() if s.source == "bundled"]
        plugin = [s for s in self._skills.values() if s.source == "plugin"]
        approved_auto = [s for s in self._skills.values() if s.source == "auto" and s.lifecycle == SKILL_APPROVED]
        draft_auto = [s for s in self._skills.values() if s.source == "auto" and s.lifecycle == SKILL_DRAFT]

        if curated:
            lines.append("[Curated Skills - Always Available]")
            lines.extend(s.to_catalog_line() for s in curated)
        if bundled:
            lines.append("[Bundled Skills - Engine & User Installed]")
            lines.extend(s.to_catalog_line() for s in bundled)
        if plugin:
            lines.append("[Plugin Skills - DAON Plugins]")
            lines.extend(s.to_catalog_line() for s in plugin)
        if approved_auto:
            lines.append("[Approved Auto-Skills - Verified & Available]")
            lines.extend(s.to_catalog_line() for s in approved_auto)
        if draft_auto:
            lines.append(f"[Draft Auto-Skills - {len(draft_auto)} pending review, NOT available for selection]")

        return "\n".join(lines)

    def load_skills(self, skill_names: list[str]) -> str:
        """
        Load and concatenate the content of requested skills.
        Returns a formatted string ready for injection into agent system_prompt.
        """
        if not skill_names:
            return ""

        sections = []
        for name in skill_names:
            clean_name = name.strip().lower().replace(" ", "-")
            entry = self._skills.get(clean_name)
            if entry:
                sections.append(
                    f"=== SKILL: {entry.name} ({entry.source}) ===\n"
                    f"{entry.content}\n"
                    f"=== END SKILL: {entry.name} ==="
                )
            else:
                print(f"[SkillRegistry] Warning: Skill '{name}' not found in registry.")

        if not sections:
            return ""

        return (
            "\n[INJECTED SKILLS — You MUST follow these guidelines]\n"
            + "\n\n".join(sections)
            + "\n"
        )

    def load_skills_for_reviewer(self, skill_names: list[str]) -> str:
        """
        Load skills formatted for the reviewer agent.
        The reviewer checks compliance against these skills.
        """
        if not skill_names:
            return ""

        sections = []
        for name in skill_names:
            clean_name = name.strip().lower().replace(" ", "-")
            entry = self._skills.get(clean_name)
            if entry:
                sections.append(f"- {entry.name}: {entry.title}")

        if not sections:
            return ""

        return (
            "\n[SKILLS TO VERIFY COMPLIANCE AGAINST]\n"
            "The developer agents were given the following skills. "
            "You MUST verify that the output strictly adheres to the guidelines defined in each skill:\n"
            + "\n".join(sections)
            + "\n"
        )

    def get_skill(self, name: str) -> Optional[SkillEntry]:
        """Get a single skill entry by name."""
        return self._skills.get(name.strip().lower().replace(" ", "-"))

    def get(self, name: str, default=None) -> Optional[SkillEntry]:
        """Dict-like access alias for get_skill."""
        res = self.get_skill(name)
        return res if res is not None else default

    def list_skill_names(self) -> list[str]:
        """Return a sorted list of all registered skill names."""
        return sorted(self._skills.keys())

    def reload(self) -> None:
        """Force re-scan of skill directories."""
        self._scan()
        print(f"[SkillRegistry] Reloaded. {len(self._skills)} skills indexed.")

    # --- Manifest Management ---

    @staticmethod
    def _load_manifest(directory: Path) -> dict:
        """Load the skill lifecycle manifest from a directory."""
        manifest_path = directory / _MANIFEST_FILE
        if manifest_path.exists():
            try:
                return json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                _logger.warning("Failed to parse manifest at %s", manifest_path, exc_info=True)
                return {}
        return {}

    @staticmethod
    def _save_manifest(directory: Path, manifest: dict) -> None:
        """Save the skill lifecycle manifest to a directory."""
        manifest_path = directory / _MANIFEST_FILE
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def promote_skill(self, skill_name: str, to_status: str = SKILL_APPROVED) -> bool:
        """Promote an auto-distilled skill's lifecycle status.

        Usage:
            registry.promote_skill("auto_skill_xxx", SKILL_APPROVED)
        """
        entry = self.get_skill(skill_name)
        if not entry or entry.source != "auto":
            print(f"[SkillRegistry] Cannot promote '{skill_name}': not an auto-distilled skill.")
            return False

        # Find the correct manifest directory by checking where the skill actually lives
        auto_dir = None
        skill_path = entry.path
        for candidate in _get_all_auto_skills_dirs():
            try:
                skill_path.relative_to(candidate)
                auto_dir = candidate
                break
            except ValueError:
                continue
        if auto_dir is None:
            auto_dir = _resolve_auto_skills_dir()

        manifest = self._load_manifest(auto_dir)
        manifest[skill_name] = {
            "status": to_status,
            "promoted_at": time.time(),
            "previous_status": entry.lifecycle,
        }
        self._save_manifest(auto_dir, manifest)
        entry.lifecycle = to_status
        print(f"[SkillRegistry] Skill '{skill_name}' promoted to '{to_status}'.")
        return True

    def reject_skill(self, skill_name: str) -> bool:
        """Mark an auto-distilled skill as rejected."""
        return self.promote_skill(skill_name, to_status=SKILL_REJECTED)

    def get_lifecycle_summary(self) -> dict:
        """Return a summary of skill lifecycle states."""
        summary = {SKILL_DRAFT: [], SKILL_REVIEW: [], SKILL_APPROVED: [], SKILL_REJECTED: []}
        for s in self._skills.values():
            if s.source == "curated":
                summary[SKILL_APPROVED].append(s.name)
            else:
                status = s.lifecycle
                if status in summary:
                    summary[status].append(s.name)
        return summary

    @staticmethod
    def register_new_auto_skill(skill_path: Path, lifecycle: str = SKILL_DRAFT) -> None:
        """Register a newly auto-distilled skill in the manifest.
        Called by AutoSkillExtractor after saving a new skill file.

        skill_path is expected to be: auto/{skill_name}/SKILL.md
        Manifest is stored in: auto/_skill_manifest.json

        If a non-default profile is active, skills are registered in the
        profile-specific auto skills directory (~/.hermes/profiles/{profile}/skills/auto/).

        Args:
            skill_path: Path to the SKILL.md file.
            lifecycle: Initial lifecycle state (default: SKILL_DRAFT).
                       Pass SKILL_APPROVED for user-approved saves.
        """
        # skill_path = auto/{name}/SKILL.md → parent = auto/{name}/ → parent.parent = auto/
        skill_dir = skill_path.parent
        auto_dir = skill_dir.parent

        # If profile-specific auto dir is active, redirect registration there
        profile_auto_dir = _resolve_profile_auto_skills_dir()
        if profile_auto_dir and auto_dir == _resolve_auto_skills_dir():
            # Skill was created in global dir but a profile is active — move to profile dir
            profile_skill_dir = profile_auto_dir / skill_dir.name
            if not profile_skill_dir.exists():
                import shutil
                shutil.copytree(skill_dir, profile_skill_dir)
                auto_dir = profile_auto_dir
                skill_dir = profile_skill_dir
                print(f"[SkillRegistry] Copied auto-skill '{skill_dir.name}' from global to profile directory.")

        manifest_path = auto_dir / _MANIFEST_FILE
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                _logger.warning("Failed to parse existing manifest at %s, starting fresh", manifest_path, exc_info=True)
        
        skill_name = skill_dir.name
        manifest[skill_name] = {
            "status": lifecycle,
            "created_at": time.time(),
            "auto_distilled": True,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[SkillRegistry] Registered new auto-skill as {lifecycle.upper()}: {skill_name}")


# Module-level singleton
_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Get or create the global SkillRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        curated_count = sum(1 for s in _registry._skills.values() if s.source == 'curated')
        auto_count = sum(1 for s in _registry._skills.values() if s.source == 'auto')
        print(f"[SkillRegistry] Initialized: {curated_count} curated + {auto_count} auto-distilled = {len(_registry._skills)} total skills")
    return _registry
