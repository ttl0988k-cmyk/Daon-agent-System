"""
DAON TTS Server — Dedicated voice synthesis server (Supertonic 3 local primary, Edge TTS fallback).

Runs on a separate port (default 9091) so that long-running TTS synthesis
does not block the main agent server (9090) which handles CDP browser tools
and SSE streaming.

Usage:
    python tts_server.py --port 9091

GET /tts?text=...&voice=F1 → audio/mpeg (or audio/wav if ffmpeg missing)
GET /health → {"status": "ok", "service": "tts"}
"""

import io
import subprocess
import sys
import os
import json
import threading
import wave
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_SUPERTONIC_MODEL_DIR = r'C:\Users\ttl09\.cache\supertonic3'
_VALID_VOICES = ('F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5')
_DEFAULT_VOICE = 'F1'

# Edge TTS fallback voice
EDGE_TTS_VOICE = 'ko-KR-SunHiNeural'  # Natural female — best quality
TTS_TIMEOUT = 25  # seconds
TEXT_LIMIT = 2000

_tts = None
_tts_lock = threading.Lock()
_tts_failed = False


def _get_tts():
    global _tts, _tts_failed
    if _tts is not None:
        return _tts
    if _tts_failed:
        return None
    with _tts_lock:
        if _tts is not None:
            return _tts
        if _tts_failed:
            return None
        try:
            from supertonic import TTS
            _tts = TTS(model_dir=_SUPERTONIC_MODEL_DIR)
            print(f"[TTS-Server] Supertonic 3 resident model loaded", flush=True)
            return _tts
        except Exception as e:
            _tts_failed = True
            print(f"[TTS-Server] Supertonic load failed ({e}), Edge fallback only", flush=True)
            return None


# Pre-load at import so first request has zero load delay
_get_tts()


def _synth_supertonic(text, voice):
    import numpy as np
    tts = _get_tts()
    if tts is None:
        raise RuntimeError('Supertonic model unavailable')
    style = tts.get_voice_style(voice_name=voice)
    out = tts.synthesize(text, voice_style=style, lang='ko')
    wav = out[0] if isinstance(out, tuple) else out
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim == 2:
        wav = wav[0] if wav.shape[0] <= 2 else wav.flatten()
    try:
        proc = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error',
             '-f', 'f32le', '-ar', '44100', '-ac', '1', '-i', 'pipe:0',
             '-codec:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', 'pipe:1'],
            input=wav.tobytes(), capture_output=True, timeout=TTS_TIMEOUT,
        )
        if proc.returncode == 0 and proc.stdout and len(proc.stdout) > 100:
            return proc.stdout, 'audio/mpeg'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    pcm = (wav * 32767.0).clip(-32768, 32767).astype('<i2').tobytes()
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(pcm)
    return buf.getvalue(), 'audio/wav'


def _synth_edge(text):
    proc = subprocess.run(
        ['edge-tts', '--voice', EDGE_TTS_VOICE, '--text', text, '--write-media', '-'],
        capture_output=True, timeout=TTS_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"edge-tts rc={proc.returncode}")
    if not proc.stdout or len(proc.stdout) < 100:
        raise RuntimeError("edge-tts empty output")
    return proc.stdout


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

        voice = (query.get('voice', [_DEFAULT_VOICE])[0] or _DEFAULT_VOICE).strip().upper()
        if voice not in _VALID_VOICES:
            voice = _DEFAULT_VOICE

        text = text.strip()
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT - 3] + '...'

        if not text:
            self._send_json({'error': 'text is empty after trimming'}, 400)
            return

        # 1) Supertonic primary
        try:
            audio_data, content_type = _synth_supertonic(text, voice)
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(audio_data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(audio_data)
            return
        except Exception as e:
            print(f"[TTS-Server] Supertonic failed ({e}), Edge fallback...", flush=True)

        # 2) Edge fallback
        try:
            audio_data = _synth_edge(text)
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
            self._send_json({'error': 'Both TTS engines failed'}, 500)
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
