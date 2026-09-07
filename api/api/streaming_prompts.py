"""api.streaming_prompts - Complete prompt composition and multimodal formatting for streaming agents.

Extracted from streaming.py to separate prompt/system-message building responsibilities.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger('api.streaming_prompts')


def resize_image_bytes(raw_bytes: bytes, mime: str) -> bytes:
    """Resize/compress image via Pillow (max 2048px on longest edge, JPEG quality 75)."""
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(raw_bytes))
        fmt = img.format or ('PNG' if 'png' in mime else 'JPEG')
        w, h = img.size
        max_dim = 2048
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            new_size = (int(w * ratio), int(h * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        # Convert to RGB for JPEG output (avoids RGBA issues)
        if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        buf = _io.BytesIO()
        save_kwargs = {}
        if fmt == 'JPEG':
            save_kwargs = {'quality': 75, 'optimize': True}
        elif fmt == 'PNG':
            save_kwargs = {'optimize': True}
        elif fmt == 'WEBP':
            save_kwargs = {'quality': 75}
        img.save(buf, format=fmt, **save_kwargs)
        return buf.getvalue()
    except Exception as e:
        _logger.debug("Image resize fallback to raw bytes: %s", e)
        return raw_bytes


def build_user_payload(
    workspace: str | Path,
    msg_text: str,
    workspace_ctx: str = "",
    attachments: Optional[List[str]] = None,
) -> Any:
    """Process user message and optional file attachments into multimodal content or string."""
    user_message_payload: Any = workspace_ctx + msg_text
    if not attachments:
        return user_message_payload

    multimodal_content = [{"type": "text", "text": workspace_ctx + msg_text}]
    has_images = False

    ws_path = Path(workspace)
    for filename in attachments:
        file_path = ws_path / filename
        if file_path.exists() and file_path.is_file():
            mime_type, _ = mimetypes.guess_type(str(file_path))
            if not mime_type:
                ext = file_path.suffix.lower().lstrip('.')
                if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'):
                    mime_type = f"image/{ext}"
                    if ext == 'svg':
                        mime_type = "image/svg+xml"

            if mime_type and (mime_type.startswith('image/') or mime_type == 'image/svg+xml'):
                try:
                    img_bytes = file_path.read_bytes()
                    # Resize/compress for non-SVG images (SVG is vector, skip Pillow)
                    if mime_type != 'image/svg+xml' and not mime_type.startswith('image/gif'):
                        img_bytes = resize_image_bytes(img_bytes, mime_type)
                    b64_data = base64.b64encode(img_bytes).decode('utf-8')
                    multimodal_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}"
                        }
                    })
                    has_images = True
                    _logger.debug("Image '%s' encoded (%d chars base64)", filename, len(b64_data))
                except Exception as img_err:
                    _logger.warning("Failed to read image attachment %s: %s", filename, img_err)

    if has_images:
        return multimodal_content
    return user_message_payload


def compose_system_message(
    session_id: str,
    workspace: str | Path,
    resolved_model: str,
    msg_text: str = "",
    planning_mode: bool = False,
    open_tabs: Optional[List[Dict[str, Any]]] = None,
    injected_mcp_count: int = 0,
) -> Tuple[str, List[int]]:
    """Compose full system prompt with open tabs, Korean language instruction,

    Markdown rules, browser notes, long-term memory, patch registry notices,
    self-evolution cognitive block, and handover ledger.
    Returns (composed_system_message, injected_fact_ids).
    """
    ws_str = str(workspace)

    # OS Context
    is_windows = platform.system() == 'Windows'
    os_ctx = (
        "\n\nOperating System: Windows (bash shell available via Git Bash / WSL). "
        "PREFER built-in file tools (read_file, search_files, write_to_file, apply_diff) "
        "over terminal commands whenever possible — they are safer and more reliable. "
        "When terminal commands are necessary: bash-style commands (ls, cat, grep, cp, mv, rm) "
        "work because the terminal runs bash, not cmd.exe. "
        "File paths accept both forward slashes and backslashes."
    ) if is_windows else ""

    # Open Tabs Context
    tabs_sys = ""
    tabs_list = open_tabs or []
    if tabs_list:
        active_tab = None
        for t in tabs_list:
            if t.get('active'):
                active_tab = t
                break
        tab_paths = [t.get('path', '') for t in tabs_list if t.get('path')]
        if tab_paths:
            tabs_sys = (
                f"[OPEN EDITOR TABS]\n"
                f"  Files: {', '.join(tab_paths)}\n"
            )
            if active_tab and active_tab.get('path'):
                tabs_sys += f"  Active (focused) tab: {active_tab['path']}\n"
            tabs_sys += (
                "  These files are OPEN in the user's editor RIGHT NOW. "
                "When the user asks you to modify / fix / improve \"this\" file without specifying a path, "
                "infer they mean the active tab or one of the open tabs.\n"
            )

    base_msg = (
        f"Active workspace: {ws_str}\n"
        "Use this directory for ALL file operations unless the user specifies otherwise.\n\n"
        + tabs_sys +
        "[LANGUAGE — CRITICAL]\n"
        "Always think (reasoning) and respond in Korean (한국어). Your internal\n"
        "thinking/reasoning output is displayed to the user in a '💭 생각 중' panel —\n"
        "write it in Korean too, never in English. All user-facing text, tool call\n"
        "previews, and reasoning must be in Korean.\n"
        "HARD RULE — 도구 실행 전 고지: 반드시 도구 호출 직전에 지금 무엇을 하려는지 한국어 한 문장으로 먼저 쓴 뒤 호출할 것 (예: '브라우저로 example.com을 열어보겠습니다.', '설치본 chat.js에서 카드 렌더링 코드를 확인하겠습니다.'). 연속 호출이면 첫 회만 고지하고 작업 방향이 바뀌면 다시 고지. 설명 없는 침묵 도구 호출은 금지 — 사용자가 화면에서 현재 작업의 목적을 항상 따라갈 수 있어야 함.\n\n"
        "[WEBUI ENVIRONMENT]\n"
        "You are running in the Daon WebUI — a rich web-based chat interface.\n"
        "You CAN and MUST use Markdown formatting directly in your text responses.\n"
        "DO NOT use browser tools (browser_navigate, execute_command with curl, etc.) to \"verify\" "
        "whether markdown works — it ALREADY works. Just output the markdown and it will render.\n\n"
        "Supported Markdown features:\n"
        "- **bold**, *italic*, `inline code`, ```code blocks```, lists, headers\n"
        "- Images: ![alt text](image_url) — renders inline. Use https:// URLs for web images.\n"
        f"  For local workspace files, use: /api/file/raw?session_id={session_id}&path=RELATIVE_PATH\n"
        f"  Example: ![chart](/api/file/raw?session_id={session_id}&path=output/chart.png)\n"
        "- Links: [link text](url) — rendered as clickable hyperlinks\n\n"
        "IMPORTANT: When you want to show an image, simply write ![description](url) in your response.\n"
        "The frontend already has a working markdown-to-HTML renderer that converts this to an <img> tag.\n"
        "You do NOT need to test, verify, or debug image rendering — just use the markdown syntax.\n\n"
        "[BUILT-IN BROWSER]\n"
        "This app has a built-in shared browser: an in-app tab driven over CDP that the user can see.\n"
        "When a task requires viewing, scraping, or interacting with a web page, use the browser tools\n"
        "(browser_navigate, browser_snapshot, browser_click, browser_type, browser_scroll, browser_press,\n"
        "browser_console) — they operate this built-in tab. Do NOT launch external browsers, and do NOT\n"
        "use curl/wget for pages that need rendering or interaction. Workflow: call browser_navigate\n"
        "first; the returned snapshot lists interactive elements as @eN refs — use those refs with\n"
        "browser_click/browser_type. (This does NOT override the markdown rule above — never use the\n"
        "browser just to verify rendering.)\n\n"
        "[MEMORY POLICY]\n"
        "You have access to Memory MCP tools (mcp_memory_*) for long-term knowledge storage.\n"
        "CRITICAL: Do NOT automatically save information to memory. Only use memory tools when:\n"
        "1. The user explicitly asks you to remember/save/store something, OR\n"
        "2. The user asks you to recall/search previously stored memories.\n"
        "Do not proactively create entities or relations. Memory is on-demand only.\n"
        "Never store transient tool-state as durable memory: MCP server connection errors "
        "('server is not connected / unreachable / dead'), API outages, timeouts, or failed "
        "tool calls are momentary states, not lasting facts. If an MCP server call fails, "
        "re-check the server's live status (status / reconnect) before concluding anything; "
        "a liveness assumption about a server is never a memory-worthy fact.\n\n"
        f"You are running as model: {resolved_model}."
    ) + os_ctx

    injected_fact_ids: List[int] = []

    # 1. Long-term memory prompt injection
    try:
        from api.memory_store import build_memory_prompt
        _memory_prompt = build_memory_prompt(query_text=msg_text or '', session_id=session_id or '')
        if _memory_prompt:
            base_msg += "\n\n" + _memory_prompt
    except Exception as _mem_e:
        _logger.warning("Memory prompt injection failed: %s", _mem_e)

    # 2. Phase 6: Injected fact IDs tracking
    try:
        from api.memory_store import get_last_injected_fact_ids as _p6_get_injected
        injected_fact_ids = _p6_get_injected(session_id or '')
    except Exception:
        injected_fact_ids = []

    # 3. Patch Registry prompt injection
    try:
        from api.patch_registry import get_system_prompt_block
        _patch_prompt = get_system_prompt_block()
        if _patch_prompt:
            base_msg += "\n\n" + _patch_prompt
    except Exception as _pr_prompt_e:
        _logger.warning("Patch registry prompt injection failed: %s", _pr_prompt_e)

    # 4. Self-Evolution cognitive block
    try:
        from api.dynamic.self_evolution import get_self_evolution_prompt_block as _sevo_block_fn
        _sevo_prompt = _sevo_block_fn()
        if _sevo_prompt:
            base_msg += "\n\n" + _sevo_prompt
    except Exception as _sevo_prompt_e:
        _logger.warning("Self-evolution prompt injection failed: %s", _sevo_prompt_e)

    # 5. Evolution Ledger handover block
    try:
        from api.dynamic.evolution_ledger import get_handover_prompt_block as _ev_handover_fn
        _handover_prompt = _ev_handover_fn()
        if _handover_prompt:
            base_msg += "\n\n" + _handover_prompt
            _logger.info("Injected self-evolution restart handover context into system prompt.")
    except Exception as _ev_prompt_e:
        _logger.warning("Evolution handover prompt injection failed: %s", _ev_prompt_e)

    # 6. Agent-to-agent inbox injection
    try:
        from api.profiles import get_active_profile_name
        from api.memory_store import format_inbox_prompt
        _chat_agent_name = get_active_profile_name() or 'default'
        _inbox_prompt = format_inbox_prompt(_chat_agent_name)
        if _inbox_prompt:
            base_msg += "\n\n" + _inbox_prompt
    except Exception as _inbox_e:
        _logger.warning("Agent inbox injection failed: %s", _inbox_e)

    # 7. Planning Mode instructions
    if planning_mode:
        base_msg += (
            "\n\n[PLANNING MODE ENABLED]\n"
            "The user has enabled Planning Mode. You must act carefully before making code changes.\n"
            "If the request requires significant logic, major changes, or is complex:\n"
            "1. Research and understand the codebase first.\n"
            "2. Create a detailed `plan.md` in the workspace outlining your proposed changes.\n"
            "3. Stop execution and explicitly ask the user for approval.\n"
            "4. Only proceed with actual modifications after the user approves the plan.\n"
            "If the request is trivial, you may execute it directly without a plan."
        )

    # 8. MCP injection instructions
    if injected_mcp_count > 0:
        base_msg += (
            f"\n\n[MCP INJECTION ACTIVE]\n"
            f"You have been dynamically injected with {injected_mcp_count} MCP tools from the WebUI.\n"
            f"These tools are prefixed with `mcp_` (e.g. `mcp_filesystem_...`, `mcp_github_...`).\n"
            f"CRITICAL: You MUST call these tools natively as standard function calls.\n"
            f"DO NOT try to execute them via HTTP API (e.g. /api/mcp/invoke) or Python scripts.\n"
            f"They are fully registered in your environment; just call them directly!\n"
            f"IMPORTANT: For ANY browser/web-page work, use the dedicated browser tools "
            f"(browser_navigate, browser_snapshot, browser_click, etc.) instead of mcp_playwright_* "
            f"tools — the internal browser shares the app window and is always available.\n"
        )

    return base_msg, injected_fact_ids
