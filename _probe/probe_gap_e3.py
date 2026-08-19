"""
Gap E-3 probe: self-modify bootstrap (watcher/watched separation).

Group 1 — api/api/dynamic/restart_request.py (server side, watched):
    module surface, job guard, payload contract, atomic write, consume/corrupt
    handling. All state is injected (temp state dir + fake job lists), so no
    live server or real STATE_DIR is touched.

Group 2 — electron/restart_orchestrator.js (electron side, watcher):
    syntax check (node --check) plus the dedicated node probe
    (_probe/probe_gap_e3.js) run as a subprocess with fake deps.

Group 3 — electron/main.js wiring:
    static checks that the orchestrator is required, created, guarded in the
    exit handler, and stopped on quit.

Run: python _probe/probe_gap_e3.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

CHECKS = 0
FAILS = []


def check(cond, msg):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append("[%d] %s" % (CHECKS, msg))
        print("FAIL [%d] %s" % (CHECKS, msg))


def group(title):
    print("--- %s ---" % title)


def main():
    from api.dynamic import restart_request as rr

    # ================= Group 1: restart_request.py =================
    group("Group 1: restart_request module surface")
    check(rr.REQUEST_FILE_NAME == "restart-request.json",
          "REQUEST_FILE_NAME constant")
    check(rr.REQUEST_TYPE == "self_modify_restart", "REQUEST_TYPE constant")
    check(rr.REQUEST_VERSION == 1, "REQUEST_VERSION constant")
    check(rr._ACTIVE_STATUSES == ("running", "clarifying", "awaiting_approval"),
          "_ACTIVE_STATUSES mirrors cancellable statuses")
    check(issubclass(rr.RestartRequestError, Exception),
          "RestartRequestError is an Exception")

    group("Group 1: path resolution with injected state dir")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        p = rr.get_restart_request_path(state_dir)
        check(p == state_dir / "restart-request.json",
              "request path under injected state dir")

    group("Group 1: active-job guard (injected job lists)")
    check(rr.count_active_jobs([]) == 0, "no jobs -> 0 active")
    check(rr.count_active_jobs([{"status": "running"}]) == 1,
          "running job counted as active")
    check(rr.count_active_jobs([{"status": "clarifying"}]) == 1,
          "clarifying job counted as active")
    check(rr.count_active_jobs([{"status": "awaiting_approval"}]) == 1,
          "awaiting_approval job counted as active")
    check(rr.count_active_jobs([{"status": "done"}]) == 0,
          "done job not active")
    check(rr.count_active_jobs([{"status": "error"}]) == 0,
          "error job not active")
    check(rr.count_active_jobs([{"status": "cancelled"}]) == 0,
          "cancelled job not active")
    mixed = [
        {"status": "running"},
        {"status": "done"},
        {"status": "clarifying"},
        {"status": "error"},
        {"status": "awaiting_approval"},
        {},
    ]
    check(rr.count_active_jobs(mixed) == 3, "mixed registry -> 3 active")

    group("Group 1: request_restart validation")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        for bad_reason in ("", "   ", None):
            try:
                rr.request_restart(bad_reason, state_dir=state_dir, jobs=[])
                check(False, "empty/blank reason must raise (%r)" % (bad_reason,))
            except rr.RestartRequestError:
                check(True, "empty/blank reason raises (%r)" % (bad_reason,))
        check(not (state_dir / "restart-request.json").exists(),
              "refused request writes no file")

        busy = [{"status": "running"}, {"status": "done"}]
        try:
            rr.request_restart("apply fix", state_dir=state_dir, jobs=busy)
            check(False, "active jobs must refuse restart")
        except rr.RestartRequestError as e:
            check("active job" in str(e), "active jobs refuse with message")
        check(not (state_dir / "restart-request.json").exists(),
              "refused (busy) request writes no file")

    group("Group 1: request_restart happy path + atomic write")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        payload = rr.request_restart(
            "apply fix to server routes",
            checkpoint_ref="abc1234",
            state_dir=state_dir,
            jobs=[{"status": "done"}],
        )
        check(payload["type"] == "self_modify_restart", "payload type")
        check(payload["version"] == 1, "payload version")
        check(payload["reason"] == "apply fix to server routes", "payload reason")
        check(payload["checkpoint_ref"] == "abc1234", "payload checkpoint_ref")
        check(isinstance(payload["requested_at"], str) and len(payload["requested_at"]) >= 19,
              "payload requested_at timestamp")
        check(payload["server_pid"] == os.getpid(), "payload server_pid")

        path = state_dir / "restart-request.json"
        check(path.exists(), "request file written")
        check(not (state_dir / "restart-request.json.tmp").exists(),
              "tmp file replaced (atomic write leaves no .tmp)")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        check(on_disk == payload, "on-disk JSON round-trips the payload")

        # default checkpoint_ref is None
        payload2 = rr.request_restart("second", state_dir=state_dir, jobs=[])
        check(payload2["checkpoint_ref"] is None,
              "checkpoint_ref defaults to None")

    group("Group 1: consume_restart_request")
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        check(rr.consume_restart_request(state_dir) is None,
              "consume with no file -> None")

        rr.request_restart("to be consumed", checkpoint_ref="ref1",
                           state_dir=state_dir, jobs=[])
        got = rr.consume_restart_request(state_dir)
        check(isinstance(got, dict) and got["reason"] == "to be consumed",
              "consume returns payload")
        check(not (state_dir / "restart-request.json").exists(),
              "consume removes the file")
        check(rr.consume_restart_request(state_dir) is None,
              "second consume -> None")

        # corrupt file: removed, returns None (must not block future restarts)
        bad = state_dir / "restart-request.json"
        bad.write_text("{not json", encoding="utf-8")
        check(rr.consume_restart_request(state_dir) is None,
              "corrupt file -> None")
        check(not bad.exists(), "corrupt file removed on consume")

        # non-dict JSON payload
        bad.write_text("[1, 2]", encoding="utf-8")
        check(rr.consume_restart_request(state_dir) is None,
              "non-dict JSON -> None")
        check(not bad.exists(), "non-dict file removed on consume")

    # ================= Group 2: node orchestrator probe =================
    group("Group 2: node syntax check + node probe subprocess")
    node = shutil.which("node")
    check(node is not None, "node executable available")
    if node:
        orch_js = ROOT / "electron" / "restart_orchestrator.js"
        probe_js = ROOT / "_probe" / "probe_gap_e3.js"
        check(orch_js.exists(), "electron/restart_orchestrator.js exists")
        check(probe_js.exists(), "_probe/probe_gap_e3.js exists")

        r_syntax = subprocess.run(
            [node, "--check", str(orch_js)],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        check(r_syntax.returncode == 0,
              "node --check restart_orchestrator.js (rc=%s)" % r_syntax.returncode)

        r_probe = subprocess.run(
            [node, str(probe_js)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
        )
        check(r_probe.returncode == 0,
              "node probe exit 0 (rc=%s, stderr=%s)"
              % (r_probe.returncode, (r_probe.stderr or "")[:300]))
        check("ALL GAP-E3 (node) PROBES PASSED" in (r_probe.stdout or ""),
              "node probe reports all passed (stdout=%s)"
              % (r_probe.stdout or "")[:300])

    # ================= Group 3: main.js wiring =================
    group("Group 3: electron/main.js wiring (static)")
    main_js = (ROOT / "electron" / "main.js").read_text(encoding="utf-8")
    check("require('./restart_orchestrator')" in main_js,
          "main.js requires restart_orchestrator")
    check("createRestartOrchestrator" in main_js,
          "main.js constructs the orchestrator")
    check("let restartOrchestrator = null;" in main_js,
          "restartOrchestrator handle declared")
    check("let selfModifyRestartActive = false;" in main_js,
          "selfModifyRestartActive flag declared")
    check(main_js.count("selfModifyRestartActive") >= 4,
          "flag used in kill/exit-guard/afterCycle (>=4 references, got %d)"
          % main_js.count("selfModifyRestartActive"))
    check("restartOrchestrator.stop()" in main_js,
          "orchestrator stopped on quit")
    check("gitRollback" in main_js, "gitRollback dep wired")
    check("afterCycle" in main_js, "afterCycle dep wired")
    check("probeServerHealthStable" in main_js,
          "healthCheck uses probeServerHealthStable")
    check("killProcessTree" in main_js, "killServer uses killProcessTree")

    # ================= Summary =================
    print()
    if FAILS:
        print("GAP-E3 PROBE FAILED: %d of %d checks failed" % (len(FAILS), CHECKS))
        sys.exit(1)
    print("ALL GAP-E3 PROBES PASSED (%d checks)" % CHECKS)


if __name__ == "__main__":
    main()
