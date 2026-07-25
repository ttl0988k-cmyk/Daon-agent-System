"""
Style Card extraction from design demos and frontend outputs.

Mirrors the Demo→Skill pipeline (skill_extractor.py) but for Style Cards:
- Extract Design DNA from a completed frontend project output
- Auto-evaluate and save as a Style Card YAML to the Reference Library
- Can be triggered manually (POST /api/style-cards/extract) or automatically
  from Dynamic Harness runs that produce visual output

Provides:
- _extract_and_save_style_card(): background task that distills a Style Card
  from Harness final output containing design work
"""

import json
import logging
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)


def _sanitize_style_card_id(task: str) -> str:
    """Create a clean, filesystem-safe style card ID from a task string."""
    normalized = unicodedata.normalize("NFC", task.strip())
    tokens = []
    for ch in normalized:
        if ch.isascii() and ch.isalnum():
            tokens.append(ch.lower())
        elif ch in (' ', '-', '_'):
            tokens.append('_')
    raw = "".join(tokens).strip("_")
    raw = re.sub(r"_+", "_", raw)
    return raw[:40] if raw else "unnamed_style"


def _extract_and_save_style_card(
    task: str,
    plan: dict,
    final_output: str,
    run_id: str,
    source_type: str = "harness",
) -> None:
    """Background task to extract a Style Card from a successful Harness run.

    Called by the orchestrator after a Harness run that produced UI/frontend output.
    Uses the same LLM distillation pattern as skill_extractor.py.

    Args:
        task: The original task description.
        plan: The DAG plan that was executed.
        final_output: The merged final output from all agents.
        run_id: Unique identifier for this Harness run.
        source_type: Origin label (e.g., "harness", "demo", "manual").
    """
    try:
        from api.dynamic.direct_calls import _call_direct

        def _worker():
            try:
                system_instruction = (
                    "You are an expert Design System Architect and Style Card Creator.\n"
                    "Your job is to analyze a completed frontend/UI project output and extract\n"
                    "its visual design identity into a structured YAML Style Card.\n\n"
                    "The Style Card must contain:\n"
                    "- id, name, category, sub_category, tags\n"
                    "- source_url (leave empty if none), source_type\n"
                    "- design_dna with colors (primary, accent, background, surface,\n"
                    "  text_primary, text_secondary, palette_name, palette_harmony),\n"
                    "  typography (heading_font, body_font, mono_font, scale,\n"
                    "  heading_weight, body_weight, letter_spacing_heading, line_height_body),\n"
                    "  layout (grid, max_width, padding_desktop, padding_mobile, alignment,\n"
                    "  glass_effect, border_radius, backdrop_blur),\n"
                    "  animation (entrance, hover, scroll, page_transition, duration_base,\n"
                    "  easing, motion_intensity),\n"
                    "  spacing (density, section_gap, element_gap)\n"
                    "- composition with structure list (e.g. [\"hero\", \"features\", \"cta\", \"footer\"])\n"
                    "- guidelines with do and dont lists\n"
                    "- compatible_with and conflicts_with (skill names)\n"
                    "- evaluation with auto-score (1-10 for originality, accessibility, responsiveness)\n\n"
                    "Output ONLY valid YAML wrapped in ```yaml code block. Nothing else.\n"
                    "IMPORTANT: Infer realistic design values from the code. Don't use placeholder values.\n"
                    "If you cannot determine a value, use a sensible default from modern design systems."
                )

                plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
                prompt = (
                    f"=== ORIGINAL TASK ===\n{task}\n\n"
                    f"=== EXECUTION DAG (PLAN) ===\n{plan_json}\n\n"
                    f"=== FINAL OUTPUT ===\n{final_output[:3000]}\n\n"
                    "Extract the visual design identity from this output as a YAML Style Card.\n"
                    "Identify the category (hero, navbar, cta, footer, card, form, etc.) from the context."
                )

                _logger.info(
                    "Distilling Style Card for run '%s' in background...", run_id
                )
                yaml_response = _call_direct(
                    prompt, system_instruction=system_instruction
                )

                # Extract YAML from code block
                clean_yaml = yaml_response.strip()
                yaml_match = re.search(
                    r"```(?:yaml)?\s*\n(.*?)\n```", clean_yaml, re.DOTALL
                )
                if yaml_match:
                    clean_yaml = yaml_match.group(1).strip()

                # Parse YAML
                from api.style_card import StyleCard, get_references_dir

                _yaml_load = None
                try:
                    from yaml import safe_load as _yaml_load
                except ImportError:
                    import json as _json

                    _yaml_load = _json.loads

                data = _yaml_load(clean_yaml) if _yaml_load else {}

                # Handle style_card wrapper
                if isinstance(data, dict) and "style_card" in data:
                    data = data["style_card"]

                # Generate ID from task if not present
                if not data.get("id"):
                    data["id"] = _sanitize_style_card_id(task)

                # Ensure required fields
                data.setdefault("name", data.get("id", "Unnamed Style"))
                data.setdefault("category", "misc")
                data.setdefault("source_type", source_type)
                data.setdefault(
                    "created", datetime.now(timezone.utc).strftime("%Y-%m-%d")
                )
                data.setdefault("tags", [])
                data.setdefault("compatible_with", [])
                data.setdefault("conflicts_with", [])

                # Build StyleCard
                card = StyleCard._from_dict(data)

                # Evaluate
                card.evaluate()

                # Save to Reference Library
                refs_dir = get_references_dir()
                card_path = card.save(refs_dir)

                _logger.info(
                    "Successfully saved Style Card to: %s (score: %.1f)",
                    card_path,
                    card.evaluation.score,
                )

                # Register in StyleCardRegistry
                try:
                    from api.style_card import get_style_card_registry

                    registry = get_style_card_registry()
                    registry.add(card, save=False)
                    registry.rebuild_index()
                    _logger.info("Style Card registered: %s", card.id)
                except Exception as reg_err:
                    _logger.warning(
                        "Failed to register Style Card in registry: %s", reg_err
                    )

            except Exception as e:
                _logger.warning(
                    "Failed to extract Style Card for run '%s': %s", run_id, e
                )

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"StyleCardExtractor_{run_id}",
        ).start()

    except Exception as e:
        _logger.warning(
            "Failed to launch Style Card extractor thread for run '%s': %s", run_id, e
        )


def extract_style_card_from_text(
    description: str,
    card_name: str = "",
    category: str = "misc",
    source_url: str = "",
) -> Optional[str]:
    """Extract a Style Card from a natural language design description.

    This is the browser-free path: describe the visual design, get a Style Card.

    Args:
        description: Natural language description of the visual design.
        card_name: Optional name for the Style Card.
        category: Component category (hero, navbar, cta, etc.).
        source_url: Optional URL to the original reference.

    Returns:
        The saved Style Card file path, or None on failure.
    """
    try:
        from api.dynamic.direct_calls import _call_direct

        system_instruction = (
            "You are an expert Design System Architect. Extract a YAML Style Card\n"
            "from a natural language description of a visual design.\n"
            "Output ONLY valid YAML wrapped in ```yaml code block.\n"
            "Infer all design DNA values (colors, typography, layout, animation, spacing)\n"
            "from the description. Use sensible modern defaults for unspecified aspects."
        )

        prompt = (
            f"=== DESIGN DESCRIPTION ===\n{description}\n\n"
            f"=== CARD NAME ===\n{card_name or '(infer from description)'}\n\n"
            f"=== CATEGORY ===\n{category}\n\n"
            f"=== SOURCE URL ===\n{source_url or '(none)'}\n\n"
            "Generate a complete YAML Style Card from this design description."
        )

        yaml_response = _call_direct(prompt, system_instruction=system_instruction)

        clean_yaml = yaml_response.strip()
        yaml_match = re.search(
            r"```(?:yaml)?\s*\n(.*?)\n```", clean_yaml, re.DOTALL
        )
        if yaml_match:
            clean_yaml = yaml_match.group(1).strip()

        try:
            from yaml import safe_load as _yaml_load
        except ImportError:
            import json as _json

            _yaml_load = _json.loads

        data = _yaml_load(clean_yaml) if _yaml_load else {}
        if isinstance(data, dict) and "style_card" in data:
            data = data["style_card"]

        if not data.get("id"):
            data["id"] = _sanitize_style_card_id(card_name or description)
        data.setdefault("name", card_name or data["id"])
        data.setdefault("category", category)
        data.setdefault("source_type", "text")
        data.setdefault("source_url", source_url)
        data.setdefault("created", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        from api.style_card import StyleCard, get_references_dir, get_style_card_registry

        card = StyleCard._from_dict(data)
        card.evaluate()

        refs_dir = get_references_dir()
        card_path = card.save(refs_dir)

        registry = get_style_card_registry()
        registry.add(card, save=False)
        registry.rebuild_index()

        _logger.info(
            "Style Card extracted from text: %s (score: %.1f)",
            card.id,
            card.evaluation.score,
        )
        return str(card_path)

    except Exception as e:
        _logger.exception("Failed to extract Style Card from text")
        return None
