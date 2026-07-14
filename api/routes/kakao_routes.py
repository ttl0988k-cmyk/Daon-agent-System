"""
KakaoTalk bridge routes — web UI → KakaoTalk MemoChat delivery.
"""
from __future__ import annotations

import json


def handle_post_kakao_send(handler, body: dict) -> bool:
    """POST /api/kakao/send — send a message to KakaoTalk "나와의 채팅".

    Body: {"message": "text to send"}
    """
    message = (body or {}).get("message", "")
    if not message or not str(message).strip():
        handler.send_json({"ok": False, "error": "message is required"}, status=400)
        return True

    from api.kakao_bridge import send_message
    result = send_message(str(message).strip())

    if result.get("ok"):
        handler.send_json(result)
    else:
        handler.send_json(result, status=502)
    return True


def handle_get_kakao_status(handler, parsed) -> bool:
    """GET /api/kakao/status — check Kakao bridge availability."""
    try:
        from api.kakao_bridge import _get_gateway_token_and_url
        token, url = _get_gateway_token_and_url()
        has_token = bool(token)
    except Exception:
        has_token = False

    handler.send_json({
        "ok": True,
        "configured": has_token,
        "tool": "KakaotalkChat-MemoChat",
        "note": "Send-only bridge (Web UI → KakaoTalk). Message reading requires KakaoTalk PC local DB access.",
    })
    return True
