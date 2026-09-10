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


def inject_mcp_tools(agent: Any, cancel_event: threading.Event, session_id: str) -> int:
    """Inject active MCP tools from MCPManager into Hermes Registry and agent.tools."""
    injected_count = 0
    try:
        from api.mcp_client import get_mcp_manager
        from tools.registry import registry

        mcp_manager = get_mcp_manager()

        # Wait up to 5 seconds for MCP servers (25 * 0.2s)
        mcp_tools: List[Dict[str, Any]] = []
        for _ in range(25):
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
            agent.tools.append(api_schema)
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
        agent.tools.append(_pr_query_schema)
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
        agent.tools.append(_pr_register_schema)
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
        agent.tools.append(_mf_schema)
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
        agent.tools.append(_mg_img_schema)
        agent.valid_tool_names.add("generate_image")
        agent.tools.append(_mg_vid_schema)
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
        agent.tools.append(_su_schema)
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


def inject_self_evolution_tool(agent: Any) -> None:
    """Inject propose_self_evolution tool into agent."""
    try:
        from api.dynamic.self_evolution import register_self_evolution_tools as _se_register
        from tools.registry import registry as _se_registry

        _se_schema = _se_register(_se_registry)
        if _se_schema:
            agent.tools.append(_se_schema)
            agent.valid_tool_names.add("propose_self_evolution")
            _logger.debug("Injected propose_self_evolution tool into agent.")
    except Exception as _se_inj_e:
        _logger.warning("Self-evolution tool injection failed: %s", _se_inj_e)


def register_all_streaming_tools(
    agent: Any,
    session: Any,
    session_id: str,
    cancel_event: threading.Event,
) -> int:
    """Convenience orchestrator that injects all dynamic streaming tools.

    Returns the number of injected MCP tools.
    """
    mcp_count = inject_mcp_tools(agent, cancel_event, session_id)
    inject_patch_registry_tools(agent)
    inject_memory_forget_tool(agent)
    inject_media_generation_tools(agent)
    inject_self_update_tool(agent, session, session_id)
    inject_self_evolution_tool(agent)
    return mcp_count
