"""
Daon Agent System — Integration routes (Slack & Notion).

Provides:
  POST /api/integration/slack/send       — send a message to Slack
  POST /api/integration/slack/test        — test Slack connection
  POST /api/integration/notion/create     — create a Notion page
  POST /api/integration/notion/test       — test Notion connection
  GET  /api/integration/config            — get integration configuration status
  POST /api/integration/config            — save integration configuration
"""
import json
import logging
import threading
from pathlib import Path
from urllib.parse import parse_qs

from api.helpers import require, bad, j
from api.models import get_session

_logger = logging.getLogger(__name__)

# ── Configuration persistence ──
_INTEGRATION_CONFIG_PATH = Path("data/integration_config.json")
_config_lock = threading.Lock()

_DEFAULT_CONFIG = {
    "slack": {
        "enabled": False,
        "webhook_url": "",
        "bot_token": "",
        "default_channel": "#general",
    },
    "notion": {
        "enabled": False,
        "token": "",
        "database_id": "",
        "default_status": "Completed",
    },
}


def _load_config() -> dict:
    """Load integration config from disk, merging with defaults."""
    with _config_lock:
        try:
            if _INTEGRATION_CONFIG_PATH.exists():
                raw = json.loads(_INTEGRATION_CONFIG_PATH.read_text(encoding="utf-8"))
                cfg = _DEFAULT_CONFIG.copy()
                cfg.update(raw)
                return cfg
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("Failed to load integration config: %s", e)
        return _DEFAULT_CONFIG.copy()


def _save_config(config: dict) -> None:
    """Persist integration config to disk."""
    with _config_lock:
        try:
            _INTEGRATION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _INTEGRATION_CONFIG_PATH.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _logger.info("Integration config saved")
        except OSError as e:
            _logger.error("Failed to save integration config: %s", e)


def _resolve_slack_creds(config: dict) -> tuple:
    """Resolve Slack credentials: config first, then env vars. Returns (webhook_url, bot_token)."""
    sc = config.get("slack", {})
    import os
    webhook = sc.get("webhook_url", "").strip() or os.getenv("SLACK_WEBHOOK_URL", "").strip()
    token = sc.get("bot_token", "").strip() or os.getenv("SLACK_BOT_TOKEN", "").strip()
    return webhook, token


def _resolve_notion_creds(config: dict) -> tuple:
    """Resolve Notion credentials: config first, then env vars. Returns (token, database_id)."""
    nc = config.get("notion", {})
    import os
    token = nc.get("token", "").strip() or os.getenv("NOTION_TOKEN", "").strip()
    db_id = nc.get("database_id", "").strip() or os.getenv("NOTION_DATABASE_ID", "").strip()
    return token, db_id


# ═══════════════════════════════════════════════════════════════════
# Slack helpers
# ═══════════════════════════════════════════════════════════════════

def _slack_via_webhook(webhook_url: str, text: str, channel: str = None) -> dict:
    """Send message via Slack Incoming Webhook."""
    import urllib.request

    payload = {"text": text}
    if channel:
        payload["channel"] = channel

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            if resp.status == 200:
                return {"success": True, "channel": channel or "default"}
            return {"success": False, "error": f"HTTP {resp.status}: {body[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _slack_via_api(token: str, text: str, channel: str) -> dict:
    """Send message via Slack Web API (chat.postMessage)."""
    import urllib.request

    payload = {
        "channel": channel,
        "text": text,
        "mrkdwn": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                return {"success": True, "channel": body.get("channel", channel), "ts": body.get("ts")}
            return {"success": False, "error": body.get("error", "unknown Slack API error")}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Notion helpers
# ═══════════════════════════════════════════════════════════════════

def _notion_create_page(token: str, database_id: str, title: str, content: str,
                        tags: list = None, status: str = None) -> dict:
    """Create a page in a Notion database."""
    import urllib.request

    # Build properties dict
    properties = {
        "Name": {  # Default title property name (case-sensitive, but most DBs use "Name" or "Title")
            "title": [{"text": {"content": title[:2000]}}]
        }
    }
    if tags:
        properties["Tags"] = {
            "multi_select": [{"name": t[:100]} for t in tags if t]
        }
    if status:
        properties["Status"] = {
            "status": {"name": status}
        }

    # Build children blocks from markdown content
    children = _markdown_to_notion_blocks(content)

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": children[:100],  # Notion API limit: 100 blocks per create
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            page_id = body.get("id", "")
            page_url = body.get("url", f"https://notion.so/{page_id}")
            return {"success": True, "page_id": page_id, "url": page_url}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else str(e)
        _logger.error("Notion API error: %s", err_body)
        return {"success": False, "error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _markdown_to_notion_blocks(md: str) -> list:
    """Convert a simple markdown string into Notion block objects."""
    blocks = []
    lines = md.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # Code block (```)
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            code_text = "\n".join(code_lines)[:2000]
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": code_text}}],
                    "language": lang or "plain text",
                }
            })
            continue

        # Heading 1 (# )
        if line.startswith("# "):
            text = line[2:]
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                }
            })
            i += 1
            continue

        # Heading 2 (## )
        if line.startswith("## "):
            text = line[3:]
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                }
            })
            i += 1
            continue

        # Heading 3 (### )
        if line.startswith("### "):
            text = line[4:]
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                }
            })
            i += 1
            continue

        # Bullet (- or *)
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            text = line.strip()[2:]
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                }
            })
            i += 1
            continue

        # Divider (--- or ***)
        if line.strip() in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # Regular paragraph - collect consecutive non-empty lines
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", "```", "- ", "* ")):
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            text = "\n".join(para_lines)[:2000]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                }
            })

    return blocks


# ═══════════════════════════════════════════════════════════════════
# GET /api/integration/config
# ═══════════════════════════════════════════════════════════════════

def handle_get_integration_config(handler, parsed) -> bool:
    """Return current integration config with masked credentials."""
    config = _load_config()
    slack_webhook, slack_token = _resolve_slack_creds(config)
    notion_token, notion_db = _resolve_notion_creds(config)

    return j(handler, {
        "slack": {
            "enabled": config["slack"]["enabled"],
            "configured": bool(slack_webhook or slack_token),
            "default_channel": config["slack"]["default_channel"],
            "webhook_set": bool(slack_webhook),
            "bot_token_set": bool(slack_token),
        },
        "notion": {
            "enabled": config["notion"]["enabled"],
            "configured": bool(notion_token and notion_db),
            "token_set": bool(notion_token),
            "database_set": bool(notion_db),
        },
    })


# ═══════════════════════════════════════════════════════════════════
# POST /api/integration/config
# ═══════════════════════════════════════════════════════════════════

def handle_post_integration_config(handler, body) -> bool:
    """Save integration configuration."""
    config = body.get("config")
    if not isinstance(config, dict):
        return bad(handler, "config object is required")

    saved = _load_config()

    if "slack" in config:
        sc = config["slack"]
        if isinstance(sc, dict):
            saved["slack"]["enabled"] = bool(sc.get("enabled", saved["slack"]["enabled"]))
            if "webhook_url" in sc:
                saved["slack"]["webhook_url"] = str(sc["webhook_url"]).strip()
            if "bot_token" in sc:
                saved["slack"]["bot_token"] = str(sc["bot_token"]).strip()
            if "default_channel" in sc:
                saved["slack"]["default_channel"] = str(sc["default_channel"]).strip() or "#general"

    if "notion" in config:
        nc = config["notion"]
        if isinstance(nc, dict):
            saved["notion"]["enabled"] = bool(nc.get("enabled", saved["notion"]["enabled"]))
            if "token" in nc:
                saved["notion"]["token"] = str(nc["token"]).strip()
            if "database_id" in nc:
                saved["notion"]["database_id"] = str(nc["database_id"]).strip()

    _save_config(saved)
    return j(handler, {"ok": True, "message": "Integration config saved"})


# ═══════════════════════════════════════════════════════════════════
# POST /api/integration/slack/send
# ═══════════════════════════════════════════════════════════════════

def handle_post_slack_send(handler, body) -> bool:
    """Send a message to Slack."""
    require(body, "text")

    text = str(body["text"])[:4000]
    config = _load_config()
    channel = str(body.get("channel") or config["slack"]["default_channel"] or "#general")
    webhook_url, bot_token = _resolve_slack_creds(config)

    if not webhook_url and not bot_token:
        return bad(handler,
                   "Slack is not configured. Set SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN env var, "
                   "or save config via POST /api/integration/config", 400)

    # Prefer bot token (more features), fall back to webhook
    if bot_token:
        result = _slack_via_api(bot_token, text, channel)
    else:
        result = _slack_via_webhook(webhook_url, text, channel)

    if result["success"]:
        return j(handler, result)
    return bad(handler, f"Slack send failed: {result.get('error', 'unknown')}")


# ═══════════════════════════════════════════════════════════════════
# POST /api/integration/slack/test
# ═══════════════════════════════════════════════════════════════════

def handle_post_slack_test(handler, body) -> bool:
    """Test Slack connection by sending a test message."""
    config = _load_config()
    webhook_url, bot_token = _resolve_slack_creds(config)
    channel = str(body.get("channel") or config["slack"]["default_channel"] or "#general")

    if not webhook_url and not bot_token:
        return bad(handler, "Slack is not configured")

    test_text = (
        "✅ *Daon Agent System — Slack 연동 테스트*\n"
        f"전송 시간: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"대상 채널: {channel}\n"
        "연동이 정상적으로 작동합니다."
    )

    if bot_token:
        result = _slack_via_api(bot_token, test_text, channel)
    else:
        result = _slack_via_webhook(webhook_url, test_text, channel)

    if result["success"]:
        return j(handler, {"ok": True, "message": "Slack test message sent successfully", "result": result})
    return bad(handler, f"Slack test failed: {result.get('error', 'unknown')}")


# ═══════════════════════════════════════════════════════════════════
# POST /api/integration/notion/create
# ═══════════════════════════════════════════════════════════════════

def handle_post_notion_create(handler, body) -> bool:
    """Create a page in Notion database."""
    require(body, "title")

    title = str(body["title"])[:2000]
    content = str(body.get("content") or body.get("text") or "")[:10000]
    tags = body.get("tags")
    if isinstance(tags, list):
        tags = [str(t)[:100] for t in tags]
    elif isinstance(tags, str):
        tags = [t.strip()[:100] for t in tags.split(",") if t.strip()]
    else:
        tags = None

    config = _load_config()
    token, database_id = _resolve_notion_creds(config)
    status = str(body.get("status") or config["notion"]["default_status"] or "Completed")

    if not token or not database_id:
        return bad(handler,
                   "Notion is not configured. Set NOTION_TOKEN and NOTION_DATABASE_ID env vars, "
                   "or save config via POST /api/integration/config", 400)

    result = _notion_create_page(token, database_id, title, content, tags=tags, status=status)

    if result["success"]:
        return j(handler, {"ok": True, "page": result})
    return bad(handler, f"Notion create failed: {result.get('error', 'unknown')}")


# ═══════════════════════════════════════════════════════════════════
# POST /api/integration/notion/test
# ═══════════════════════════════════════════════════════════════════

def handle_post_notion_test(handler, body) -> bool:
    """Test Notion connection by creating a test page."""
    config = _load_config()
    token, database_id = _resolve_notion_creds(config)

    if not token or not database_id:
        return bad(handler, "Notion is not configured")

    test_title = "🧪 Daon Agent System — Notion 연동 테스트"
    test_content = (
        "## 연동 테스트 결과\n\n"
        f"- **테스트 시간**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "- **상태**: ✅ 정상 연결\n"
        "- **대상 데이터베이스**: 연결됨\n\n"
        "이 페이지는 연동 테스트를 위해 자동 생성되었습니다."
    )
    result = _notion_create_page(token, database_id, test_title, test_content,
                                 tags=["Test", "Integration"])

    if result["success"]:
        return j(handler, {"ok": True, "message": "Notion test page created", "page": result})
    return bad(handler, f"Notion test failed: {result.get('error', 'unknown')}")
