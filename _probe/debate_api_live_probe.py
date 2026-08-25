# -*- coding: utf-8 -*-
"""
Daon Agent System — Debate & Meeting Mode HTTP API Live Probe.
Tests live backend REST endpoints on http://127.0.0.1:9090:
- POST /api/debate/start (validation, error cases, missing params)
- POST /api/debate/cancel (graceful cancellation)
- POST /api/debate/next (status handling)
"""
import json
import sys
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_URL = "http://127.0.0.1:9090"
NO_PROXY = {"http": None, "https": None}


def test_api():
    print("[PROBE] Testing live backend debate routes...")

    # 1. Validation test: start without params
    r1 = requests.post(f"{BASE_URL}/api/debate/start", json={}, proxies=NO_PROXY, timeout=5)
    print(f"  POST /api/debate/start (empty body) -> Status: {r1.status_code}")
    assert r1.status_code == 400, f"Expected 400, got {r1.status_code}"

    # 2. Validation test: start with < 2 models
    r2 = requests.post(
        f"{BASE_URL}/api/debate/start",
        json={"session_id": "nonexistent", "topic": "test", "models": ["model1"]},
        proxies=NO_PROXY,
        timeout=5,
    )
    print(f"  POST /api/debate/start (<2 models) -> Status: {r2.status_code}")
    assert r2.status_code == 400, f"Expected 400, got {r2.status_code}"

    # 3. Next test without active debate
    r3 = requests.post(
        f"{BASE_URL}/api/debate/next",
        json={"session_id": "nonexistent"},
        proxies=NO_PROXY,
        timeout=5,
    )
    print(f"  POST /api/debate/next (no active debate) -> Status: {r3.status_code}")
    assert r3.status_code == 400, f"Expected 400, got {r3.status_code}"

    # 4. Cancel test with session
    r4 = requests.post(
        f"{BASE_URL}/api/debate/cancel",
        json={"session_id": "test_session_id"},
        proxies=NO_PROXY,
        timeout=5,
    )
    print(f"  POST /api/debate/cancel -> Status: {r4.status_code}, Response: {r4.json()}")
    assert r4.status_code == 200, f"Expected 200, got {r4.status_code}"
    assert r4.json().get("ok") is True

    print("\n[SUCCESS] All live HTTP API debate route contracts verified successfully!")


if __name__ == "__main__":
    test_api()
