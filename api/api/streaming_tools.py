"""api.streaming_tools - Tool injection and registration for streaming AIAgent instances.

Extracted from streaming.py to modularize MCP, patch registry, memory forget,
media generation, self-update, and self-evolution tool injections.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

_logger = logging.getLogger('api.streaming_tools')


def _safe_name(raw: str) -> str:
    """Normalize tool names while preserving hyphens."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(raw or ''))


def _clean_schema_node(node: Any) -> Any:
    """Recursively clean an arbitrary JSON schema node (dict, list, or primitive)."""
    if isinstance(node, list):
        return [_clean_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node
    import copy
    n = copy.copy(node)
    for meta_key in ("$schema", "$id", "title"):
        n.pop(meta_key, None)
    if "const" in n:
        c_val = n.pop("const")
        if "enum" not in n:
            n["enum"] = [c_val]
    if "properties" in n:
        if isinstance(n["properties"], dict):
            n["properties"] = {k: _clean_schema_node(v) for k, v in n["properties"].items()}
        else:
            n["properties"] = {}
    if "items" in n and isinstance(n["items"], (dict, list)):
        n["items"] = _clean_schema_node(n["items"])
    for comp_key in ("anyOf", "oneOf", "allOf"):
        if comp_key in n and isinstance(n[comp_key], list):
            n[comp_key] = [_clean_schema_node(item) for item in n[comp_key]]
    if not any(k in n for k in ("type", "enum", "anyOf", "oneOf", "allOf", "$ref")):
        n["type"] = "object" if "properties" in n else "string"
    return n


def _normalize_input_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Ensure schema has valid type, properties, and clean properties for OpenAI/Hermes tool-calling."""
    if not schema or not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    cleaned = _clean_schema_node(schema)
    if cleaned.get("type") != "object":
        cleaned["type"] = "object"
    if "properties" not in cleaned or not isinstance(cleaned["properties"], dict):
        cleaned["properties"] = {}
    return cleaned


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


def inject_mcp_tools(agent: Any, cancel_event: threading.Event, session_id: str, user_prompt: str = "") -> int:
    """Inject active MCP tools from MCPManager into Hermes Registry and agent.tools.

    user_prompt가 주어지면 Laya prune_mcp로 요약문에 노출할 서버를 1~2개로
    선별한다 (스킬 match_skill과 같은 자리). 전체 도구는 레지스트리/핸들러에
    그대로 등록되므로 list_mcp_tools로 언제든 꺼내 쓸 수 있다.
    """
    injected_count = 0
    try:
        from api.mcp_client import get_mcp_manager
        from tools.registry import registry

        mcp_manager = get_mcp_manager()

        # ── [Laya On-Demand 2026-10-17] 후보=등록 전체+카탈로그, 선택된 standby 서버는 일시 연결 ──
        # 종전엔 연결된 서버만 후보라 꺼둔 MCP는 Laya가 고를 수 없었다.
        # 이제 등록 전체를 후보로 넘기고, 고른 standby 서버는 여기서 깨운 뒤
        # 아래 기존 루프가 그대로 등록한다 (enabled 변경·config 저장 없음 → 재기동 시 standby 복귀).
        # 깨우기 실패분은 후보에서 제외(fail-open). playwright는 8088 스켈레톤을 쓰므로 후보 제외.
        # [임베딩 이관 2026-10-17] Laya prune보다 SkillRanker /rank_mcps를 먼저 시도.
        # 실측: 동일 20케이스 TOP1 18/20·TOP3 19/20 (Laya 4/20). 랭커 무확신/실패 시 Laya 폴백.
        _preselected_all = None
        if user_prompt:
            try:
                from api.laya_client import laya_client as _laya_prune_client, get_mcp_desc_map
                _all_registered = [
                    s.get('server_id') for s in (mcp_manager.list_servers() or [])
                    if s.get('server_id') and s.get('server_id') != 'playwright'
                ]
                _ranker_hit = False
                try:
                    import json as _js, urllib.request as _url
                    _rq = _url.Request(
                        'http://127.0.0.1:8766/rank_mcps',
                        data=_js.dumps({'prompt': user_prompt, 'top_n': 3}).encode('utf-8'),
                        headers={'Content-Type': 'application/json'})
                    _rr = _url.urlopen(_rq, timeout=5)
                    _rd = _js.loads(_rr.read().decode('utf-8') or '{}')
                    if _rd.get('confident') and _rd.get('mcps'):
                        _ranked = [m.get('name') for m in _rd['mcps'] if m.get('name')]
                        # 등록된 서버만 (카탈로그 기준이라 미등록 ID가 섞일 수 있음)
                        _ranked = [s for s in _ranked if s in _all_registered]
                        if _ranked:
                            _preselected_all = _ranked
                            _ranker_hit = True
                            _logger.info("[MCP-Preselect] ranker hit: %s (margin=%s)",
                                         _ranked, _rd.get('margin'))
                except Exception as _e_rank:
                    _logger.debug("ranker preselect bypassed: %s", _e_rank)
                if not _ranker_hit:
                    _mcp_descs_all = get_mcp_desc_map(_all_registered) or None
                    _preselected_all = _laya_prune_client.prune_mcp(
                        user_prompt, _all_registered, mcp_descriptions=_mcp_descs_all)
                if _preselected_all:
                    _already_on = {
                        sid for sid, c in mcp_manager._connections.items()
                        if getattr(c, 'connected', False)
                    }
                    _to_wake = [s for s in _preselected_all if s not in _already_on]
                    if _to_wake:
                        _wake_res = mcp_manager.connect_transient_many(_to_wake)
                        _wake_failed = {s for s, _ok in _wake_res.items() if not _ok}
                        if _wake_failed:
                            _logger.info("[Laya-OnDemand] transient connect failed, excluded: %s", sorted(_wake_failed))
                            _preselected_all = [s for s in _preselected_all if s not in _wake_failed]
                        else:
                            _logger.info("[Laya-OnDemand] woken standby servers: %s", _to_wake)
            except Exception as _e_wake:
                _logger.debug("Laya on-demand preselect bypassed: %s", _e_wake)
                _preselected_all = None

        # Wait up to 3 seconds for active MCP servers (15 * 0.2s)
        mcp_tools: List[Dict[str, Any]] = []
        for _ in range(15):
            if cancel_event.is_set():
                _logger.info("MCP sync aborted — stream cancelled for session %s", session_id)
                break
            mcp_tools = mcp_manager.get_all_tools()
            pending = sum(
                1 for c in mcp_manager._connections.values()
                if getattr(c, 'enabled', True)
                and not getattr(c, 'expired', False)
                and not c.connected
                and not c.error
                and getattr(c, 'server_id', '') != 'playwright'
            )
            if pending == 0:
                break
            time.sleep(0.2)

        registered_toolsets = set()
        tools_by_server: Dict[str, List[Dict[str, Any]]] = {}

        # Lazy MCP Router: enabled by default when total tools > 5 or env DAON_LAZY_MCP != '0'
        use_lazy_mcp = os.getenv("DAON_LAZY_MCP", "1").strip().lower() not in ("0", "false", "no", "off")

        for t in mcp_tools:
            server_id = t.get('_mcp_server', 'unknown')
            orig_name = t.get('name', '')

            tools_by_server.setdefault(server_id, []).append(t)

            safe_srv = _safe_name(server_id)
            safe_tool = _safe_name(orig_name)
            mcp_func_name = f"mcp_{safe_srv}_{safe_tool}"
            toolset_name = f"mcp-{safe_srv}"

            # Always add to valid_tool_names so any direct call / subagent delegation is allowed
            if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
                agent.valid_tool_names.add(mcp_func_name)

            mcp_desc = (t.get('description') or '').strip() or f"MCP tool {orig_name} from {server_id}"

            # If not in lazy mode, push all individual schemas to agent.tools
            if not use_lazy_mcp:
                api_schema = {
                    "type": "function",
                    "function": {
                        "name": mcp_func_name,
                        "description": mcp_desc,
                        "parameters": _normalize_input_schema(t.get('inputSchema'))
                    }
                }
                _append_tool_if_missing(agent, api_schema, mcp_func_name)

            # Flat registry schema for dispatch
            registry_schema = {
                "name": mcp_func_name,
                "description": mcp_desc,
                "parameters": _normalize_input_schema(t.get('inputSchema')),
            }

            # Handler: call mcp_manager
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

            # check_fn: server connectivity
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

        # ── Lazy MCP Router ──
        # Expose only call_mcp_tool and list_mcp_tools to agent.tools
        # saving 10,000+ tokens per turn while keeping full capability.
        if use_lazy_mcp and injected_count > 0:
            # ── [Laya MCP Pre-select] 스킬 match_skill과 같은 자리: 질문 기준 1~2개만 요약 노출 ──
            # (전체 도구는 위에서 레지스트리에 그대로 등록됨. 요약문만 가지치기)
            _summary_servers = sorted(tools_by_server.items())
            if user_prompt:
                try:
                    _needed = None
                    if _preselected_all is not None:
                        # [2026-10-17] 위에서 등록 전체로 이미 선별 → Laya 재호출 없이 재사용
                        _wanted = set(_preselected_all)
                        _needed = [sid for sid, _ in _summary_servers if sid in _wanted]
                        _logger.info("[Laya-MCP-Preselect] prompt=%.60s needed=%s (registry-wide, reused)", user_prompt, _needed)
                    else:
                        from api.laya_client import laya_client as _laya_prune_client, get_mcp_desc_map
                        _all_sids = [sid for sid, _ in _summary_servers]
                        # [FIX 2026-10-13] MCP 카탈로그 — 신규 MCP는 JSON 1줄로 온보딩, Laya 코드 수정 불필요.
                        # 카탈로그에서 {sid: 설명}을 만들어 prune_mcp에 넘긴다. 읽기 실패 시 None → ID만 전달(fail-open).
                        _mcp_descs = get_mcp_desc_map(_all_sids) or None
                        _needed = _laya_prune_client.prune_mcp(user_prompt, _all_sids, mcp_descriptions=_mcp_descs)
                    if _needed:
                        _needed_set = set(_needed)
                        _summary_servers = [(sid, tl) for sid, tl in _summary_servers if sid in _needed_set]
                        _logger.info("[Laya-MCP-Preselect] prompt=%.60s needed=%s", user_prompt, _needed)
                    else:
                        _summary_servers = []
                        _logger.info("[Laya-MCP-Preselect] prompt=%.60s needed=[] (pure coding/conversation)", user_prompt)
                except Exception as _e_prune:
                    _logger.debug("Laya MCP preselect bypassed: %s", _e_prune)
            summary_lines = []
            for sid, tool_list in _summary_servers:
                tnames = [tool.get('name', '') for tool in tool_list]
                shown = tnames[:8]
                extra = f" (+{len(tnames)-8} more)" if len(tnames) > 8 else ""
                summary_lines.append(f"  * {sid}: {', '.join(shown)}{extra}")
            servers_text = "\n".join(summary_lines) if summary_lines else "  (none preselected — use list_mcp_tools to discover)"

            call_mcp_schema = {
                "type": "function",
                "function": {
                    "name": "call_mcp_tool",
                    "description": (
                        "Call an MCP tool on an active MCP server.\n"
                        "Active MCP servers and tools:\n"
                        f"{servers_text}\n"
                        "Use `list_mcp_tools` to view parameter schemas for any server."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "server_name": {
                                "type": "string",
                                "description": "The target MCP server ID (e.g. 'filesystem', 'serena', 'daon-design')"
                            },
                            "tool_name": {
                                "type": "string",
                                "description": "The tool name on that MCP server (e.g. 'read_file', 'write_file')"
                            },
                            "arguments": {
                                "type": "object",
                                "description": "Arguments dictionary required by the tool"
                            }
                        },
                        "required": ["server_name", "tool_name", "arguments"]
                    }
                }
            }
            _append_tool_if_missing(agent, call_mcp_schema, "call_mcp_tool")
            if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
                agent.valid_tool_names.add("call_mcp_tool")

            def _call_mcp_router_handler(args: dict, **kwargs) -> str:
                sname = (args.get('server_name') or args.get('server') or args.get('ServerName') or '').strip()
                tname = (args.get('tool_name') or args.get('tool') or args.get('ToolName') or '').strip()
                cargs = args.get('arguments') or args.get('args') or args.get('Arguments') or {}
                if not isinstance(cargs, dict):
                    cargs = {}

                # If tool_name has mcp_{server}_ prefix, normalize it
                if sname and tname.startswith(f"mcp_{sname}_"):
                    tname = tname[len(f"mcp_{sname}_"):]

                # If server_name was omitted, try to auto-detect from tool_name
                if not sname and tname:
                    for sid, tool_list in tools_by_server.items():
                        if any(t.get('name') == tname for t in tool_list):
                            sname = sid
                            break

                if not sname or not tname:
                    return json.dumps({
                        "error": "Both server_name and tool_name are required.",
                        "available_servers": list(tools_by_server.keys())
                    }, ensure_ascii=False)

                _logger.info("Lazy MCP Router call: server=%s, tool=%s, args_keys=%s", sname, tname, list(cargs.keys()))
                result = mcp_manager.call_tool(sname, tname, cargs)
                if result.get('ok'):
                    payload = result.get('result', 'Success')
                    if isinstance(payload, str):
                        return json.dumps({"result": payload}, ensure_ascii=False)
                    return json.dumps({"result": json.dumps(payload, ensure_ascii=False, default=str)}, ensure_ascii=False)
                else:
                    return json.dumps({"error": result.get('error', f"MCP tool call failed: {sname}/{tname}")}, ensure_ascii=False)

            registry.register(
                name="call_mcp_tool",
                toolset="mcp-router",
                schema={
                    "name": "call_mcp_tool",
                    "description": "Call an MCP tool on an active MCP server",
                    "parameters": call_mcp_schema["function"]["parameters"]
                },
                handler=_call_mcp_router_handler,
                check_fn=lambda: True,
                is_async=False,
                description="Call an MCP tool on an active MCP server",
            )

            # list_mcp_tools
            list_mcp_schema = {
                "type": "function",
                "function": {
                    "name": "list_mcp_tools",
                    "description": (
                        "List available tools for active MCP servers — returns name + short description "
                        "+ parameter summary (param names, types, '*' = required). "
                        "To get ONE tool's full parameter schema, also pass tool_name."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "server_name": {
                                "type": "string",
                                "description": "Optional: server ID to inspect (e.g. 'filesystem'). Omit to list all."
                            },
                            "tool_name": {
                                "type": "string",
                                "description": (
                                    "Optional: exact tool name. Requires server_name. "
                                    "Returns that single tool's detailed schema (use when the "
                                    "summary is not enough to build the arguments)."
                                )
                            }
                        }
                    }
                }
            }
            _append_tool_if_missing(agent, list_mcp_schema, "list_mcp_tools")
            if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
                agent.valid_tool_names.add("list_mcp_tools")

            # ── [2026-10-19 토큰 최적화] inputSchema 축약 ──
            # rea MCP는 도구 139개 inputSchema 합계가 551,161자(examples·$schema 포함)라
            # list_mcp_tools(server_name='rea') 1회가 1,405,920자(~164,940 토큰)를 반환해
            # 컨텍스트를 즉사시켰다. 목록 모드는 파라미터 이름/타입/필수만 남기고,
            # 상세는 tool_name 단일 조회로 분리한다.
            def _slim_schema(node, _depth: int = 0, _max_depth: int = 4, _max_items: int = 40):
                """examples/$schema 등 비용만 큰 키를 제거한 스키마 사본."""
                try:
                    if _depth > _max_depth:
                        return '...'
                    if isinstance(node, list):
                        return [_slim_schema(_x, _depth + 1, _max_depth, _max_items) for _x in node[:3]]
                    if not isinstance(node, dict):
                        return node
                    _drop = {'examples', '$schema', '$id', 'additionalProperties', 'outputSchema', 'title'}
                    out = {}
                    for _i, (_k, _v) in enumerate(node.items()):
                        if _k in _drop:
                            continue
                        if _i >= _max_items:
                            out['_truncated'] = f'+{len(node) - _max_items} more keys'
                            break
                        out[_k] = _slim_schema(_v, _depth + 1, _max_depth, _max_items)
                    return out
                except Exception:
                    return {}

            def _summarize_input_schema(sch, _max_props: int = 30) -> dict:
                """properties → {파라미터명: '타입'} 요약 ('*' = 필수)."""
                try:
                    if not isinstance(sch, dict):
                        return {}
                    # anyOf/oneOf/allOf 병합 — rea는 anyOf[0]에 실스키마를 둔다
                    for _uk in ('anyOf', 'oneOf', 'allOf'):
                        _cand = sch.get(_uk)
                        if isinstance(_cand, list) and _cand and isinstance(_cand[0], dict):
                            sch = _cand[0]
                            break
                    props = sch.get('properties')
                    if not isinstance(props, dict) or not props:
                        return {}
                    _req = set(sch.get('required') or [])
                    out = {}
                    for _i, (_k, _v) in enumerate(props.items()):
                        if _i >= _max_props:
                            out['...'] = f'(+{len(props) - _max_props} more params)'
                            break
                        _t = ''
                        if isinstance(_v, dict):
                            _t = _v.get('type') or ''
                            if not _t:
                                for _uk in ('anyOf', 'oneOf', 'allOf'):
                                    _u = _v.get(_uk)
                                    if isinstance(_u, list) and _u and isinstance(_u[0], dict):
                                        _t = _u[0].get('type') or ''
                                        if _t:
                                            break
                            if not _t and isinstance(_v.get('properties'), dict):
                                _t = 'object'
                        if _t:
                            out[_k] = f"{_t}{'*' if _k in _req else ''}"
                        else:
                            out[_k] = '*' if _k in _req else 'any'
                    return out
                except Exception:
                    return {}

            def _list_mcp_router_handler(args: dict, **kwargs) -> str:
                target_srv = (args.get('server_name') or args.get('server') or '').strip().lower()
                target_tool = (args.get('tool_name') or args.get('tool') or '').strip()
                # ── 단일 도구 상세 조회 (server_name + tool_name) ──
                if target_srv and target_tool:
                    for sid, tool_list in tools_by_server.items():
                        if sid.lower() != target_srv:
                            continue
                        for t in tool_list:
                            if (t.get('name') or '').lower() == target_tool.lower():
                                return json.dumps({
                                    "server": sid,
                                    "tool": t.get('name'),
                                    "description": (t.get('description') or ''),
                                    "parameters": _slim_schema(t.get('inputSchema') or {}),
                                }, ensure_ascii=False, indent=2)
                        break
                    return json.dumps(
                        {"error": f"tool '{target_tool}' not found on server '{target_srv}'"},
                        ensure_ascii=False)
                # ── 목록 모드: 파라미터 요약만 (상세는 tool_name으로) ──
                grouped = {}
                for sid, tool_list in tools_by_server.items():
                    if target_srv and sid.lower() != target_srv:
                        continue
                    grouped[sid] = [
                        {
                            "name": t.get('name'),
                            "description": (t.get('description') or '')[:120],
                            "parameters": _summarize_input_schema(t.get('inputSchema') or {})
                        }
                        for t in tool_list
                    ]
                payload = json.dumps({"servers": grouped}, ensure_ascii=False, indent=2)
                # 하드 상한 — 비정상적으로 큰 스키마가 와도 컨텍스트 폭발 방지.
                # 초과 시 앞부분을 잘라내는 대신 서버 인덱스로 폴백한다
                # (잘라내면 뒤쪽 서버가 통째로 사라져 어떤 서버가 있는지조차 안 보임).
                _LIMIT = 60000
                if len(payload) > _LIMIT:
                    index = {
                        sid: {"tool_count": len(tl), "call": f"list_mcp_tools(server_name='{sid}')"}
                        for sid, tl in grouped.items()
                    }
                    payload = json.dumps({
                        "servers_index": index,
                        "note": (f"full listing would exceed {_LIMIT} chars — showing server index. "
                                 "Call again with server_name for one server's tool list, "
                                 "or server_name + tool_name for a single tool's schema."),
                    }, ensure_ascii=False, indent=2)
                return payload

            registry.register(
                name="list_mcp_tools",
                toolset="mcp-router",
                schema={
                    "name": "list_mcp_tools",
                    "description": "List available tools for active MCP servers",
                    "parameters": list_mcp_schema["function"]["parameters"]
                },
                handler=_list_mcp_router_handler,
                check_fn=lambda: True,
                is_async=False,
                description="List available tools for active MCP servers",
            )

        # ── [Lazy-MCP-DIAG 2026-10-22] agent.tools 실측: 44 -> 295 로 늘어나는 지점 포착용 ──
        try:
            if hasattr(agent, "tools") and isinstance(agent.tools, list):
                _names_all = [t.get("function", {}).get("name", "") for t in agent.tools if isinstance(t, dict)]
                _names_mcp = [n for n in _names_all if n.startswith("mcp_")]
                print(
                    "[Lazy-MCP-DIAG] lazy=%s registered=%d agent.tools=%d mcp_individual=%d"
                    % (use_lazy_mcp, injected_count, len(_names_all), len(_names_mcp)),
                    flush=True,
                )
                if _names_mcp:
                    _by_srv = {}
                    for _n in _names_mcp:
                        _parts = _n.split("_")
                        _key = _parts[1] if len(_parts) > 1 else "?"
                        _by_srv[_key] = _by_srv.get(_key, 0) + 1
                    print("[Lazy-MCP-DIAG] mcp by server: %s" % sorted(_by_srv.items(), key=lambda kv: -kv[1])[:12], flush=True)
        except Exception as _e_diag:
            print("[Lazy-MCP-DIAG] failed: %s" % _e_diag, flush=True)

        if injected_count > 0:
            if use_lazy_mcp:
                _logger.info("Registered %d MCP tools (Lazy MCP Router: call_mcp_tool & list_mcp_tools exposed to LLM).", injected_count)
            else:
                _logger.info("Registered %d MCP tools into Hermes registry + agent.tools (Eager mode).", injected_count)
    except Exception as e:
        _logger.warning("Failed to inject MCP tools: %s", e, exc_info=True)

    # ── [DIAG-TOOLS 2026-10-22] 매 턴 agent.tools 구성 진단 ──
    try:
        _diag_tools = getattr(agent, "tools", None)
        if isinstance(_diag_tools, list):
            _KEEP_DIAG = ("call_mcp_tool", "list_mcp_tools", "mcp_manage")
            _names = [(_t.get("function", {}) or {}).get("name", "") for _t in _diag_tools if isinstance(_t, dict)]
            _mcp_ind = [n for n in _names if n.startswith("mcp_") and n not in _KEEP_DIAG]
            _keep = [n for n in _names if n in _KEEP_DIAG]
            print(
                "[DIAG-TOOLS] lazy=%s registered_mcp=%d agent.tools=%d mcp_individual=%d router=%d"
                % (use_lazy_mcp, injected_count, len(_names), len(_mcp_ind), len(_keep)),
                flush=True,
            )
            if _mcp_ind:
                print("[DIAG-TOOLS] LEAK individual mcp in agent.tools: %s" % ", ".join(_mcp_ind[:15]), flush=True)
    except Exception as _e_diag:
        print("[DIAG-TOOLS] diag failed: %s" % _e_diag, flush=True)

    return injected_count


def inject_mcp_manage_tool(agent: Any) -> None:
    """Inject mcp_manage tool so the agent can connect, disconnect, and list MCP servers dynamically."""
    try:
        from tools.mcp_manager_tool import MCP_MANAGE_SCHEMA, _dispatch, _result
        from tools.registry import registry

        mcp_manage_schema = {
            "type": "function",
            "function": {
                "name": "mcp_manage",
                "description": (
                    "Manage MCP servers at runtime: connect, disconnect, list, add, remove, tools. "
                    "Allows the agent to turn MCP servers ON or OFF as needed (e.g. action='connect', action='disconnect', action='list')."
                ),
                "parameters": MCP_MANAGE_SCHEMA,
            },
        }
        _append_tool_if_missing(agent, mcp_manage_schema, "mcp_manage")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("mcp_manage")

        if "mcp_manage" not in registry._tools:
            registry.register(
                name="mcp_manage",
                toolset="mcp",
                schema=MCP_MANAGE_SCHEMA,
                handler=lambda args, **kw: _result(_dispatch(args)),
                check_fn=lambda: True,
                emoji="🧩",
                description="Manage MCP servers at runtime: connect, disconnect, list, add, remove.",
            )
        _logger.info("Injected mcp_manage tool into agent tools.")
    except Exception as e:
        _logger.warning("mcp_manage injection failed: %s", e)


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
                        },
                        "nodeId": {
                            "type": "integer",
                            "description": "스냅샷에서 확인된 대상 요소의 정수 번호 (node_id의 camelCase 별칭)"
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
            raw_node_id = args.get('node_id') if args.get('node_id') is not None else args.get('nodeId')
            node_id = None
            if raw_node_id is not None:
                try:
                    node_id = int(raw_node_id)
                except (ValueError, TypeError):
                    node_id = None

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
                attr_parts.append(f'nodeId="{node_id}"')
                attr_parts.append(f'node_id="{node_id}"')

            tag = f"<daon_action {' '.join(attr_parts)} />"
            _logger.info("Executed daon_action tool call: %s", tag)
            try:
                from api import browser_ext_bridge as _bx
                _cmd = {"action": action, "target": target, "text": text, "selector": selector,
                        "url": url, "direction": direction, "key": key,
                        "nth": nth, "node_id": node_id, "nodeId": node_id}
                _cid = _bx.enqueue(_cmd)
                _res = _bx.wait_result(_cid, timeout=25.0)
                if _res is not None:
                    _logger.info("daon_action executed via extension: %s", tag)
                    return json.dumps({"ok": True, "status": "executed", "action": action,
                                       "tag": tag, "result": _res}, ensure_ascii=False)
                return json.dumps({"ok": True, "status": "action_queued", "action": action, "tag": tag,
                                   "message": f"'{action}' 동작을 예약했습니다. 크롬에서 DAON 확장 사이드패널이 열려 있는지 확인하세요."},
                                  ensure_ascii=False)
            except Exception as _xe:
                _logger.warning("browser_ext_bridge unavailable, falling back: %s", _xe)
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


def inject_worker_tools(agent: Any) -> None:
    """Inject delegate_to_worker tool for Raon and other agents to utilize external CLI workers."""
    try:
        from api.managers.herdr_manager import herdr_manager
        from tools.registry import registry

        _worker_schema = {
            "type": "function",
            "function": {
                "name": "delegate_to_worker",
                "description": (
                    "외부 전문 코딩 워커(Codex 또는 Claude Code CLI)에게 독립적인 구현, "
                    "코드 작성, 버그 수정, 리팩터링 작업을 위임하고 최종 실행 결과와 코드를 받아옵니다. "
                    "라온(총괄기획)이 복잡한 코딩이나 CLI 기반 자율 에이전트 작업을 위임할 때 사용합니다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "worker_name": {
                            "type": "string",
                            "enum": ["worker-codex", "worker-claude"],
                            "description": "작업을 위임할 외부 워커 이름 (기본값: worker-codex)"
                        },
                        "task": {
                            "type": "string",
                            "description": "워커에게 전달할 구체적인 작업 요구사항 및 프롬프트 (prompt 파라미터와 동일)"
                        },
                        "prompt": {
                            "type": "string",
                            "description": "워커에게 전달할 프롬프트 (task 파라미터와 동일)"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "작업 완료 대기 시간(초) (기본값: 120)"
                        }
                    }
                }
            }
        }

        def _worker_handler(args: dict, **kwargs) -> str:
            worker_name = args.get("worker_name", "worker-codex")
            task = (args.get("task") or args.get("prompt") or args.get("instruction") or args.get("message") or "").strip()
            timeout = int(args.get("timeout") or 120)

            if not task:
                return json.dumps({"ok": False, "error": "task or prompt is required"}, ensure_ascii=False)

            _logger.info(f"[delegate_to_worker] Delegating task to {worker_name}: {task[:60]}...")
            try:
                res = herdr_manager.execute_worker_task(name=worker_name, prompt=task, timeout=timeout)
                return json.dumps(res, ensure_ascii=False)
            except Exception as e:
                _logger.error(f"[delegate_to_worker] Failed to execute worker task: {e}")
                return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

        registry.register(
            name="delegate_to_worker",
            toolset="worker-dispatch",
            schema={
                "name": "delegate_to_worker",
                "description": _worker_schema["function"]["description"],
                "parameters": _worker_schema["function"]["parameters"]
            },
            handler=_worker_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Delegate coding/refactoring tasks to external CLI workers (Codex / Claude Code)"
        )
        registry.register_toolset_alias("worker", "worker-dispatch")
        registry.register_toolset_alias("codex", "worker-dispatch")
        _append_tool_if_missing(agent, _worker_schema, "delegate_to_worker")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("delegate_to_worker")
        _logger.debug("Injected delegate_to_worker tool into agent.")
    except Exception as e:
        _logger.warning("Worker tool injection failed: %s", e)


def inject_colleague_tools(agent: Any, session_id: str = None) -> None:
    """Inject delegate_to_agent tool for Raon and other agents to collaborate with internal colleague agents."""
    try:
        from tools.registry import registry
        from api.collaborator import execute_agent_task

        _agent_schema = {
            "type": "function",
            "function": {
                "name": "delegate_to_agent",
                "description": (
                    "DAON 멀티 에이전트 팀의 전문 동료 에이전트(빌: 백엔드/API개발, 셜록: 코드리뷰/QA/검수, "
                    "토니: 기획/설계, 프라다: UI/UX디자인)에게 구체적인 하위 작업을 위임하고, "
                    "작업이 완료될 때까지 실행한 뒤 동료의 최종 작업 결과와 산출물을 받아옵니다. "
                    "총괄 기획자(라온)가 동료 에이전트들과 협업하여 작업을 분담·완주할 때 사용합니다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "작업을 위임할 동료 에이전트 (예: bill, sherlock, tony, prada, 빌, 셜록, 토니, 프라다)"
                        },
                        "task": {
                            "type": "string",
                            "description": "동료 에이전트에게 지시할 구체적인 작업 요구사항 및 지시사항 (프롬프트)"
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "동료 작업 완료 대기 시간(초) (기본값: 180, 최대: 600)"
                        }
                    },
                    "required": ["agent_name", "task"]
                }
            }
        }

        def _colleague_handler(args: dict, **kwargs) -> str:
            agent_name = (args.get("agent_name") or args.get("name") or "").strip()
            task = (args.get("task") or args.get("prompt") or args.get("instruction") or args.get("message") or "").strip()
            timeout = int(args.get("timeout") or 180)

            if not agent_name or not task:
                return json.dumps({"ok": False, "error": "agent_name and task are required"}, ensure_ascii=False)

            _logger.info(f"[delegate_to_agent] Delegating task to {agent_name}: {task[:60]}...")
            try:
                res = execute_agent_task(
                    agent_name=agent_name,
                    task=task,
                    timeout=timeout,
                    parent_session_id=session_id
                )
                return json.dumps(res, ensure_ascii=False)
            except Exception as e:
                _logger.error(f"[delegate_to_agent] Failed to execute colleague task: {e}")
                return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

        registry.register(
            name="delegate_to_agent",
            toolset="agent-colleague",
            schema={
                "name": "delegate_to_agent",
                "description": _agent_schema["function"]["description"],
                "parameters": _agent_schema["function"]["parameters"]
            },
            handler=_colleague_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Delegate subtasks to internal colleague agents (Bill, Sherlock, Tony, Prada)"
        )
        registry.register_toolset_alias("colleague", "agent-colleague")
        registry.register_toolset_alias("agent", "agent-colleague")
        _append_tool_if_missing(agent, _agent_schema, "delegate_to_agent")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("delegate_to_agent")
        _logger.debug("Injected delegate_to_agent tool into agent.")
    except Exception as e:
        _logger.warning("Colleague tool injection failed: %s", e)


def inject_boardroom_tools(agent: Any, session_id: str = None) -> None:
    """Inject the convene_boardroom tool — 라온이 채팅에서 회의실을 소집한다.

    라온은 회의실에 '참석하지 않는다'. 안건을 정리해 이 도구를 호출하면
    빌·셜록·토니·프라다·다온 + CLI 워커가 1차 토론하고, 라온이 사회자로서
    취합 → 이견 재질의 → 합의까지 진행한 뒤 결론을 돌려준다.
    회의 진행 상황은 회의실 탭이 /api/boardroom/live 폴링으로 지켜본다.
    """
    try:
        from tools.registry import registry

        _schema = {
            "type": "function",
            "function": {
                "name": "convene_boardroom",
                "description": (
                    "회의실을 소집해 전문 에이전트 팀(빌:개발, 셜록:검수, 토니:기획, "
                    "프라다:디자인, 다온:응대, 코덱스/클로드 워커)에게 안건을 토론시킵니다. "
                    "라온은 발언자로 참석하지 않고 사회자·통합자로만 일합니다 — "
                    "1차 토론 → 라온 취합 → 이견이 있으면 해당 에이전트 재질의 → 합의까지 반복한 뒤 "
                    "합의 결론·남은 이견·다음 단계(작업 목록)를 돌려줍니다. "
                    "대표님이 '회의해봐', '토론시켜봐', '의견 모아봐'라고 지시하면 "
                    "대표님 요구를 실행 가능한 안건 문장으로 정리해 이 도구를 호출하세요. "
                    "결과는 반드시 채팅창에서 대표님께 보고하고 승인을 받으세요. "
                    "승인 뒤에 작업장으로 넘어갑니다."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agenda": {
                            "type": "string",
                            "description": (
                                "회의 안건. 대표님의 원문을 그대로 넣지 말고, "
                                "무엇을 결정/설계/검토해야 하는지가 드러나게 정리한 문장."
                            )
                        },
                        "participants": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "참여시킬 에이전트 키 목록 "
                                "(bill, sherlock, tony, prada, daon, worker-codex, worker-claude). "
                                "생략하면 회의실 탭에서 대표님이 체크한 참여 선택을 그대로 따르고, "
                                "저장된 선택이 없을 때만 전체 참여한다. "
                                "대표님이 회의실 탭에서 참여자를 골라 뒀다면 이 인자는 넘기지 마세요."
                            )
                        },
                        "max_rounds": {
                            "type": "integer",
                            "description": "1차 토론 + 재질의 최대 라운드 (기본 3, 최대 3)"
                        }
                    },
                    "required": ["agenda"]
                }
            }
        }

        def _boardroom_handler(args: dict, **kwargs) -> str:
            agenda = (args.get("agenda") or args.get("task") or args.get("topic") or "").strip()
            if not agenda:
                return json.dumps({"ok": False, "error": "agenda is required"}, ensure_ascii=False)

            try:
                from api.routes.boardroom_routes import (
                    run_boardroom, resolve_selected_slots, _format_report,
                    live_reset, live_emit, MAX_ROUNDS, remember_panes,
                    get_saved_slots, get_saved_models,
                )
            except Exception as e:  # noqa: BLE001
                return json.dumps({"ok": False, "error": f"boardroom unavailable: {e}"},
                                  ensure_ascii=False)

            # 참여자 결정:
            #   * participants를 명시하면 그 값을 그대로 존중한다(다른 호출자 보호).
            #   * 생략하면 회의실 탭이 저장해 둔 참여 선택(POST /api/boardroom/selection)을
            #     쓴다. 그래야 채팅에서 소집해도 체크 해제한 에이전트가 빠진다.
            #   * 저장값도 없으면 기존대로 전체 참여(하위호환).
            raw_participants = args.get("participants")
            saved_slots = get_saved_slots() if raw_participants is None else None
            if saved_slots:
                selected = saved_slots
                source = "boardroom_selection"
            else:
                selected = resolve_selected_slots(raw_participants)
                source = "participants" if raw_participants is not None else "all_slots"
            if not selected:
                return json.dumps({"ok": False, "error": "참여 에이전트가 없습니다."},
                                  ensure_ascii=False)

            # 슬롯별 모델도 회의실 탭 선택을 따른다(하드코딩 {} 였던 부분).
            models = get_saved_models()

            try:
                max_rounds = int(args.get("max_rounds") or MAX_ROUNDS)
            except (TypeError, ValueError):
                max_rounds = MAX_ROUNDS

            _logger.info("[convene_boardroom] agenda=%s source=%s participants=%s models=%s",
                         agenda[:60], source, [s[0] for s in selected], models)
            live_reset(agenda)
            try:
                res = run_boardroom(
                    agenda, selected, models=models, emit=live_emit,
                    max_rounds=max(1, min(max_rounds, MAX_ROUNDS)),
                )
            except Exception as e:  # noqa: BLE001
                _logger.exception("[convene_boardroom] run failed")
                return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

            payload = {
                "ok": bool(res.get("ok")),
                "agenda": res.get("agenda"),
                "rounds": res.get("rounds"),
                "consensus": res.get("consensus"),
                "summary": res.get("summary"),
                "points": res.get("points"),
                "disagreements": res.get("disagreements"),
                "next_steps": res.get("next_steps"),
                "report": _format_report(res),
                "workshop_panes": remember_panes(res),
                "instruction": (
                    "위 'report'를 대표님께 채팅창에서 그대로 보고하세요. "
                    "요약을 지어내지 말고 report 내용을 사용하고, "
                    "마지막에 승인을 요청하세요. 승인 전에는 작업장을 열지 마세요. "
                    "대표님이 승인하면 open_workshop 도구를 호출하세요."
                ),
            }
            return json.dumps(payload, ensure_ascii=False)

        def _open_workshop_handler(args: dict, **kwargs) -> str:
            try:
                from api.routes.boardroom_routes import get_last_panes
            except Exception as e:  # noqa: BLE001
                return json.dumps({"ok": False, "error": f"workshop unavailable: {e}"},
                                  ensure_ascii=False)
            info = get_last_panes()
            panes = info.get("panes") or []
            if not panes:
                # 폴백: 회의 기록이 없더라도 작업장 기본 구성(라온, 빌, 프라다, 셜록)으로 열기 지원
                panes = [
                    {"profile": "raon", "sessionId": session_id or "", "model": "", "slot": "01", "speaker": "라온 (사회자·통합)", "kind": "agent"},
                    {"profile": "bill", "sessionId": "", "model": "", "slot": "02", "speaker": "빌 (개발)", "kind": "agent"},
                    {"profile": "prada", "sessionId": "", "model": "", "slot": "05", "speaker": "프라다 (디자인)", "kind": "agent"},
                    {"profile": "sherlock", "sessionId": "", "model": "", "slot": "03", "speaker": "셜록 (검수)", "kind": "agent"},
                ]
            _logger.info("[open_workshop] handing off %d panes for agenda=%s",
                         len(panes), (info.get("agenda") or "일반 작업")[:50])
            return json.dumps({
                "ok": True,
                "panes": panes,
                "agenda": info.get("agenda") or "작업장 가동",
                "rounds": info.get("rounds", 1),
                "next_steps": info.get("next_steps", []),
                "instruction": (
                    "작업장(멀티뷰) 화면을 성공적으로 열고 참여 에이전트를 pane으로 복원했습니다! "
                    "이제 말로만 끝내지 말고, 지금 즉시 delegate_to_agent(agent_name='bill'|'prada'|'sherlock'|'tony', task='...') "
                    "도구를 연속 호출하여 각 에이전트에게 실제 구현 작업을 배분하세요."
                ),
            }, ensure_ascii=False)

        _open_workshop_schema = {
            "type": "function",
            "function": {
                "name": "open_workshop",
                "description": (
                    "회의 결론에 대한 대표님 승인이 떨어진 뒤, 작업장(멀티뷰)을 열고 "
                    "작업을 시작합니다. 회의에 참여했던 에이전트들이 각자 자기 세션 맥락을 "
                    "안고 pane으로 복원되므로, 이어서 결론의 next_steps를 배분하면 됩니다. "
                    "⚠️ 대표님이 '해', '승인', '진행해' 등으로 명확히 승인한 뒤에만 호출하세요. "
                    "승인 전에 호출하면 회의 맥락 없이 빈 작업장이 열립니다."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        }

        registry.register(
            name="convene_boardroom",
            toolset="agent-boardroom",
            schema={
                "name": "convene_boardroom",
                "description": _schema["function"]["description"],
                "parameters": _schema["function"]["parameters"],
            },
            handler=_boardroom_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Convene the boardroom: agents debate, Raon moderates and returns a conclusion",
        )
        registry.register_toolset_alias("boardroom", "agent-boardroom")
        _append_tool_if_missing(agent, _schema, "convene_boardroom")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("convene_boardroom")

        registry.register(
            name="open_workshop",
            toolset="agent-boardroom",
            schema={
                "name": "open_workshop",
                "description": _open_workshop_schema["function"]["description"],
                "parameters": _open_workshop_schema["function"]["parameters"],
            },
            handler=_open_workshop_handler,
            check_fn=lambda: True,
            is_async=False,
            description="Open the multi-view workshop with the last meeting's agent sessions",
        )
        _append_tool_if_missing(agent, _open_workshop_schema, "open_workshop")
        if hasattr(agent, "valid_tool_names") and isinstance(agent.valid_tool_names, set):
            agent.valid_tool_names.add("open_workshop")
        _logger.debug("Injected convene_boardroom + open_workshop tools into agent.")
    except Exception as e:
        _logger.warning("Boardroom tool injection failed: %s", e)


def register_all_streaming_tools(
    agent: Any,
    session: Any,
    session_id: str,
    cancel_event: threading.Event,
    stream_id: str = None,
    user_prompt: str = "",
) -> int:
    """Convenience orchestrator that injects all dynamic streaming tools.

    Returns the number of injected MCP tools.
    """
    mcp_count = inject_mcp_tools(agent, cancel_event, session_id, user_prompt=user_prompt)
    inject_mcp_manage_tool(agent)
    inject_patch_registry_tools(agent)
    inject_memory_forget_tool(agent)
    inject_media_generation_tools(agent)
    inject_self_update_tool(agent, session, session_id)
    inject_self_evolution_tool(agent, session_id=session_id, stream_id=stream_id)
    inject_daon_action_tool(agent)
    inject_fast_decision_tool(agent)
    inject_worker_tools(agent)
    inject_colleague_tools(agent, session_id=session_id)
    inject_boardroom_tools(agent, session_id=session_id)
    _deduplicate_agent_tools(agent)
    return mcp_count
