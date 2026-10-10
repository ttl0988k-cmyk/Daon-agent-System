"""
browser_bridge.py — Path A Integration Bridge

Maps hermes-agent's _run_browser_command(task_id, command, args, timeout)
calls to browser_routes._submit_task(action, **kwargs), eliminating the
agent-browser CLI (Path B) and using Playwright directly via CDP.

Usage (in api/streaming.py after AIAgent creation):
    from api.browser_bridge import patch_browser_tool
    patch_browser_tool()
"""

import logging
import os
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)


def _convert_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Convert _submit_task result to _run_browser_command compatible format.

    _submit_task returns:  {_result_id, status: "ok", ...} or {_result_id, error: "..."}
    _run_browser_command expects: {success: bool, data: {...}} or {success: bool, error: "..."}
    """
    if "error" in result:
        return {"success": False, "error": result["error"]}
    data = {k: v for k, v in result.items() if k not in ("_result_id", "status")}
    return {"success": True, "data": data}


def _run_browser_command_via_bridge(
    task_id: str,
    command: str,
    args: List[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Replacement for hermes-agent's _run_browser_command that uses Path A (Playwright).

    Maps agent-browser CLI commands to _submit_task actions in browser_routes.py.
    The timeout parameter is ignored (browser_routes has its own 35s timeout).
    """
    args = args or []

    # ── [DAON 8088 브라우저 서비스(Playwright Screencast) 직접 위임] ──
    try:
        from browser_service.manager import call_browser_api, ensure_service_running, is_service_running
        # 8088 브라우저 서비스가 항상 우선 기동되도록 보장
        if ensure_service_running():
            if command in ("open", "navigate"):
                url = args[0] if args else "about:blank"
                res = call_browser_api("navigate", {"url": url}, timeout=timeout or 25)
                if res.get("ok"):
                    return {"success": True, "data": {"url": res.get("url", url), "title": res.get("title", "")}}
                else:
                    return {"success": False, "error": res.get("error", "Navigation failed")}

            elif command == "snapshot":
                res = call_browser_api("snapshot", method="POST", timeout=timeout or 15)
                if res.get("ok"):
                    return {"success": True, "data": {
                        "url": res.get("url", ""),
                        "title": res.get("title", ""),
                        "snapshot": res.get("snapshot", ""),
                        "text": res.get("text", ""),
                        "elements": res.get("elements", []),
                        "refs": res.get("refs", {}),
                    }}
                else:
                    return {"success": False, "error": res.get("error", "Snapshot failed")}

            elif command == "click":
                ref = args[0].lstrip("@") if args else ""
                res = call_browser_api("click", {"selector": ref, "ref": ref}, method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"clicked": True, "ref": ref}}
                else:
                    return {"success": False, "error": res.get("error", "Click failed")}

            elif command == "fill":
                ref = args[0].lstrip("@") if args else ""
                text = args[1] if len(args) > 1 else ""
                res = call_browser_api("type", {"selector": ref, "ref": ref, "text": text}, method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"filled": True, "ref": ref}}
                else:
                    return {"success": False, "error": res.get("error", "Fill failed")}

            elif command == "scroll":
                direction = args[0] if args else "down"
                pixels = int(args[1]) if len(args) > 1 and str(args[1]).isdigit() else 500
                res = call_browser_api("scroll", {"direction": direction, "pixels": pixels}, method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"scrolled": True, "direction": direction, "pixels": pixels}}
                else:
                    return {"success": False, "error": res.get("error", "Scroll failed")}

            elif command == "back":
                res = call_browser_api("back", method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"url": res.get("url", ""), "title": res.get("title", "")}}
                else:
                    return {"success": False, "error": res.get("error", "Back failed")}

            elif command == "forward":
                res = call_browser_api("forward", method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"url": res.get("url", ""), "title": res.get("title", "")}}
                else:
                    return {"success": False, "error": res.get("error", "Forward failed")}

            elif command == "press":
                key = args[0] if args else "Enter"
                res = call_browser_api("press", {"key": key}, method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"pressed": key}}
                else:
                    return {"success": False, "error": res.get("error", "Press failed")}

            elif command == "screenshot":
                res = call_browser_api("screenshot", method="POST", timeout=timeout or 12)
                if res.get("ok"):
                    out_path = None
                    for _a in args:
                        if isinstance(_a, str) and _a.lower().endswith(".png"):
                            out_path = _a
                    image_b64 = res.get("image", "")
                    ret_data = {"image": image_b64, "image_base64": image_b64}
                    if out_path and image_b64:
                        import base64 as _b64
                        _dir = os.path.dirname(out_path)
                        if _dir:
                            os.makedirs(_dir, exist_ok=True)
                        with open(out_path, "wb") as _f:
                            _f.write(_b64.b64decode(image_b64))
                        ret_data["path"] = out_path
                    return {"success": True, "data": ret_data}
                else:
                    return {"success": False, "error": res.get("error", "Screenshot failed")}

            elif command == "eval":
                expression = args[0] if args else ""
                res = call_browser_api("eval", {"js": expression}, method="POST", timeout=timeout or 10)
                if res.get("ok"):
                    return {"success": True, "data": {"result": res.get("result")}}
                else:
                    return {"success": False, "error": res.get("error", "Eval failed")}

            elif command == "close":
                res = call_browser_api("session/close", method="POST", timeout=timeout or 10)
                return {"success": True, "data": {"closed": True}}
    except Exception as _e_bridge:
        _logger.warning("Browser bridge 8088 direct delegation error: %s", _e_bridge)

    from api.routes.browser_routes import _submit_task

    args = args or []

    # ── Command → Action mapping ──

    if command == "open":
        url = args[0] if args else "about:blank"
        result = _submit_task("open", session_id=task_id, url=url)
        return _convert_result(result)

    elif command == "snapshot":
        compact = "-c" in args
        result = _submit_task("snapshot", session_id=task_id, compact=compact)
        converted = _convert_result(result)
        if converted.get("success"):
            data = converted.get("data", {})
            # Convert elements list to refs dict (agent-browser format)
            elements = data.get("elements", [])
            if elements and not data.get("refs"):
                refs = {}
                for el in elements:
                    refs[el.get("ref", "")] = el
                data["refs"] = refs
            # Use inner_text as snapshot text if accessibility snapshot wasn't available
            if not data.get("snapshot") and data.get("text"):
                data["snapshot"] = data["text"]
        return converted

    elif command == "click":
        ref = args[0] if args else ""
        ref = ref.lstrip("@")  # strip agent-browser @ prefix
        result = _submit_task("click", session_id=task_id, ref=ref)
        return _convert_result(result)

    elif command == "fill":
        ref = args[0].lstrip("@") if args else ""
        text = args[1] if len(args) > 1 else ""
        result = _submit_task("fill", session_id=task_id, ref=ref, text=text)
        return _convert_result(result)

    elif command == "scroll":
        direction = args[0] if args else "down"
        pixels = args[1] if len(args) > 1 else "500"
        result = _submit_task("scroll", session_id=task_id, direction=direction, pixels=pixels)
        return _convert_result(result)

    elif command == "back":
        result = _submit_task("back", session_id=task_id)
        return _convert_result(result)

    elif command == "forward":
        result = _submit_task("forward", session_id=task_id)
        return _convert_result(result)

    elif command == "tabs":
        result = _submit_task("tabs", session_id=task_id)
        return _convert_result(result)

    elif command == "tab":
        # agent-browser 스타일: "tab select <index>" → 활성 탭 전환
        if args and args[0] == "select":
            try:
                idx = int(args[1]) if len(args) > 1 else 0
            except (ValueError, TypeError):
                return {"success": False, "error": f"Invalid tab index: {args[1] if len(args) > 1 else ''}"}
            result = _submit_task("switch_tab", session_id=task_id, index=idx)
            return _convert_result(result)
        result = _submit_task("tabs", session_id=task_id)
        return _convert_result(result)

    elif command == "switch_tab":
        # args: [index] 또는 [url] — 정수면 인덱스, 아니면 URL로 취급
        index = None
        url = ""
        if args:
            try:
                index = int(args[0])
            except (ValueError, TypeError):
                url = args[0]
        result = _submit_task("switch_tab", session_id=task_id, index=index, url=url)
        return _convert_result(result)

    elif command == "press":
        key = args[0] if args else ""
        result = _submit_task("press", session_id=task_id, key=key)
        return _convert_result(result)

    elif command == "console":
        clear = "--clear" in args
        result = _submit_task("console", session_id=task_id, clear=clear)
        return _convert_result(result)

    elif command == "errors":
        clear = "--clear" in args
        result = _submit_task("errors", session_id=task_id, clear=clear)
        return _convert_result(result)

    elif command == "eval":
        expression = args[0] if args else ""
        result = _submit_task("evaluate", session_id=task_id, expression=expression)
        return _convert_result(result)

    elif command == "record":
        subcommand = args[0] if args else "start"
        path = args[1] if len(args) > 1 else None
        result = _submit_task("record", session_id=task_id, subcommand=subcommand, path=path)
        return _convert_result(result)

    elif command == "screenshot":
        # agent-browser CLI는 Set-of-Marks 오버레이를 "--annotate"로 요청한다.
        # (과거 "--labeled"/-l"만 검사해 browser_vision의 annotate가 무시되던 버그)
        labeled = ("--labeled" in (args or []) or "-l" in (args or [])
                   or "--annotate" in (args or []))
        result = _submit_task("screenshot", session_id=task_id, labeled=labeled)
        converted = _convert_result(result)
        if converted.get("success"):
            # agent-browser CLI는 인자로 받은 경로에 파일을 쓴다. 여기서는 CDP가
            # base64를 돌려주므로, hermes browser_vision이 기대하는 "출력 경로 파일"을
            # 직접 만들어 준다. (경로 인자 없으면 기존처럼 base64만 반환)
            out_path = None
            for _a in (args or []):
                if isinstance(_a, str) and _a.lower().endswith(".png"):
                    out_path = _a
            if out_path:
                image_b64 = (converted.get("data", {}) or {}).get("image_base64", "")
                if not image_b64:
                    return {"success": False, "error": "Screenshot returned no image data"}
                import base64 as _b64
                import os as _os
                _dir = _os.path.dirname(out_path)
                if _dir:
                    _os.makedirs(_dir, exist_ok=True)
                with open(out_path, "wb") as _f:
                    _f.write(_b64.b64decode(image_b64))
                converted["data"]["path"] = out_path
        return converted

    elif command == "batch":
        import json as _json
        actions = []
        if args and isinstance(args[0], list):
            actions = args[0]
        elif args and isinstance(args[0], str):
            try:
                actions = _json.loads(args[0])
            except Exception:
                actions = []
        result = _submit_task("batch", session_id=task_id, actions=actions)
        return _convert_result(result)

    elif command == "close":
        # cleanup_browser가 세션 정리 시 호출 — Playwright 드라이버를 닫고
        # 상태를 리셋한다 (Electron 뷰 자체는 닫지 않는다).
        result = _submit_task("close", session_id=task_id)
        return _convert_result(result)

    else:
        return {"success": False, "error": f"Unsupported browser command: {command}"}


def patch_browser_tool():
    """Monkey-patch hermes-agent's _run_browser_command to use Path A (Playwright).

    Must be called AFTER AIAgent is created and hermes-agent modules are imported.
    Returns True on success, False on failure.
    """
    try:
        import tools.browser_tool as bt_module

        bt_module._run_browser_command = _run_browser_command_via_bridge
        bt_module._daon_electron_bridge_ready = True
        _logger.info(
            "browser_tool._run_browser_command patched → Path A (Playwright via CDP)"
        )
        return True
    except Exception as e:
        _logger.warning("Failed to patch browser_tool: %s", e)
        return False
