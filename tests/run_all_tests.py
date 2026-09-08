# -*- coding: utf-8 -*-
"""
tests/run_all_tests.py — DAON Agent System Master Test Suite Runner
==================================================================

Executes all automated test suites across Python backend, Electron main process,
and Frontend modular sub-panels.

Suites:
  1. Phase 2: Route Dispatcher & Schema Parity (Python)
  2. Phase 3: Electron Modular Architecture & Supervisors (Node.js)
  3. Phase 4: Frontend Sub-Panels & Memory Leak Prevention (Node.js)
  4. Phase 5: SQLite HarnessJobStore, Lineage DAG & Recovery (Python)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEST_SUITES = [
    {
        "name": "Phase 2: Route Dispatcher & Health API",
        "cmd": [sys.executable, "tests/test_phase2_routes.py"],
        "cwd": ROOT,
    },
    {
        "name": "Phase 3: Electron Modular Subsystems",
        "cmd": ["node", "tests/test_phase3_electron.js"],
        "cwd": ROOT,
    },
    {
        "name": "Phase 4: Frontend Sub-Panels & Leaks Guard",
        "cmd": ["node", "tests/test_phase4_frontend.js"],
        "cwd": ROOT,
    },
    {
        "name": "Phase 4b: URL Autolinking & Markdown Parser",
        "cmd": ["node", "tests/test_url_links.js"],
        "cwd": ROOT,
    },
    {
        "name": "Phase 5: SQLite JobStore, Lineage & Recovery",
        "cmd": [sys.executable, "tests/test_phase5_persistence.py"],
        "cwd": ROOT,
    },
]


def run_suite(suite: dict) -> dict:
    name = suite["name"]
    cmd = suite["cmd"]
    cwd = suite["cwd"]

    print(f"\n>> Running [{name}] ...")
    start_ts = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        duration = time.time() - start_ts
        passed = (proc.returncode == 0)
        return {
            "name": name,
            "passed": passed,
            "duration": duration,
            "output": proc.stdout.strip(),
            "returncode": proc.returncode,
        }
    except Exception as e:
        duration = time.time() - start_ts
        return {
            "name": name,
            "passed": False,
            "duration": duration,
            "output": f"Execution failed with exception: {e}",
            "returncode": -1,
        }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("================================================================")
    print("       DAON AGENT SYSTEM -- MASTER REGRESSION TEST SUITE        ")
    print("================================================================")

    results = []
    all_passed = True

    for suite in TEST_SUITES:
        res = run_suite(suite)
        results.append(res)
        if not res["passed"]:
            all_passed = False
            print(f"FAILED: {res['name']}")
            print("--- Output ---")
            print(res["output"])
            print("--------------")
        else:
            print(f"PASSED: {res['name']} ({res['duration']:.2f}s)")

    print("\n================================================================")
    print("                      TEST EXECUTION SUMMARY                    ")
    print("================================================================")
    print(f"{'Suite Name':<45} | {'Result':<8} | {'Duration':<8}")
    print("-" * 66)

    total_duration = 0.0
    for res in results:
        status = "PASS" if res["passed"] else "FAIL"
        total_duration += res["duration"]
        print(f"{res['name']:<45} | {status:<8} | {res['duration']:.2f}s")

    print("-" * 66)
    print(f"{'Total Execution Time':<45} | {'':<8} | {total_duration:.2f}s")
    print("================================================================")

    if all_passed:
        print("\nALL 4 PHASES REGRESSION TESTS PASSED! (100% GREEN)")
        sys.exit(0)
    else:
        print("\nONE OR MORE TEST SUITES FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()
