# -*- coding: utf-8 -*-
"""hermes patch(ShellFileOperations.patch_replace) 개행 정규화 버그 재현/검증.

배경(보고된 증상):
  Windows 로컬 백엔드(LocalEnvironment)는 bash로 명령을 실행하며
    - 쓰기: Popen(text=True) 의 stdin TextIOWrapper 가 '\\n' -> os.linesep('\\r\\n') 로
            자동 변환한다(내부는 bash 'cat > file', 출력물은 CRLF로 디스크에 기록).
    - 읽기: cat 출력을 raw 로 디코딩해 CRLF를 보존한다(개행 변환 없음).
  반면 patch_replace 의 new_content 는 LF 기준이다.
  따라서 L807 의 `verify_result.stdout != new_content` 비교는 항상 불일치가 되고,
  **쓰기는 성공했는데도** "The patch did not persist" 라는 거짓 실패를 반환한다.

이 스크립트는 그 인과를 외부에서(순수 파이썬) 재현하고,
  실제 patch_replace 를 실행해 '거짓 실패' 여부를 확인한다.

반환코드: 0 = 기대대로(거짓 실패 재현됨)  /  1 = 버그가 재현되지 않음
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hermes-agent"))

print(f"OS linesep = {os.linesep!r}  platform = {sys.platform}")

# ---------------------------------------------------------------------------
# 1) 순수 파이썬 재현: text=True 파이프의 쓰기/읽기 개행 처리 비대칭
# ---------------------------------------------------------------------------
tmp = Path(tempfile.mkdtemp(prefix="daon_nl_"))
try:
    # 부모는 text=True(쓰기측 개행변환 ON)로 LF를 파이프에 쓴다.
    # 자식은 stdin의 raw 바이트를 그대로 stdout에 되돌린다(개행 변환 없음).
    # 읽기는 base.py 와 동일하게 proc.stdout.buffer 로 raw 바이트를 취한다.
    child = (
        "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); "
        "sys.stdout.buffer.flush()"
    )
    p = subprocess.Popen(
        [sys.executable, "-c", child], text=True, encoding="utf-8",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    p.stdin.write("line1\nline2\n")   # LF 로 기록
    p.stdin.close()
    p.wait(timeout=30)
    raw = p.stdout.buffer.read()      # base.py 와 동일: raw 바이트
    p.stdout.close()
    print("  [repro] wrote LF , raw stdout has CRLF ?", b"\r\n" in raw)
    print("  [repro] raw stdout repr:", raw)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------------------
# 2) 실제 patch_replace 실행 — 거짓 실패 재현
# ---------------------------------------------------------------------------
from tools.environments.local import LocalEnvironment  # noqa: E402
from tools.file_operations import ShellFileOperations  # noqa: E402

work = Path(tempfile.mkdtemp(prefix="daon_patch_"))
try:
    target = work / "sample.txt"
    original = "alpha\nbeta\ngamma\n"     # LF 기준 원본
    target.write_text(original, encoding="utf-8", newline="")

    env = LocalEnvironment(cwd=str(work))
    ops = ShellFileOperations(env, cwd=str(work))

    # 읽기 단계가 CRLF/개행을 어떻게 보는지 먼저 확인
    read = ops.read_file(str(target))
    rc = getattr(read, "content", None)
    print("  [patch] read_file succeeded:", getattr(read, "error", None) is None)
    print("  [patch] read content has CR ?", ("\r" in rc) if rc else "n/a")

    res = ops.patch_replace(str(target), "beta", "BETA")

    print("  [patch] success flag :", getattr(res, "success", None))
    print("  [patch] error        :", getattr(res, "error", None))

    disk = target.read_bytes()
    print("  [patch] on-disk BETA applied ?", b"BETA" in disk)
    print("  [patch] on-disk has CRLF   ?", b"\r\n" in disk)

    false_failure = (
        getattr(res, "success", False) is False
        and (getattr(res, "error", "") or "").lower().find("did not persist") >= 0
        and b"BETA" in disk
    )
    if false_failure:
        print("\n  => CONFIRMED: write succeeded on disk, tool reported FALSE failure.")
        code = 0
    else:
        print("\n  => NOT reproduced (patch behaved correctly here).")
        code = 1
finally:
    shutil.rmtree(work, ignore_errors=True)

sys.exit(code)
