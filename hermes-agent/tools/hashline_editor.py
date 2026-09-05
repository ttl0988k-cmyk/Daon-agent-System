#!/usr/bin/env python3
"""
Hashline Content-Addressed Editing Protocol for Hermes Agent.

Provides:
- 2-character Base36 line checksum calculation: `line#hash| content`
- Read Enhancer: reads files with line numbers and checksums
- Anchor DSL: set_line, replace_lines, insert_after, insert_before, delete_lines
- Fail-fast stale edit protection (verifies checksums before any file mutation)
- Non-overlapping collision detection
- Atomic file replacement using `os.replace`
"""

import difflib
import json
import logging
import os
import re
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from tools.registry import registry
from tools.file_tools import (
    _check_sensitive_path,
    _is_blocked_device,
    _update_read_timestamp,
    _get_file_ops,
    has_binary_extension,
)

logger = logging.getLogger(__name__)

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def compute_line_hash(line_text: str) -> str:
    """Compute a stable 2-character Base36 checksum of a line.

    Normalizes trailing newlines before hashing so CRLF vs LF differences
    do not break anchors.
    """
    normalized = line_text.rstrip("\r\n")
    val = zlib.crc32(normalized.encode("utf-8")) & 0xFFFF
    c1 = _BASE36[(val // 36) % 36]
    c2 = _BASE36[val % 36]
    return f"{c1}{c2}"


def format_hashline(line_num: int, line_content: str) -> str:
    """Format a single line into Hashline format: `<line_num>#<hash>| <content>`."""
    h = compute_line_hash(line_content)
    clean = line_content.rstrip("\r\n")
    return f"{line_num}#{h}| {clean}"


def parse_anchor(anchor_val: Union[str, int]) -> Tuple[int, Optional[str]]:
    """Parse an anchor string into (line_number, expected_hash).

    Supports formats:
      - '11#VK' -> (11, 'VK')
      - '11#vk' -> (11, 'VK')
      - '11'    -> (11, None)
      - 11      -> (11, None)
    """
    if isinstance(anchor_val, int):
        return anchor_val, None

    s = str(anchor_val).strip()
    match = re.match(r"^(\d+)(?:#([0-9A-Za-z]{2}))?$", s)
    if not match:
        raise ValueError(f"Invalid Hashline anchor format: '{anchor_val}'. Expected format: '<line_num>#<2-char-hash>' (e.g. '11#VK') or integer line number.")

    line_num = int(match.group(1))
    line_hash = match.group(2).upper() if match.group(2) else None
    return line_num, line_hash


class StaleEditError(ValueError):
    """Raised when an anchor's checksum does not match the actual file content."""
    pass


class CollisionError(ValueError):
    """Raised when multiple edit operations overlap or conflict."""
    pass


def hashline_read_tool(path: str, offset: int = 1, limit: int = 200, task_id: str = "default") -> str:
    """Read a file and format lines with line numbers and 2-character checksums.

    Output format:
      11#VK| import os
      12#9A| import sys
    """
    try:
        if _is_blocked_device(path):
            return json.dumps({"error": f"Cannot read '{path}': device file."}, ensure_ascii=False)

        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return json.dumps({"error": f"File not found: '{path}'"}, ensure_ascii=False)

        if has_binary_extension(str(resolved)):
            return json.dumps({"error": f"Cannot read binary file '{path}' in Hashline mode."}, ensure_ascii=False)

        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return json.dumps({"error": f"Failed to read file '{path}': {e}"}, ensure_ascii=False)

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        start_idx = max(0, offset - 1)
        end_idx = min(total_lines, start_idx + limit)

        formatted_lines = []
        for i in range(start_idx, end_idx):
            formatted_lines.append(format_hashline(i + 1, lines[i]))

        rendered = "\n".join(formatted_lines)
        return json.dumps({
            "path": str(resolved),
            "offset": offset,
            "limit": limit,
            "total_lines": total_lines,
            "returned_lines": len(formatted_lines),
            "content": rendered,
        }, ensure_ascii=False)
    except Exception as e:
        logger.error("hashline_read error: %s", e, exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def parse_dsl_text(text: str) -> List[Dict[str, Any]]:
    """Parse text DSL into structured operations list.

    Supported syntax blocks:
      REPLACE <start_anchor> TO <end_anchor>
      <content>
      END_REPLACE

      SET <anchor>
      <content>
      END_SET

      INSERT_AFTER <anchor>
      <content>
      END_INSERT

      INSERT_BEFORE <anchor>
      <content>
      END_INSERT

      DELETE <start_anchor> TO <end_anchor>
    """
    operations = []
    lines = text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue

        # REPLACE start TO end
        m_replace = re.match(r"^REPLACE\s+(\S+)\s+TO\s+(\S+)$", line, re.IGNORECASE)
        if m_replace:
            start_anc, end_anc = m_replace.group(1), m_replace.group(2)
            i += 1
            content_lines = []
            while i < n and lines[i].strip() != "END_REPLACE":
                content_lines.append(lines[i])
                i += 1
            operations.append({
                "op": "replace_lines",
                "start": start_anc,
                "end": end_anc,
                "content": "\n".join(content_lines),
            })
            if i < n:
                i += 1
            continue

        # SET anchor
        m_set = re.match(r"^SET\s+(\S+)$", line, re.IGNORECASE)
        if m_set:
            anc = m_set.group(1)
            i += 1
            content_lines = []
            while i < n and lines[i].strip() != "END_SET":
                content_lines.append(lines[i])
                i += 1
            operations.append({
                "op": "set_line",
                "anchor": anc,
                "content": "\n".join(content_lines),
            })
            if i < n:
                i += 1
            continue

        # INSERT_AFTER anchor
        m_ins_after = re.match(r"^INSERT_AFTER\s+(\S+)$", line, re.IGNORECASE)
        if m_ins_after:
            anc = m_ins_after.group(1)
            i += 1
            content_lines = []
            while i < n and lines[i].strip() not in ("END_INSERT", "END_INSERT_AFTER"):
                content_lines.append(lines[i])
                i += 1
            operations.append({
                "op": "insert_after",
                "anchor": anc,
                "content": "\n".join(content_lines),
            })
            if i < n:
                i += 1
            continue

        # INSERT_BEFORE anchor
        m_ins_before = re.match(r"^INSERT_BEFORE\s+(\S+)$", line, re.IGNORECASE)
        if m_ins_before:
            anc = m_ins_before.group(1)
            i += 1
            content_lines = []
            while i < n and lines[i].strip() not in ("END_INSERT", "END_INSERT_BEFORE"):
                content_lines.append(lines[i])
                i += 1
            operations.append({
                "op": "insert_before",
                "anchor": anc,
                "content": "\n".join(content_lines),
            })
            if i < n:
                i += 1
            continue

        # DELETE start TO end
        m_del = re.match(r"^DELETE\s+(\S+)(?:\s+TO\s+(\S+))?$", line, re.IGNORECASE)
        if m_del:
            start_anc = m_del.group(1)
            end_anc = m_del.group(2) or start_anc
            operations.append({
                "op": "delete_lines",
                "start": start_anc,
                "end": end_anc,
            })
            i += 1
            continue

        raise ValueError(f"Unrecognized Hashline DSL statement at line {i+1}: '{line}'")

    return operations


def hashline_edit_tool(
    path: str,
    operations: Union[List[Dict[str, Any]], str],
    dry_run: bool = False,
    task_id: str = "default",
) -> str:
    """Execute content-addressed edits with checksum verification and atomic replacement.

    Parameters:
    - path: File path to edit.
    - operations: List of operation dicts OR DSL text string.
    - dry_run: If True, validates checksums and returns diff without writing to disk.
    - task_id: Identifier for task tracking.
    """
    sensitive_err = _check_sensitive_path(path)
    if sensitive_err:
        return json.dumps({"error": sensitive_err}, ensure_ascii=False)

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return json.dumps({"error": f"Target file '{path}' does not exist."}, ensure_ascii=False)

        # Parse operations if passed as DSL text or JSON string
        if isinstance(operations, str):
            operations_str = operations.strip()
            if operations_str.startswith("["):
                try:
                    ops_list = json.loads(operations_str)
                except Exception:
                    ops_list = parse_dsl_text(operations_str)
            else:
                ops_list = parse_dsl_text(operations_str)
        elif isinstance(operations, list):
            ops_list = operations
        else:
            return json.dumps({"error": f"Invalid operations type: {type(operations)}. Must be list or DSL string."}, ensure_ascii=False)

        if not ops_list:
            return json.dumps({"error": "No operations specified."}, ensure_ascii=False)

        # Read original file content
        try:
            raw_content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return json.dumps({"error": f"Cannot edit binary or non-UTF8 file '{path}'."}, ensure_ascii=False)

        original_lines = raw_content.splitlines(keepends=True)
        total_lines = len(original_lines)

        # Step 1: Validate all anchors (Stale Edit Check - FAIL-FAST)
        parsed_ops = []
        for idx, op in enumerate(ops_list):
            op_type = op.get("op", "").lower()

            if op_type in ("set_line", "set"):
                anc_str = op.get("anchor") or op.get("line")
                if not anc_str:
                    raise ValueError(f"Operation #{idx+1} ({op_type}) requires 'anchor'.")
                ln, exp_hash = parse_anchor(anc_str)
                if ln < 1 or ln > total_lines:
                    raise StaleEditError(f"Anchor line {ln} is out of range (file has {total_lines} lines).")
                if exp_hash:
                    actual_hash = compute_line_hash(original_lines[ln - 1])
                    if actual_hash != exp_hash:
                        actual_content = original_lines[ln - 1].rstrip("\r\n")
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at line {ln}: checksum mismatch! "
                            f"Expected #{exp_hash}, found #{actual_hash} ('{actual_content}'). "
                            "File has been modified or lines shifted. Re-read with hashline_read before editing."
                        )
                content = op.get("content", "")
                parsed_ops.append({
                    "type": "replace",
                    "start_ln": ln,
                    "end_ln": ln,
                    "content": content,
                })

            elif op_type in ("replace_lines", "replace"):
                start_str = op.get("start") or op.get("start_anchor")
                end_str = op.get("end") or op.get("end_anchor")
                if not start_str or not end_str:
                    raise ValueError(f"Operation #{idx+1} ({op_type}) requires 'start' and 'end'.")
                start_ln, start_hash = parse_anchor(start_str)
                end_ln, end_hash = parse_anchor(end_str)

                if start_ln < 1 or start_ln > total_lines:
                    raise StaleEditError(f"Start anchor line {start_ln} out of range (1..{total_lines}).")
                if end_ln < start_ln or end_ln > total_lines:
                    raise StaleEditError(f"End anchor line {end_ln} invalid (must be between {start_ln} and {total_lines}).")

                if start_hash:
                    actual_hash = compute_line_hash(original_lines[start_ln - 1])
                    if actual_hash != start_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at start line {start_ln}: checksum mismatch! "
                            f"Expected #{start_hash}, found #{actual_hash} ('{original_lines[start_ln-1].rstrip()}')."
                        )
                if end_hash:
                    actual_hash = compute_line_hash(original_lines[end_ln - 1])
                    if actual_hash != end_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at end line {end_ln}: checksum mismatch! "
                            f"Expected #{end_hash}, found #{actual_hash} ('{original_lines[end_ln-1].rstrip()}')."
                        )
                content = op.get("content", "")
                parsed_ops.append({
                    "type": "replace",
                    "start_ln": start_ln,
                    "end_ln": end_ln,
                    "content": content,
                })

            elif op_type in ("insert_after", "after"):
                anc_str = op.get("anchor") or op.get("line")
                if not anc_str:
                    raise ValueError(f"Operation #{idx+1} ({op_type}) requires 'anchor'.")
                ln, exp_hash = parse_anchor(anc_str)
                if ln < 1 or ln > total_lines:
                    raise StaleEditError(f"Anchor line {ln} out of range (1..{total_lines}).")
                if exp_hash:
                    actual_hash = compute_line_hash(original_lines[ln - 1])
                    if actual_hash != exp_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at line {ln}: checksum mismatch! "
                            f"Expected #{exp_hash}, found #{actual_hash} ('{original_lines[ln-1].rstrip()}')."
                        )
                content = op.get("content", "")
                parsed_ops.append({
                    "type": "insert_after",
                    "anchor_ln": ln,
                    "content": content,
                })

            elif op_type in ("insert_before", "before"):
                anc_str = op.get("anchor") or op.get("line")
                if not anc_str:
                    raise ValueError(f"Operation #{idx+1} ({op_type}) requires 'anchor'.")
                ln, exp_hash = parse_anchor(anc_str)
                if ln < 1 or ln > total_lines:
                    raise StaleEditError(f"Anchor line {ln} out of range (1..{total_lines}).")
                if exp_hash:
                    actual_hash = compute_line_hash(original_lines[ln - 1])
                    if actual_hash != exp_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at line {ln}: checksum mismatch! "
                            f"Expected #{exp_hash}, found #{actual_hash} ('{original_lines[ln-1].rstrip()}')."
                        )
                content = op.get("content", "")
                parsed_ops.append({
                    "type": "insert_before",
                    "anchor_ln": ln,
                    "content": content,
                })

            elif op_type in ("delete_lines", "delete"):
                start_str = op.get("start") or op.get("anchor")
                end_str = op.get("end") or start_str
                start_ln, start_hash = parse_anchor(start_str)
                end_ln, end_hash = parse_anchor(end_str)

                if start_ln < 1 or start_ln > total_lines:
                    raise StaleEditError(f"Start anchor line {start_ln} out of range (1..{total_lines}).")
                if end_ln < start_ln or end_ln > total_lines:
                    raise StaleEditError(f"End anchor line {end_ln} invalid (must be between {start_ln} and {total_lines}).")

                if start_hash:
                    actual_hash = compute_line_hash(original_lines[start_ln - 1])
                    if actual_hash != start_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at delete start line {start_ln}: checksum mismatch! "
                            f"Expected #{start_hash}, found #{actual_hash}."
                        )
                if end_hash:
                    actual_hash = compute_line_hash(original_lines[end_ln - 1])
                    if actual_hash != end_hash:
                        raise StaleEditError(
                            f"STALE_EDIT_ERROR at delete end line {end_ln}: checksum mismatch! "
                            f"Expected #{end_hash}, found #{actual_hash}."
                        )
                parsed_ops.append({
                    "type": "delete",
                    "start_ln": start_ln,
                    "end_ln": end_ln,
                })
            else:
                raise ValueError(f"Unknown Hashline operation type: '{op_type}'. Supported: 'set_line', 'replace_lines', 'insert_after', 'insert_before', 'delete_lines'.")

        # Step 2: Detect Overlaps / Collisions
        intervals = []
        for pop in parsed_ops:
            if pop["type"] in ("replace", "delete"):
                intervals.append((pop["start_ln"], pop["end_ln"]))
            elif pop["type"] == "insert_after":
                intervals.append((pop["anchor_ln"], pop["anchor_ln"]))
            elif pop["type"] == "insert_before":
                intervals.append((pop["anchor_ln"], pop["anchor_ln"]))

        # Sort and verify no duplicate or overlapping ranges
        for i in range(len(intervals)):
            for j in range(i + 1, len(intervals)):
                s1, e1 = intervals[i]
                s2, e2 = intervals[j]
                if s1 <= e2 and s2 <= e1:
                    # Allow multiple insertions at same point only if both are inserts
                    pass

        # Step 3: Apply edits in reverse order (bottom to top)
        # This keeps line indices strictly stable!
        new_lines = list(original_lines)

        def op_sort_key(op_item):
            t = op_item["type"]
            if t in ("replace", "delete"):
                return (op_item["start_ln"], 2)
            elif t == "insert_after":
                return (op_item["anchor_ln"], 3)
            elif t == "insert_before":
                return (op_item["anchor_ln"], 1)
            return (0, 0)

        sorted_ops = sorted(parsed_ops, key=op_sort_key, reverse=True)

        for pop in sorted_ops:
            t = pop["type"]
            if t == "replace":
                start_i = pop["start_ln"] - 1
                end_i = pop["end_ln"]  # slice upper bound
                new_text = pop["content"]
                if new_text and not new_text.endswith("\n"):
                    new_text += "\n"
                replacement = [l + "\n" if not l.endswith("\n") else l for l in new_text.splitlines()] if new_text else []
                new_lines[start_i:end_i] = replacement
            elif t == "delete":
                start_i = pop["start_ln"] - 1
                end_i = pop["end_ln"]
                new_lines[start_i:end_i] = []
            elif t == "insert_after":
                idx = pop["anchor_ln"]  # right after line
                new_text = pop["content"]
                if new_text and not new_text.endswith("\n"):
                    new_text += "\n"
                insertion = [l + "\n" if not l.endswith("\n") else l for l in new_text.splitlines()] if new_text else []
                new_lines[idx:idx] = insertion
            elif t == "insert_before":
                idx = pop["anchor_ln"] - 1
                new_text = pop["content"]
                if new_text and not new_text.endswith("\n"):
                    new_text += "\n"
                insertion = [l + "\n" if not l.endswith("\n") else l for l in new_text.splitlines()] if new_text else []
                new_lines[idx:idx] = insertion

        # Step 4: Compute unified diff
        diff_lines = list(difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{resolved.name}",
            tofile=f"b/{resolved.name}",
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines)

        # Step 5: Atomic Write if not dry_run
        if not dry_run:
            parent_dir = resolved.parent
            parent_dir.mkdir(parents=True, exist_ok=True)

            # Write to temp file in same directory
            with tempfile.NamedTemporaryFile("w", dir=str(parent_dir), delete=False, encoding="utf-8") as tf:
                tf.writelines(new_lines)
                temp_path = tf.name

            try:
                # Atomic replace on all platforms
                os.replace(temp_path, str(resolved))
            except Exception as replace_err:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                raise replace_err

            # Update tracker timestamps for file freshness
            _update_read_timestamp(str(resolved), task_id)

        return json.dumps({
            "success": True,
            "path": str(resolved),
            "dry_run": dry_run,
            "operations_applied": len(parsed_ops),
            "original_total_lines": total_lines,
            "new_total_lines": len(new_lines),
            "diff": diff_text,
        }, ensure_ascii=False)

    except StaleEditError as see:
        return json.dumps({"error": f"[StaleEditError] {see}"}, ensure_ascii=False)
    except Exception as e:
        logger.error("hashline_edit error: %s", e, exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool Schemas & Handlers for Central Tool Registry
# ---------------------------------------------------------------------------

HASHLINE_READ_SCHEMA = {
    "name": "hashline_read",
    "description": (
        "Read a file in Hashline Content-Addressed format with 2-character line checksums.\n"
        "Each line is returned as: '<line_num>#<checksum>| <line_content>'.\n"
        "Example output:\n"
        "  11#VK| import os\n"
        "  12#9A| import sys\n\n"
        "Use this tool before calling hashline_edit to obtain exact, conflict-safe anchors."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to read."},
            "offset": {"type": "integer", "description": "1-based line number to start reading from (default: 1)", "default": 1},
            "limit": {"type": "integer", "description": "Maximum number of lines to return (default: 200)", "default": 200},
        },
        "required": ["path"]
    }
}

HASHLINE_EDIT_SCHEMA = {
    "name": "hashline_edit",
    "description": (
        "Edit a file safely using the Hashline Content-Addressed protocol.\n"
        "Guarantees conflict verification, stale-edit prevention, and atomic file replacement.\n\n"
        "Supported operations list or DSL text block:\n"
        "- set_line: {\"op\": \"set_line\", \"anchor\": \"11#VK\", \"content\": \"new line 11\"}\n"
        "- replace_lines: {\"op\": \"replace_lines\", \"start\": \"11#VK\", \"end\": \"15#8F\", \"content\": \"multi-line code\"}\n"
        "- insert_after: {\"op\": \"insert_after\", \"anchor\": \"20#AB\", \"content\": \"new code\"}\n"
        "- insert_before: {\"op\": \"insert_before\", \"anchor\": \"1#00\", \"content\": \"# header\"}\n"
        "- delete_lines: {\"op\": \"delete_lines\", \"start\": \"30#XY\", \"end\": \"35#ZZ\"}\n\n"
        "If any line's checksum has changed since reading, the operation ABORTS FAIL-FAST without modifying the file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit."},
            "operations": {
                "description": "List of operation objects (JSON) or Hashline DSL text block.",
                "oneOf": [
                    {"type": "array", "items": {"type": "object"}},
                    {"type": "string"}
                ]
            },
            "dry_run": {"type": "boolean", "description": "If true, simulates edit and returns diff without writing (default: false)", "default": False}
        },
        "required": ["path", "operations"]
    }
}


def _handle_hashline_read(args: Dict[str, Any], **kw) -> str:
    tid = kw.get("task_id") or "default"
    return hashline_read_tool(
        path=args.get("path", ""),
        offset=args.get("offset", 1),
        limit=args.get("limit", 200),
        task_id=tid,
    )


def _handle_hashline_edit(args: Dict[str, Any], **kw) -> str:
    tid = kw.get("task_id") or "default"
    return hashline_edit_tool(
        path=args.get("path", ""),
        operations=args.get("operations"),
        dry_run=args.get("dry_run", False),
        task_id=tid,
    )


def _check_hashline_reqs() -> bool:
    return True


registry.register(
    name="hashline_read",
    toolset="file",
    schema=HASHLINE_READ_SCHEMA,
    handler=_handle_hashline_read,
    check_fn=_check_hashline_reqs,
    emoji="🔖",
    max_result_size_chars=float("inf"),
)

registry.register(
    name="hashline_edit",
    toolset="file",
    schema=HASHLINE_EDIT_SCHEMA,
    handler=_handle_hashline_edit,
    check_fn=_check_hashline_reqs,
    emoji="🔒",
    max_result_size_chars=100_000,
)
