"""
Agent Template Loader — loads static agent templates from api/agents/ directory.

Provides:
- load_template(template_id): Load a single template YAML by ID
- load_all_templates(): Load all templates into a dict
- get_catalog_text(): Format catalog for CEO prompt injection
- search_templates(query, top_k): Simple keyword search over templates
"""

import os
from pathlib import Path
from typing import Optional

import yaml

from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)

# Template directory: api/agents/ (relative to project root)
_AGENTS_DIR: Optional[Path] = None
_TEMPLATE_CACHE: dict[str, dict] = {}
_CATALOG_CACHE: Optional[dict] = None


def _get_agents_dir() -> Path:
    """Resolve the agents/ directory path."""
    global _AGENTS_DIR
    if _AGENTS_DIR is not None:
        return _AGENTS_DIR

    # Try relative to this file: api/api/dynamic/ -> api/agents/
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "agents",  # api/agents/
        Path.cwd() / "api" / "agents",
        Path.cwd() / "agents",
    ]
    for c in candidates:
        if c.is_dir() and (c / "_catalog.yaml").exists():
            _AGENTS_DIR = c
            return c

    # Fallback: first candidate even if not fully valid
    _AGENTS_DIR = candidates[0]
    _log.warning("Agents directory not found at expected paths, using: %s", _AGENTS_DIR)
    return _AGENTS_DIR


def load_template(template_id: str) -> Optional[dict]:
    """Load a single agent template by ID. Searches all category subdirectories."""
    if template_id in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[template_id]

    agents_dir = _get_agents_dir()
    # Search in category subdirectories
    for category_dir in agents_dir.iterdir():
        if not category_dir.is_dir():
            continue
        yaml_file = category_dir / f"{template_id}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    data["_category"] = category_dir.name
                    data["_file"] = str(yaml_file)
                    _TEMPLATE_CACHE[template_id] = data
                    return data
            except Exception as e:
                _log.warning("Failed to load template '%s': %s", yaml_file, e)

    _log.warning("Template '%s' not found in %s", template_id, agents_dir)
    return None


def load_all_templates() -> dict[str, dict]:
    """Load all templates from all category directories. Returns {id: template_dict}."""
    if _TEMPLATE_CACHE:
        return dict(_TEMPLATE_CACHE)

    agents_dir = _get_agents_dir()
    for category_dir in sorted(agents_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        for yaml_file in sorted(category_dir.glob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, dict) and "id" in data:
                    data["_category"] = category_dir.name
                    data["_file"] = str(yaml_file)
                    _TEMPLATE_CACHE[data["id"]] = data
            except Exception as e:
                _log.warning("Failed to load template file '%s': %s", yaml_file, e)

    _log.info("Loaded %d agent templates from %s", len(_TEMPLATE_CACHE), agents_dir)
    return dict(_TEMPLATE_CACHE)


def load_catalog() -> dict:
    """Load the _catalog.yaml index file."""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    catalog_file = _get_agents_dir() / "_catalog.yaml"
    try:
        with open(catalog_file, "r", encoding="utf-8") as f:
            _CATALOG_CACHE = yaml.safe_load(f) or {}
    except Exception as e:
        _log.warning("Failed to load _catalog.yaml: %s", e)
        _CATALOG_CACHE = {}
    return _CATALOG_CACHE


def get_catalog_text(max_entries: int = 100) -> str:
    """Format the template catalog as a compact text block for CEO prompt injection.

    Format per entry:
      [category/id] display_name — capability (tags: ...) [AVOID: ...]
    """
    catalog = load_catalog()
    if not catalog:
        # Fallback: build from loaded templates
        templates = load_all_templates()
        lines = []
        for tid, t in sorted(templates.items()):
            cat = t.get("category", t.get("_category", "unknown"))
            name = t.get("display_name", tid)
            cap = t.get("capability", t.get("description", ""))
            tags = ", ".join(t.get("tags", [])[:5])
            avoid = t.get("avoid_when", [])
            avoid_str = f" [AVOID: {', '.join(avoid)}]" if avoid else ""
            cost_tier = (t.get("cost_profile") or {}).get("tier", "mid")
            lines.append(f"  [{cat}/{tid}] {name} — {cap} (tags: {tags}) [COST: {cost_tier}]{avoid_str}")
        return "\n".join(lines[:max_entries])

    lines: list[str] = []
    categories = catalog.get("categories", {})
    for cat_name, cat_data in categories.items():
        cat_desc = cat_data.get("description", "")
        lines.append(f"## {cat_name} ({cat_desc})")
        for entry in cat_data.get("templates", []):
            tid = entry.get("id", "")
            display = entry.get("display_name", tid)
            cap = entry.get("capability", "")
            tags = ", ".join(entry.get("tags", [])[:6])
            avoid = entry.get("avoid_when", [])
            avoid_str = f" [AVOID: {', '.join(avoid)}]" if avoid else ""
            cost_tier = entry.get("cost_tier", "mid")
            lines.append(f"  [{cat_name}/{tid}] {display} — {cap} (tags: {tags}) [COST: {cost_tier}]{avoid_str}")
        lines.append("")

    return "\n".join(lines)


def search_templates(query: str, top_k: int = 10) -> list[dict]:
    """Simple keyword search over template tags, description, and display_name.

    Returns list of (template_dict, score) sorted by relevance.
    """
    templates = load_all_templates()
    query_lower = query.lower()
    query_tokens = set(query_lower.replace(",", " ").replace("/", " ").split())

    scored: list[tuple[dict, int]] = []
    for tid, t in templates.items():
        score = 0
        # Exact ID match
        if tid in query_lower:
            score += 100
        # Tag matches
        tags = set(tag.lower() for tag in t.get("tags", []))
        score += len(query_tokens & tags) * 10
        # Description/name token overlap
        text = f"{t.get('display_name', '')} {t.get('description', '')}".lower()
        for token in query_tokens:
            if token in text:
                score += 3
        # Category match
        cat = t.get("category", t.get("_category", "")).lower()
        if cat in query_tokens:
            score += 15

        if score > 0:
            scored.append((t, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[:top_k]]


def resolve_template_for_node(node: dict) -> dict:
    """Given a plan node with 'template_id', resolve and merge template properties.

    Returns a fully-resolved node dict with:
    - system_prompt (from template + CEO's subtask-specific additions)
    - tools (from template)
    - skills (from template)
    - role (template category)
    - model_prefs (from template, for model selector)

    If no template_id, returns node unchanged (backward compatibility).
    """
    template_id = node.get("template_id")
    if not template_id:
        return node

    template = load_template(template_id)
    if not template:
        _log.warning("Template '%s' not found, using node as-is", template_id)
        return node

    resolved = dict(node)

    # Template provides base system_prompt; CEO's system_prompt (if any) is appended
    template_prompt = template.get("system_prompt", "")
    ceo_prompt = node.get("system_prompt", "")
    if ceo_prompt:
        resolved["system_prompt"] = f"{template_prompt}\n\n[ADDITIONAL INSTRUCTIONS]\n{ceo_prompt}"
    else:
        resolved["system_prompt"] = template_prompt

    # Tools from template
    resolved["tools"] = template.get("tools", ["file", "terminal"])

    # Skills from template (CEO can override via node's skills field)
    if not resolved.get("skills"):
        resolved["skills"] = template.get("skills", [])

    # Role from template category
    if not resolved.get("role"):
        resolved["role"] = template.get("category", template.get("_category", "specialist"))

    # Type: derive from tools
    tools = resolved.get("tools", [])
    if "web_search" in tools:
        resolved["type"] = "llm+web_search"
    elif "image_gen" in tools:
        resolved["type"] = "llm+image_tool"
    elif "terminal" in tools:
        resolved["type"] = "llm+terminal"
    else:
        resolved["type"] = "llm"

    # Model preferences (for model selector downstream)
    resolved["_model_prefs"] = template.get("model_prefs", {})

    # Runtime configuration (max_iterations, max_tokens, temperature, etc.)
    resolved["_runtime"] = template.get("runtime", {})

    # Capability score (8 dimensions, 1-10 scale)
    resolved["_capability_score"] = template.get("capability_score", {})

    # Cost profile (tier + estimated tokens)
    resolved["_cost_profile"] = template.get("cost_profile", {})

    # Model preference (per-task-type model ordering)
    resolved["_model_preference"] = template.get("model_preference", {})

    # Success criteria (informational)
    resolved["_success_criteria"] = template.get("success_criteria", [])

    # Display name for logging
    resolved["_display_name"] = template.get("display_name", template_id)

    _log.info("Resolved template '%s' (%s) for node '%s'",
              template_id, resolved.get("_display_name"), node.get("name"))

    return resolved


def invalidate_cache():
    """Clear template cache (useful for hot-reload during development)."""
    global _CATALOG_CACHE
    _TEMPLATE_CACHE.clear()
    _CATALOG_CACHE = None
