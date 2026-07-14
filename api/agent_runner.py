import os
import time
import queue
import threading
import traceback
from pathlib import Path
from api.config import STREAMS, STREAMS_LOCK, CANCEL_FLAGS, DEFAULT_MODEL
from api.models import get_session

# Lazy import AIAgent and SessionDB
AIAgent = None
SessionDB = None

def init_agent_imports():
    global AIAgent, SessionDB
    if AIAgent is not None:
        return
    try:
        from run_agent import AIAgent as _AIAgent
        from hermes_state import SessionDB as _SessionDB
        AIAgent = _AIAgent
        SessionDB = _SessionDB
    except ImportError as e:
        print("[agent_runner] ImportError during dynamic import:")
        import traceback
        traceback.print_exc()
        pass

def run_agent_stream(session_id, msg_text, model, workspace, stream_id):
    init_agent_imports()
    
    q = STREAMS.get(stream_id)
    if q is None:
        return

    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event

    def put(event, data):
        if cancel_event.is_set() and event not in ('cancel', 'error', 'apperror'):
            return
        try:
            q.put_nowait((event, data))
        except Exception:
            pass

    def run_thread():
        try:
            s = get_session(session_id)
            s.workspace = str(Path(workspace).expanduser().resolve())
            s.model = model or DEFAULT_MODEL

            # Pre-flight cancel check
            if cancel_event.is_set():
                put('cancel', {'message': 'Cancelled before start'})
                return

            # Helper callbacks
            def on_token(text):
                if text is not None:
                    put('token', {'text': text})

            def on_tool(event_type, tool_name, preview, args):
                # tool_executor.py calls with 4 positional args:
                #   agent.tool_progress_callback("tool.started", function_name, preview, function_args)
                #   agent.tool_progress_callback("tool.completed", function_name, None, None, duration=..., is_error=...)
                print(f"[MonacoEditorUX-debug] on_tool called: event_type={event_type} tool_name={tool_name}", flush=True)
                args_snap = {}
                if isinstance(args, dict):
                    for k, v in list(args.items())[:4]:
                        s2 = str(v)
                        args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')
                put('tool', {'name': tool_name, 'event': event_type, 'preview': preview, 'args': args_snap})
                # Monaco Editor UX를 위한 파일 편집 이벤트 전송
                if event_type == 'tool.started' and tool_name in ('write_file', 'patch') and isinstance(args, dict):
                    print(f"[MonacoEditorUX-debug] ✅ file_edit event SENT for {tool_name} args_keys={list(args.keys())}", flush=True)
                    put('file_edit', {'name': tool_name, 'args': args})

            # Check if AIAgent is loaded
            if AIAgent is None:
                raise ImportError("AIAgent is not available. Please verify hermes-agent path settings.")

            # Try to inject API keys from global env or ~/.hermes/auth.json
            try:
                import json
                auth_path = Path.home() / '.hermes' / 'auth.json'
                if auth_path.exists():
                    auth_data = json.loads(auth_path.read_text(encoding='utf-8'))
                    pool = auth_data.get('credential_pool', {})
                    # Gemini/Google key
                    if not os.getenv('GOOGLE_API_KEY') and 'gemini' in pool:
                        tok = pool['gemini'][0].get('access_token') if pool['gemini'] else None
                        if tok: os.environ['GOOGLE_API_KEY'] = tok
                    # OpenAI key
                    if not os.getenv('OPENAI_API_KEY') and 'openai' in pool:
                        tok = pool['openai'][0].get('access_token') if pool['openai'] else None
                        if tok: os.environ['OPENAI_API_KEY'] = tok
                    # OpenRouter key
                    if not os.getenv('OPENROUTER_API_KEY') and 'openrouter' in pool:
                        tok = pool['openrouter'][0].get('access_token') if pool['openrouter'] else None
                        if tok: os.environ['OPENROUTER_API_KEY'] = tok
                    # Ollama key
                    if not os.getenv('OLLAMA_API_KEY') and 'ollama-cloud' in pool:
                        tok = pool['ollama-cloud'][0].get('access_token') if pool['ollama-cloud'] else None
                        if tok: os.environ['OLLAMA_API_KEY'] = tok
                    # MiniMax key
                    if not os.getenv('MINIMAX_API_KEY') and 'minimax' in pool:
                        tok = pool['minimax'][0].get('access_token') if pool['minimax'] else None
                        if tok: os.environ['MINIMAX_API_KEY'] = tok
                    # DeepSeek key
                    if not os.getenv('DEEPSEEK_API_KEY') and 'deepseek' in pool:
                        tok = pool['deepseek'][0].get('access_token') if pool['deepseek'] else None
                        if tok: os.environ['DEEPSEEK_API_KEY'] = tok
                    # NVIDIA key
                    if not os.getenv('NVIDIA_API_KEY') and 'nvidia' in pool:
                        tok = pool['nvidia'][0].get('access_token') if pool['nvidia'] else None
                        if tok: os.environ['NVIDIA_API_KEY'] = tok
            except Exception as _e:
                print(f"[webui] Key injection warning: {_e}")

            # Instantiate AIAgent
            session_db = SessionDB() if SessionDB else None
            
            # Resolve provider & base_url & api_key based on model name via model_manager
            from api.managers.model_manager import model_manager
            resolved_model, resolved_provider, resolved_base_url = model_manager.resolve_model_provider(s.model)
            resolved_api_key = None

            if resolved_provider == 'minimax':
                resolved_api_key = os.getenv('MINIMAX_API_KEY')
            elif resolved_provider == 'deepseek':
                resolved_api_key = os.getenv('DEEPSEEK_API_KEY')
            elif resolved_provider == 'nvidia':
                resolved_api_key = os.getenv('NVIDIA_API_KEY')
            elif resolved_provider == 'openrouter':
                resolved_api_key = os.getenv('OPENROUTER_API_KEY')
            elif resolved_provider == 'local':
                resolved_api_key = os.getenv('LOCAL_API_KEY') or 'local'
            elif resolved_provider == 'google':
                resolved_api_key = os.getenv('GOOGLE_API_KEY')
            else:
                resolved_api_key = os.getenv('OPENAI_API_KEY')

            agent = AIAgent(
                model=resolved_model,
                provider=resolved_provider,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                platform='cli',
                quiet_mode=True,
                session_id=session_id,
                session_db=session_db,
                stream_delta_callback=on_token,
                tool_progress_callback=on_tool,
            )

            # Workspace context message prefixes
            is_windows = os.name == 'nt'
            os_ctx = (
                "\n\nOperating System: Windows. "
                "The execute_command tool runs via cmd.exe. "
                "Use Windows-style commands: dir (not ls), type (not cat), findstr (not grep), "
                "copy (not cp), move (not mv), del (not rm). "
                "For PowerShell, prefix with 'powershell -Command \"...\"'. "
                "File paths use backslashes or forward slashes."
            ) if is_windows else ""

            workspace_ctx = f"[Workspace: {s.workspace}] [Model: {resolved_model}]\n"
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace: /path] [Model: model-name] tags.\n"
                "[Workspace: ...] is the single authoritative source of the active working directory — "
                "always use the most recent value for ALL file operations. "
                "[Model: ...] is the single authoritative source of which AI model you are running as — "
                "if the user asks what model you are, always answer from this tag, never from memory or "
                "prior conversation. Both tags update with every message and override anything else."
            ) + os_ctx

            # Save the user's message to local session history first
            # to make sure it persists even on crash
            if not any(m.get('role') == 'user' and m.get('content') == msg_text for m in s.messages[-2:]):
                s.messages.append({'role': 'user', 'content': msg_text, 'timestamp': int(time.time())})
                s.save()

            # Clean messages for API (strip system messages and metadata)
            clean_history = []
            for m in s.messages[:-1]:
                if m.get('role') == 'system':
                    continue
                clean_history.append({
                    'role': m.get('role'),
                    'content': m.get('content'),
                    'tool_calls': m.get('tool_calls')
                })

            # Run agent loop
            result = agent.run_conversation(
                user_message=workspace_ctx + msg_text,
                system_message=workspace_system_msg,
                conversation_history=clean_history,
                task_id=session_id
            )

            # Update session history with the result
            res_msgs = result.get('messages')
            if res_msgs:
                s.messages = [m for m in res_msgs if m.get('role') != 'system']
            
            # Format and save metadata
            for m in s.messages:
                if not m.get('timestamp'):
                    m['timestamp'] = int(time.time())
            
            # Parse tool calls for UI rendering
            tool_calls = []
            pending_names = {}
            pending_args = {}
            pending_asst_idx = {}
            for msg_idx, m in enumerate(s.messages):
                if m.get('role') == 'assistant':
                    c = m.get('content', '')
                    if isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict) and p.get('type') == 'tool_use':
                                tid = p.get('id', '')
                                pending_names[tid] = p.get('name', '')
                                pending_args[tid] = p.get('input', {})
                                pending_asst_idx[tid] = msg_idx
                elif m.get('role') == 'tool':
                    tid = m.get('tool_call_id') or m.get('tool_use_id', '')
                    name = pending_names.get(tid, '')
                    if name:
                        asst_idx = pending_asst_idx.get(tid, -1)
                        args = pending_args.get(tid, {})
                        raw = str(m.get('content', ''))
                        snippet = raw[:200]
                        tool_calls.append({
                            'name': name,
                            'snippet': snippet,
                            'tid': tid,
                            'assistant_msg_idx': asst_idx,
                            'args': args
                        })
            
            s.tool_calls = tool_calls
            s.updated_at = time.time()
            s.save()

            # Done signal
            put('done', {
                'session': s.compact() | {'messages': s.messages, 'tool_calls': tool_calls},
                'usage': {
                    'input_tokens': getattr(agent, 'session_prompt_tokens', 0) or 0,
                    'output_tokens': getattr(agent, 'session_completion_tokens', 0) or 0
                }
            })

        except Exception as e:
            traceback.print_exc()
            put('apperror', {'message': str(e), 'type': 'error'})
        finally:
            with STREAMS_LOCK:
                STREAMS.pop(stream_id, None)
                CANCEL_FLAGS.pop(stream_id, None)

    # Start thread
    threading.Thread(target=run_thread, daemon=True).start()

def cancel_stream(stream_id):
    with STREAMS_LOCK:
        if stream_id not in STREAMS:
            return False
        flag = CANCEL_FLAGS.get(stream_id)
        if flag:
            flag.set()
        q = STREAMS.get(stream_id)
        if q:
            q.put_nowait(('cancel', {'message': 'Cancelled by user'}))
        return True
