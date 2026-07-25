# agent/tool_executor.py
#
# Tool execution — extracted from AIAgent in run_agent.py.
# All functions take an ``agent`` parameter (the AIAgent instance) instead of ``self``.

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Top-level dispatch ───────────────────────────────────────────────────────


def execute_tool_calls(
    agent: Any,
    assistant_message: Any,
    messages: list,
    effective_task_id: str,
    api_call_count: int = 0,
) -> None:
    """Execute tool calls from the assistant message and append results to messages.

    Dispatches to concurrent execution only for batches that look
    independent.
    """
    tool_calls = assistant_message.tool_calls
    agent._executing_tools = True
    try:
        from run_agent import _should_parallelize_tool_batch
        if not _should_parallelize_tool_batch(tool_calls):
            return execute_tool_calls_sequential(
                agent, assistant_message, messages, effective_task_id, api_call_count,
            )
        return execute_tool_calls_concurrent(
            agent, assistant_message, messages, effective_task_id, api_call_count,
        )
    finally:
        agent._executing_tools = False


# ── Single tool invocation ───────────────────────────────────────────────────


def invoke_tool(
    agent: Any,
    function_name: str,
    function_args: dict,
    effective_task_id: str,
    tool_call_id: Optional[str] = None,
) -> str:
    """Invoke a single tool and return the result string. No display logic."""
    block_message: Optional[str] = None
    try:
        from hermes_cli.plugins import get_pre_tool_call_block_message
        block_message = get_pre_tool_call_block_message(
            function_name, function_args, task_id=effective_task_id or "",
        )
    except Exception:
        pass
    if block_message is not None:
        return json.dumps({"error": block_message}, ensure_ascii=False)

    if function_name == "todo":
        from tools.todo_tool import todo_tool as _todo_tool
        return _todo_tool(
            todos=function_args.get("todos"),
            merge=function_args.get("merge", False),
            store=agent._todo_store,
        )
    elif function_name == "session_search":
        if not agent._session_db:
            return json.dumps({"success": False, "error": "Session database not available."})
        from tools.session_search_tool import session_search as _session_search
        return _session_search(
            query=function_args.get("query", ""),
            role_filter=function_args.get("role_filter"),
            limit=function_args.get("limit", 3),
            db=agent._session_db,
            current_session_id=agent.session_id,
        )
    elif function_name == "memory":
        target = function_args.get("target", "memory")
        from tools.memory_tool import memory_tool as _memory_tool
        result = _memory_tool(
            action=function_args.get("action"),
            target=target,
            content=function_args.get("content"),
            old_text=function_args.get("old_text"),
            store=agent._memory_store,
        )
        if agent._memory_manager and function_args.get("action") in ("add", "replace"):
            try:
                agent._memory_manager.on_memory_write(
                    function_args.get("action", ""),
                    target,
                    function_args.get("content", ""),
                )
            except Exception:
                pass
        return result
    elif agent._memory_manager and agent._memory_manager.has_tool(function_name):
        return agent._memory_manager.handle_tool_call(function_name, function_args)
    elif function_name == "clarify":
        from tools.clarify_tool import clarify_tool as _clarify_tool
        return _clarify_tool(
            question=function_args.get("question", ""),
            choices=function_args.get("choices"),
            callback=agent.clarify_callback,
        )
    elif function_name == "delegate_task":
        from tools.delegate_tool import delegate_task as _delegate_task
        return _delegate_task(
            goal=function_args.get("goal"),
            context=function_args.get("context"),
            toolsets=function_args.get("toolsets"),
            tasks=function_args.get("tasks"),
            max_iterations=function_args.get("max_iterations"),
            parent_agent=agent,
        )
    elif function_name == "terminal":
        # Inject stream_callback so terminal output streams in real time
        # via the agent's tool_progress_callback (if set).
        from tools.terminal_tool import terminal_tool as _terminal_tool
        _stream_cb = None
        if agent.tool_progress_callback:
            def _terminal_stream(text: str) -> None:
                try:
                    agent.tool_progress_callback("tool.output", function_name, text, None)
                except Exception:
                    pass
            _stream_cb = _terminal_stream
        return _terminal_tool(**function_args, stream_callback=_stream_cb)
    else:
        from run_agent import handle_function_call
        return handle_function_call(
            function_name, function_args, effective_task_id,
            tool_call_id=tool_call_id,
            session_id=agent.session_id or "",
            enabled_tools=list(agent.valid_tool_names) if agent.valid_tool_names else None,
            skip_pre_tool_call_hook=True,
        )


# ── Verbose wrapping ─────────────────────────────────────────────────────────


def wrap_verbose(agent: Any, label: str, text: str, indent: str = "     ") -> str:
    """Word-wrap verbose tool output to fit the terminal width."""
    import shutil as _shutil
    import textwrap as _tw
    cols = _shutil.get_terminal_size((120, 24)).columns
    wrap_width = max(40, cols - len(indent))
    out_lines: list[str] = []
    for raw_line in text.split("\n"):
        if len(raw_line) <= wrap_width:
            out_lines.append(raw_line)
        else:
            wrapped = _tw.wrap(raw_line, width=wrap_width,
                               break_long_words=True,
                               break_on_hyphens=False)
            out_lines.extend(wrapped or [raw_line])
    body = ("\n" + indent).join(out_lines)
    return f"{indent}{label}{body}"


# ── Concurrent execution ─────────────────────────────────────────────────────


def execute_tool_calls_concurrent(
    agent: Any,
    assistant_message: Any,
    messages: list,
    effective_task_id: str,
    api_call_count: int = 0,
) -> None:
    """Execute multiple tool calls concurrently using a thread pool."""
    from run_agent import (
        _build_tool_preview, _get_cute_tool_message_impl, _detect_tool_failure,
        _is_destructive_command, _MAX_TOOL_WORKERS, KawaiiSpinner,
        maybe_persist_tool_result, get_active_env, enforce_turn_budget,
    )

    tool_calls = assistant_message.tool_calls
    num_tools = len(tool_calls)

    if agent._interrupt_requested:
        print(f"{agent.log_prefix}⚡ Interrupt: skipping {num_tools} tool call(s)")
        for tc in tool_calls:
            messages.append({
                "role": "tool",
                "content": f"[Tool execution cancelled — {tc.function.name} was skipped due to user interrupt]",
                "tool_call_id": tc.id,
            })
        return

    parsed_calls = []
    for tool_call in tool_calls:
        function_name = tool_call.function.name

        if function_name == "memory":
            agent._turns_since_memory = 0
        elif function_name == "skill_manage":
            agent._iters_since_skill = 0

        try:
            function_args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            function_args = {}
        if not isinstance(function_args, dict):
            function_args = {}

        if function_name in ("write_file", "patch") and agent._checkpoint_mgr.enabled:
            try:
                file_path = function_args.get("path", "")
                if file_path:
                    work_dir = agent._checkpoint_mgr.get_working_dir_for_path(file_path)
                    agent._checkpoint_mgr.ensure_checkpoint(work_dir, f"before {function_name}")
            except Exception:
                pass

        if function_name == "terminal" and agent._checkpoint_mgr.enabled:
            try:
                cmd = function_args.get("command", "")
                if _is_destructive_command(cmd):
                    cwd = function_args.get("workdir") or os.getenv("TERMINAL_CWD", os.getcwd())
                    agent._checkpoint_mgr.ensure_checkpoint(
                        cwd, f"before terminal: {cmd[:60]}"
                    )
            except Exception:
                pass

        parsed_calls.append((tool_call, function_name, function_args))

    tool_names_str = ", ".join(name for _, name, _ in parsed_calls)
    if not agent.quiet_mode:
        print(f"  ⚡ Concurrent: {num_tools} tool calls — {tool_names_str}")
        for i, (tc, name, args) in enumerate(parsed_calls, 1):
            args_str = json.dumps(args, ensure_ascii=False)
            if agent.verbose_logging:
                print(f"  📞 Tool {i}: {name}({list(args.keys())})")
                print(wrap_verbose(agent, "Args: ", json.dumps(args, indent=2, ensure_ascii=False)))
            else:
                args_preview = args_str[:agent.log_prefix_chars] + "..." if len(args_str) > agent.log_prefix_chars else args_str
                print(f"  📞 Tool {i}: {name}({list(args.keys())}) - {args_preview}")

    for tc, name, args in parsed_calls:
        if agent.tool_progress_callback:
            try:
                preview = _build_tool_preview(name, args)
                agent.tool_progress_callback("tool.started", name, preview, args)
            except Exception as cb_err:
                logging.debug(f"Tool progress callback error: {cb_err}")

    for tc, name, args in parsed_calls:
        if agent.tool_start_callback:
            try:
                agent.tool_start_callback(tc.id, name, args)
            except Exception as cb_err:
                logging.debug(f"Tool start callback error: {cb_err}")

    results = [None] * num_tools
    agent._current_tool = tool_names_str
    agent._touch_activity(f"executing {num_tools} tools concurrently: {tool_names_str}")

    def _run_tool(index, tool_call, function_name, function_args):
        _worker_tid = threading.current_thread().ident
        with agent._tool_worker_threads_lock:
            agent._tool_worker_threads.add(_worker_tid)
        if agent._interrupt_requested:
            try:
                from tools.interrupt import set_interrupt as _sif
                _sif(True, _worker_tid)
            except Exception:
                pass
        try:
            from tools.environments.base import set_activity_callback
            set_activity_callback(agent._touch_activity)
        except Exception:
            pass
        start = time.time()
        try:
            result = invoke_tool(agent, function_name, function_args, effective_task_id, tool_call.id)
        except Exception as tool_error:
            result = f"Error executing tool '{function_name}': {tool_error}"
            logger.error("_invoke_tool raised for %s: %s", function_name, tool_error, exc_info=True)
        duration = time.time() - start
        is_error, _ = _detect_tool_failure(function_name, result)
        if is_error:
            logger.info("tool %s failed (%.2fs): %s", function_name, duration, result[:200])
        else:
            logger.info("tool %s completed (%.2fs, %d chars)", function_name, duration, len(result))
        results[index] = (function_name, function_args, result, duration, is_error)
        with agent._tool_worker_threads_lock:
            agent._tool_worker_threads.discard(_worker_tid)
        try:
            from tools.interrupt import set_interrupt as _sif
            _sif(False, _worker_tid)
        except Exception:
            pass

    spinner = None
    if agent._should_emit_quiet_tool_messages() and agent._should_start_quiet_spinner():
        face = random.choice(KawaiiSpinner.get_waiting_faces())
        spinner = KawaiiSpinner(f"{face} ⚡ running {num_tools} tools concurrently", spinner_type='dots', print_fn=agent._print_fn)
        spinner.start()

    try:
        max_workers = min(num_tools, _MAX_TOOL_WORKERS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for i, (tc, name, args) in enumerate(parsed_calls):
                f = executor.submit(_run_tool, i, tc, name, args)
                futures.append(f)

            _conc_start = time.time()
            _interrupt_logged = False
            while True:
                done, not_done = concurrent.futures.wait(
                    futures, timeout=5.0,
                )
                if not not_done:
                    break

                if agent._interrupt_requested:
                    if not _interrupt_logged:
                        _interrupt_logged = True
                        agent._vprint(
                            f"{agent.log_prefix}⚡ Interrupt: cancelling "
                            f"{len(not_done)} pending concurrent tool(s)",
                            force=True,
                        )
                    for f in not_done:
                        f.cancel()
                    concurrent.futures.wait(not_done, timeout=3.0)
                    break

                _conc_elapsed = int(time.time() - _conc_start)
                if _conc_elapsed > 0 and _conc_elapsed % 30 < 6:
                    _still_running = [
                        parsed_calls[futures.index(f)][1]
                        for f in not_done
                        if f in futures
                    ]
                    agent._touch_activity(
                        f"concurrent tools running ({_conc_elapsed}s, "
                        f"{len(not_done)} remaining: {', '.join(_still_running[:3])})"
                    )
    finally:
        if spinner:
            completed = sum(1 for r in results if r is not None)
            total_dur = sum(r[3] for r in results if r is not None)
            spinner.stop(f"⚡ {completed}/{num_tools} tools completed in {total_dur:.1f}s total")

    for i, (tc, name, args) in enumerate(parsed_calls):
        r = results[i]
        if r is None:
            if agent._interrupt_requested:
                function_result = f"[Tool execution cancelled — {name} was skipped due to user interrupt]"
            else:
                function_result = f"Error executing tool '{name}': thread did not return a result"
            tool_duration = 0.0
        else:
            function_name, function_args, function_result, tool_duration, is_error = r

            if is_error:
                result_preview = function_result[:200] if len(function_result) > 200 else function_result
                logger.warning("Tool %s returned error (%.2fs): %s", function_name, tool_duration, result_preview)

            if agent.tool_progress_callback:
                try:
                    agent.tool_progress_callback(
                        "tool.completed", function_name, None, None,
                        duration=tool_duration, is_error=is_error,
                    )
                except Exception as cb_err:
                    logging.debug(f"Tool progress callback error: {cb_err}")

            if agent.verbose_logging:
                logging.debug(f"Tool {function_name} completed in {tool_duration:.2f}s")
                logging.debug(f"Tool result ({len(function_result)} chars): {function_result}")

        if agent._should_emit_quiet_tool_messages():
            cute_msg = _get_cute_tool_message_impl(name, args, tool_duration, result=function_result)
            agent._safe_print(f"  {cute_msg}")
        elif not agent.quiet_mode:
            if agent.verbose_logging:
                print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s")
                print(wrap_verbose(agent, "Result: ", function_result))
            else:
                response_preview = function_result[:agent.log_prefix_chars] + "..." if len(function_result) > agent.log_prefix_chars else function_result
                print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s - {response_preview}")

        agent._current_tool = None
        agent._touch_activity(f"tool completed: {name} ({tool_duration:.1f}s)")

        if agent.tool_complete_callback:
            try:
                agent.tool_complete_callback(tc.id, name, args, function_result)
            except Exception as cb_err:
                logging.debug(f"Tool complete callback error: {cb_err}")

        function_result = maybe_persist_tool_result(
            content=function_result,
            tool_name=name,
            tool_use_id=tc.id,
            env=get_active_env(effective_task_id),
        )

        subdir_hints = agent._subdirectory_hints.check_tool_call(name, args)
        if subdir_hints:
            function_result += subdir_hints

        tool_msg = {
            "role": "tool",
            "content": function_result,
            "tool_call_id": tc.id,
        }
        messages.append(tool_msg)

        agent._apply_pending_steer_to_tool_results(messages, 1)

    num_tools = len(parsed_calls)
    if num_tools > 0:
        turn_tool_msgs = messages[-num_tools:]
        enforce_turn_budget(turn_tool_msgs, env=get_active_env(effective_task_id))

    if num_tools > 0:
        agent._apply_pending_steer_to_tool_results(messages, num_tools)


# ── Sequential execution ─────────────────────────────────────────────────────


def execute_tool_calls_sequential(
    agent: Any,
    assistant_message: Any,
    messages: list,
    effective_task_id: str,
    api_call_count: int = 0,
) -> None:
    """Execute tool calls sequentially (original behavior)."""
    from run_agent import (
        _build_tool_preview, _get_cute_tool_message_impl, _detect_tool_failure,
        _is_destructive_command, KawaiiSpinner, _get_tool_emoji,
        maybe_persist_tool_result, get_active_env, enforce_turn_budget,
        handle_function_call,
    )

    for i, tool_call in enumerate(assistant_message.tool_calls, 1):
        if agent._interrupt_requested:
            remaining_calls = assistant_message.tool_calls[i-1:]
            if remaining_calls:
                agent._vprint(f"{agent.log_prefix}⚡ Interrupt: skipping {len(remaining_calls)} tool call(s)", force=True)
            for skipped_tc in remaining_calls:
                skipped_name = skipped_tc.function.name
                skip_msg = {
                    "role": "tool",
                    "content": f"[Tool execution cancelled — {skipped_name} was skipped due to user interrupt]",
                    "tool_call_id": skipped_tc.id,
                }
                messages.append(skip_msg)
            break

        function_name = tool_call.function.name

        try:
            function_args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            logging.warning(f"Unexpected JSON error after validation: {e}")
            function_args = {}
        if not isinstance(function_args, dict):
            function_args = {}

        _block_msg: Optional[str] = None
        try:
            from hermes_cli.plugins import get_pre_tool_call_block_message
            _block_msg = get_pre_tool_call_block_message(
                function_name, function_args, task_id=effective_task_id or "",
            )
        except Exception:
            pass

        if _block_msg is not None:
            pass
        else:
            if function_name == "memory":
                agent._turns_since_memory = 0
            elif function_name == "skill_manage":
                agent._iters_since_skill = 0

        if not agent.quiet_mode:
            args_str = json.dumps(function_args, ensure_ascii=False)
            if agent.verbose_logging:
                print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())})")
                print(wrap_verbose(agent, "Args: ", json.dumps(function_args, indent=2, ensure_ascii=False)))
            else:
                args_preview = args_str[:agent.log_prefix_chars] + "..." if len(args_str) > agent.log_prefix_chars else args_str
                print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())}) - {args_preview}")

        if _block_msg is None:
            agent._current_tool = function_name
            agent._touch_activity(f"executing tool: {function_name}")

        if _block_msg is None:
            try:
                from tools.environments.base import set_activity_callback
                set_activity_callback(agent._touch_activity)
            except Exception:
                pass

        if _block_msg is None and agent.tool_progress_callback:
            try:
                preview = _build_tool_preview(function_name, function_args)
                agent.tool_progress_callback("tool.started", function_name, preview, function_args)
            except Exception as cb_err:
                logging.debug(f"Tool progress callback error: {cb_err}")

        if _block_msg is None and agent.tool_start_callback:
            try:
                agent.tool_start_callback(tool_call.id, function_name, function_args)
            except Exception as cb_err:
                logging.debug(f"Tool start callback error: {cb_err}")

        if _block_msg is None and function_name in ("write_file", "patch") and agent._checkpoint_mgr.enabled:
            try:
                file_path = function_args.get("path", "")
                if file_path:
                    work_dir = agent._checkpoint_mgr.get_working_dir_for_path(file_path)
                    agent._checkpoint_mgr.ensure_checkpoint(
                        work_dir, f"before {function_name}"
                    )
            except Exception:
                pass

        if _block_msg is None and function_name == "terminal" and agent._checkpoint_mgr.enabled:
            try:
                cmd = function_args.get("command", "")
                if _is_destructive_command(cmd):
                    cwd = function_args.get("workdir") or os.getenv("TERMINAL_CWD", os.getcwd())
                    agent._checkpoint_mgr.ensure_checkpoint(
                        cwd, f"before terminal: {cmd[:60]}"
                    )
            except Exception:
                pass

        tool_start_time = time.time()

        if _block_msg is not None:
            function_result = json.dumps({"error": _block_msg}, ensure_ascii=False)
            tool_duration = 0.0
        elif function_name == "todo":
            from tools.todo_tool import todo_tool as _todo_tool
            function_result = _todo_tool(
                todos=function_args.get("todos"),
                merge=function_args.get("merge", False),
                store=agent._todo_store,
            )
            tool_duration = time.time() - tool_start_time
            if agent._should_emit_quiet_tool_messages():
                agent._vprint(f"  {_get_cute_tool_message_impl('todo', function_args, tool_duration, result=function_result)}")
        elif function_name == "session_search":
            if not agent._session_db:
                function_result = json.dumps({"success": False, "error": "Session database not available."})
            else:
                from tools.session_search_tool import session_search as _session_search
                function_result = _session_search(
                    query=function_args.get("query", ""),
                    role_filter=function_args.get("role_filter"),
                    limit=function_args.get("limit", 3),
                    db=agent._session_db,
                    current_session_id=agent.session_id,
                )
            tool_duration = time.time() - tool_start_time
            if agent._should_emit_quiet_tool_messages():
                agent._vprint(f"  {_get_cute_tool_message_impl('session_search', function_args, tool_duration, result=function_result)}")
        elif function_name == "memory":
            target = function_args.get("target", "memory")
            from tools.memory_tool import memory_tool as _memory_tool
            function_result = _memory_tool(
                action=function_args.get("action"),
                target=target,
                content=function_args.get("content"),
                old_text=function_args.get("old_text"),
                store=agent._memory_store,
            )
            if agent._memory_manager and function_args.get("action") in ("add", "replace"):
                try:
                    agent._memory_manager.on_memory_write(
                        function_args.get("action", ""),
                        target,
                        function_args.get("content", ""),
                    )
                except Exception:
                    pass
            tool_duration = time.time() - tool_start_time
            if agent._should_emit_quiet_tool_messages():
                agent._vprint(f"  {_get_cute_tool_message_impl('memory', function_args, tool_duration, result=function_result)}")
        elif function_name == "clarify":
            from tools.clarify_tool import clarify_tool as _clarify_tool
            function_result = _clarify_tool(
                question=function_args.get("question", ""),
                choices=function_args.get("choices"),
                callback=agent.clarify_callback,
            )
            tool_duration = time.time() - tool_start_time
            if agent._should_emit_quiet_tool_messages():
                agent._vprint(f"  {_get_cute_tool_message_impl('clarify', function_args, tool_duration, result=function_result)}")
        elif function_name == "delegate_task":
            from tools.delegate_tool import delegate_task as _delegate_task
            tasks_arg = function_args.get("tasks")
            if tasks_arg and isinstance(tasks_arg, list):
                spinner_label = f"🔀 delegating {len(tasks_arg)} tasks"
            else:
                goal_preview = (function_args.get("goal") or "")[:30]
                spinner_label = f"🔀 {goal_preview}" if goal_preview else "🔀 delegating"
            spinner = None
            if agent._should_emit_quiet_tool_messages() and agent._should_start_quiet_spinner():
                face = random.choice(KawaiiSpinner.get_waiting_faces())
                spinner = KawaiiSpinner(f"{face} {spinner_label}", spinner_type='dots', print_fn=agent._print_fn)
                spinner.start()
            agent._delegate_spinner = spinner
            _delegate_result = None
            try:
                function_result = _delegate_task(
                    goal=function_args.get("goal"),
                    context=function_args.get("context"),
                    toolsets=function_args.get("toolsets"),
                    tasks=tasks_arg,
                    max_iterations=function_args.get("max_iterations"),
                    parent_agent=agent,
                )
                _delegate_result = function_result
            finally:
                agent._delegate_spinner = None
                tool_duration = time.time() - tool_start_time
                cute_msg = _get_cute_tool_message_impl('delegate_task', function_args, tool_duration, result=_delegate_result)
                if spinner:
                    spinner.stop(cute_msg)
                elif agent._should_emit_quiet_tool_messages():
                    agent._vprint(f"  {cute_msg}")
        elif agent._context_engine_tool_names and function_name in agent._context_engine_tool_names:
            spinner = None
            if agent._should_emit_quiet_tool_messages():
                face = random.choice(KawaiiSpinner.get_waiting_faces())
                emoji = _get_tool_emoji(function_name)
                preview = _build_tool_preview(function_name, function_args) or function_name
                spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=agent._print_fn)
                spinner.start()
            _ce_result = None
            try:
                function_result = agent.context_compressor.handle_tool_call(function_name, function_args, messages=messages)
                _ce_result = function_result
            except Exception as tool_error:
                function_result = json.dumps({"error": f"Context engine tool '{function_name}' failed: {tool_error}"})
                logger.error("context_engine.handle_tool_call raised for %s: %s", function_name, tool_error, exc_info=True)
            finally:
                tool_duration = time.time() - tool_start_time
                cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_ce_result)
                if spinner:
                    spinner.stop(cute_msg)
                elif agent._should_emit_quiet_tool_messages():
                    agent._vprint(f"  {cute_msg}")
        elif agent._memory_manager and agent._memory_manager.has_tool(function_name):
            spinner = None
            if agent._should_emit_quiet_tool_messages() and agent._should_start_quiet_spinner():
                face = random.choice(KawaiiSpinner.get_waiting_faces())
                emoji = _get_tool_emoji(function_name)
                preview = _build_tool_preview(function_name, function_args) or function_name
                spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=agent._print_fn)
                spinner.start()
            _mem_result = None
            try:
                function_result = agent._memory_manager.handle_tool_call(function_name, function_args)
                _mem_result = function_result
            except Exception as tool_error:
                function_result = json.dumps({"error": f"Memory tool '{function_name}' failed: {tool_error}"})
                logger.error("memory_manager.handle_tool_call raised for %s: %s", function_name, tool_error, exc_info=True)
            finally:
                tool_duration = time.time() - tool_start_time
                cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_mem_result)
                if spinner:
                    spinner.stop(cute_msg)
                elif agent._should_emit_quiet_tool_messages():
                    agent._vprint(f"  {cute_msg}")
        elif agent.quiet_mode:
            spinner = None
            if agent._should_emit_quiet_tool_messages() and agent._should_start_quiet_spinner():
                face = random.choice(KawaiiSpinner.get_waiting_faces())
                emoji = _get_tool_emoji(function_name)
                preview = _build_tool_preview(function_name, function_args) or function_name
                spinner = KawaiiSpinner(f"{face} {emoji} {preview}", spinner_type='dots', print_fn=agent._print_fn)
                spinner.start()
            _spinner_result = None
            try:
                function_result = handle_function_call(
                    function_name, function_args, effective_task_id,
                    tool_call_id=tool_call.id,
                    session_id=agent.session_id or "",
                    enabled_tools=list(agent.valid_tool_names) if agent.valid_tool_names else None,
                    skip_pre_tool_call_hook=True,
                )
                _spinner_result = function_result
            except Exception as tool_error:
                function_result = f"Error executing tool '{function_name}': {tool_error}"
                logger.error("handle_function_call raised for %s: %s", function_name, tool_error, exc_info=True)
            finally:
                tool_duration = time.time() - tool_start_time
                cute_msg = _get_cute_tool_message_impl(function_name, function_args, tool_duration, result=_spinner_result)
                if spinner:
                    spinner.stop(cute_msg)
                elif agent._should_emit_quiet_tool_messages():
                    agent._vprint(f"  {cute_msg}")
        else:
            try:
                function_result = handle_function_call(
                    function_name, function_args, effective_task_id,
                    tool_call_id=tool_call.id,
                    session_id=agent.session_id or "",
                    enabled_tools=list(agent.valid_tool_names) if agent.valid_tool_names else None,
                    skip_pre_tool_call_hook=True,
                )
            except Exception as tool_error:
                function_result = f"Error executing tool '{function_name}': {tool_error}"
                logger.error("handle_function_call raised for %s: %s", function_name, tool_error, exc_info=True)
            tool_duration = time.time() - tool_start_time

        result_preview = function_result if agent.verbose_logging else (
            function_result[:200] if len(function_result) > 200 else function_result
        )

        _is_error_result, _ = _detect_tool_failure(function_name, function_result)
        if _is_error_result:
            logger.warning("Tool %s returned error (%.2fs): %s", function_name, tool_duration, result_preview)
        else:
            logger.info("tool %s completed (%.2fs, %d chars)", function_name, tool_duration, len(function_result))

        if agent.tool_progress_callback:
            try:
                agent.tool_progress_callback(
                    "tool.completed", function_name, None, None,
                    duration=tool_duration, is_error=_is_error_result,
                )
            except Exception as cb_err:
                logging.debug(f"Tool progress callback error: {cb_err}")

        agent._current_tool = None
        agent._touch_activity(f"tool completed: {function_name} ({tool_duration:.1f}s)")

        if agent.verbose_logging:
            logging.debug(f"Tool {function_name} completed in {tool_duration:.2f}s")
            logging.debug(f"Tool result ({len(function_result)} chars): {function_result}")

        if agent.tool_complete_callback:
            try:
                agent.tool_complete_callback(tool_call.id, function_name, function_args, function_result)
            except Exception as cb_err:
                logging.debug(f"Tool complete callback error: {cb_err}")

        function_result = maybe_persist_tool_result(
            content=function_result,
            tool_name=function_name,
            tool_use_id=tool_call.id,
            env=get_active_env(effective_task_id),
        )

        subdir_hints = agent._subdirectory_hints.check_tool_call(function_name, function_args)
        if subdir_hints:
            function_result += subdir_hints

        tool_msg = {
            "role": "tool",
            "content": function_result,
            "tool_call_id": tool_call.id
        }
        messages.append(tool_msg)

        agent._apply_pending_steer_to_tool_results(messages, 1)

        if not agent.quiet_mode:
            if agent.verbose_logging:
                print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s")
                print(wrap_verbose(agent, "Result: ", function_result))
            else:
                response_preview = function_result[:agent.log_prefix_chars] + "..." if len(function_result) > agent.log_prefix_chars else function_result
                print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s - {response_preview}")

        if agent._interrupt_requested and i < len(assistant_message.tool_calls):
            remaining = len(assistant_message.tool_calls) - i
            agent._vprint(f"{agent.log_prefix}⚡ Interrupt: skipping {remaining} remaining tool call(s)", force=True)
            for skipped_tc in assistant_message.tool_calls[i:]:
                skipped_name = skipped_tc.function.name
                skip_msg = {
                    "role": "tool",
                    "content": f"[Tool execution skipped — {skipped_name} was not started. User sent a new message]",
                    "tool_call_id": skipped_tc.id
                }
                messages.append(skip_msg)
            break

        if agent.tool_delay > 0 and i < len(assistant_message.tool_calls):
            time.sleep(agent.tool_delay)

    num_tools_seq = len(assistant_message.tool_calls)
    if num_tools_seq > 0:
        enforce_turn_budget(messages[-num_tools_seq:], env=get_active_env(effective_task_id))

    if num_tools_seq > 0:
        agent._apply_pending_steer_to_tool_results(messages, num_tools_seq)


# ── Max iterations handler ───────────────────────────────────────────────────


def handle_max_iterations(agent: Any, messages: list, api_call_count: int) -> str:
    """Request a summary when max iterations are reached. Returns the final response text."""
    print(f"⚠️  Reached maximum iterations ({agent.max_iterations}). Requesting summary...")

    summary_request = (
        "You've reached the maximum number of tool-calling iterations allowed. "
        "Please provide a final response summarizing what you've found and accomplished so far, "
        "without calling any more tools."
    )
    messages.append({"role": "user", "content": summary_request})

    try:
        # Build API messages, stripping internal-only fields
        # (finish_reason, reasoning) that strict APIs like Mistral reject with 422
        _needs_sanitize = agent._should_sanitize_tool_calls()
        api_messages = []
        for msg in messages:
            api_msg = msg.copy()
            for internal_field in ("reasoning", "finish_reason", "_thinking_prefill"):
                api_msg.pop(internal_field, None)
            if _needs_sanitize:
                agent._sanitize_tool_calls_for_strict_api(api_msg)
            api_messages.append(api_msg)

        effective_system = agent._cached_system_prompt or ""
        if agent.ephemeral_system_prompt:
            effective_system = (effective_system + "\n\n" + agent.ephemeral_system_prompt).strip()
        if effective_system:
            api_messages = [{"role": "system", "content": effective_system}] + api_messages
        if agent.prefill_messages:
            sys_offset = 1 if effective_system else 0
            for idx, pfm in enumerate(agent.prefill_messages):
                api_messages.insert(sys_offset + idx, pfm.copy())

        summary_extra_body = {}
        try:
            from agent.auxiliary_client import _fixed_temperature_for_model
        except Exception:
            _fixed_temperature_for_model = None
        _summary_temperature = (
            _fixed_temperature_for_model(agent.model, agent.base_url)
            if _fixed_temperature_for_model is not None
            else None
        )
        _is_nous = "nousresearch" in agent._base_url_lower
        if agent._supports_reasoning_extra_body():
            if agent.reasoning_config is not None:
                summary_extra_body["reasoning"] = agent.reasoning_config
            else:
                summary_extra_body["reasoning"] = {
                    "enabled": True,
                    "effort": "medium"
                }
        if _is_nous:
            summary_extra_body["tags"] = ["product=hermes-agent"]

        if agent.api_mode == "codex_responses":
            codex_kwargs = agent._build_api_kwargs(api_messages)
            codex_kwargs.pop("tools", None)
            summary_response = agent._run_codex_stream(codex_kwargs)
            assistant_message, _ = agent._normalize_codex_response(summary_response)
            final_response = (assistant_message.content or "").strip() if assistant_message else ""
        else:
            summary_kwargs = {
                "model": agent.model,
                "messages": api_messages,
            }
            if _summary_temperature is not None:
                summary_kwargs["temperature"] = _summary_temperature
            if agent.max_tokens is not None:
                summary_kwargs.update(agent._max_tokens_param(agent.max_tokens))

            # Include provider routing preferences
            provider_preferences = {}
            if agent.providers_allowed:
                provider_preferences["only"] = agent.providers_allowed
            if agent.providers_ignored:
                provider_preferences["ignore"] = agent.providers_ignored
            if agent.providers_order:
                provider_preferences["order"] = agent.providers_order
            if agent.provider_sort:
                provider_preferences["sort"] = agent.provider_sort
            if provider_preferences:
                summary_extra_body["provider"] = provider_preferences

            if summary_extra_body:
                summary_kwargs["extra_body"] = summary_extra_body

            if agent.api_mode == "anthropic_messages":
                from agent.anthropic_adapter import build_anthropic_kwargs as _bak, normalize_anthropic_response as _nar
                _ant_kw = _bak(model=agent.model, messages=api_messages, tools=None,
                               max_tokens=agent.max_tokens, reasoning_config=agent.reasoning_config,
                               is_oauth=agent._is_anthropic_oauth,
                               preserve_dots=agent._anthropic_preserve_dots())
                summary_response = agent._anthropic_messages_create(_ant_kw)
                _msg, _ = _nar(summary_response, strip_tool_prefix=agent._is_anthropic_oauth)
                final_response = (_msg.content or "").strip()
            else:
                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary").chat.completions.create(**summary_kwargs)

                if summary_response.choices and summary_response.choices[0].message.content:
                    final_response = summary_response.choices[0].message.content
                else:
                    final_response = ""

        if final_response:
            if "<think>" in final_response:
                import re
                final_response = re.sub(r'<think>.*?</think>\s*', '', final_response, flags=re.DOTALL).strip()
            if final_response:
                messages.append({"role": "assistant", "content": final_response})
            else:
                final_response = "I reached the iteration limit and couldn't generate a summary."
        else:
            # Retry summary generation
            if agent.api_mode == "codex_responses":
                codex_kwargs = agent._build_api_kwargs(api_messages)
                codex_kwargs.pop("tools", None)
                retry_response = agent._run_codex_stream(codex_kwargs)
                retry_msg, _ = agent._normalize_codex_response(retry_response)
                final_response = (retry_msg.content or "").strip() if retry_msg else ""
            elif agent.api_mode == "anthropic_messages":
                from agent.anthropic_adapter import build_anthropic_kwargs as _bak2, normalize_anthropic_response as _nar2
                _ant_kw2 = _bak2(model=agent.model, messages=api_messages, tools=None,
                                is_oauth=agent._is_anthropic_oauth,
                                max_tokens=agent.max_tokens, reasoning_config=agent.reasoning_config,
                                preserve_dots=agent._anthropic_preserve_dots())
                retry_response = agent._anthropic_messages_create(_ant_kw2)
                _retry_msg, _ = _nar2(retry_response, strip_tool_prefix=agent._is_anthropic_oauth)
                final_response = (_retry_msg.content or "").strip()
            else:
                summary_kwargs = {
                    "model": agent.model,
                    "messages": api_messages,
                }
                if _summary_temperature is not None:
                    summary_kwargs["temperature"] = _summary_temperature
                if agent.max_tokens is not None:
                    summary_kwargs.update(agent._max_tokens_param(agent.max_tokens))
                if summary_extra_body:
                    summary_kwargs["extra_body"] = summary_extra_body

                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary_retry").chat.completions.create(**summary_kwargs)

                if summary_response.choices and summary_response.choices[0].message.content:
                    final_response = summary_response.choices[0].message.content
                else:
                    final_response = ""

            if final_response:
                if "<think>" in final_response:
                    import re
                    final_response = re.sub(r'<think>.*?</think>\s*', '', final_response, flags=re.DOTALL).strip()
                if final_response:
                    messages.append({"role": "assistant", "content": final_response})
                else:
                    final_response = "I reached the iteration limit and couldn't generate a summary."
            else:
                final_response = "I reached the iteration limit and couldn't generate a summary."

    except Exception as e:
        logging.warning(f"Failed to get summary response: {e}")
        final_response = f"I reached the maximum iterations ({agent.max_iterations}) but couldn't summarize. Error: {str(e)}"

    return final_response