"""
Hermes Web UI — Speech-to-text route.
POST /api/whisper/transcribe  (multipart: audio file → ASR → text)

Uses local faster-whisper (small) — offline, no API key needed.
"""
import os
import sys
import re
import json
import time
import tempfile
import threading
import email.parser
import urllib.request
import urllib.error
import logging

from api.helpers import j, bad

_logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25MB max audio upload

# cloud ASR providers (tried in order — empty = skip straight to local)
_ASR_PROVIDERS = []

# ── local faster-whisper (lazy-loaded or pre-warmed, GPU if available) ──
_local_whisper_model = None
_model_ready = False
_model_lock = threading.Lock()
_local_whisper_model_name = os.getenv('LOCAL_WHISPER_MODEL', 'small')
# 'auto' = try CUDA GPU first, fall back to CPU if CUDA libs missing
_local_whisper_device = os.getenv('LOCAL_WHISPER_DEVICE', 'auto')


def _register_nvidia_cuda_dlls():
    """Register nvidia-cublas and nvidia-cudnn DLL paths on Windows so CTranslate2 finds cublas64_12.dll."""
    if sys.platform == 'win32':
        exe_dir = os.path.dirname(sys.executable)
        user_site = r'C:\Users\ttl09\AppData\Local\Programs\Python\Python312\Lib\site-packages'
        search_dirs = [
            exe_dir,
            os.path.join(user_site, 'nvidia', 'cublas', 'bin'),
            os.path.join(user_site, 'nvidia', 'cudnn', 'bin'),
            os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', 'cublas', 'bin'),
            os.path.join(sys.prefix, 'Lib', 'site-packages', 'nvidia', 'cudnn', 'bin'),
        ]
        if hasattr(sys, '_MEIPASS'):
            search_dirs.extend([
                sys._MEIPASS,
                os.path.join(sys._MEIPASS, 'nvidia', 'cublas', 'bin'),
                os.path.join(sys._MEIPASS, 'nvidia', 'cudnn', 'bin'),
            ])

        for d in search_dirs:
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                    os.environ['PATH'] = d + os.path.pathsep + os.environ.get('PATH', '')
                except Exception:
                    pass

_register_nvidia_cuda_dlls()


def warmup_whisper_async():
    """Pre-load Whisper model in background at startup so first user click has zero load delay."""
    def _warmup():
        global _model_ready
        msg_start = "[Whisper Lifecycle] Whisper preload started"
        _logger.info(msg_start)
        print(msg_start, flush=True)
        try:
            model = _load_local_whisper()
            if model:
                _model_ready = True
                msg_end = "[Whisper Lifecycle] Whisper preload finished successfully"
                _logger.info(msg_end)
                print(msg_end, flush=True)
        except Exception as e:
            _logger.warning("[Whisper Lifecycle] Whisper background warmup failed: %s", e)
    threading.Thread(target=_warmup, name="whisper-warmup", daemon=True).start()


def _parse_multipart_audio(rfile, content_type, content_length):
    """Extract file (audio) and text fields from a multipart form-data request.

    Returns (filename, audio_bytes, fields_dict) or raises ValueError.
    """
    m = re.search(r'boundary=([^;\s]+)', content_type)
    if not m:
        raise ValueError('No boundary in Content-Type')
    boundary = m.group(1).strip('"').encode()
    raw = rfile.read(content_length)
    delimiter = b'--' + boundary
    parts = raw.split(delimiter)

    filename = None
    audio_bytes = None
    fields = {}

    for part in parts[1:]:
        stripped = part.lstrip(b'\r\n')
        if stripped.startswith(b'--'):
            break
        sep = b'\r\n\r\n' if b'\r\n\r\n' in part else b'\n\n'
        if sep not in part:
            continue
        header_raw, body = part.split(sep, 1)
        if body.endswith(b'\r\n'):
            body = body[:-2]
        elif body.endswith(b'\n'):
            body = body[:-1]
        header_text = header_raw.lstrip(b'\r\n').decode('utf-8', errors='replace')
        msg = email.parser.HeaderParser().parsestr(header_text)
        disp = msg.get('Content-Disposition', '')
        file_m = re.search(r'filename="([^"]*)"', disp)
        field_m = re.search(r'name="([^"]*)"', disp)

        if file_m:
            filename = file_m.group(1)
            audio_bytes = body
        elif field_m:
            fields[field_m.group(1)] = body.decode('utf-8', errors='replace')

    if audio_bytes is None:
        raise ValueError('No file found in multipart request')

    return filename, audio_bytes, fields


def _try_single_provider(api_key, api_url, provider_name, model, audio_bytes, filename, language=None):
    """Try a single ASR provider. Returns transcribed text or raises on failure."""
    boundary = '----DaonWhisperBoundary'
    body_parts = []

    body_parts.append(f'--{boundary}'.encode())
    body_parts.append(b'Content-Disposition: form-data; name="model"')
    body_parts.append(b'')
    body_parts.append(model.encode())

    if language:
        body_parts.append(f'--{boundary}'.encode())
        body_parts.append(b'Content-Disposition: form-data; name="language"')
        body_parts.append(b'')
        body_parts.append(language.encode())

    ext = os.path.splitext(filename)[1] or '.webm'
    content_type_map = {
        '.webm': 'audio/webm',
        '.mp3': 'audio/mpeg',
        '.mp4': 'audio/mp4',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
        '.wav': 'audio/wav',
        '.flac': 'audio/flac',
    }
    mime_type = content_type_map.get(ext.lower(), 'audio/webm')

    body_parts.append(f'--{boundary}'.encode())
    body_parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode())
    body_parts.append(f'Content-Type: {mime_type}'.encode())
    body_parts.append(b'')
    body_parts.append(audio_bytes)

    body_parts.append(f'--{boundary}--\r\n'.encode())
    body = b'\r\n'.join(body_parts)

    req = urllib.request.Request(
        api_url,
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
        method='POST',
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode('utf-8').strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                if 'text' in data and data['text']:
                    return data['text']
        except json.JSONDecodeError:
            pass
        return raw


def _call_transcribe_api(audio_bytes, filename, language=None, initial_prompt=None):
    """Call ASR API with failover sequence: cloud providers → local faster-whisper."""
    last_error = None

    for prov in _ASR_PROVIDERS:
        api_key = os.getenv(prov['env_key'], '')
        if not api_key:
            continue
        try:
            text = _try_single_provider(
                api_key, prov['url'], prov['name'], prov['model'],
                audio_bytes, filename, language
            )
            if text and text.strip():
                return text.strip()
        except Exception as e:
            last_error = e

    # Local faster-whisper fallback
    text = _transcribe_local(audio_bytes, language=language, initial_prompt=initial_prompt)
    return text


def _load_local_whisper():
    """Lazy-load the faster-whisper model (thread-safe singleton)."""
    global _local_whisper_model, _model_ready
    if _local_whisper_model is not None:
        return _local_whisper_model

    with _model_lock:
        if _local_whisper_model is not None:
            return _local_whisper_model

        _logger.info(
            'Loading local faster-whisper model: %s (device=%s)...',
            _local_whisper_model_name, _local_whisper_device,
        )
        try:
            from faster_whisper import WhisperModel

            if _local_whisper_device == 'cpu':
                _local_whisper_model = WhisperModel(
                    _local_whisper_model_name,
                    device='cpu',
                    compute_type='int8',
                )
            else:
                try:
                    _local_whisper_model = WhisperModel(
                        _local_whisper_model_name,
                        device=_local_whisper_device,
                        compute_type='auto',
                    )
                except Exception:
                    _logger.warning('GPU load failed (%s), falling back to CPU', _local_whisper_device)
                    _local_whisper_model = WhisperModel(
                        _local_whisper_model_name,
                        device='cpu',
                        compute_type='int8',
                    )
            _model_ready = True
            _logger.info('Local faster-whisper model loaded successfully')
            return _local_whisper_model
        except Exception:
            _logger.exception('Failed to load local faster-whisper model')
            _local_whisper_model = False
            return None


_cpu_fallback_whisper_model = None

def _get_cpu_fallback_model():
    """Singleton getter for CPU fallback model to prevent reloading model weights on every request."""
    global _cpu_fallback_whisper_model
    if _cpu_fallback_whisper_model is not None:
        return _cpu_fallback_whisper_model
    from faster_whisper import WhisperModel
    _cpu_fallback_whisper_model = WhisperModel(_local_whisper_model_name, device='cpu', compute_type='int8')
    return _cpu_fallback_whisper_model


def _transcribe_local(audio_bytes, language=None, initial_prompt=None):
    """Transcribe using local faster-whisper with milestone logging."""
    if not audio_bytes or len(audio_bytes) < 2000:
        return ""

    model = _load_local_whisper()
    if model is None or model is False:
        raise RuntimeError('Local faster-whisper model not available')

    print("[STT Pipeline] Decode start", flush=True)
    suffix = '.webm'
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(tmp_fd, audio_bytes)
        os.close(tmp_fd)
        print(f"[STT Pipeline] Decode end (bytes={len(audio_bytes)})", flush=True)

        lang = language or 'ko'
        t0 = time.perf_counter()
        used_device = _local_whisper_device

        print(f"[STT Pipeline] Whisper start (device={used_device}, prompt='{initial_prompt or ''}')", flush=True)
        try:
            segments, info = model.transcribe(
                tmp_path,
                language=lang,
                initial_prompt=initial_prompt,
                beam_size=1,
                vad_filter=False,
            )
            segments_list = list(segments)
        except Exception as gpu_err:
            err_str = str(gpu_err)
            if 'InvalidDataError' in err_str or 'Invalid data' in err_str or 'Invalid argument' in err_str:
                _logger.warning("Corrupted or incomplete audio chunk ignored: %s", err_str)
                return ""
            _logger.warning("Whisper inference failed on %s (%s), falling back to cached CPU int8...", _local_whisper_device, gpu_err)
            used_device = 'cpu-fallback'
            try:
                cpu_model = _get_cpu_fallback_model()
                segments, info = cpu_model.transcribe(
                    tmp_path,
                    language=lang,
                    initial_prompt=initial_prompt,
                    beam_size=1,
                    vad_filter=False,
                )
                segments_list = list(segments)
            except Exception as cpu_err:
                _logger.warning("CPU fallback also failed on audio chunk: %s", cpu_err)
                return ""

        t1 = time.perf_counter()
        inference_ms = (t1 - t0) * 1000.0

        text = ' '.join(seg.text.strip() for seg in segments_list)

        msg = f'[STT Pipeline] Whisper end (inference_ms={inference_ms:.1f}ms, audio_duration={info.duration:.2f}s, text="{text[:60]}")'
        _logger.info(msg)
        print(msg, flush=True)
        return text

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def handle_post_whisper_transcribe(handler, parsed):
    """POST /api/whisper/transcribe

    Accepts multipart/form-data with:
        audio: audio file (webm, mp3, wav, etc.)
        prompt/initial_prompt: optional context prompt from preceding transcript
        language: optional language code (e.g., 'ko', 'en', 'auto')

    Returns JSON: { text: "transcribed text" }
    """
    try:
        content_type = handler.headers.get('Content-Type', '')
        content_length = int(handler.headers.get('Content-Length', 0) or 0)

        print(f"[STT Pipeline] Request received (content_length={content_length})", flush=True)

        if not _model_ready and _local_whisper_model is None:
            print("[STT Pipeline] Response sent (503 Service Unavailable — Model initializing)", flush=True)
            return j(handler, {'status': 'loading', 'error': 'Whisper engine is initializing...'}, status=503)

        if content_length == 0:
            return bad(handler, 'Empty request body', 400)

        if content_length > MAX_AUDIO_BYTES:
            return bad(handler, f'Audio too large (max {MAX_AUDIO_BYTES // 1024 // 1024}MB)', 413)

        if 'multipart/form-data' not in content_type:
            return bad(handler, 'Expected multipart/form-data', 400)

        # Parse multipart (audio file + text fields)
        filename, audio_bytes, fields = _parse_multipart_audio(
            handler.rfile, content_type, content_length
        )

        if len(audio_bytes) < 100:
            return bad(handler, 'Audio file too small (likely empty)', 400)

        initial_prompt = fields.get('prompt') or fields.get('initial_prompt')

        # Call ASR API
        try:
            text = _call_transcribe_api(audio_bytes, filename, initial_prompt=initial_prompt)
        except Exception as e:
            return j(handler, {'error': str(e)}, status=502)

        print(f"[STT Pipeline] Response sent (200 OK, text_len={len(text.strip())})", flush=True)
        return j(handler, {'text': text.strip()})

    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        _logger.exception('Whisper transcription failed')
        return j(handler, {'error': f'Transcription failed: {str(e)}'}, status=500)
