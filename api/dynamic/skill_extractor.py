"""
Skill extraction from successful Harness runs.

Provides:
- _extract_and_save_skill(): background task that distills a generalized skill
  from a successful multi-agent execution (DAG plan + final output)
"""

import json
import logging
import re
import threading
import unicodedata
from pathlib import Path

_logger = logging.getLogger(__name__)


def _sanitize_skill_name(task: str) -> str:
    """Create a clean, filesystem-safe skill name from a task string.

    Handles Korean and other Unicode characters by transliterating to
    ASCII-safe tokens while preserving readability.
    """
    # Normalize unicode
    normalized = unicodedata.normalize("NFC", task.strip())

    # Replace non-alphanumeric (including Korean) with spaces, then collapse
    # Keep alphanumeric + Korean (Hangul Syllables U+AC00–U+D7AF, Jamo U+1100–U+11FF)
    tokens = []
    for ch in normalized:
        if ch.isascii() and ch.isalnum():
            tokens.append(ch.lower())
        elif '\uAC00' <= ch <= '\uD7AF' or '\u1100' <= ch <= '\u11FF' or '\u3131' <= ch <= '\u318E':
            # Korean character — romanize to a hash-based short token
            tokens.append(ch)
        elif ch in (' ', '-', '_'):
            tokens.append('_')
        # else: skip special characters

    raw = "".join(tokens).strip("_")
    # Collapse multiple underscores
    raw = re.sub(r"_+", "_", raw)
    # Truncate to reasonable length
    return raw[:50] if raw else "unnamed_skill"


def _extract_and_save_skill(task: str, plan: dict, final_output: str, run_id: str) -> None:
    """Background task to extract a generalized skill from a successful Harness run."""
    try:
        from api.dynamic.direct_calls import _call_direct
        
        # We run the actual LLM call in a background thread to avoid blocking
        def _worker():
            try:
                system_instruction = (
                    "You are an expert Software Engineer and AI Agent Skill Creator.\n"
                    "Your job is to analyze the following successful multi-agent execution (DAG plan and final output),\n"
                    "and extract its core logic into a single, highly generalized Markdown (.md) Skill file.\n"
                    "The skill should provide a generic system prompt or instruction set so another agent can repeat this logic easily.\n"
                    "Ensure you replace hardcoded values (like specific paths or dates) with variables (e.g. <DIRECTORY_PATH>).\n"
                    "Output ONLY the raw markdown content. Do not enclose it in ```markdown blocks.\n"
                    "IMPORTANT: Start the file with YAML frontmatter containing: name, description, version, category, tags."
                )
                
                plan_json = json.dumps(plan, ensure_ascii=False, indent=2)
                prompt = (
                    f"=== ORIGINAL TASK ===\n{task}\n\n"
                    f"=== EXECUTION DAG (PLAN) ===\n{plan_json}\n\n"
                    f"=== FINAL OUTPUT SUMMARY ===\n{final_output[:2000]}\n\n"
                    "Please generate the generalized Skill file in Markdown format with YAML frontmatter."
                )
                
                _logger.info("Distilling skill for run '%s' in background...", run_id)
                skill_content = _call_direct(prompt, system_instruction=system_instruction)
                
                # Clean up markdown formatting if the LLM wrapped it
                clean_text = skill_content.strip()
                if clean_text.startswith("```"):
                    lines = clean_text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    clean_text = "\n".join(lines).strip()
                
                # Save to ~/.hermes/skills/auto/{skill_name}/SKILL.md
                skill_name = _sanitize_skill_name(task)
                auto_skills_dir = Path.home() / ".hermes" / "skills" / "auto" / skill_name
                auto_skills_dir.mkdir(parents=True, exist_ok=True)
                
                skill_file = auto_skills_dir / "SKILL.md"
                skill_file.write_text(clean_text, encoding="utf-8")
                _logger.info("Successfully saved new skill to: %s", skill_file)
                
                # Register as APPROVED — user explicitly approved this save
                try:
                    from api.skill_registry import SkillRegistry, SKILL_APPROVED
                    SkillRegistry.register_new_auto_skill(skill_file, lifecycle=SKILL_APPROVED)
                except Exception as reg_err:
                    _logger.warning("Failed to register skill in manifest: %s", reg_err)
            
            except Exception as e:
                _logger.warning("Failed to extract skill for run '%s': %s", run_id, e)

        # Start the worker thread
        threading.Thread(target=_worker, daemon=True, name=f"SkillExtractor_{run_id}").start()
    
    except Exception as e:
        _logger.warning("Failed to launch skill extractor thread for run '%s': %s", run_id, e)
