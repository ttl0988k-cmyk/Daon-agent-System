"""
Node execution with retry/fallback and DAG parallel batch runner.

Provides:
- _build_node_context(): resolves dependency inputs for a node
- _persist_node_result(): records success/failure into results dict and state
- _run_node_with_retries(): executes a single node across fallback model chain
- _handle_timeout_node(): records a timeout failure
- ParallelRunner: orchestrates DAG execution across parallel batches
"""

import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from api.dynamic.state import (
    StreamLogBuffer, NodeMetrics, HermesStateManager, ReviewFailedException
)
from api.dynamic.limits import _load_harness_limits
from api.dynamic.dag_utils import (
    _build_dag_structures, _compute_execution_batches,
    _get_model_chain_for_node, _compress_context, _extract_assistant_content
)
from api.dynamic.logging_utils import get_logger

_log = get_logger(__name__)


def _build_node_context(
    node: dict,
    agent_name: str,
    state_manager: "HermesStateManager",
    parent_list: dict,
    main_task: str,
    run_dir: str = None,
) -> tuple[str, list[str]]:
    """Build the agent query string and resolve parent lineage for a DAG node."""
    input_key = node.get("input") or ""
    context_data = ""
    if input_key:
        best_output = state_manager.get_best(input_key)
        if best_output:
            compressed_output = _compress_context(best_output)
            source_agent = next(
                (c.origin for c in state_manager.store.get(input_key, []) if c.value == best_output),
                "unknown",
            )
            context_data = f"\n--- [Input Context from '{source_agent}' ({input_key})] ---\n{compressed_output}\n"
    node_parents = list(parent_list.get(agent_name, []))
    if input_key:
        for c in state_manager.store.get(input_key, []):
            if c.origin not in node_parents:
                node_parents.append(c.origin)

    import sys as _sys, os as _os
    _platform = _sys.platform
    _is_windows = _platform == "win32"
    _os_name = "Windows" if _is_windows else ("macOS" if _platform == "darwin" else "Linux")

    # Detect actual shell (bash may run on Windows via Git Bash / MSYS2)
    _shell = _os.environ.get('SHELL', '') or _os.environ.get('COMSPEC', '')
    _is_bash = 'bash' in _shell.lower()
    _shell_label = _shell if _shell else ('cmd.exe' if _is_windows else '/bin/bash')

    query = f"We are solving this main task: {main_task}\n"
    query += f"\n[SYSTEM ENVIRONMENT]\n"
    query += f"- Operating System: {_os_name} ({_platform})\n"
    query += f"- Shell: {_shell_label}\n"
    if _is_windows and not _is_bash:
        query += "- Shell WARNING: cmd.exe does NOT support heredoc (<<), sudo, or Unix commands. Use write_file tool instead of cat/echo heredoc. Use PowerShell for advanced scripting.\n"
    elif _is_bash:
        query += "- Bash is available: heredoc (<<), Unix commands (ls, find, grep, cat, etc.) are supported. Use POSIX path style when possible.\n"
    query += "\n"
    if run_dir:
        query += f"\n[CRITICAL DIRECTIVE] Your Current Workspace Directory is: '{run_dir}'. ALL FILE OPERATIONS MUST be done inside this directory. NEVER use relative paths. ALWAYS use absolute paths starting with '{run_dir}'.\n\n"
    query += f"Your specific subtask is: {node['subtask']}\n"
    if context_data:
        query += f"Here is the dependency input data you must utilize:\n{context_data}\n"
    query += "\nPlease work on this subtask and output your final result clearly."
    return query, node_parents


def _persist_node_result(
    agent_name: str,
    node: dict,
    node_parents: list[str],
    success: bool,
    assistant_content: str,
    last_err: Exception | None,
    model_configs: list[dict],
    node_start_time: float,
    attempts_count: int,
    metrics_entry,
    results: dict,
    state_manager: HermesStateManager,
    generation: int,
    mission_tracker: dict,
) -> None:
    """Persist node execution result into results dict and state_manager."""
    if success:
        results[agent_name] = {
            "name": agent_name,
            "role": node["role"],
            "subtask": node["subtask"],
            "status": "success",
            "output": assistant_content,
            "output_key": node["output"],
            "generation": generation,
            "parents": node_parents,
            "model_used": model_configs[0]["model"] if model_configs else "",
            "duration_seconds": time.time() - node_start_time,
        }
        state_manager.add(
            key=node["output"],
            value=assistant_content,
            origin=agent_name,
            generation=generation,
            parents=node_parents,
            status="success",
        )
        _log.info("Node '%s' completed successfully.", agent_name)
    else:
        fail_output = (
            f"Error: Mission timeout exceeded during execution: {last_err}"
            if isinstance(last_err, TimeoutError)
            else f"Error (Failed after retries & fallbacks): {last_err}"
        )
        results[agent_name] = {
            "name": agent_name,
            "role": node["role"],
            "subtask": node["subtask"],
            "status": "failed",
            "output": fail_output,
            "output_key": node["output"],
            "generation": generation,
            "parents": node_parents,
            "model_used": model_configs[0]["model"] if model_configs else "",
            "duration_seconds": time.time() - node_start_time,
        }
        state_manager.add(
            key=node["output"],
            value=f"Error: {last_err}",
            origin=agent_name,
            generation=generation,
            parents=node_parents,
            status="failed",
        )
        _log.info("Node '%s' FAILED permanently.", agent_name)
        metrics_entry = NodeMetrics(
            node_name=agent_name,
            start_time=node_start_time,
            end_time=time.time(),
            model_used=model_configs[0]["model"],
            provider=model_configs[0]["provider"],
            status="failed",
            attempts=attempts_count,
        )
    if mission_tracker and "add_node_metrics" in mission_tracker and metrics_entry:
        mission_tracker["add_node_metrics"](agent_name, metrics_entry)


def _extract_review_score(content: str, limits: dict) -> int:
    """Extract a numeric score (0-100) from reviewer/QA agent output.

    Detection priority:
    1. Explicit score format: `SCORE: 85` or `점수: 85` or `Score: 85/100`
    2. Percentage patterns: `85/100`, `85%`
    3. Six-axis counting: count ✅ / total checks, convert to percentage
    4. Binary fallback: ✅ → 100, ❌ → 0, mixed → 50
    """
    # Priority 1: Explicit score marker
    score_patterns = [
        r'(?:SCORE|점수|Score|score)\s*[:：]\s*(\d{1,3})',
        r'(\d{1,3})\s*/\s*100',
        r'(\d{1,3})\s*점',
    ]
    for pattern in score_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            return min(max(score, 0), 100)

    # Priority 2: Percentage pattern
    pct_match = re.search(r'(\d{1,3})\s*%', content)
    if pct_match:
        score = int(pct_match.group(1))
        return min(max(score, 0), 100)

    # Priority 3: Count pass/fail markers for weighted scoring
    pass_count = len(re.findall(r'✅|\[PASS\]|\[통과\]|pass', content, re.IGNORECASE))
    fail_count = len(re.findall(r'❌|⛔|\[FAIL\]|\[실패\]|fail(?:ed)?', content, re.IGNORECASE))
    total_checks = pass_count + fail_count

    if total_checks > 0:
        score = int((pass_count / total_checks) * 100)
        return score

    # Priority 4: Binary fallback
    if re.search(r'✅|\[PASS\]|\[통과\]|all pass|모두 통과', content, re.IGNORECASE):
        return 100
    if re.search(r'❌|⛔|\[FAIL\]|\[실패\]|reject', content, re.IGNORECASE):
        return 0

    return limits.get("scoring", {}).get("pass_threshold", 70)


def _run_node_with_retries(
    agent_name: str,
    node: dict,
    agent_query: str,
    node_parents: list[str],
    model_configs: list[dict],
    limits: dict,
    generation: int,
    state_manager: HermesStateManager,
    mission_tracker: dict,
    results: dict,
    agent_class,
    log_callback=None,
) -> dict:
    """Execute a DAG node across a fallback model chain with per-attempt retry logic."""
    max_retries = limits["node"]["max_retries"]
    success, last_err, assistant_content = False, None, ""
    metrics_entry, attempts_count = None, 0
    node_start_time = time.time()

    for cfg in model_configs:
        model_name = cfg["model"]
        provider = cfg["provider"]
        api_key = cfg["api_key"]
        base_url = cfg["base_url"]
        for attempt in range(max_retries):
            attempts_count += 1
            if mission_tracker and "check_timeout" in mission_tracker:
                try:
                    mission_tracker["check_timeout"]()
                except TimeoutError as te:
                    last_err = te
                    break
            try:
                agent = agent_class(
                    model=model_name,
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    enabled_toolsets=node["tools"] or None,
                    quiet_mode=True,
                )
                agent.is_dynamic_runner = True
                
                # === [NEW] Inject MCP tools into the Hermes Registry (Dynamic Hermes nodes) ===
                # registry는 싱글톤이므로 최초 1회만 등록되지만, Dynamic Hermes가 먼저
                # 실행되는 경우를 대비해 여기에도 동일한 주입 로직을 둔다.
                try:
                    from tools.registry import registry as _mcp_registry
                    from api.mcp_client import get_mcp_manager
                    import re as _mcp_re
                    
                    _mcp_mgr = get_mcp_manager()
                    _mcp_tools = _mcp_mgr.get_all_tools()
                    
                    def _safe_name(raw: str) -> str:
                        return _mcp_re.sub(r'[^A-Za-z0-9_]', '_', str(raw or ''))
                    
                    def _normalize_input_schema(schema):
                        if not schema:
                            return {"type": "object", "properties": {}}
                        if schema.get("type") == "object" and "properties" not in schema:
                            return {**schema, "properties": {}}
                        return schema
                    
                    _injected_count = 0
                    for _t in _mcp_tools:
                        _sid = _t.get('_mcp_server', 'unknown')
                        _oname = _t.get('name', '')
                        _safe_srv = _safe_name(_sid)
                        _safe_tool = _safe_name(_oname)
                        _mcp_fn = f"mcp_{_safe_srv}_{_safe_tool}"
                        _ts_name = f"mcp-{_safe_srv}"
                        
                        # Skip if already in agent.tools (prevent duplicate per-node)
                        _existing_names = {t.get("function", {}).get("name", "") for t in agent.tools}
                        if _mcp_fn in _existing_names:
                            continue
                        
                        # OpenAI-format schema for agent.tools
                        _api_schema = {
                            "type": "function",
                            "function": {
                                "name": _mcp_fn,
                                "description": _t.get('description', f"MCP tool {_oname} from {_sid}"),
                                "parameters": _t.get('inputSchema', {"type": "object", "properties": {}})
                            }
                        }
                        agent.tools.append(_api_schema)
                        agent.valid_tool_names.add(_mcp_fn)
                        _injected_count += 1
                        
                        # Register in global registry if not already there (first node only)
                        _existing_reg = [n for n in _mcp_registry.get_all_tool_names() if n == _mcp_fn]
                        if not _existing_reg:
                            # Registry schema
                            _reg_schema = {
                                "name": _mcp_fn,
                                "description": _api_schema["function"]["description"],
                                "parameters": _normalize_input_schema(_t.get('inputSchema')),
                            }
                            
                            def _make_handler(sid, tname):
                                def _handler(args: dict, **kwargs) -> str:
                                    import json as _json
                                    result = _mcp_mgr.call_tool(sid, tname, args)
                                    if result.get('ok'):
                                        payload = result.get('result', 'Success')
                                        if isinstance(payload, str):
                                            return _json.dumps({"result": payload}, ensure_ascii=False)
                                        return _json.dumps({"result": _json.dumps(payload, ensure_ascii=False, default=str)}, ensure_ascii=False)
                                    else:
                                        return _json.dumps({"error": result.get('error', 'Unknown error')}, ensure_ascii=False)
                                return _handler
                            
                            def _make_check_fn(sid):
                                def _check() -> bool:
                                    conn = _mcp_mgr._connections.get(sid)
                                    return conn is not None and conn.connected
                                return _check
                            
                            _mcp_registry.register(
                                name=_mcp_fn,
                                toolset=_ts_name,
                                schema=_reg_schema,
                                handler=_make_handler(_sid, _oname),
                                check_fn=_make_check_fn(_sid),
                                is_async=False,
                                description=_reg_schema["description"],
                            )
                            
                            _alias = _ts_name.replace("mcp-", "", 1)
                            _mcp_registry.register_toolset_alias(_alias, _ts_name)
                    
                    if _injected_count > 0:
                        _log.info("[DynamicHermes] Injected %d MCP tools for node '%s'.", _injected_count, agent_name)
                except Exception as _mcp_e:
                    _log.debug("[DynamicHermes] MCP injection skipped for node '%s': %s", agent_name, _mcp_e)
                # ========================================================
                
                buffer = StreamLogBuffer(f"{agent_name} ({model_name})", log_callback)
                def stream_cb(chunk):
                    buffer.write(chunk)

                res = agent.run_conversation(
                    user_message=agent_query,
                    system_message=node["system_prompt"],
                    stream_callback=stream_cb
                )
                buffer.flush()
                if res.get("failed"):
                    raise RuntimeError(res.get("error"))
                if res.get("id") == "partial-stream-stub":
                    raise RuntimeError("Stream dropped mid-generation (partial stub returned). Triggering fallback.")
                assistant_content = _extract_assistant_content(res.get("messages", []))

                if log_callback:
                    log_callback(f"{agent_name} ({model_name})", "\n", "done")
                if not assistant_content.strip():
                    raise RuntimeError("Agent returned empty assistant content.")

                # [NEW] Semantic QA Feedback Loop with Scoring System
                role_str = str(node.get("role", "")).lower()
                if "qa" in role_str or "review" in role_str or "검수" in role_str:
                    score = _extract_review_score(assistant_content, limits)
                    pass_threshold = limits.get("scoring", {}).get("pass_threshold", 70)
                    if score < pass_threshold:
                        raise ReviewFailedException(
                            f"Reviewer score {score}/100 below threshold {pass_threshold}. "
                            f"Feedback:\n{assistant_content[:1500]}"
                        )
                    _log.info("Node '%s' QA score: %d/100 (threshold: %d) ✅", agent_name, score, pass_threshold)

                success = True
                metrics_entry = NodeMetrics(
                    node_name=agent_name,
                    start_time=node_start_time,
                    end_time=time.time(),
                    model_used=model_name,
                    provider=provider,
                    status="success",
                    attempts=attempts_count,
                    input_tokens=res.get("input_tokens", 0),
                    output_tokens=res.get("output_tokens", 0),
                    cache_read_tokens=res.get("cache_read_tokens", 0),
                    cache_write_tokens=res.get("cache_write_tokens", 0),
                    reasoning_tokens=res.get("reasoning_tokens", 0),
                )
                break
            except Exception as e:
                last_err = e
                _log.info(
                    "Node '%s' failed with '%s' (Attempt %d/%d): %s",
                    agent_name, model_name, attempt + 1, max_retries, e,
                )
                if attempt < max_retries - 1:
                    sleep_sec = (2 ** attempt) * 3 + random.uniform(0.5, 2.0)
                    _log.info("Retrying in %.2f seconds...", sleep_sec)
                    time.sleep(sleep_sec)
        if success or isinstance(last_err, TimeoutError) or isinstance(last_err, ReviewFailedException):
            break

    _persist_node_result(
        agent_name,
        node,
        node_parents,
        success,
        assistant_content,
        last_err,
        model_configs,
        node_start_time,
        attempts_count,
        metrics_entry,
        results,
        state_manager,
        generation,
        mission_tracker,
    )
    return results[agent_name]


def _handle_timeout_node(
    node_name: str,
    agent_by_name: dict,
    node_timeout: float,
    generation: int,
    parent_list: dict,
    state_manager: HermesStateManager,
    results: dict,
    mission_tracker: dict,
) -> None:
    """Record a timeout failure for a node that exceeded its wall-time limit."""
    _log.info("Node '%s' wall-time limit exceeded (%ss).", node_name, node_timeout)
    node = agent_by_name.get(node_name)
    node_parents = list(parent_list.get(node_name, []))
    results[node_name] = {
        "name": node_name,
        "role": node["role"],
        "subtask": node["subtask"],
        "status": "failed",
        "output": f"Error: Node execution wall-time limit exceeded ({node_timeout}s)",
        "output_key": node["output"],
        "generation": generation,
        "parents": node_parents,
    }
    state_manager.add(
        key=node["output"],
        value="Error: Timeout",
        origin=node_name,
        generation=generation,
        parents=node_parents,
        status="failed",
    )
    metrics_entry = NodeMetrics(
        node_name=node_name,
        start_time=time.time() - node_timeout,
        end_time=time.time(),
        model_used=node.get("model") or "unknown",
        provider="unknown",
        status="timeout",
        attempts=1,
    )
    if mission_tracker and "add_node_metrics" in mission_tracker:
        mission_tracker["add_node_metrics"](node_name, metrics_entry)


class ParallelRunner:
    """Execute DAG nodes in parallel batches via topological sort."""

    @staticmethod
    def run(
        agents: list[dict],
        edges: list[list[str]],
        main_task: str,
        initial_outputs: list[dict] = None,
        state_manager: "HermesStateManager" = None,
        generation: int = 0,
        mission_tracker: dict = None,
        log_callback=None,
        run_dir: str = None,
        session_id: str = None,
        run_id: str = None,
    ) -> list[dict]:
        """Execute DAG nodes in parallel batches via topological sort. Resolves data flows and failures."""
        agent_path = str(Path(__file__).resolve().parent.parent.parent / "hermes-agent")
        if agent_path not in sys.path:
            sys.path.append(agent_path)
        from run_agent import AIAgent

        limits = _load_harness_limits()
        node_timeout = limits["node"]["max_wall_time_seconds"]
        results: dict = {}
        agent_by_name = {a["name"]: a for a in agents}

        if state_manager is None:
            state_manager = HermesStateManager()
        if initial_outputs:
            for out in initial_outputs:
                state_manager.add(
                    key=out["output_key"],
                    value=out["content"],
                    origin=out["name"],
                    generation=out.get("generation", 0),
                    parents=out.get("parents", []),
                    status="success",
                )

        in_degree, adj_list, parent_list = _build_dag_structures(agents, edges)
        batches = _compute_execution_batches(in_degree, adj_list)
        _log.info("Computed Execution Batches: %s", batches)
        from api.dynamic.model_selector import get_allowed_providers, set_allowed_providers
        current_allowed = get_allowed_providers()

        def _run_single_node(agent_name: str) -> dict:
            """Thin closure: resolve context then delegate to module-level runner."""
            set_allowed_providers(current_allowed)
            if session_id:
                try:
                    from tools.approval import set_current_session_key
                    set_current_session_key(session_id)
                except ImportError:
                    pass
            if mission_tracker and "check_timeout" in mission_tracker:
                mission_tracker["check_timeout"]()
            node = agent_by_name.get(agent_name)
            if not node:
                return {"status": "failed", "error": "Node definition not found"}
            _log.info("Executing Node: %s (Type: %s, Role: %s)", agent_name, node['type'], node['role'])
            agent_query, node_parents = _build_node_context(node, agent_name, state_manager, parent_list, main_task, run_dir)
            _node_role = node.get("role", "")
            _node_type = node.get("type", "llm")
            _node_strength = "code"
            _node_ctx = 32000
            try:
                from api.dynamic.model_selector import DynamicModelSelector
                _node_strength = DynamicModelSelector.infer_strength_from_role(_node_role, _node_type)
                _node_ctx = DynamicModelSelector.estimate_context_tokens(node.get("subtask", main_task), _node_role)
            except Exception as e:
                _log.warning("Failed to infer model selection metadata: %s", e)
            model_configs = _get_model_chain_for_node(
                node.get("model") or "",
                role=_node_role,
                task=node.get("subtask") or main_task,
                required_strength=_node_strength,
                required_context=_node_ctx,
            )
            return _run_node_with_retries(
                agent_name,
                node,
                agent_query,
                node_parents,
                model_configs,
                limits,
                generation,
                state_manager,
                mission_tracker,
                results,
                AIAgent,
                log_callback,
            )

        max_workers = max(len(b) for b in batches) if batches else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for batch in batches:
                if run_id:
                    import api.dynamic_jobs as dj
                    if dj.is_job_cancelled(run_id):
                        _log.warning("Harness execution cancelled by user!")
                        raise Exception("Harness execution cancelled by user")

                if mission_tracker and "check_timeout" in mission_tracker:
                    mission_tracker["check_timeout"]()
                _log.info("Running Batch in Parallel: %s", batch)
                futures = {executor.submit(_run_single_node, name): name for name in batch}
                for future in futures:
                    node_name = futures[future]
                    try:
                        future.result(timeout=node_timeout)
                    except TimeoutError:
                        _handle_timeout_node(
                            node_name,
                            agent_by_name,
                            node_timeout,
                            generation,
                            parent_list,
                            state_manager,
                            results,
                            mission_tracker,
                        )
                    except Exception as e:
                        _log.info("Node '%s' raised execution exception: %s", node_name, e)

        return [results[name] for batch in batches for name in batch if name in results]
