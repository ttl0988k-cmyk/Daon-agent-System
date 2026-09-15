# -*- coding: utf-8 -*-
"""CRLF read/write basis probe for hermes-agent file_operations on Windows.

Decides two questions with the REAL LocalEnvironment + ShellFileOperations:

  Q1. Does the read path (`cat` via _run_bash) strip CR, i.e. return LF?
      -> If it returns LF, patch's `new_content` is LF and the write adds
         exactly one CR (no doubling). If it keeps CRLF, re-writing CRLF
         content produces `\r\r\n` -> doubling (index.html corruption).
  Q2. Does `write_file` of LF content land on disk as CRLF (1x) or CRCRLF?

Run:  python scripts/verify_crlf_read_basis.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hermes-agent"))

from tools.environments.local import LocalEnvironment  # noqa: E402
from tools.file_operations import ShellFileOperations  # noqa: E402


def main() -> int:
    env = LocalEnvironment(cwd=str(ROOT), timeout=30)
    fops = ShellFileOperations(env)
    td = tempfile.mkdtemp(prefix="crlf_probe_")

    print(f"[env] os.linesep = {os.linesep!r}")

    # ---- Q1: read basis ------------------------------------------------
    p = os.path.join(td, "crlf.txt")
    with open(p, "wb") as f:
        f.write(b"alpha\r\nbeta\r\ngamma\r\n")
    raw = fops.read_file_raw(p)
    exec_cat = fops._exec(f"cat {p}")
    print(f"[Q1] on-disk source bytes : {open(p, 'rb').read()!r}")
    print(f"[Q1] read_file_raw content: {raw.content!r}")
    print(f"[Q1] _exec(cat) stdout    : {exec_cat.stdout!r}")
    read_strips_cr = "\r" not in raw.content
    print(f"[Q1] read strips CR ? {read_strips_cr}")

    # ---- Q2: write basis ----------------------------------------------
    w = fops.write_file(p, "alpha\nbeta\ngamma\n")
    disk = open(p, "rb").read()
    print(f"[Q2] write_file error     : {w.error}")
    print(f"[Q2] on-disk after write  : {disk!r}")
    doubled = b"\r\r\n" in disk
    single_crlf = disk == b"alpha\r\nbeta\r\ngamma\r\n"
    print(f"[Q2] disk has CRCRLF (doubling) ? {doubled}")
    print(f"[Q2] disk is clean CRLF        ? {single_crlf}")

    # ---- Q3: two consecutive patch_replace on an LF file ----------------
    p2 = os.path.join(td, "twice.txt")
    with open(p2, "wb") as f:
        f.write(b"one\ntwo\n")
    r1 = fops.patch_replace(p2, "two", "TWO")
    d1 = open(p2, "rb").read()
    r2 = fops.patch_replace(p2, "one", "ONE")
    d2 = open(p2, "rb").read()
    print(f"[Q3] patch#1 success={r1.success} err={r1.error}")
    print(f"[Q3] disk after #1 : {d1!r}")
    print(f"[Q3] patch#2 success={r2.success} err={r2.error}")
    print(f"[Q3] disk after #2 : {d2!r}")

    # ---- Q4: patch a file whose on-disk content is already CRLF ---------
    p3 = os.path.join(td, "crlf_src.txt")
    with open(p3, "wb") as f:
        f.write(b"foo\r\nbar\r\n")
    r3 = fops.patch_replace(p3, "bar", "BAR")
    d3 = open(p3, "rb").read()
    print(f"[Q4] patch(CRLF src) success={r3.success} err={r3.error}")
    print(f"[Q4] disk after patch : {d3!r}")

    checks = [
        ("write of LF lands as single CRLF (no doubling)", (not doubled) and single_crlf),
        ("patch#1 reports success (no false failure)", r1.success),
        ("patch#2 reports success (no false failure)", r2.success),
        ("no CRCRLF after repeated patches", (b"\r\r\n" not in d1) and (b"\r\r\n" not in d2)),
        ("patch of CRLF source reports success", r3.success),
        ("no CRCRLF when patching CRLF source", b"\r\r\n" not in d3),
    ]
    print()
    print("=== regression checks ===")
    failed = 0
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        failed += 0 if ok else 1
    print(f"\n[RESULT] {len(checks) - failed}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
