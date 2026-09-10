# -*- coding: utf-8 -*-
"""
tests/test_phase5_persistence.py
================================
Unit test suite for Phase 5:
- SQLite persistence via HarnessJobStore
- Lineage DAG tree registration, retrieval, and cascading subtree deletion
- Server crash recovery (interrupted job detection)
- dynamic_jobs.py backward compatibility and write-through cache
- Thread-local environment isolation (get_thread_env, _set_thread_env)
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "api"))


class TestPhase5HarnessJobStore(unittest.TestCase):
    """Test the low-level SQLite HarnessJobStore operations."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_harness.db"
        from api.dynamic.job_store import HarnessJobStore
        self.store = HarnessJobStore(db_path=self.db_path)

    def tearDown(self):
        if hasattr(self, "store") and self.store:
            self.store.close()
        self.temp_dir.cleanup()

    def test_schema_and_crud(self):
        """Test creating and retrieving jobs with diverse statuses."""
        run_id = "test_run_001"
        job_data = {
            "session_id": "sess_123",
            "status": "running",
            "started_at": 1000.0,
            "result": None,
            "error": "",
            "clarification": None,
            "approval_message": None,
            "available_actions": None,
            "approval_action": None,
        }
        self.store.save_job(run_id, job_data)

        fetched = self.store.get_job(run_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["run_id"], run_id)
        self.assertEqual(fetched["session_id"], "sess_123")
        self.assertEqual(fetched["status"], "running")
        self.assertEqual(fetched["logs"], [])

        # Update status to clarifying
        job_data["status"] = "clarifying"
        job_data["clarification"] = {"questions": ["Q1?"], "turn": 1}
        self.store.save_job(run_id, job_data)

        updated = self.store.get_job(run_id)
        self.assertEqual(updated["status"], "clarifying")
        self.assertEqual(updated["clarification"]["questions"], ["Q1?"])

    def test_logs_and_incremental_polling(self):
        """Test appending logs and cursor-based incremental log polling."""
        run_id = "test_run_logs"
        self.store.save_job(run_id, {"status": "running", "started_at": 1000.0})

        self.store.append_log(run_id, "Agent1", "Step 1 started")
        self.store.append_log(run_id, "Agent1", "Step 1 finished")
        self.store.append_log(run_id, "Agent2", "Step 2 started")

        # Initial poll (from cursor 0)
        logs, next_cursor = self.store.get_logs_since(run_id, cursor=0)
        self.assertEqual(len(logs), 3)
        self.assertEqual(next_cursor, 3)
        self.assertEqual(logs[0]["content"], "Step 1 started")
        self.assertEqual(logs[2]["agent_id"], "Agent2")

        # Second poll (no new logs)
        logs2, next_cursor2 = self.store.get_logs_since(run_id, cursor=next_cursor)
        self.assertEqual(len(logs2), 0)
        self.assertEqual(next_cursor2, 3)

        # Append one more log and poll from cursor 3
        self.store.append_log(run_id, "Agent2", "Step 2 complete")
        logs3, next_cursor3 = self.store.get_logs_since(run_id, cursor=next_cursor)
        self.assertEqual(len(logs3), 1)
        self.assertEqual(next_cursor3, 4)
        self.assertEqual(logs3[0]["content"], "Step 2 complete")

    def test_concurrent_batch_writes(self):
        """Test high-concurrency batch writing and non-blocking queue under 20 parallel threads."""
        import threading
        num_threads = 20
        logs_per_thread = 10
        errors = []

        def worker(thread_idx: int):
            try:
                run_id = f"concurrent_run_{thread_idx}"
                self.store.save_job(run_id, {
                    "session_id": f"sess_{thread_idx}",
                    "status": "running",
                    "started_at": 1000.0 + thread_idx,
                })
                for log_idx in range(logs_per_thread):
                    self.store.append_log(run_id, f"Agent_{thread_idx}", f"Log {log_idx}")
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors occurred: {errors}")

        # Flush to guarantee all in-flight batch writes are committed
        self.store.flush(timeout=3.0)

        # Verify all jobs and logs were persisted correctly
        for i in range(num_threads):
            run_id = f"concurrent_run_{i}"
            job = self.store.get_job(run_id, include_logs=True)
            self.assertIsNotNone(job, f"Job {run_id} missing")
            self.assertEqual(job["status"], "running")
            self.assertEqual(len(job["logs"]), logs_per_thread, f"Expected {logs_per_thread} logs for {run_id}")

    def test_lineage_tree_and_subtree_deletion(self):
        """Test delegation lineage registration and recursive subtree cleanup."""
        # Hierarchy:
        # root
        #  ├── child1
        #  │    └── grand1
        #  └── child2
        self.store.save_lineage("root", "", "root", 0, "Initial task")
        self.store.save_lineage("child1", "root", "root", 1, "Delegate frontend")
        self.store.save_lineage("grand1", "child1", "root", 2, "Delegate button UI")
        self.store.save_lineage("child2", "root", "root", 1, "Delegate backend")

        # Check direct lineage
        g_info = self.store.get_lineage("grand1")
        self.assertIsNotNone(g_info)
        self.assertEqual(g_info["parent_run_id"], "child1")
        self.assertEqual(g_info["root_run_id"], "root")
        self.assertEqual(g_info["depth"], 2)

        # Delete child1 subtree (should remove child1 and grand1)
        removed = self.store.delete_lineage_subtree("child1")
        self.assertIn("child1", removed)
        self.assertIn("grand1", removed)

        self.assertIsNone(self.store.get_lineage("child1"))
        self.assertIsNone(self.store.get_lineage("grand1"))

        # root and child2 must remain intact
        self.assertIsNotNone(self.store.get_lineage("root"))
        self.assertIsNotNone(self.store.get_lineage("child2"))

    def test_recover_interrupted_jobs(self):
        """Test that active/in-flight jobs are safely recovered to 'interrupted' on crash."""
        self.store.save_job("job_running", {"status": "running", "started_at": 100.0})
        self.store.save_job("job_clarifying", {"status": "clarifying", "started_at": 101.0})
        self.store.save_job("job_approval", {"status": "awaiting_approval", "started_at": 102.0})
        self.store.save_job("job_done", {"status": "done", "result": "OK", "started_at": 103.0})
        self.store.save_job("job_error", {"status": "error", "error": "Failed", "started_at": 104.0})

        recovered_count = self.store.recover_interrupted_jobs()
        self.assertEqual(recovered_count, 3)

        self.assertEqual(self.store.get_job("job_running")["status"], "interrupted")
        self.assertIn("서버가 재시작되어", self.store.get_job("job_running")["error"])
        self.assertEqual(self.store.get_job("job_clarifying")["status"], "interrupted")
        self.assertEqual(self.store.get_job("job_approval")["status"], "interrupted")

        # Completed jobs remain unchanged
        self.assertEqual(self.store.get_job("job_done")["status"], "done")
        self.assertEqual(self.store.get_job("job_error")["status"], "error")


class TestPhase5DynamicJobsIntegration(unittest.TestCase):
    """Test dynamic_jobs module integration with in-memory cache and persistence."""

    def test_dynamic_jobs_apis(self):
        from api import dynamic_jobs as dj

        run_id = f"test_harness_integration_{os.urandom(4).hex()}"
        job = dj.init_job(run_id, session_id="sess_integration")
        self.assertEqual(job["status"], "running")

        # Memory read
        self.assertIn(run_id, dj._DYNAMIC_JOBS)
        self.assertEqual(dj.get_job(run_id)["session_id"], "sess_integration")

        # Append logs
        dj.append_job_log(run_id, "CEO", "Planning execution phase", "running")
        logs, cursor = dj.get_job_logs_since(run_id, cursor=0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["agent_id"], "CEO")

        # Awaiting approval & response
        dj.set_job_awaiting_approval(run_id, message="Please approve plan")
        self.assertEqual(dj.get_job(run_id)["status"], "awaiting_approval")

        dj.set_job_approval_response(run_id, "approve")
        self.assertEqual(dj.get_job(run_id)["status"], "running")
        self.assertEqual(dj.get_job(run_id)["approval_action"], "approve")

        # Complete job
        dj.set_job_done(run_id, "All steps verified successfully.")
        status_resp = dj.get_job_status_response(run_id)
        self.assertEqual(status_resp["status"], "done")
        self.assertEqual(status_resp["result"], "All steps verified successfully.")

    def test_lineage_and_cancel_propagation(self):
        from api import dynamic_jobs as dj

        root_id = f"root_{os.urandom(4).hex()}"
        child_id = f"child_{os.urandom(4).hex()}"

        dj.init_job(root_id)
        dj.init_job(child_id)
        dj.register_lineage(child_id, root_id, root_id, 1, "Testing child delegation")

        # Verify lineage
        self.assertEqual(dj.get_lineage(child_id)["parent_run_id"], root_id)
        self.assertIn(child_id, dj.get_descendants(root_id))

        # Cancel root job -> should cancel root and cascade to child
        res = dj.cancel_job(root_id)
        self.assertTrue(res)
        self.assertEqual(dj.get_job(root_id)["status"], "cancelled")
        self.assertEqual(dj.get_job(child_id)["status"], "cancelled")
        self.assertTrue(dj.is_job_cancelled(root_id))
        self.assertTrue(dj.is_job_cancelled(child_id))


class TestPhase5EnvironmentIsolation(unittest.TestCase):
    """Test thread-local environment isolation and auth key helpers."""

    def test_thread_env_isolation(self):
        from api.config import _set_thread_env, _clear_thread_env, get_thread_env
        import threading

        results = {}

        def thread_worker(name, val):
            _set_thread_env(CUSTOM_VAR=val)
            results[name] = get_thread_env("CUSTOM_VAR")
            _clear_thread_env()

        t1 = threading.Thread(target=thread_worker, args=("t1", "ALPHA"))
        t2 = threading.Thread(target=thread_worker, args=("t2", "BETA"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["t1"], "ALPHA")
        self.assertEqual(results["t2"], "BETA")

    def test_init_hermes_auth_env(self):
        from api.config import init_hermes_auth_env
        # Should execute safely without raising exceptions
        try:
            init_hermes_auth_env()
        except Exception as e:
            self.fail(f"init_hermes_auth_env raised an exception: {e}")


if __name__ == "__main__":
    unittest.main()
