print("[BUILD ID]: server-v5-2026-08-03-22:50", flush=True)
import json
import os
import sys
import time
import queue
import urllib.parse
import traceback
import webbrowser
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class RobustThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer가 예외로 죽지 않도록 보호."""
    request_queue_size = 32
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        """개별 요청 처리 실패가 서버 전체를 죽이지 않도록 예외 캡처."""
        print(f"[Server Warning] Request error from {client_address}:", flush=True)
        traceback.print_exc()


# Force UTF-8 stdout/stderr to prevent cp949 encode crashes from emoji/special chars.
# Guard against PyInstaller --windowed mode where sys.stdout/stderr are None.
# When None (no console), redirect to devnull so print()/traceback.print_exc() don't crash.
if sys.platform == 'win32':
    try:
        if sys.stdout is None or not hasattr(sys.stdout, 'write'):
            sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        elif hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr is None or not hasattr(sys.stderr, 'write'):
            sys.stderr = open(os.devnull, 'w', encoding='utf-8')
        elif hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Module-level server start timestamp (set by main())
_server_start_ts = time.time()
NO_BROWSER = False  # [v4] main()에서 --no-browser 플래그로 설정
from pathlib import Path

# Resolve static resource paths and run directories for PyInstaller environment
if hasattr(sys, '_MEIPASS'):
    RUN_DIR = Path(sys.executable).parent.resolve()
    if (RUN_DIR / 'static').exists():
        RESOURCE_DIR = RUN_DIR
    elif (Path.cwd() / 'static').exists():
        RESOURCE_DIR = Path.cwd()
    else:
        RESOURCE_DIR = Path(sys._MEIPASS)
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    RUN_DIR = Path(__file__).parent.resolve()

# PyInstaller bundle helper imports
import asyncio
import logging.handlers
import concurrent.futures
import base64
import tempfile
# Load profile/user environment from ~/.hermes/.env
try:
    import dotenv
    from pathlib import Path
    _hermes_env = Path.home() / '.hermes' / '.env'
    if _hermes_env.exists():
        dotenv.load_dotenv(_hermes_env, override=True)
except Exception:
    pass

try:
    import openai
    import anthropic
    import fire
    import psutil
    import dotenv
    import rich
    import tenacity
    import yaml
    import requests
    import pydantic
    import prompt_toolkit
    import httpx
except ImportError:
    pass

# Setup paths (use RESOURCE_DIR in PyInstaller, project root otherwise)
sys.path.insert(0, str(RESOURCE_DIR))
sys.path.insert(0, str(RESOURCE_DIR / 'api'))
if (RESOURCE_DIR / 'api' / 'api').exists():
    sys.path.insert(0, str(RESOURCE_DIR / 'api' / 'api'))
# PyInstaller bundles hermes-agent inside _MEIPASS; ensure it's on sys.path
if hasattr(sys, '_MEIPASS'):
    _meipass = Path(sys._MEIPASS)
    sys.path.insert(0, str(_meipass / 'hermes-agent'))
    sys.path.insert(0, str(_meipass))

from api.config import PORT, HOST, MIME_MAP, STREAMS, STREAMS_LOCK, load_settings, save_settings
# [v4] api.agent_runner, api.managers.model_manager 제거됨 (server.py에서 미사용, import 체인이 서버 시작 차단)
# [v4] api.profiles는 main() 내부에서 lazy import



class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Optional: Print structured logs
        print(f"[HTTP] {self.command} {self.path} - {args[1]}")

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            # Health check for Electron + 웹앱 CLI 콘솔 하트비트
            if path == '/health':
                uptime_sec = int(time.time() - _server_start_ts)
                h, m, s = uptime_sec // 3600, (uptime_sec % 3600) // 60, uptime_sec % 60
                
                active_streams_count = 0
                active_agents_count = 0
                last_req_display = "none"
                try:
                    from api.config import STREAMS
                    from api.streaming import _ACTIVE_AGENTS
                    active_streams_count = len(STREAMS)
                    active_agents_count = len(_ACTIVE_AGENTS)
                except Exception:
                    pass

                # Session Store 건강 상태 체크
                session_status = "ok"
                try:
                    from api.models import SESSION_DIR
                    if not SESSION_DIR.exists():
                        session_status = "dir_missing"
                except Exception as e:
                    session_status = f"error: {str(e)}"

                # Skill Registry 건강 상태 체크
                skill_status = "ok"
                try:
                    from api.skill_registry import _resolve_skills_dir
                    s_dir = _resolve_skills_dir()
                    if not s_dir.exists():
                        skill_status = "dir_missing"
                except Exception as e:
                    skill_status = f"error: {str(e)}"

                thread_cnt = threading.active_count()
                is_healthy = (
                    thread_cnt < 200 and 
                    active_streams_count < 50
                )
                warnings = []
                if session_status != "ok":
                    warnings.append(f"session_store: {session_status}")
                if skill_status != "ok":
                    warnings.append(f"skill_registry: {skill_status}")

                self.send_json({
                    'status': 'ok',
                    'healthy': is_healthy,
                    'warnings': warnings,
                    'uptime_seconds': uptime_sec,
                    'uptime_display': f'{h:02d}:{m:02d}:{s:02d}',
                    'thread_count': thread_cnt,
                    'active_streams': active_streams_count,
                    'active_agents': active_agents_count,
                    'session_store': session_status,
                    'skill_registry': skill_status,
                    'pid': os.getpid(),
                })
                return

            # Serve Frontend
            if path in ('/', '/index.html'):
                self.serve_file(RESOURCE_DIR / 'index.html')
                return

            if path.startswith('/static/'):
                rel_path = path[len('/static/'):]
                self.serve_file(RESOURCE_DIR / 'static' / rel_path)
                return

            # Delegate to standard api.routes.handle_get
            from api.routes import handle_get
            
            # Preview Workspace Static Files
            if path.startswith('/preview/'):
                parts = path.strip('/').split('/', 2)
                if len(parts) >= 2:
                    sess_id = parts[1]
                    rel_path = parts[2] if len(parts) > 2 else 'index.html'
                    
                    from api.models import get_session
                    try:
                        s = get_session(sess_id)
                        target_file = Path(s.workspace) / rel_path
                        self.serve_file(target_file)
                        return
                    except KeyError:
                        pass # Session not found, fall through to 404/500
                        
            if handle_get(self, parsed):
                return

            # ── Browser Automation API (GET) — direct dispatch ──
            if path == '/api/browser/status':
                try:
                    from api.routes.browser_routes import handle_get_browser_status
                    if handle_get_browser_status(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Browser status error", 500)
                    return

            if path == '/api/browser/proxy':
                try:
                    from api.routes.browser_routes import handle_get_browser_proxy
                    if handle_get_browser_proxy(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Browser proxy error", 500)
                    return

            # ── Demo-to-Skill API (GET) — direct dispatch ──
            demo_get_routes = {
                '/api/demo/status': 'handle_get_demo_status',
                '/api/demo/events': 'handle_get_demo_events',
            }
            if path in demo_get_routes:
                try:
                    from api.routes.demo_to_skill_routes import (
                        handle_get_demo_status,
                        handle_get_demo_events,
                    )
                    func_name = demo_get_routes[path]
                    func = locals()[func_name]
                    if func(self, query):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Demo-to-Skill GET error", 500)
                    return

            # Native Fallback Endpoints — delegated to api/native_dialogs.py
            if path == '/api/workspaces/select':
                try:
                    from api.native_dialogs import select_workspace_dialog
                    selected = select_workspace_dialog()
                    self.send_json({'path': selected})
                except Exception as e:
                    traceback.print_exc()
                    self.send_error_json(str(e), 500)
                return

            if path == '/api/file/select':
                try:
                    from api.native_dialogs import select_file_dialog
                    ws_dir = query.get('workspace', [''])[0]
                    selected = select_file_dialog(ws_dir)
                    self.send_json({'path': selected})
                except Exception as e:
                    traceback.print_exc()
                    self.send_error_json(str(e), 500)
                return

            if path == '/api/fs/list':
                try:
                    dir_path = query.get('path', [''])[0]
                    # If empty path, list root drives on Windows
                    if not dir_path:
                        import string
                        from ctypes import windll
                        drives = []
                        bitmask = windll.kernel32.GetLogicalDrives()
                        for letter in string.ascii_uppercase:
                            if bitmask & 1:
                                drives.append(f"{letter}:/")
                            bitmask >>= 1
                        self.send_json({
                            'current': '',
                            'parent': '',
                            'drives': drives,
                            'entries': [{'name': d, 'path': d, 'type': 'drive'} for d in drives]
                        })
                        return
                    
                    # Otherwise, list directories and files in dir_path
                    p = Path(dir_path)
                    if not p.exists() or not p.is_dir():
                        raise FileNotFoundError(f"Directory not found: {dir_path}")
                    
                    entries = []
                    # Add parent directory if possible
                    parent_path = str(p.parent).replace('\\', '/') if p.parent != p else ''
                    
                    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                        try:
                            # Skip hidden/system files to keep it clean
                            if item.name.startswith('.'):
                                continue
                            entries.append({
                                'name': item.name,
                                'path': str(item).replace('\\', '/'),
                                'type': 'dir' if item.is_dir() else 'file'
                            })
                        except Exception:
                            pass # Skip permission errors or broken symlinks
                    
                    self.send_json({
                        'current': str(p).replace('\\', '/'),
                        'parent': parent_path,
                        'entries': entries
                    })
                except Exception as e:
                    traceback.print_exc()
                    self.send_error_json(str(e), 500)
                return

            # ── Config Score ──
            if path == '/api/score/evaluate':
                try:
                    from api.routes.score_routes import handle_get_score_evaluate
                    if handle_get_score_evaluate(self, query):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Score evaluation error", 500)
                    return

            # ── Setup Generator ──
            if path == '/api/setup/preview':
                try:
                    from api.routes.setup_routes import handle_get_setup_preview
                    if handle_get_setup_preview(self, query):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Setup preview error", 500)
                    return

            if path == '/api/setup/detect':
                try:
                    from api.routes.setup_routes import handle_get_setup_detect
                    if handle_get_setup_detect(self, query):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Setup detect error", 500)
                    return

            # ── MCP Recommender ──
            if path == '/api/mcp/recommend':
                try:
                    from api.routes.mcp_routes import handle_get_mcp_recommend
                    if handle_get_mcp_recommend(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("MCP recommend error", 500)
                    return

            # ── Capability Diagnosis ──
            if path == '/api/capability/diagnose':
                try:
                    from api.routes.mcp_routes import handle_get_capability_diagnose
                    if handle_get_capability_diagnose(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Capability diagnosis error", 500)
                    return

            # ── Capability Tests ──
            if path == '/api/capability/tests':
                try:
                    from api.routes.mcp_routes import handle_get_capability_tests
                    if handle_get_capability_tests(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Capability tests error", 500)
                    return

            # ── Capability Mappings ──
            if path == '/api/capability/mappings':
                try:
                    from api.routes.mcp_routes import handle_get_capability_mappings
                    if handle_get_capability_mappings(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Capability mappings error", 500)
                    return


            if path == '/api/dynamic/status':
                run_id = query.get('run_id', [''])[0]
                log_cursor = int(query.get('log_cursor', ['0'])[0])
                if not run_id:
                    self.send_error_json("run_id is required", 400)
                    return
                import api.dynamic_jobs as dj
                resp = dj.get_job_status_response(run_id)
                if resp is None:
                    self.send_error_json(f"run_id not found: {run_id}", 404)
                    return
                new_logs, next_cursor = dj.get_job_logs_since(run_id, log_cursor)
                resp['logs'] = new_logs
                resp['next_cursor'] = next_cursor
                self.send_json(resp)
                return

            # ── TTS (Edge TTS voice synthesis) ──
            if path == '/api/speak/tts':
                try:
                    from api.routes.speak_routes import handle_tts
                    if handle_tts(self, parsed):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("TTS error", 500)
                    return

            # If path not handled
            self.send_error_json("Not found", 404)

        except Exception as e:
            traceback.print_exc()
            self.send_error_json("Internal server error", 500)

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            
            # ── Demo-to-Skill API (POST) — direct dispatch ──
            demo_post_routes = {
                '/api/demo/start': 'handle_post_demo_start',
                '/api/demo/stop': 'handle_post_demo_stop',
                '/api/demo/cancel': 'handle_post_demo_cancel',
                '/api/demo/text-workflow': 'handle_post_demo_text_workflow',
                '/api/demo/add-event': 'handle_post_demo_add_event',
            }
            if path in demo_post_routes:
                try:
                    from api.routes.demo_to_skill_routes import (
                        handle_post_demo_start,
                        handle_post_demo_stop,
                        handle_post_demo_cancel,
                        handle_post_demo_text_workflow,
                        handle_post_demo_add_event,
                    )
                    func_name = demo_post_routes[path]
                    func = locals()[func_name]
                    # Read body
                    content_length = int(self.headers.get('Content-Length', 0))
                    body_bytes = self.rfile.read(content_length) if content_length > 0 else b''
                    body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
                    if func(self, body):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Demo-to-Skill POST error", 500)
                    return

            # ── Setup Generator (POST) ──
            if path == '/api/setup/generate':
                try:
                    from api.routes.setup_routes import handle_post_setup_generate
                    content_length = int(self.headers.get('Content-Length', 0))
                    body_bytes = self.rfile.read(content_length) if content_length > 0 else b''
                    body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
                    if handle_post_setup_generate(self, body):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Setup generate error", 500)
                    return

            # Delegate to standard api.routes.handle_post
            from api.routes import handle_post
            if handle_post(self, parsed):
                return

            # ── Browser Automation API (POST) — direct dispatch ──
            browser_post_routes = {
                '/api/browser/navigate': 'handle_post_browser_navigate',
                '/api/browser/snapshot': 'handle_post_browser_snapshot',
                '/api/browser/click': 'handle_post_browser_click',
                '/api/browser/type': 'handle_post_browser_type',
                '/api/browser/screenshot': 'handle_post_browser_screenshot',
                '/api/browser/execute': 'handle_post_browser_execute',
                '/api/browser/close': 'handle_post_browser_close',
            }
            if path in browser_post_routes:
                try:
                    from api.routes.browser_routes import (
                        handle_post_browser_navigate,
                        handle_post_browser_snapshot,
                        handle_post_browser_click,
                        handle_post_browser_type,
                        handle_post_browser_screenshot,
                        handle_post_browser_execute,
                        handle_post_browser_close,
                    )
                    func_name = browser_post_routes[path]
                    func = locals()[func_name]
                    # Read body
                    content_length = int(self.headers.get('Content-Length', 0))
                    body_bytes = self.rfile.read(content_length) if content_length > 0 else b''
                    body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
                    if func(self, body):
                        return
                except Exception:
                    traceback.print_exc()
                    self.send_error_json("Browser POST error", 500)
                    return

            # Native Fallback Endpoints for Daon Agent System
            if hasattr(self, 'body'):
                body = self.body
            else:
                content_length = int(self.headers.get('Content-Length', 0))
                body_bytes = self.rfile.read(content_length) if content_length > 0 else b''
                body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}

            if path == '/api/dynamic/run':
                import api.dynamic_jobs as dj
                try:
                    run_id = dj.start_harness_job(body)
                except ValueError as e:
                    self.send_error_json(str(e), 400)
                    return
                self.send_json({'ok': True, 'run_id': run_id, 'status': 'running'})
                return

            if path == '/api/dynamic/cancel':
                run_id = body.get('run_id')
                if not run_id:
                    self.send_error_json("run_id is required", 400)
                    return
                import api.dynamic_jobs as dj
                if dj.cancel_job(run_id):
                    self.send_json({'ok': True, 'message': 'Job cancelled'})
                else:
                    self.send_error_json("Job not found or already completed", 404)
                return

            self.send_error_json("Not found", 404)

        except Exception as e:
            traceback.print_exc()
            self.send_error_json("Internal server error", 500)

    def serve_file(self, file_path: Path):
        try:
            if not file_path.exists() or not file_path.is_file():
                self.send_error_json("File not found", 404)
                return
            ext = file_path.suffix.lower()
            mime = MIME_MAP.get(ext, 'application/octet-stream')
            raw_bytes = file_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw_bytes)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw_bytes)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # Client disconnected before response could be sent

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # Client disconnected before response could be sent
        return True

    def send_error_json(self, message, status=400):
        try:
            self.send_json({'error': message}, status=status)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def handle_sse(self, stream_id):
        q = STREAMS.get(stream_id)
        if q is None:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        while True:
            try:
                event, data = q.get(timeout=15)
            except queue.Empty:
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                    break  # Client disconnected
                continue
            
            try:
                payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                self.wfile.write(payload.encode('utf-8'))
                self.wfile.flush()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
                break  # Client disconnected

            if event in ('done', 'error', 'cancel', 'apperror'):
                break

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--tts-mode', action='store_true',
                        help='Run as dedicated TTS server instead of the main agent server.')
    parser.add_argument('--tts-port', type=int, default=9091,
                        help='TTS server port (default: 9091, only with --tts-mode)')
    args, _ = parser.parse_known_args()

    # ── TTS-only mode: skip agent server, just serve TTS on a dedicated port ──
    if args.tts_mode:
        from tts_server import run_tts_server
        run_tts_server(args.tts_port)
        return
    
    global NO_BROWSER
    NO_BROWSER = args.no_browser
    
    # Override PORT if passed
    global PORT
    if args.port is not None:
        PORT = args.port

    # ── Configure Python logging so all _logger.info(...) calls are visible ──
    import logging
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='[%(name)s] %(message)s',
        stream=sys.stdout,
    )

    # Make sure static directory exists
    try:
        static_dir = RESOURCE_DIR / 'static'
        static_dir.mkdir(exist_ok=True)
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════════
    # [v4] 서버 우선 시작 아키텍처
    # HTTP 서버를 먼저 바인딩하여 Electron 헬스체크(/health)에 즉시 응답.
    # 무거운 초기화(Profile, Whisper CUDA)는 서버 LISTENING 후 백그라운드 수행.
    # ══════════════════════════════════════════════════════════════════════
    server = RobustThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Server running at http://localhost:{PORT}", flush=True)
    global _server_start_ts
    _server_start_ts = time.time()

    # ── 백그라운드 초기화 (서버 이미 포트 바인딩 완료 상태) ──
    def _background_init():
        # Initialize profile state
        try:
            import api.profiles as prof
            prof.init_profile_state()
            print(f"[Profiles] Active profile: {prof.get_active_profile_name()}", flush=True)
        except Exception as e:
            print(f"[Profiles] Init failed: {e}", flush=True)

        # Pre-warm Whisper model in background so first user mic click has 0s load delay
        try:
            from api.routes.whisper_routes import warmup_whisper_async
            warmup_whisper_async()
            print("[Whisper] Background model pre-warm started.", flush=True)
        except Exception as e:
            print(f"[Whisper] Warmup trigger failed: {e}", flush=True)

    threading.Thread(target=_background_init, name="daon-bg-init", daemon=True).start()

    # ── Heartbeat logger (every 30s) ──
    def _heartbeat_loop():
        print(f"[Heartbeat] Started (interval=30s, pid={os.getpid()})")
        while True:
            time.sleep(30)
            try:
                import threading as _th
                uptime = int(time.time() - _server_start_ts)
                h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
                active_threads = _th.active_count()
                print(f"[Heartbeat] Uptime={h:02d}:{m:02d}:{s:02d}  Threads={active_threads}  pid={os.getpid()}", flush=True)
            except Exception as _e:
                print(f"[Heartbeat] Error: {_e}", flush=True)

    threading.Thread(target=_heartbeat_loop, name="daon-heartbeat", daemon=True).start()

    # ── atexit shutdown log ──
    import atexit as _atexit
    def _on_shutdown():
        uptime = int(time.time() - _server_start_ts)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        print(f"[Shutdown] Server stopping after Uptime={h:02d}:{m:02d}:{s:02d}  pid={os.getpid()}", flush=True)
    _atexit.register(_on_shutdown)

    # Start background cron scheduler timer
    def run_cron_scheduler():
        print("[Cron] Background scheduler ticker started.")
        # Wait a few seconds for the HTTP server to settle
        time.sleep(5)
        from cron.scheduler import tick as cron_tick
        while True:
            try:
                cron_tick(verbose=False)
            except Exception as e:
                print(f"[Cron] Scheduler tick error: {e}")
            time.sleep(60)
            
    threading.Thread(target=run_cron_scheduler, name="webui-cron-scheduler", daemon=True).start()
    
    # Auto open browser
    def open_browser():
        if not NO_BROWSER:
            time.sleep(1.5)
            webbrowser.open(f"http://127.0.0.1:{PORT}")
    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[Shutdown] KeyboardInterrupt received", flush=True)
    except Exception as _e:
        print(f"[Shutdown] Unexpected error: {_e}", flush=True)
        traceback.print_exc()
    finally:
        print("[Shutdown] serve_forever exited", flush=True)

if __name__ == '__main__':
    main()
