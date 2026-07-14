"""
Hermes Web UI — Speech-to-text route.
POST /api/whisper/transcribe  (multipart: audio file → ASR → text)

Priority:
  1. MiniMax ASR (MINIMAX_API_KEY) — OpenAI-compatible endpoint
  2. OpenAI Whisper (OPENAI_API_KEY) — fallback
"""
import os
import re
import json
import email.parser
import urllib.request
import urllib.error
import logging

from api.helpers import j, bad

_logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25MB max audio upload

# ASR providers (tried in order)
_ASR_PROVIDERS = [
    {
        'name': 'MiniMax ASR',
        'url': 'https://api.minimax.io/v1/audio/transcriptions',
        'env_key': 'MINIMAX_API_KEY',
        'model': 'speech-01',
    },
    {
        'name': 'OpenAI Whisper',
        'url': 'https://api.openai.com/v1/audio/transcriptions',
        'env_key': 'OPENAI_API_KEY',
        'model': 'whisper-1',
    },
]


def _resolve_asr_provider():
    """Return the first available ASR provider (api_key, url, name, model)."""
    for prov in _ASR_PROVIDERS:
        api_key = os.getenv(prov['env_key'], '')
        if api_key:
            _logger.info(f'Using ASR provider: {prov["name"]}')
            return api_key, prov['url'], prov['name'], prov['model']
    return None, None, None, None


def _parse_multipart_audio(rfile, content_type, content_length):
    """Extract the first file (audio) from a multipart form-data request.

    Returns (filename, bytes) or raises ValueError.
    """
    m = re.search(r'boundary=([^;\s]+)', content_type)
    if not m:
        raise ValueError('No boundary in Content-Type')
    boundary = m.group(1).strip('"').encode()
    raw = rfile.read(content_length)
    delimiter = b'--' + boundary
    parts = raw.split(delimiter)

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
        if file_m:
            return file_m.group(1), body

    raise ValueError('No file found in multipart request')


def _call_transcribe_api(audio_bytes, filename, language=None):
    """Send audio to an ASR API (MiniMax or OpenAI Whisper) and return transcribed text.

    Uses OpenAI-compatible multipart/form-data format.
    """
    api_key, api_url, provider_name, model = _resolve_asr_provider()
    if not api_key:
        raise RuntimeError(
            'No ASR API key configured. Set MINIMAX_API_KEY or OPENAI_API_KEY in .env'
        )

    # Build multipart body (OpenAI-compatible format)
    boundary = '----DaonWhisperBoundary'
    body_parts = []

    # Model parameter
    body_parts.append(f'--{boundary}'.encode())
    body_parts.append(b'Content-Disposition: form-data; name="model"')
    body_parts.append(b'')
    body_parts.append(model.encode())

    # Language parameter (optional)
    if language:
        body_parts.append(f'--{boundary}'.encode())
        body_parts.append(b'Content-Disposition: form-data; name="language"')
        body_parts.append(b'')
        body_parts.append(language.encode())

    # File
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

    # End boundary
    body_parts.append(f'--{boundary}--'.encode())

    body = b'\r\n'.join(body_parts)
    content_type = f'multipart/form-data; boundary={boundary}'

    req = urllib.request.Request(
        api_url,
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': content_type,
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8').strip()
            _logger.debug(f'{provider_name} raw response ({len(raw)} chars): {raw[:300]}')

            # Try JSON first (MiniMax returns {"text": "..."})
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    # MiniMax: {"text": "transcribed text"}
                    if 'text' in data and data['text']:
                        return data['text']
                    # OpenAI verbose_json: {"text": "..."}
                    # Some providers: {"transcription": "..."}
                    for key in ('transcription', 'result', 'data'):
                        val = data.get(key)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                        if isinstance(val, dict) and val.get('text'):
                            return val['text']
                _logger.warning(f'{provider_name} unexpected JSON structure: {list(data.keys()) if isinstance(data, dict) else type(data)}')
                # Fall through to return raw
            except json.JSONDecodeError:
                pass

            # Plain text response (OpenAI Whisper default)
            return raw
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        _logger.error(f'{provider_name} HTTP {e.code}: {error_body}')
        raise RuntimeError(f'{provider_name} error ({e.code}): {error_body[:500]}')
    except urllib.error.URLError as e:
        _logger.error(f'{provider_name} connection error: {e.reason}')
        raise RuntimeError(f'{provider_name} unreachable: {e.reason}')


def handle_post_whisper_transcribe(handler, parsed):
    """POST /api/whisper/transcribe

    Accepts multipart/form-data with:
        audio: audio file (webm, mp3, wav, etc.)
        language: optional language code (e.g., 'ko', 'en', 'auto')

    Returns JSON: { text: "transcribed text" }
    """
    try:
        content_type = handler.headers.get('Content-Type', '')
        content_length = int(handler.headers.get('Content-Length', 0) or 0)

        if content_length == 0:
            return bad(handler, 'Empty request body', 400)

        if content_length > MAX_AUDIO_BYTES:
            return bad(handler, f'Audio too large (max {MAX_AUDIO_BYTES // 1024 // 1024}MB)', 413)

        if 'multipart/form-data' not in content_type:
            return bad(handler, 'Expected multipart/form-data', 400)

        # Parse multipart
        filename, audio_bytes = _parse_multipart_audio(
            handler.rfile, content_type, content_length
        )

        if len(audio_bytes) < 100:
            return bad(handler, 'Audio file too small (likely empty)', 400)

        # Call ASR API (MiniMax or OpenAI Whisper)
        try:
            text = _call_transcribe_api(audio_bytes, filename)
        except RuntimeError as e:
            return j(handler, {'error': str(e)}, status=502)

        return j(handler, {'text': text.strip()})

    except ValueError as e:
        return bad(handler, str(e), 400)
    except Exception as e:
        _logger.exception('Whisper transcription failed')
        return j(handler, {'error': f'Transcription failed: {str(e)}'}, status=500)
