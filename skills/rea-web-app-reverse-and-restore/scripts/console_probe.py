"""CDP 진단기 — 복원 페이지의 실제 상태와 JS 예외를 수집한다.

browser_console / REA inspect 모두 페이지 내부 상태를 깊게 못 본다.
이 스크립트는 Runtime/Log/Page/Network 도메인을 켜고 리로드해서
canvas 개수·WebGL2 지원·던져진 예외를 직접 잡는다.

렌더 성공 판정 기준:
  - title이 대상 페이지여야 함 (엉뚱하면 옛 Chrome이 포트 점유 중)
  - canvasCount > 0, canvasSize가 대상 해상도와 일치
  - EVENTS에 EXCEPTION 0건

사용:
  python console_probe.py 9335
"""
import json
import sys
import time
import urllib.request

import websocket

EVENT_WINDOW_SEC = 7.0


def page_ws_url(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        targets = json.load(resp)
    for target in targets:
        if target.get("type") == "page":
            return target["webSocketDebuggerUrl"]
    raise RuntimeError(f"포트 {port}에서 페이지 타깃을 찾지 못했습니다.")


def probe_state(ws) -> dict:
    """현재 페이지의 DOM/WebGL 상태를 평가한다."""
    expression = """
    (() => {
      const canvas = document.querySelector('canvas');
      let webgl2 = false, glError = null;
      try {
        const c = document.createElement('canvas');
        webgl2 = !!c.getContext('webgl2');
      } catch (e) { glError = String(e); }
      return JSON.stringify({
        title: document.title,
        readyState: document.readyState,
        canvasCount: document.querySelectorAll('canvas').length,
        canvasSize: canvas ? [canvas.width, canvas.height] : null,
        webgl2Supported: webgl2,
        webgl2Error: glError,
        scriptCount: document.querySelectorAll('script').length,
        containerChildren: (document.getElementById('container')||{}).childElementCount ?? null
      });
    })()
    """
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            return json.loads(msg["result"]["result"]["value"])


def arm_domains(ws) -> None:
    for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
        ws.send(json.dumps({"id": None, "method": method}))


def reload_and_collect(ws, seconds: float) -> list:
    ws.send(json.dumps({"id": 2, "method": "Page.reload", "params": {"ignoreCache": True}}))
    deadline = time.time() + seconds
    ws.settimeout(1.0)
    events = []
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        msg = json.loads(raw)
        method = msg.get("method", "")
        if method == "Runtime.exceptionThrown":
            detail = msg["params"]["exceptionDetails"]
            events.append(("EXCEPTION", detail.get("text", "") + " | " +
                           str(detail.get("exception", {}).get("description", ""))[:300]))
        elif method == "Runtime.consoleAPICalled":
            args = msg["params"].get("args", [])
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
            events.append((msg["params"]["type"].upper(), text[:200]))
        elif method == "Log.entryAdded":
            entry = msg["params"]["entry"]
            events.append(("LOG:" + entry.get("level", "?"), entry.get("text", "")[:200]))
        elif method == "Network.loadingFailed":
            events.append(("NET_FAIL", str(msg["params"])[:200]))
    return events


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9333
    ws = websocket.create_connection(page_ws_url(port), timeout=10)
    print("=== BEFORE RELOAD ===")
    print(json.dumps(probe_state(ws), ensure_ascii=False, indent=2))

    arm_domains(ws)
    events = reload_and_collect(ws, EVENT_WINDOW_SEC)

    print(f"\n=== AFTER RELOAD ({EVENT_WINDOW_SEC}s) ===")
    print(json.dumps(probe_state(ws), ensure_ascii=False, indent=2))
    print(f"\n=== EVENTS ({len(events)}) ===")
    for kind, text in events:
        print(f"  [{kind}] {text}")
    ws.close()


if __name__ == "__main__":
    main()
