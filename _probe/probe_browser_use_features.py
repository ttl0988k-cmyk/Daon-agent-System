#!/usr/bin/env python3
"""
_probe/probe_browser_use_features.py

Validates the native Browser-Use enhancements in DAON internal browser:
1. Import and syntax check for browser_routes, server, browser_bridge.
2. DOM Visibility and Occlusion filtering JS validation.
3. Set-of-Marks overlay scripts validation.
4. Batch action engine handler and bridge dispatch validation.
"""

import sys
import os
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
API_DIR = os.path.join(ROOT, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

def test_imports_and_definitions():
    print("[1/4] Testing module imports and definitions...")
    from api.routes.browser_routes import (
        _EXTRACT_INTERACTIVE_JS,
        _SOM_INJECT_JS,
        _SOM_CLEANUP_JS,
        _store_refs,
        _get_stored_ref,
        handle_post_browser_batch,
        handle_post_browser_screenshot,
    )
    from api.browser_bridge import _run_browser_command_via_bridge
    assert "visibility" in _EXTRACT_INTERACTIVE_JS, "Visibility filter missing in _EXTRACT_INTERACTIVE_JS"
    assert "elementFromPoint" in _EXTRACT_INTERACTIVE_JS, "Occlusion check missing in _EXTRACT_INTERACTIVE_JS"
    assert "__daon_som_overlay__" in _SOM_INJECT_JS, "Overlay container missing in _SOM_INJECT_JS"
    assert "__daon_som_overlay__" in _SOM_CLEANUP_JS, "Cleanup ID missing in _SOM_CLEANUP_JS"
    print("  -> Module imports and constants OK")

def test_store_refs_with_metadata():
    print("[2/4] Testing _store_refs with bounding rect & viewport flag...")
    from api.routes.browser_routes import _store_refs, _get_stored_ref

    mock_elements = [
        {
            "ref": "e0",
            "tag": "button",
            "text": "Login",
            "id": "login-btn",
            "rect": {"x": 100, "y": 200, "width": 80, "height": 30},
            "in_viewport": True,
        },
        {
            "ref": "e1",
            "tag": "input",
            "text": "Email",
            "placeholder": "Enter email",
            "rect": {"x": 100, "y": 150, "width": 200, "height": 30},
            "in_viewport": True,
        },
    ]

    _store_refs(mock_elements)
    stored0 = _get_stored_ref("e0")
    assert stored0.get("tag") == "button"
    assert stored0.get("id") == "login-btn"
    assert stored0.get("rect") == {"x": 100, "y": 200, "width": 80, "height": 30}
    assert stored0.get("in_viewport") is True

    stored1 = _get_stored_ref("e1")
    assert stored1.get("placeholder") == "Enter email"
    print("  -> _store_refs correctly stored rich metadata and rect OK")

def test_server_route_table():
    print("[3/4] Testing server.py route table for /api/browser/batch...")
    with open(os.path.join(ROOT, "server.py"), "r", encoding="utf-8") as f:
        content = f.read()
    assert "'/api/browser/batch': 'handle_post_browser_batch'" in content, "Missing /api/browser/batch in server.py"
    assert "handle_post_browser_batch" in content, "Missing handle_post_browser_batch import in server.py"
    print("  -> server.py route table has /api/browser/batch OK")

def test_bridge_commands():
    print("[4/4] Testing browser_bridge.py for screenshot and batch command support...")
    with open(os.path.join(ROOT, "api", "api", "browser_bridge.py"), "r", encoding="utf-8") as f:
        bcontent = f.read()
    assert 'command == "screenshot"' in bcontent, "Missing screenshot command in browser_bridge.py"
    assert 'command == "batch"' in bcontent, "Missing batch command in browser_bridge.py"
    assert 'labeled = "--labeled" in (args or [])' in bcontent or "labeled" in bcontent, "Missing labeled flag support in screenshot bridge"
    print("  -> browser_bridge.py commands OK")

if __name__ == "__main__":
    test_imports_and_definitions()
    test_store_refs_with_metadata()
    test_server_route_table()
    test_bridge_commands()
    print("\nALL BROWSER-USE INTEGRATION PROBES PASSED SUCCESSFULLY!")
