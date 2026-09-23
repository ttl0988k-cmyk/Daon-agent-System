"""api.streaming_tools - Tool injection and registration for streaming AIAgent instances.

Extracted from streaming.py to modularize MCP, patch registry, memory forget,
media generation, self-update, and self-evolution tool injections.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

_logger = logging.getLogger('api.streaming_tools')


def _safe_name(raw: str) -> str:
    """Normalize tool names while preserving hyphens."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(raw or ''))


def _normalize_input_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure schema has valid type and properties to prevent 400 bad request from LLM providers."""
    if not schema:
        return {"type": "object", "properties": {}}
    if schema.get("type") == "object" and "properties" not in schema:
        return {**schema, "properties": {}}
    return schema


def _append_tool_if_missing(agent: Any, schema: Dict[str, Any], tool_name: str) -> None:
    """Safely append a tool schema to agent.tools without introducing duplicates."""
    if not hasattr(agent, "tools") or not isinstance(agent.tools, list):
        return
    for t in agent.tools:
        if isinstance(t, dict):
            fn_name = t.get("function", {}).get("name") or t.get("name")
            if fn_name == tool_name:
                return
    agent.tools.append(schema)


def _deduplicate_agent_tools(agent: Any) -> None:
    """Ensure agent.tools has strictly unique tool names.

    Strict gateways like OpenCode Go / Anthropic reject requests with HTTP 400
    ('tools contains duplicate names: ...') if any name is duplicated.
    """
    if not hasattr(agent, "tools") or not isinstance(agent.tools, list):
        return
    seen_names = set()
    deduped = []
    for t in agent.tools:
        name = None
        if isinstance(t, dict):
            name = t.get("function", {}).get("name") or t.get("name")
        if name:
            if name in seen_names:
                continue
            seen_names.add(name)
        deduped.append(t)
    agent.tools = deduped


def inject_mcp_tools(agent: Any, cancel_event: threading.Event, session_id: str) -> int:
    """Inject active MCP tools from MCPManager into Hermes Registry and agent.tools."""
    injected_count = 0
    try:
        from api.mcp_client import get_mcp_manager
        from tools.registry import registry

        mcp_manager = get_mcp_manager()

        # Wait up to 10 seconds for MCP servers (50 * 0.2s)
        mcp_tools: List[Dict[str, Any]] = []
        for _ in range(50):
            if cancel_event.is_set():
                _logger.info("MCP sync aborted — stream cancelled for session %s", session_id)
                break
            mcp_tools = mcp_manager.get_all_tools()
            pending = sum(1 for c in mcp_manager._connections.values() if not c.connected and not c.error)
            if pending == 0:
                break
            time.sleep(0.2)

        registered_toolsets = set()

        for t in mcp_tools:
            server_id = t.get('_mcp_server', 'unknown')
            orig_name = t.get('name', '')

            safe_srv = _safe_name(server_id)
            safe_tool = _safe_name(orig_name)
            mcp_func_name = f"mcp_{safe_srv}_{safe_tool}"
            toolset_name = f"mcp-{safe_srv}"

            # 1) OpenAI-format schema for agent.tools (model visibility)
            mcp_desc = (t.get('description') or '').strip() or f"MCP tool {orig_name} from {server_id}"
            api_schema = {
                "type": "function",
                "function": {
                    "name": mcp_func_name,
                    "description": mcp_desc,
                    "parameters": _normalize_input_schema(t.get('inputSchema'))
                }
            }
            _append_tool_if_missing(agent, api_schema, mcp_func_name)
            agent.valid_tool_names.add(mcp_func_name)

            # 2) Flat registry schema for dispatch
            registry_schema = {
                "name": mcp_func_name,
                "description": mcp_desc,
                "parameters": _normalize_input_schema(t.get('inputSchema')),
            }

            # 3) Handler: call mcp_manager
            def _make_handler(sid: str, tname: str):
                def _handler(args: dict, **kwargs) -> str:
                    _logger.debug("MCP call -> server=%s tool=%s args=%s", sid, tname, list(args.keys()) if args else 'none')
                    result = mcp_manager.call_tool(sid, tname, args)
                    if result.get('ok'):
                        payload = result.get('result', 'Success')
                        if isinstance(payload, str):
                            return json.dumps({"result": payload}, ensure_ascii=False)
                        return json.dumps({"result": json.dumps(payload, ensure_ascii=False, default=str)}, ensure_ascii=False)
                    else:
                        return json.dumps({"error": result.get('error', 'Unknown error')}, ensure_ascii=False)
                return _handler

            # 4) check_fn: server connectivity
            def _make_check_fn(sid: str):
                def _check() -> bool:
                    conn = mcp_manager._connections.get(sid)
                    return conn is not None and conn.connected
                return _check

            registry.register(
                name=mcp_func_name,
                toolset=toolset_name,
                schema=registry_schema,
                handler=_make_handler(server_id, orig_name),
                check_fn=_make_check_fn(server_id),
                is_async=False,
                description=registry_schema["description"],
            )
            registered_toolsets.add(toolset_name)
            injected_count += 1

        for ts in registered_toolsets:
            alias = ts.replace("mcp-", "", 1)
            registry.register_toolset_alias(alias, ts)

        if injected_count > 0:
            _logger.info("Registered %d MCP tools into Hermes registry + agent.tools.", injected_count)
    except Exception as e:
        _logger.warning("Failed to inject MCP tools: %s", e, exc_info=True)

    return injected_count


def inject_patch_registry_tools(agent: Any) -> None:
    """Inject query_patches and register_patch tools into agent."""
    try:
        from api.patch_registry import query_patches as _pr_query, register_patch as _pr_register
        from tools.registry import registry as _pr_registry

        # 1) query_patches
        _pr_query_schema = {
            "type": "function",
            "function": {
                "name": "query_patches",
                "description": "Query registered load-bearing patches for a file. Returns warnings about code that must NOT be reverted. Use before editing any file to check for protected patches.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Relative file path to query patches for (e.g. 'api/api/streaming.py')"},
                    },
                    "required": ["file_path"]
                }
            }
        }
        _append_tool_if_missing(agent, _pr_query_schema, "query_patches")
        agent.valid_tool_names.add("query_patches")

        def _pr_query_handler(args: dict, **kwargs) -> str:
            fp = args.get('file_path', '')
            results = _pr_query(fp)
            if not results:
                return json.dumps({"patches": [], "message": f"No registered patches for {fp}"}, ensure_ascii=False)
            return json.dumps({"patches": results, "count": len(results)}, ensure_ascii=False)

        _pr_registry.register(
            name="query_patches",
            toolset="patch-registry",
            schema={"name": "query_patches", "description": "Query registered patches for a file", "parameters": _pr_query_schema["function"]["parameters"]},
            handler=_pr_query_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Query registered load-bearing patches for a file",
        )

        # 2) register_patch
        _pr_register_schema = {
            "type": "function",
            "function": {
                "name": "register_patch",
                "description": "Register a load-bearing patch that must NOT be reverted. Call this after making an important fix so future edits are warned.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Relative file path the patch applies to"},
                        "description": {"type": "string", "description": "What the patch does (e.g. 'str type guard for models list')"},
                        "reason": {"type": "string", "description": "Why this patch exists and why reverting it would break things"},
                        "commit_hash": {"type": "string", "description": "Git commit hash if available"},
                    },
                    "required": ["file_path", "description"]
                }
            }
        }
        _append_tool_if_missing(agent, _pr_register_schema, "register_patch")
        agent.valid_tool_names.add("register_patch")

        def _pr_register_handler(args: dict, **kwargs) -> str:
            fp = args.get('file_path', '')
            desc = args.get('description', '')
            reason = args.get('reason', '')
            commit = args.get('commit_hash', '')
            if not fp or not desc:
                return json.dumps({"error": "file_path and description are required"}, ensure_ascii=False)
            pid = _pr_register(fp, desc, commit_hash=commit, reason=reason)
            if pid:
                return json.dumps({"ok": True, "patch_id": pid, "message": f"Patch registered for {fp}"}, ensure_ascii=False)
            return json.dumps({"error": "Failed to register patch"}, ensure_ascii=False)

        _pr_registry.register(
            name="register_patch",
            toolset="patch-registry",
            schema={"name": "register_patch", "description": "Register a load-bearing patch", "parameters": _pr_register_schema["function"]["parameters"]},
            handler=_pr_register_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Register a load-bearing patch that must not be reverted",
        )

        _pr_registry.register_toolset_alias("patches", "patch-registry")
        _logger.debug("Injected query_patches + register_patch tools into agent.")
    except Exception as _pr_inj_e:
        _logger.warning("Patch registry tool injection failed: %s", _pr_inj_e)


def inject_memory_forget_tool(agent: Any) -> None:
    """Inject memory_forget tool into agent."""
    try:
        from api import memory_store as _mf_store
        from tools.registry import registry as _mf_registry

        _mf_schema = {
            "type": "function",
            "function": {
                "name": "memory_forget",
                "description": "Delete a fact from the user's long-term memory. Use when the user asks to forget/remove a remembered fact. Provide fact_id if known; otherwise provide query to search matching facts first.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact_id": {"type": "integer", "description": "Numeric id of the fact to delete"},
                        "query": {"type": "string", "description": "Text to search matching facts when fact_id is unknown"},
                    },
                }
            }
        }
        _append_tool_if_missing(agent, _mf_schema, "memory_forget")
        agent.valid_tool_names.add("memory_forget")

        def _mf_handler(args: dict, **kwargs) -> str:
            fact_id = args.get('fact_id')
            query = (args.get('query') or '').strip()
            if fact_id is not None:
                try:
                    result = _mf_store.delete_fact(fact_id)
                except Exception as _e:
                    return json.dumps({"ok": False, "error": str(_e)}, ensure_ascii=False)
                if result and result.get('ok'):
                    return json.dumps({"ok": True, "deleted_fact_id": fact_id,
                                        "impact": result.get('impact', {})}, ensure_ascii=False)
                return json.dumps({"ok": False, "error": f"Fact {fact_id} not found or delete failed"}, ensure_ascii=False)
            if query:
                try:
                    facts = _mf_store.list_facts(limit=100)
                except Exception:
                    facts = []
                q = query.lower()
                matches = [f for f in facts if q in str(f.get('content', '')).lower()]
                if not matches:
                    return json.dumps({"ok": False, "error": f"No facts matching '{query}'"}, ensure_ascii=False)
                candidates = [{"id": f.get('id'), "content": f.get('content')} for f in matches[:10]]
                return json.dumps({
                    "ok": False, "need_fact_id": True, "candidates": candidates,
                    "message": "Multiple matches found. Confirm with the user which fact to delete, then call memory_forget again with the exact fact_id."
                }, ensure_ascii=False)
            return json.dumps({"ok": False, "error": "Provide fact_id or query"}, ensure_ascii=False)

        _mf_registry.register(
            name="memory_forget",
            toolset="memory-store",
            schema={"name": "memory_forget", "description": "Delete a fact from long-term memory", "parameters": _mf_schema["function"]["parameters"]},
            handler=_mf_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Forget (delete) a fact from the user's long-term memory",
        )
        _mf_registry.register_toolset_alias("forget", "memory-store")
        _logger.debug("Injected memory_forget tool into agent.")
    except Exception as _mf_inj_e:
        _logger.warning("Memory forget tool injection failed: %s", _mf_inj_e)


def inject_media_generation_tools(agent: Any) -> None:
    """Inject generate_image and generate_video tools into agent."""
    try:
        from api.media_generation import register_media_generation_tools as _mg_register
        from tools.registry import registry as _mg_registry

        _mg_img_schema, _mg_vid_schema = _mg_register(_mg_registry)
        _append_tool_if_missing(agent, _mg_img_schema, "generate_image")
        agent.valid_tool_names.add("generate_image")
        _append_tool_if_missing(agent, _mg_vid_schema, "generate_video")
        agent.valid_tool_names.add("generate_video")
        _logger.debug("Injected generate_image + generate_video tools.")
    except Exception as _mg_inj_e:
        _logger.warning("Media tools injection failed: %s", _mg_inj_e)


def inject_self_update_tool(agent: Any, session: Any, session_id: str) -> None:
    """Inject request_server_update tool for supervisor-controlled restart."""
    try:
        from api.dynamic.restart_request import request_restart as _rr_request, RestartRequestError as _RR_Error
        from tools.registry import registry as _su_registry

        _su_schema = {
            "type": "function",
            "function": {
                "name": "request_server_update",
                "description": "Request a server restart so backend changes take effect. The Electron supervisor kills the server, optionally rebuilds/replaces server.exe, respawns it, health-checks it, and rolls back on failure. Refused while dynamic harness jobs are still running.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Why the restart is needed (e.g. 'backend patch applied to config.py')"},
                        "checkpoint_ref": {"type": "string", "description": "Git ref (commit hash) to roll back to if the restarted server fails its health check"},
                        "rebuild": {"type": "boolean", "description": "True when backend Python source changed and server.exe must be rebuilt before respawn"},
                    },
                    "required": ["reason"]
                }
            }
        }
        _append_tool_if_missing(agent, _su_schema, "request_server_update")
        agent.valid_tool_names.add("request_server_update")

        def _su_handler(args: dict, **kwargs) -> str:
            reason = (args.get('reason') or '').strip()
            if not reason:
                return json.dumps({"ok": False, "error": "reason is required"}, ensure_ascii=False)
            try:
                rebuild_flag = bool(args.get('rebuild'))
                rebuild_notice = " (server.exe 바이너리 재빌드 포함)" if rebuild_flag else ""

                # Checkpoint turn to disk before server is killed
                try:
                    checkpoint_msg = {
                        "role": "assistant",
                        "content": (
                            f"🔄 **[자가 수리/확장 재기동 안내]**\n\n"
                            f"대표님, 작업하신 변경 사항을 시스템에 안전하게 적용하기 위해 서버 재기동{rebuild_notice}을 시작합니다.\n\n"
                            f"- **재기동 사유**: {reason}\n"
                            f"- **체크포인트**: `{args.get('checkpoint_ref') or '현재 작업 상태'}`\n\n"
                            f"재기동 완료 후 이 세션에서 작업을 그대로 이어가며, 제가 수정한 모든 내역을 기억하고 있겠습니다."
                        ),
                        "timestamp": int(time.time()),
                        "is_checkpoint": True,
                    }
                    if not any(m.get('content') == checkpoint_msg['content'] for m in session.messages[-2:]):
                        session.messages.append(checkpoint_msg)
                        session.save()
                        from api.models import _write_session_index
                        _write_session_index()
                except Exception as _cp_save_e:
                    _logger.warning("Turn checkpoint save failed: %s", _cp_save_e)

                payload = _rr_request(
                    reason,
                    checkpoint_ref=args.get('checkpoint_ref'),
                    rebuild=rebuild_flag,
                    session_id=session_id,
                    files_modified=args.get('files_modified'),
                    summary=reason,
                )
                return json.dumps({"ok": True, **payload,
                                    "message": "Restart request recorded. The supervisor will restart the server within ~5s."}, ensure_ascii=False)
            except _RR_Error as _e:
                return json.dumps({"ok": False, "error": str(_e)}, ensure_ascii=False)

        _su_registry.register(
            name="request_server_update",
            toolset="self-update",
            schema={"name": "request_server_update", "description": "Request supervised server restart (optionally rebuild) to apply backend changes",
                    "parameters": _su_schema["function"]["parameters"]},
            handler=_su_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Record a self-update restart request for the Electron supervisor",
        )
        _su_registry.register_toolset_alias("update", "self-update")
        _logger.debug("Injected request_server_update tool into agent.")
    except Exception as _su_inj_e:
        _logger.warning("Self-update tool injection failed: %s", _su_inj_e)


def inject_self_evolution_tool(
    agent: Any, session_id: str = None, stream_id: str = None
) -> None:
    """Inject the propose_self_evolution tool into the agent.

    session_id / stream_id are captured in the handler closure so the
    background proposal thread can route progress to a live SSE queue. The
    dispatch chain now also forwards session_id (registry.dispatch resolves
    stream_id from it), so both paths resolve to the same queue.
    """
    tool_name = "propose_self_evolution"
    try:
        from api.dynamic.self_evolution import register_self_evolution_tools as _se_register
        from tools.registry import registry as _se_registry

        # registry 는 원본(raw) 스키마(name/description/parameters)만 보관하고
        # OpenAI 래퍼({"type":"function","function":{...}})는 감싸지 않는다.
        # 따라서 entry.schema 를 그대로 agent.tools 에 넣으면 로더/디스패처가
        # tool_name 을 찾지 못해 도구가 없는 것처럼 보인다. 재사용 분기를 두지
        # 않고 항상 register 반환(래퍼) 스키마를 주입한다.
        # (동일 이름/동일 toolset 재등록은 registry 에서 멱등 overwrite 된다.)
        # register_self_evolution_tools 는 절대 raise 하지 않는다.
        _se_schema = _se_register(
            _se_registry, session_id=session_id, stream_id=stream_id
        )

        if not _se_schema:
            _logger.warning("Self-evolution tool injection skipped: no schema returned.")
            return

        _append_tool_if_missing(agent, _se_schema, tool_name)
        agent.valid_tool_names.add(tool_name)
        _logger.debug(
            "Injected %s tool into agent (session=%s, stream=%s).",
            tool_name, session_id or "-", stream_id or "-",
        )
    except Exception as _se_inj_e:
        _logger.warning("Self-evolution tool injection failed: %s", _se_inj_e)


def inject_daon_action_tool(agent: Any) -> None:
    """Inject daon_action tool into agent so the model can invoke browser actions natively."""
    try:
        from tools.registry import registry

        _daon_action_schema = {
            "type": "function",
            "function": {
                "name": "daon_action",
                "description": (
                    "실제 사용자의 구글 크롬 브라우저 활성 탭 화면에서 버튼/링크/요소를 클릭하거나, 텍스트를 입력하거나, "
                    "URL로 이동하거나, 스크롤하거나, 스냅샷을 캡처하는 실시간 브라우저 제어 도구입니다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["click", "type", "navigate", "scroll", "snapshot", "screenshot", "wait", "switch_tab", "close_tab", "new_tab", "hover", "press"],
                            "description": "수행할 브라우저 동작: click(클릭), type(입력), navigate(URL 이동), scroll(스크롤), snapshot(스냅샷), screenshot(스크린샷), wait(대기), hover(마우스 올리기), press(키 입력)"
                        },
                        "target": {
                            "type": "string",
                            "description": "클릭하거나 입력할 버튼/링크/요소의 텍스트, placeholder, 식별자 (예: 'Generate Image', '로그인')"
                        },
                        "selector": {
                            "type": "string",
                            "description": "조작할 대상의 CSS 선택자 (옵션)"
                        },
                        "text": {
                            "type": "string",
                            "description": "입력할 텍스트 내용 (action이 'type'일 때 필수)"
                        },
                        "url": {
                            "type": "string",
                            "description": "이동할 URL (action이 'navigate' 또는 'new_tab'일 때)"
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down"],
                            "description": "스크롤 방향 (action이 'scroll'일 때)"
                        },
                        "key": {
                            "type": "string",
                            "description": "입력할 키 (action이 'press'일 때, 예: 'Enter')"
                        },
                        "nth": {
                            "type": "integer",
                            "description": "동일 텍스트 요소가 여러 개일 때 순번 (1부터 시작)"
                        },
                        "node_id": {
                            "type": "integer",
                            "description": "스냅샷에서 확인된 대상 요소의 정수 번호 (옵션)"
                        }
                    },
                    "required": ["action"]
                }
            }
        }

        _append_tool_if_missing(agent, _daon_action_schema, "daon_action")
        if hasattr(agent, 'valid_tool_names') and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("daon_action")

        def _daon_action_handler(args: dict, **kwargs) -> str:
            action = args.get('action', 'click')
            target = args.get('target', '')
            text = args.get('text', '')
            selector = args.get('selector', '')
            url = args.get('url', '')
            direction = args.get('direction', '')
            key = args.get('key', '')
            nth = args.get('nth')
            node_id = args.get('node_id')

            attr_parts = [f'action="{action}"']
            if target:
                attr_parts.append(f'target="{target}"')
            if text:
                attr_parts.append(f'text="{text}"')
            if selector:
                attr_parts.append(f'selector="{selector}"')
            if url:
                attr_parts.append(f'url="{url}"')
            if direction:
                attr_parts.append(f'direction="{direction}"')
            if key:
                attr_parts.append(f'key="{key}"')
            if nth is not None:
                attr_parts.append(f'nth="{nth}"')
            if node_id is not None:
                attr_parts.append(f'node_id="{node_id}"')

            tag = f"<daon_action {' '.join(attr_parts)} />"
            _logger.info("Executed daon_action tool call: %s", tag)
            return json.dumps({
                "ok": True,
                "status": "action_queued",
                "action": action,
                "tag": tag,
                "message": f"브라우저에서 '{action}' 동작이 예약되었습니다: {tag}"
            }, ensure_ascii=False)

        registry.register(
            name="daon_action",
            toolset="browser-extension",
            schema={
                "name": "daon_action",
                "description": _daon_action_schema["function"]["description"],
                "parameters": _daon_action_schema["function"]["parameters"]
            },
            handler=_daon_action_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Browser action dispatcher for Chrome extension"
        )
        registry.register_toolset_alias("browser-extension", "browser-extension")
        _logger.debug("Injected daon_action tool into agent.")
    except Exception as _e:
        _logger.warning("daon_action tool injection failed: %s", _e)


def expand_sibling_candidates(
    items: List[str],
    results: List[str],
    probabilities: Optional[List[Dict[str, float]]] = None,
    irrelevant_labels: Optional[set] = None,
    window: int = 2
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Expand UI control candidates based on sibling/group proximity.

    When an element in a control group (e.g., 'option', 'tab', 'radio', 'button')
    is identified as a candidate, neighboring elements sharing the same tag or role
    within +/- `window` distance are preserved to guarantee 100% recall.
    """
    if irrelevant_labels is None:
        irrelevant_labels = {
            "irrelevant", "content_noise", "noise", "none", "article_content",
            "skip", "discard", "content", "other"
        }

    tag_re = re.compile(r"^(n\d+)\s+([a-zA-Z0-9_\-]+)\s*(.*)$")
    # Clustered control groups that should expand to adjacent siblings
    CONTROL_TAGS = {"option", "tab", "radio", "menuitem", "button"}

    candidates: List[Dict[str, Any]] = []
    sibling_expansions: List[Dict[str, Any]] = []
    expanded_indices = set()

    # Pass 1: Direct positive classifications and soft-threshold candidates
    for idx, (it, res) in enumerate(zip(items, results)):
        prob_dist = probabilities[idx] if (probabilities and idx < len(probabilities)) else {}
        is_direct = res not in irrelevant_labels

        soft_hit = False
        soft_cat = None
        if not is_direct and prob_dist:
            # Only trigger soft threshold if the model is genuinely uncertain (irrelevant < 0.60)
            irrel_p = max((prob_dist.get(lbl, 0.0) for lbl in irrelevant_labels if lbl in prob_dist), default=1.0)
            if irrel_p < 0.60:
                for c_name, c_p in prob_dist.items():
                    if c_name not in irrelevant_labels and c_p >= 0.30:
                        soft_hit = True
                        soft_cat = c_name
                        break

        if is_direct:
            candidates.append({
                "index": idx,
                "item": it,
                "category": res,
                "confidence": prob_dist,
                "reason": "classified"
            })
        elif soft_hit:
            expanded_indices.add(idx)
            entry = {
                "index": idx,
                "item": it,
                "category": soft_cat or "candidate",
                "confidence": prob_dist,
                "reason": f"soft_threshold: {soft_cat} prob >= 0.30 (irrelevant={irrel_p:.2f} < 0.60)"
            }
            candidates.append(entry)
            sibling_expansions.append(entry)

    # Pass 2: Sibling proximity expansion (Control Groups: option, tab, radio, button)
    for c in list(candidates):
        idx = c["index"]
        m = tag_re.match(items[idx])
        if not m:
            continue
        tag = m.group(2).lower()
        if tag not in CONTROL_TAGS:
            continue

        # Look at adjacent neighbors within window
        for n_idx in range(max(0, idx - window), min(len(items), idx + window + 1)):
            if n_idx == idx or n_idx in expanded_indices:
                continue
            if results[n_idx] in irrelevant_labels:
                n_m = tag_re.match(items[n_idx])
                if n_m and n_m.group(2).lower() == tag:
                    expanded_indices.add(n_idx)
                    n_prob = probabilities[n_idx] if (probabilities and n_idx < len(probabilities)) else {}
                    exp_entry = {
                        "index": n_idx,
                        "item": items[n_idx],
                        "category": c["category"],
                        "confidence": n_prob,
                        "reason": f"sibling_expansion: adjacent {tag} to {items[idx]}"
                    }
                    candidates.append(exp_entry)
                    sibling_expansions.append(exp_entry)

    candidates.sort(key=lambda x: x["index"])
    return candidates, sibling_expansions


def inject_fast_decision_tool(agent: Any) -> None:
    """Inject Laya fast decision engine tool into agent.
    Allows agent to classify or score bulk items without LLM token cost.
    """
    try:
        from tools.registry import registry
        from api.laya_client import laya_client

        _decision_schema = {
            "type": "function",
            "function": {
                "name": "fast_decision_engine",
                "description": (
                    "초고속 System 1 결정 엔진 (Laya/ModernBERT 기반). "
                    "대량의 텍스트/UI 요소를 분류하거나 참/거짓 판단을 외부 LLM 토큰 소모 없이 로컬 GPU에서 0원에 초고속으로 일괄 처리합니다.\n"
                    "★ 브라우저 스냅샷 필터링 팁 (Recall 100% 보장):\n"
                    "1. 세부 액션으로 쪼개기보다 'target_control' (조작 대상: 최신순/관련도순 정렬, 검색창, 필터버튼) vs 'irrelevant' (단순 기사/광고)로 이진 분류할 때 가장 정확합니다.\n"
                    "2. criteria에 한국어 키워드(최신순, 관련도순, 날짜순, 검색옵션 등)를 명시하세요.\n"
                    "3. 형제 요소 자동 보정(Sibling Expansion)이 활성화되어 옵션 그룹(option, tab, radio) 중 하나만 잡혀도 이웃 옵션 전체가 candidates에 포함됩니다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "enum": ["batch_classify", "decide"],
                            "description": "작업 종류: 'batch_classify' (여러 텍스트 분류)"
                        },
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "batch_classify 수행 시 분류할 텍스트 항목 목록"
                        },
                        "categories": {
                            "type": "object",
                            "description": "카테고리 ID -> 설명 맵 (예: {'target_control': '정렬 옵션(최신순, 관련도순), 검색버튼, 필터', 'irrelevant': '뉴스 기사 본문, 광고, 언론사'})"
                        },
                        "instruction": {
                            "type": "string",
                            "description": "분류 또는 판단 지침"
                        }
                    },
                    "required": ["task"]
                }
            }
        }

        def _decision_handler(args: dict = None, **kwargs) -> str:
            if not laya_client.is_healthy():
                return json.dumps({
                    "ok": False,
                    "error": "Laya decision service is currently offline (localhost:8765). Run scripts/start_laya.ps1."
                }, ensure_ascii=False)

            call_args = {}
            if isinstance(args, dict):
                call_args.update(args)
            elif isinstance(args, str):
                call_args["task"] = args
            call_args.update(kwargs)

            task = call_args.get("task", "batch_classify")
            if isinstance(task, dict):
                call_args.update(task)
                task = call_args.get("task", "batch_classify")

            items = call_args.get("items")
            categories = call_args.get("categories")
            instruction = call_args.get("instruction", "")

            if task == "batch_classify":
                if not items or not categories:
                    return json.dumps({"ok": False, "error": "items and categories are required for batch_classify"})

                resp_data = laya_client.batch_classify(
                    items, categories, instruction or "Classify this item", return_details=True
                )
                if isinstance(resp_data, dict):
                    results = resp_data.get("results", [])
                    probabilities = resp_data.get("probabilities", [])
                else:
                    results = resp_data
                    probabilities = []

                candidates, sibling_expansions = expand_sibling_candidates(items, results, probabilities)

                return json.dumps({
                    "ok": True,
                    "count": len(results),
                    "candidates_count": len(candidates),
                    "candidates": candidates,
                    "sibling_expansions": sibling_expansions,
                    "results": results
                }, ensure_ascii=False)

            if task == "decide":
                state = call_args.get("state", {})
                questions = call_args.get("questions", {})
                res = laya_client.decide(state, questions)
                return json.dumps({
                    "ok": res is not None,
                    "decision": res
                }, ensure_ascii=False)

            return json.dumps({"ok": False, "error": f"Unknown task: {task}"})

        registry.register(
            name="fast_decision_engine",
            toolset="decision-engine",
            schema={
                "name": "fast_decision_engine",
                "description": _decision_schema["function"]["description"],
                "parameters": _decision_schema["function"]["parameters"]
            },
            handler=_decision_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Laya fast decision engine for high-throughput zero-token classification"
        )
        registry.register_toolset_alias("decision-engine", "decision-engine")
        _append_tool_if_missing(agent, _decision_schema, "fast_decision_engine")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("fast_decision_engine")
        _logger.debug("Injected fast_decision_engine tool into agent.")
    except Exception as _e:
        _logger.warning("fast_decision_engine tool injection failed: %s", _e)


def register_all_streaming_tools(
    agent: Any,
    session: Any,
    session_id: str,
    cancel_event: threading.Event,
    stream_id: str = None,
) -> int:
    """Convenience orchestrator that injects all dynamic streaming tools.

    Returns the number of injected MCP tools.
    """
    mcp_count = inject_mcp_tools(agent, cancel_event, session_id)
    inject_patch_registry_tools(agent)
    inject_memory_forget_tool(agent)
    inject_media_generation_tools(agent)
    inject_self_update_tool(agent, session, session_id)
    inject_self_evolution_tool(agent, session_id=session_id, stream_id=stream_id)
    inject_daon_action_tool(agent)
    inject_fast_decision_tool(agent)
    _deduplicate_agent_tools(agent)
    return mcp_count
