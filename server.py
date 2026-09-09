print("[BUILD ID]: server-v5-2026-08-14-20:05", flush=True)
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
    # [v5-fix] allow_reuse_address는 반드시 False.
    # Windows에서 True(SO_REUSEADDR)는 기존 server.exe가 같은 포트에 LISTENING 중인
    # 상태에서도 새 프로세스의 바인드를 허용 → 두 서버가 동일 포트에 동시 리슨하는
    # "좀비 리스너"가 생기고, 브라우저가 깨진 인스턴스로 연결돼
    # "잘못된 응답(ERR_EMPTY_RESPONSE)"이 발생한다. (2026-08-14 재현 확인)
    allow_reuse_address = False
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
    # One-file PyInstaller data files live under _MEIPASS. Prefer that
    # location so the bundled React webview is not shadowed by a developer's
    # working-directory legacy static folder.
    if (Path(sys._MEIPASS) / 'webview' / 'index.html').exists():
        RESOURCE_DIR = Path(sys._MEIPASS)
    elif (RUN_DIR / 'static').exists():
        RESOURCE_DIR = RUN_DIR
    elif (Path.cwd() / 'static').exists():
        RESOURCE_DIR = Path.cwd()
    else:
        RESOURCE_DIR = Path(sys._MEIPASS)
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    RUN_DIR = Path(__file__).parent.resolve()

# Roo/DAON React webview build.  The bundled server owns the frontend route so
# Electron, portable builds, and local development all use the same artifact.
# Keep the legacy root frontend as a fallback while the webview is absent.
WEBVIEW_DIR = RESOURCE_DIR / 'webview'
WEBVIEW_INDEX = WEBVIEW_DIR / 'index.html'

# PyInstaller bundle helper imports
import asyncio
import logging.handlers
import concurrent.futures
import base64
import tempfile
# Load profile/user environment from ~/.hermes/.env and project root .env
try:
    import dotenv
    from pathlib import Path
    _hermes_env = Path.home() / '.hermes' / '.env'
    if _hermes_env.exists():
        dotenv.load_dotenv(_hermes_env, override=True)
    _proj_env = Path(__file__).resolve().parent / '.env'
    if _proj_env.exists():
        dotenv.load_dotenv(_proj_env, override=False)
    _parent_env = Path(__file__).resolve().parent.parent / '.env'
    if _parent_env.exists():
        dotenv.load_dotenv(_parent_env, override=False)
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
            from api.routes import handle_get
            if handle_get(self, parsed):
                return
            self.send_error_json("Not found", 404)
        except Exception as e:
            traceback.print_exc()
            self.send_error_json("Internal server error", 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            from api.routes import handle_post
            if handle_post(self, parsed):
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
            self.send_header('Access-Control-Allow-Origin', '*')
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
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
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
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        # SSE는 단발성 응답 — done/error 후 연결을 즉시 닫아야 클라이언트
        # readline()이 EOF를 받고 루프를 탈출한다. keep-alive로 두면 서버가
        # 연결을 유지해 커넥터가 타임아웃(120s)까지 대기하는 원인이 된다.
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        self.close_connection = True

        # 최대 수명 방어선: 워커 크래시 등으로 done을 못 받는 스트림에서
        # 스레드가 영구 점유되는 누수를 막는다 (10분 후 강제 종료).
        _sse_started = time.time()
        while True:
            try:
                event, data = q.get(timeout=15)
            except queue.Empty:
                if time.time() - _sse_started > 600.0:
                    self.close_connection = True
                    break  # SSE max lifetime exceeded — free the thread
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
                self.close_connection = True  # 즉시 소켓 종료 → 클라이언트 EOF
                break

def _find_port_owner(port: int):
    """지정 포트에 LISTENING 중인 프로세스의 PID를 찾는다.

    psutil을 사용할 수 없거나 리스너가 없으면 None을 반환한다.
    (권한 부족 시 conn.pid가 None일 수 있음 — 이 경우에도 None 반환,
    실제 바인드 시도의 OSError 폴백이 최종 방어선 역할을 한다.)
    """
    try:
        import psutil
        for conn in psutil.net_connections(kind='tcp'):
            try:
                if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                    return conn.pid
            except Exception:
                continue
    except Exception:
        pass
    return None


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

    # ── [v5-fix] 포트 점유 사전 검사 ──
    # 기존 인스턴스(좀비 리스너)가 포트를 LISTENING 중이면 명확한 메시지와 함께
    # 즉시 종료한다. 이전처럼 조용히 겹쳐 바인드되는 것을 허용하지 않는다.
    _owner_pid = _find_port_owner(PORT)
    if _owner_pid is not None and _owner_pid != os.getpid():
        print(f"[ERROR] 포트 {PORT}는 이미 다른 프로세스(PID {_owner_pid})가 사용 중입니다.", flush=True)
        print("[ERROR] 기존 DAON 서버를 종료한 후 다시 실행하세요. (taskkill /PID " + str(_owner_pid) + " /F)", flush=True)
        sys.exit(1)

    try:
        server = RobustThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"[ERROR] 포트 {PORT} 바인딩 실패: {e}", flush=True)
        print(f"[ERROR] 이 포트에 다른 서버가 이미 실행 중일 수 있습니다. 'netstat -ano | findstr :{PORT}' 로 확인하세요.", flush=True)
        sys.exit(1)
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

        # Sync DAON_PLUGIN_SKILL_DIRS so Hermes skill tooling sees globally enabled
        # plugin skills right after server restart (idempotent; no-op when unchanged).
        try:
            from api.plugin_gateway import sync_plugin_skill_env
            sync_plugin_skill_env()
            print("[Plugins] Skill env synced on startup.", flush=True)
        except Exception as e:
            print(f"[Plugins] Startup skill env sync failed: {e}", flush=True)

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
