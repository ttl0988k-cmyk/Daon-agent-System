"""
Hermes Web UI — voice synthesis route (Supertonic 3 local primary, Edge TTS fallback).

GET /api/speak/tts?text=...&voice=F1 → audio/mpeg (or audio/wav if ffmpeg missing)
- Primary: local Supertonic 3 ONNX (offline, free, 10 voice styles F1~F5/M1~M5).
  Model is loaded ONCE at server startup (resident) — never reloaded per request.
- Fallback: Microsoft Edge TTS (free, online). Used only if Supertonic fails.
  NOTE 2026-10-13: Edge TTS currently 403-blocked by MS servers; fallback may fail too.
"""

import io
import subprocess
import logging
import threading
import urllib.request
import urllib.error

_logger = logging.getLogger(__name__)

# Supertonic 3 local model (resident singleton)
_SUPERTONIC_MODEL_DIR = r'C:\Users\ttl09\.cache\supertonic3'
_supertonic_tts = None
_supertonic_lock = threading.Lock()
_supertonic_failed = False  # latched True if load fails, to skip retry per request

# Voice allowlist (Supertonic style names). Default F1 (female).
_VALID_VOICES = ('F1', 'F2', 'F3', 'F4', 'F5', 'M1', 'M2', 'M3', 'M4', 'M5')
_DEFAULT_VOICE = 'F1'

# Edge TTS fallback voices
EDGE_TTS_VOICE = 'ko-KR-SunHiNeural'  # Natural female — best quality
# Fallback voices (if SunHi fails):
#   ko-KR-InJoonNeural          — Natural male
#   ko-KR-HyunsuMultilingualNeural — Multilingual male

TTS_TIMEOUT = 25  # seconds (Edge fallback + ffmpeg encode budget)
SUPERTONIC_TEXT_LIMIT = 2000


def _get_supertonic():
    """Return resident Supertonic TTS instance, loading once. None on failure."""
    global _supertonic_tts, _supertonic_failed
    if _supertonic_tts is not None:
        return _supertonic_tts
    if _supertonic_failed:
        return None
    with _supertonic_lock:
        if _supertonic_tts is not None:
            return _supertonic_tts
        if _supertonic_failed:
            return None
        try:
            from supertonic import TTS
            _supertonic_tts = TTS(model_dir=_SUPERTONIC_MODEL_DIR)
            _logger.info("[TTS] Supertonic 3 resident model loaded from %s", _SUPERTONIC_MODEL_DIR)
            return _supertonic_tts
        except Exception as e:
            _supertonic_failed = True
            _logger.error("[TTS] Supertonic load failed, will use Edge fallback: %s", e)
            return None


def warmup_supertonic_async():
    """Pre-load Supertonic model in background at startup (zero load delay on first use)."""
    def _warmup():
        tts = _get_supertonic()
        if tts:
            _logger.info("[TTS] Supertonic preload finished successfully")
            print("[TTS] Supertonic preload finished successfully", flush=True)
        else:
            _logger.warning("[TTS] Supertonic preload failed — Edge fallback only")
    th = threading.Thread(target=_warmup, name="supertonic-warmup", daemon=True)
    th.start()


_TTS_SERVER_URL = 'http://127.0.0.1:9091'


def _synth_via_9091(text, voice):
    """9091 전용 TTS 서버(시스템 파이썬 supertonic 상주)로 HTTP 위임.

    server.exe(PyInstaller 번들)에는 supertonic/onnxruntime 모듈이 없어서
    `from supertonic import TTS`가 실패한다. 대신 ServerSupervisor.js가 시스템
    파이썬(Python312)으로 띄운 9091 tts_server.py 에 위임해 supertonic 을 쓴다.
    Returns (audio_bytes, content_type) or raises.
    """
    import urllib.parse
    url = f'{_TTS_SERVER_URL}/tts?{urllib.parse.urlencode({"text": text, "voice": voice})}'
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, timeout=TTS_TIMEOUT) as resp:
        data = resp.read()
        ctype = resp.headers.get('Content-Type', 'audio/mpeg')
        if resp.status != 200:
            raise RuntimeError(f'9091 TTS HTTP {resp.status}: {data[:200]!r}')
        if not data or len(data) < 100:
            raise RuntimeError(f'9091 TTS empty output ({len(data)} bytes)')
        if 'json' in ctype:
            raise RuntimeError(f'9091 TTS error: {data[:200]!r}')
        return data, ctype


def _synth_supertonic(text, voice):
    """Synthesize via resident Supertonic 3. Returns (audio_bytes, content_type) or raises."""
    # 1) 9091 전용 서버 우선 (server.exe 번들에 supertonic 없으므로 위임이 핵심 경로)
    try:
        return _synth_via_9091(text, voice)
    except Exception as e:
        _logger.warning("[TTS] 9091 위임 실패 (%s), 번들 내 supertonic 직접 로드 시도", e)

    # 2) 번들 내 supertonic 직접 로드 (server.exe 리빌드 시 supertonic 포함된 경우)
    import numpy as np
    tts = _get_supertonic()
    if tts is None:
        raise RuntimeError('Supertonic model unavailable (9091 down + not bundled)')
    style = tts.get_voice_style(voice_name=voice)
    out = tts.synthesize(text, voice_style=style, lang='ko')
    wav = out[0] if isinstance(out, tuple) else out
    wav = np.asarray(wav, dtype=np.float32)
    if wav.ndim == 2:
        wav = wav[0] if wav.shape[0] <= 2 else wav.flatten()
    # Try mp3 encode via ffmpeg (44.1kHz mono float32 raw in)
    try:
        proc = subprocess.run(
            ['ffmpeg', '-y', '-v', 'error',
             '-f', 'f32le', '-ar', '44100', '-ac', '1', '-i', 'pipe:0',
             '-codec:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', 'pipe:1'],
            input=wav.tobytes(), capture_output=True, timeout=TTS_TIMEOUT,
        )
        if proc.returncode == 0 and proc.stdout and len(proc.stdout) > 100:
            return proc.stdout, 'audio/mpeg'
        _logger.warning("[TTS] ffmpeg mp3 encode failed, falling back to wav")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _logger.warning("[TTS] ffmpeg unavailable (%s), falling back to wav", e)
    # WAV fallback (44.1kHz 16-bit mono)
    import wave
    pcm = (wav * 32767.0).clip(-32768, 32767).astype('<i2').tobytes()
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(pcm)
    return buf.getvalue(), 'audio/wav'


def _synth_edge(text):
    """Synthesize via Edge TTS CLI. Returns mp3 bytes or raises."""
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
        raise RuntimeError(f"edge-tts failed (rc={proc.returncode}): {stderr[:300]}")
    if not proc.stdout or len(proc.stdout) < 100:
        raise RuntimeError(f"edge-tts produced empty output ({len(proc.stdout)} bytes)")
    return proc.stdout


def handle_tts(handler, parsed):
    """
    Synthesize Korean speech: Supertonic 3 local first, Edge TTS fallback.
    Query: text (required), voice (optional, F1~F5/M1~M5, default F1).
    Returns audio binary (audio/mpeg normally, audio/wav if ffmpeg missing).
    """
    from urllib.parse import parse_qs
    query = parse_qs(parsed.query) if parsed.query else {}

    text = query.get('text', [None])[0]
    if not text:
        handler.send_error_json("text parameter is required", 400)
        return True

    voice = (query.get('voice', [_DEFAULT_VOICE])[0] or _DEFAULT_VOICE).strip().upper()
    if voice not in _VALID_VOICES:
        voice = _DEFAULT_VOICE

    text = text.strip()
    if len(text) > SUPERTONIC_TEXT_LIMIT:
        text = text[:SUPERTONIC_TEXT_LIMIT - 3] + '...'

    if not text:
        handler.send_error_json("text is empty after trimming", 400)
        return True

    _logger.info(f"[TTS] Synthesizing {len(text)} chars (supertonic voice={voice})")

    # 1) Primary: Supertonic 3 local
    try:
        audio_data, content_type = _synth_supertonic(text, voice)
        handler.send_response(200)
        handler.send_header('Content-Type', content_type)
        handler.send_header('Content-Length', str(len(audio_data)))
        handler.send_header('Cache-Control', 'public, max-age=3600')
        handler.send_header('Accept-Ranges', 'none')
        handler.end_headers()
        handler.wfile.write(audio_data)
        _logger.info(f"[TTS] Supertonic success: {len(audio_data)} bytes ({content_type})")
        return True
    except Exception as e:
        _logger.warning(f"[TTS] Supertonic failed ({e}), trying Edge fallback...")

    # 2) Fallback: Edge TTS
    try:
        audio_data = _synth_edge(text)
        handler.send_response(200)
        handler.send_header('Content-Type', 'audio/mpeg')
        handler.send_header('Content-Length', str(len(audio_data)))
        handler.send_header('Cache-Control', 'public, max-age=3600')
        handler.send_header('Accept-Ranges', 'none')
        handler.end_headers()
        handler.wfile.write(audio_data)
        _logger.info(f"[TTS] Edge fallback success: {len(audio_data)} bytes")
    except subprocess.TimeoutExpired:
        _logger.error(f"[TTS] edge-tts timed out after {TTS_TIMEOUT}s")
        handler.send_error_json("TTS synthesis timed out", 504)
    except FileNotFoundError:
        _logger.error("[TTS] edge-tts command not found — is edge-tts installed?")
        handler.send_error_json("Both TTS engines failed (supertonic error + edge-tts missing)", 500)
    except Exception as e:
        _logger.error(f"[TTS] All engines failed: {e}")
        handler.send_error_json("TTS synthesis failed (local + edge)", 500)

    return True
