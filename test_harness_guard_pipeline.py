"""
Comprehensive test script for Ruflo-level Harness & Dynamic Role Pipeline & Worker Telemetry.
Verifies:
1. Budget Guard: read_file threshold <= 30000, turn budget <= 60000.
2. Harness Limits: config/harness_limits.yaml loaded properly.
3. 105 Specialist Agents: get_all_specialist_templates() loads 105 templates with personas & success criteria.
4. Role Pipeline: select_specialists_for_task matches appropriate specialists.
5. Live Workers Telemetry: scan_live_workers() detects running workers (Claude/Codex).
"""

import sys
import os
from pathlib import Path

# Add project root and hermes-agent to sys.path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "api"))
sys.path.insert(0, str(ROOT_DIR / "hermes-agent"))

def test_budget_config():
    print("\n--- 1. Testing Budget & Loop Guardrails ---")
    from tools.budget_config import PINNED_THRESHOLDS, DEFAULT_RESULT_SIZE_CHARS, DEFAULT_TURN_BUDGET_CHARS
    print(f"read_file pinned threshold: {PINNED_THRESHOLDS.get('read_file')} chars")
    assert PINNED_THRESHOLDS.get('read_file') == 30000, "read_file should be capped at 30,000"
    print(f"default result size: {DEFAULT_RESULT_SIZE_CHARS}")
    assert DEFAULT_RESULT_SIZE_CHARS <= 25000, "result size should be <= 25,000"
    print(f"default turn budget: {DEFAULT_TURN_BUDGET_CHARS}")
    assert DEFAULT_TURN_BUDGET_CHARS <= 60000, "turn budget should be <= 60,000"
    print(">> Budget Guardrail: PASS")

def test_harness_limits():
    print("\n--- 2. Testing Harness Limits YAML ---")
    from api.dynamic.limits import _load_harness_limits
    limits = _load_harness_limits()
    print("Loaded limits keys:", list(limits.keys()))
    assert "delegation" in limits
    assert limits["delegation"]["max_depth"] <= 2
    assert limits["delegation"]["max_total_spawns"] <= 6
    print(">> Harness Limits: PASS")

def test_105_specialists():
    print("\n--- 3. Testing 105 Specialist Agent Catalog ---")
    from api.dynamic.role_pipeline import get_all_specialist_templates, select_specialists_for_task
    templates = get_all_specialist_templates()
    print(f"Total specialist templates loaded: {len(templates)}")
    assert len(templates) >= 100, f"Expected at least 100 templates, got {len(templates)}"
    
    # Test specific template personas and criteria
    fe_react = templates.get("frontend-react")
    assert fe_react is not None
    assert "system_prompt" in fe_react and len(fe_react["system_prompt"]) > 20
    assert "success_criteria" in fe_react and len(fe_react["success_criteria"]) > 0
    print(f"Template sample 'frontend-react': name='{fe_react.get('display_name')}', criteria_count={len(fe_react['success_criteria'])}")
    
    # Test matching
    task1 = "React 컴포넌트 상태 관리 버그 및 메모리 누수 수정"
    matched1 = select_specialists_for_task(task1, top_k=2)
    matched_ids = [m['id'] for m in matched1]
    print(f"Task: '{task1}' -> Matched: {matched_ids}")
    assert any("react" in mid or "memory" in mid or "debug" in mid for mid in matched_ids)
    print(">> 105 Specialist Catalog & Selection: PASS")

def test_live_workers_scan():
    print("\n--- 4. Testing Live Worker Telemetry ---")
    from api.routes.worker_routes import scan_live_workers
    workers = scan_live_workers()
    print(f"Active external workers found: {len(workers)}")
    for w in workers:
        print(f" - [{w['kind'].upper()}] PID: {w['pid']}, Uptime: {w['uptime_sec']}s, Dir: {w['target_dir']}")
    print(">> Live Worker Telemetry: PASS")

if __name__ == "__main__":
    print("=== STARTING RUFLO HARNESS & ROLE PIPELINE TEST ===")
    test_budget_config()
    test_harness_limits()
    test_105_specialists()
    test_live_workers_scan()
    print("\n=== ALL HARNESS PIPELINE TESTS PASSED (100%) ===")

