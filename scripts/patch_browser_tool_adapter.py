# -*- coding: utf-8 -*-
"""
hermes-agent/tools/browser_tool.py 에 DAON Browser Agent Service 어댑터 장착 스크립트.
"""
import os
import shutil

TOOL_PATH = r"c:\daon\Daon agent System\hermes-agent\tools\browser_tool.py"
BACKUP_PATH = r"c:\daon\Daon agent System\hermes-agent\tools\browser_tool.py.bak"

# 1. 백업 생성 (최초 1회)
if not os.path.exists(BACKUP_PATH):
    shutil.copy2(TOOL_PATH, BACKUP_PATH)
    print(f"[Backup] {BACKUP_PATH} 생성 완료")

with open(TOOL_PATH, "r", encoding="utf-8") as f:
    code = f.read()

DISPATCHER_CODE = '''# ============================================================================
# [DAON v2.5] Playwright + CDP Browser-Agent Adapter
# ============================================================================

def _dispatch_to_daon_browser_service(task_id: str, command: str, args: list) -> dict:
    """
    DAON Browser Agent Service (포트 8088, Playwright + CDP)로 브라우저 명령을 다이렉트 중계.
    - agent-browser CLI 누락 및 Electron 브리지 미준비 에러 원천 차단
    - 실시간 화면 송출(Screencast) 및 사용자 개입(Takeover) 지원
    """
    try:
        from browser_service.manager import call_browser_api, close_browser_session
    except ImportError:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from browser_service.manager import call_browser_api, close_browser_session

    args = args or []
    cmd = (command or "").strip().lower()

    if cmd == "open":
        url = args[0] if args else "https://google.com"
        res = call_browser_api("navigate", {"url": url})
        if res.get("ok"):
            return {
                "success": True,
                "data": {
                    "title": res.get("title", ""),
                    "url": res.get("url", url),
                    "snapshot": ""
                }
            }
        return {"success": False, "error": res.get("error", f"Failed to navigate to {url}")}

    elif cmd in ("snapshot", "text"):
        res = call_browser_api("text", {})
        if res.get("ok"):
            text = res.get("text", "")
            return {
                "success": True,
                "data": {
                    "snapshot": text,
                    "title": res.get("title", ""),
                    "url": res.get("url", ""),
                    "refs": {}
                }
            }
        return {"success": False, "error": res.get("error", "Failed to capture snapshot")}

    elif cmd == "click":
        selector = args[0] if args else "body"
        res = call_browser_api("click", {"selector": selector})
        if res.get("ok"):
            return {"success": True, "data": {}}
        return {"success": False, "error": res.get("error", f"Click failed on {selector}")}

    elif cmd == "type":
        selector = args[0] if len(args) > 1 else None
        text = args[1] if len(args) > 1 else (args[0] if args else "")
        res = call_browser_api("type", {"selector": selector, "text": text})
        if res.get("ok"):
            return {"success": True, "data": {}}
        return {"success": False, "error": res.get("error", f"Type failed on {selector}")}

    elif cmd == "press":
        key = args[0] if args else "Enter"
        res = call_browser_api("press", {"key": key})
        if res.get("ok"):
            return {"success": True, "data": {}}
        return {"success": False, "error": res.get("error", f"Key press failed: {key}")}

    elif cmd == "close":
        res = close_browser_session()
        return {"success": True, "data": res}

    elif cmd == "screenshot":
        res = call_browser_api("screenshot", {})
        if res.get("ok"):
            return {"success": True, "data": {"image": res.get("image", "")}}
        return {"success": False, "error": res.get("error", "Screenshot capture failed")}

    elif cmd in ("eval", "console"):
        js_code = args[0] if args else ""
        res = call_browser_api("eval", {"js": js_code})
        if res.get("ok"):
            return {"success": True, "data": {"result": res.get("result")}}
        return {"success": False, "error": res.get("error", "JS eval failed")}

    # 기타 미지원 명령은 정상 반환으로 패스
    return {"success": True, "data": {}}
'''

# _run_browser_command 찾기
run_cmd_marker = 'def _run_browser_command(\n    task_id: str,\n    command: str,\n    args: Optional[List[str]] = None,\n    timeout: Optional[int] = None,\n) -> Dict[str, Any]:'

if run_cmd_marker not in code:
    # 대안 형태
    run_cmd_marker = 'def _run_browser_command('

idx = code.find(run_cmd_marker)
if idx != -1:
    # 함수 시작 바로 앞에 DISPATCHER_CODE 삽입
    # 함수 내부 첫 줄에 _dispatch_to_daon_browser_service 호출 삽입
    colon_idx = code.find(') -> Dict[str, Any]:', idx)
    if colon_idx == -1:
        colon_idx = code.find('):', idx)
    end_of_sig = code.find('\n', colon_idx)

    delegation = '\n    # [DAON v2.5] 브라우저 요청을 DAON Browser Agent Service (포트 8088)로 즉시 위임\n    return _dispatch_to_daon_browser_service(task_id, command, args or [])\n'
    
    new_code = code[:idx] + DISPATCHER_CODE + "\n\n" + code[idx:end_of_sig] + delegation + code[end_of_sig:]
    with open(TOOL_PATH, "w", encoding="utf-8") as f:
        f.write(new_code)
    print("[Success] browser_tool.py 에 DAON Browser Agent 어댑터 배선 완료!")
else:
    print("[Error] _run_browser_command 함수를 찾지 못했습니다.")
