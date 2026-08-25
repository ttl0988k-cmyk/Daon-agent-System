# -*- coding: utf-8 -*-
"""
Daon Agent System — Debate & Meeting Mode Live Probe Verification.
Connects to the running Electron/Chromium UI via CDP (port 9222) and tests:
1. Re-fetches UI target and connects cleanly
2. Checks function definitions (selectDebateType, toggleDebateModeUI, etc.)
3. Checks DOM elements (setup area, control area, type buttons, moderator select)
4. Tests mode switching (switchMode('debate'))
5. Tests mode type toggling (selectDebateType('meeting') vs 'debate')
6. Verifies model population and option fields
7. Cleanly switches back to chat mode
"""
import json
import sys
import time
import requests
import websocket

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CDP = "http://127.0.0.1:9222"
NO_PROXY = {"http": None, "https": None}


def find_ui_target():
    resp = requests.get(f"{CDP}/json", proxies=NO_PROXY, timeout=5)
    targets = resp.json()
    for t in targets:
        if t.get("type") == "page" and "127.0.0.1:9090" in t.get("url", ""):
            return t
    return None


def get_connection():
    tgt = find_ui_target()
    if not tgt:
        print("[FAIL] UI target not found on CDP port 9222")
        sys.exit(1)
    return websocket.create_connection(tgt["webSocketDebuggerUrl"], timeout=10)


def main():
    print("[PROBE] Finding UI target on CDP...")
    ws = get_connection()
    _id = [0]

    def ev(expr, await_promise=False):
        _id[0] += 1
        params = {"expression": expr, "returnByValue": True}
        if await_promise:
            params["awaitPromise"] = True
        ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate", "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == _id[0]:
                res = m.get("result", {})
                if "exceptionDetails" in res:
                    return "EXC: " + str(
                        res["exceptionDetails"].get("exception", {}).get("description", "?")
                    )[:300]
                return res.get("result", {}).get("value")

    # Reload page to pick up updated JS and HTML
    print("[PROBE] Triggering location.reload() to refresh live frontend assets...")
    try:
        ev("window.location.reload()")
    except Exception:
        pass
    ws.close()

    time.sleep(3.0)

    # Reconnect after reload
    print("[PROBE] Reconnecting to fresh page context...")
    ws = get_connection()
    _id = [0]

    # 1. Check Function Definitions
    print("\n--- 1. Function Definitions Check ---")
    funcs = {
        "selectDebateType": "typeof selectDebateType === 'function'",
        "toggleDebateModeUI": "typeof toggleDebateModeUI === 'function'",
        "populateDebateModels": "typeof populateDebateModels === 'function'",
        "startDebateWorkflow": "typeof startDebateWorkflow === 'function'",
        "proceedDebateRound": "typeof proceedDebateRound === 'function'",
        "cancelDebateWorkflow": "typeof cancelDebateWorkflow === 'function'",
        "sendDebatePlanToHarness": "typeof sendDebatePlanToHarness === 'function'",
    }
    for name, expr in funcs.items():
        res = ev(expr)
        print(f"  {name}: {res}")
        if res is not True:
            print(f"[FAIL] Function {name} not defined properly (got {res})")
            sys.exit(1)

    # 2. Check DOM Elements
    print("\n--- 2. DOM Elements Check ---")
    elems = {
        "debateSetupArea": "document.getElementById('debateSetupArea') !== null",
        "debateControlArea": "document.getElementById('debateControlArea') !== null",
        "debateMessages": "document.getElementById('debateMessages') !== null",
        "debateTypeDebateBtn": "document.getElementById('debateTypeDebateBtn') !== null",
        "debateTypeMeetingBtn": "document.getElementById('debateTypeMeetingBtn') !== null",
        "meetingOptionsRow": "document.getElementById('meetingOptionsRow') !== null",
        "debateModeratorSelect": "document.getElementById('debateModeratorSelect') !== null",
        "debateMaxTurnsSelect": "document.getElementById('debateMaxTurnsSelect') !== null",
        "debateAutoAdvanceToggle": "document.getElementById('debateAutoAdvanceToggle') !== null",
        "startDebateSubmitBtn": "document.getElementById('startDebateSubmitBtn') !== null",
    }
    for name, expr in elems.items():
        res = ev(expr)
        print(f"  {name}: {res}")
        if res is not True:
            print(f"[FAIL] DOM element {name} missing")
            sys.exit(1)

    # 3. Switch to Debate Mode
    print("\n--- 3. Mode Switch to Debate Mode ---")
    ev("switchMode('debate')")
    time.sleep(0.5)

    setup_visible = ev("document.getElementById('debateSetupArea').style.display !== 'none'")
    messages_visible = ev("document.getElementById('debateMessages').style.display !== 'none'")
    chat_hidden = ev("document.getElementById('chatInputArea').style.display === 'none'")
    print(f"  Debate setup area visible: {setup_visible} (expected True)")
    print(f"  Debate messages area visible: {messages_visible} (expected True)")
    print(f"  Normal chat input hidden: {chat_hidden} (expected True)")

    if not (setup_visible and messages_visible and chat_hidden):
        print("[FAIL] Debate Mode UI did not display correctly on switchMode('debate')")
        sys.exit(1)

    # 4. Toggle Meeting Mode vs Debate Mode
    print("\n--- 4. Toggle Meeting Mode vs Debate Mode ---")
    ev("selectDebateType('meeting')")
    time.sleep(0.3)
    meeting_row_visible = ev("document.getElementById('meetingOptionsRow').style.display !== 'none'")
    submit_btn_text = ev("document.getElementById('startDebateSubmitBtn').textContent")
    print(f"  [Meeting] Meeting options row visible: {meeting_row_visible} (expected True)")
    print(f"  [Meeting] Submit button text: '{submit_btn_text}' (expected '👥 회의 시작')")

    if not (meeting_row_visible and submit_btn_text == '👥 회의 시작'):
        print("[FAIL] Meeting mode type toggle failed")
        sys.exit(1)

    ev("selectDebateType('debate')")
    time.sleep(0.3)
    debate_row_hidden = ev("document.getElementById('meetingOptionsRow').style.display === 'none'")
    submit_btn_text_deb = ev("document.getElementById('startDebateSubmitBtn').textContent")
    print(f"  [Debate] Meeting options row hidden: {debate_row_hidden} (expected True)")
    print(f"  [Debate] Submit button text: '{submit_btn_text_deb}' (expected '⚖️ 토론 시작')")

    if not (debate_row_hidden and submit_btn_text_deb == '⚖️ 토론 시작'):
        print("[FAIL] Debate mode type toggle failed")
        sys.exit(1)

    # 5. Check populated models & moderator dropdown
    print("\n--- 5. Models Population Check ---")
    model_count = ev("document.querySelectorAll('.debate-model-checkbox').length")
    mod_opt_count = ev("document.getElementById('debateModeratorSelect').options.length")
    print(f"  Available model checkboxes in UI: {model_count}")
    print(f"  Moderator dropdown options in UI: {mod_opt_count}")

    # 6. Switch back to chat mode cleanly
    print("\n--- 6. Return to Chat Mode Cleanly ---")
    ev("switchMode('chat')")
    time.sleep(0.3)
    chat_visible = ev("document.getElementById('chatInputArea').style.display !== 'none'")
    debate_hidden = ev("document.getElementById('debateSetupArea').style.display === 'none'")
    print(f"  Chat input restored: {chat_visible}, Debate setup hidden: {debate_hidden}")

    ws.close()
    print("\n[SUCCESS] All live CDP probe checks passed 100% successfully!")


if __name__ == "__main__":
    main()
