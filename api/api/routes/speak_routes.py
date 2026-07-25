"""
Hermes Web UI — Edge TTS voice synthesis route.

GET /api/speak/tts?text=... → audio/mpeg (Edge TTS SunHi Neural)
No API key required — uses Microsoft Edge TTS (free, Windows built-in).
"""

import subprocess
import logging

_logger = logging.getLogger(__name__)

# Edge TTS Korean voices (in priority order)
EDGE_TTS_VOICE = 'ko-KR-SunHiNeural'  # Natural female — best quality
# Fallback voices (if SunHi fails):
#   ko-KR-InJoonNeural          — Natural male
#   ko-KR-HyunsuMultilingualNeural — Multilingual male

TTS_TIMEOUT = 25  # seconds


def handle_tts(handler, parsed):
    """
    Synthesize Korean speech via Microsoft Edge TTS.
    Returns audio/mpeg binary.
    """
    from urllib.parse import parse_qs
    query = parse_qs(parsed.query) if parsed.query else {}

    text = query.get('text', [None])[0]
    if not text:
        handler.send_error_json("text parameter is required", 400)
        return True

    # Limit text length (Edge TTS has practical limits)
    text = text.strip()
    if len(text) > 2000:
        text = text[:1997] + '...'

    if not text:
        handler.send_error_json("text is empty after trimming", 400)
        return True

    _logger.info(f"[TTS] Synthesizing {len(text)} chars with voice {EDGE_TTS_VOICE}")

    try:
        # Run edge-tts: write mp3 to stdout via '-'
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
            _logger.error(f"[TTS] edge-tts failed (rc={proc.returncode}): {stderr[:500]}")
            handler.send_error_json(f"TTS synthesis failed: edge-tts error", 500)
            return True

        audio_data = proc.stdout
        if not audio_data or len(audio_data) < 100:
            _logger.error(f"[TTS] edge-tts produced empty/small output ({len(audio_data)} bytes)")
            handler.send_error_json("TTS produced no audio", 500)
            return True

        handler.send_response(200)
        handler.send_header('Content-Type', 'audio/mpeg')
        handler.send_header('Content-Length', str(len(audio_data)))
        handler.send_header('Cache-Control', 'public, max-age=3600')
        handler.send_header('Accept-Ranges', 'none')
        handler.end_headers()
        handler.wfile.write(audio_data)
        _logger.info(f"[TTS] Success: {len(audio_data)} bytes")

    except subprocess.TimeoutExpired:
        _logger.error(f"[TTS] edge-tts timed out after {TTS_TIMEOUT}s")
        handler.send_error_json("TTS synthesis timed out", 504)
    except FileNotFoundError:
        _logger.error("[TTS] edge-tts command not found — is edge-tts installed?")
        handler.send_error_json("edge-tts is not installed on this server", 500)
    except Exception as e:
        _logger.error(f"[TTS] Unexpected error: {e}")
        handler.send_error_json(f"TTS internal error", 500)

    return True
