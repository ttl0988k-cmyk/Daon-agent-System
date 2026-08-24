# -*- coding: utf-8 -*-
"""Self-update pipeline probe (Python side): rebuild flag round-trip through
request_restart -> payload file -> consume_restart_request."""
import json
import sys
import tempfile
from pathlib import Path

# Package root is <repo>/api (the 'api' package itself lives at api/api).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from api.dynamic.restart_request import request_restart, RestartRequestError  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp(prefix="daon_rr_probe_"))
    state_dir = tmp / "data"
    jobs = []  # no active jobs

    # T1: rebuild=True is persisted in the payload file.
    p = request_restart("probe rebuild", checkpoint_ref="abc123", rebuild=True,
                        state_dir=state_dir, jobs=jobs)
    assert p["rebuild"] is True, f"T1 payload rebuild != True: {p}"
    raw = json.loads((state_dir / "restart-request.json").read_text(encoding="utf-8"))
    assert raw["rebuild"] is True and raw["checkpoint_ref"] == "abc123"
    print("T1 PASS rebuild=True persisted:", raw["rebuild"])

    # T2: default rebuild=False when omitted (backward compatible).
    p2 = request_restart("probe default", state_dir=state_dir, jobs=jobs)
    assert p2["rebuild"] is False, f"T2 payload rebuild != False: {p2}"
    print("T2 PASS default rebuild=False")

    # T3: active jobs still refuse (guard intact).
    try:
        request_restart("blocked", state_dir=state_dir,
                        jobs=[{"status": "running"}])
        raise AssertionError("T3 expected RestartRequestError")
    except RestartRequestError as e:
        assert "active job" in str(e)
    print("T3 PASS active-job guard intact")

    print("PY-RR-PROBE ALL-PASS")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
