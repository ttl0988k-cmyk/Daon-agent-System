"""
DAON TTS Server — Dedicated Edge TTS voice synthesis server.

Runs on a separate port (default 9091) so that long-running TTS synthesis
does not block the main agent server (9090) which handles CDP browser tools
and SSE streaming.

Usage:
    python tts_server.py --port 9091
"""

import subprocess
import sys
import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Edge TTS Korean voices (in priority order)
EDGE_TTS_VOICE = 'ko-KR-SunHiNeural'  # Natural female — best quality
TTS_TIMEOUT = 25  # seconds


class TTSHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — only serves TTS requests."""

    def log_message(self, format, *args):
        """Suppress default access log noise."""
        pass

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/tts':
            self._handle_tts(parsed)
        elif parsed.path == '/health':
            self._send_json({'status': 'ok', 'service': 'tts'})
        else:
            self.send_error(404)

    def _handle_tts(self, parsed):
        query = parse_qs(parsed.query) if parsed.query else {}
        text = query.get('text', [None])[0]

        if not text:
            self._send_json({'error': 'text parameter is required'}, 400)
            return

        text = text.strip()
        if len(text) > 2000:
            text = text[:1997] + '...'

        if not text:
            self._send_json({'error': 'text is empty after trimming'}, 400)
            return

        try:
            proc = subprocess.run(
                [
                    'edge-tts',
                    '--voice', EDGE_TTS_VOICE,
                    '--text', text,
                    '--write-media', '-',
                ],
                capture_output=True,
                timeout=TTS_TIMEOUT,
            )

            if proc.returncode != 0:
                stderr = proc.stderr.decode('utf-8', errors='replace')
                print(f"[TTS-Server] edge-tts failed (rc={proc.returncode}): {stderr[:300]}", flush=True)
                self._send_json({'error': 'TTS synthesis failed'}, 500)
                return

            audio_data = proc.stdout
            if not audio_data or len(audio_data) < 100:
                print(f"[TTS-Server] edge-tts produced empty output ({len(audio_data)} bytes)", flush=True)
                self._send_json({'error': 'TTS produced no audio'}, 500)
                return

            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg')
            self.send_header('Content-Length', str(len(audio_data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(audio_data)

        except subprocess.TimeoutExpired:
            print(f"[TTS-Server] edge-tts timed out after {TTS_TIMEOUT}s", flush=True)
            self._send_json({'error': 'TTS synthesis timed out'}, 504)
        except FileNotFoundError:
            print("[TTS-Server] edge-tts command not found", flush=True)
            self._send_json({'error': 'edge-tts is not installed'}, 500)
        except Exception as e:
            print(f"[TTS-Server] Unexpected error: {e}", flush=True)
            self._send_json({'error': 'TTS internal error'}, 500)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='DAON TTS Server')
    parser.add_argument('--port', type=int, default=9091, help='TTS server port (default: 9091)')
    args = parser.parse_args()
    run_tts_server(args.port)


def run_tts_server(port=9091):
    """Start the TTS HTTP server — callable from server.py --tts-mode as well."""
    server = HTTPServer(('127.0.0.1', port), TTSHandler)
    print(f"[TTS-Server] Running on http://127.0.0.1:{port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[TTS-Server] Shutting down...", flush=True)
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
