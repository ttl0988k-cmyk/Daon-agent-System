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
    browser_context: Optional[str] = None,
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
        + (
            (
                "[구글 크롬 사이드패널 브라우저 연동 모드 — 절대 지침]\n"
                "당신은 현재 구글 크롬(Google Chrome) 브라우저 사이드 패널에서 사용자와 1:1로 소통하고 있습니다.\n"
                "1. [실시간 화면 직접 인지]: 당신은 사용자의 실제 크롬 브라우저 화면을 실시간으로 직접 보고 있습니다! 매 대화마다 브라우저에서 자동 수집된 현재 열린 모든 탭 목록, 활성 탭(iframe 내부 포함) 제목, URL, 본문 텍스트, 주요 버튼 및 입력창 정보가 [실시간 브라우저 환경 컨텍스트]로 제공됩니다.\n"
                "2. [직전 액션 결과 자동 인지]: 당신이 내린 조작 결과는 [직전 브라우저 액션 실행 결과] 헤더로 즉시 보고됩니다. 따라서 조작 후 사용자에게 '클릭되었는지 확인해주세요', '페이지가 바뀌었나요?'라고 되묻지 마세요! 스스로 보고 판단하세요.\n"
                "3. [브라우저 전용 액션 태그 사용 필수]: 데스크톱 Electron용 도구(browser_*)나 터미널/파이썬 스크립트 도구를 쓰지 마세요. 웹페이지 조작 및 입력은 오직 아래의 XML 액션 태그(<daon_action ... />)를 사용해야 실제 브라우저에서 즉각 실행됩니다.\n"
                "4. [실시간 브라우저 제어 액션 태그]: 브라우저 조작이 필요할 때는 반드시 아래의 XML 액션 태그를 응답 텍스트에 포함하세요. 크롬 확장프로그램이 사용자의 실제 브라우저 화면에서 즉각 실행합니다:\n"
                "   - 버튼/카드/링크/메뉴 클릭: <daon_action action=\"click\" target=\"버튼텍스트 또는 CSS셀렉터\" nth=\"1\" />\n"
                "   - 마우스 호버(드롭다운/서브메뉴 열기): <daon_action action=\"hover\" target=\"메뉴텍스트 또는 셀렉터\" nth=\"1\" />\n"
                "   - 키보드 입력(Enter, Escape 등): <daon_action action=\"press\" key=\"Enter\" target=\"입력창(선택)\" />\n"
                "   - 대화형 요소 스냅샷 추출: <daon_action action=\"snapshot\" />\n"
                "   - 현재 화면 캡처(스크린샷): <daon_action action=\"screenshot\" />\n"
                "   - 잠시 대기(로딩 대기 등): <daon_action action=\"wait\" ms=\"1500\" />\n"
                "   - 현재 탭에서 사이트 이동: <daon_action action=\"navigate\" url=\"https://...\" />\n"
                "   - 새 탭에서 열기: <daon_action action=\"new_tab\" url=\"https://...\" />\n"
                "   - 다른 탭으로 전환: <daon_action action=\"switch_tab\" tab_id=\"탭ID\" />\n"
                "   - 탭 닫기: <daon_action action=\"close_tab\" tab_id=\"탭ID\" />\n"
                "   - 검색어/텍스트 입력: <daon_action action=\"type\" target=\"입력창ID/셀렉터\" text=\"입력할내용\" nth=\"1\" />\n"
                "   - 스크롤: <daon_action action=\"scroll\" direction=\"down\" /> 또는 direction=\"up\"\n"
                "   * 팁: 같은 이름의 버튼이나 링크가 여러 개일 때는 nth=\"2\"처럼 몇 번째 요소인지 지정하여 정확히 클릭할 수 있습니다.\n"
                "5. [연속 자율 실행 지원]: 사용자의 지시가 여러 단계(예: '네이버로 이동해서 AI뉴스 검색해봐')로 구성된 경우, 첫 번째 액션(<daon_action action=\"navigate\" ... />)을 응답하면 브라우저가 이동한 뒤 변경된 새 화면 컨텍스트와 함께 다음 턴이 자동으로 이어집니다! 따라서 미래 화면의 요소를 미리 추측해서 누르려 하지 말고, [이동/클릭] → [새 화면 확인 후 후속 동작] 순서대로 자연스럽게 진행하세요. 모든 목표가 완료되면 액션 태그 없이 최종 요약 결과를 사용자에게 설명하고 마무리하세요.\n"
                "6. [답변 스타일]: 불필요한 사족 없이, 친절하고 싹싹하며 자신감 넘치는 말투로 짧고 명쾌하게 행동하세요. (예: '네! 네이버로 이동해서 AI뉴스를 검색할게요. <daon_action action=\"navigate\" url=\"https://www.naver.com\" />')\n\n"
            ) if browser_context else (
                "[BUILT-IN BROWSER]\n"
                "This app has a built-in shared browser: an in-app tab driven over CDP that the user can see.\n"
                "When a task requires viewing, scraping, or interacting with a web page, use the browser tools\n"
                "(browser_navigate, browser_snapshot, browser_click, browser_type, browser_scroll, browser_press,\n"
                "browser_console) — they operate this built-in tab. Do NOT launch external browsers, and do NOT\n"
                "use curl/wget for pages that need rendering or interaction. Workflow: call browser_navigate\n"
                "first; the returned snapshot lists interactive elements as @eN refs — use those refs with\n"
                "browser_click/browser_type. (This does NOT override the markdown rule above — never use the\n"
                "browser just to verify rendering.)\n\n"
            )
        ) +
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
