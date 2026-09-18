"""P1-2 boundary hardening regression tests (audit R14 / T12).

These tests lock in the four P1-2 security invariants so a future refactor
cannot silently re-open the holes the audit found:

  1. CDP `remote-allow-origins` is narrowed (NOT the wildcard `*`), while the
     debugging port 9222 stays ENABLED (it is a core product dependency).
  2. The `install-update` IPC handler validates `installerPath` fail-closed
     before handing it to `cmd.exe`.
  3. Auth middleware (`check_auth`) is actually WIRED into `server.py`
     `do_GET` / `do_POST` (the audit found 0 call sites).
  4. `HOST` defaults to loopback (`127.0.0.1`), not `0.0.0.0`.

The Electron/JS files cannot be imported into pytest, so the JS-side checks
are static source assertions plus a Python mirror of the validator rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = two levels up from this test file (api/tests/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    p = REPO_ROOT / rel
    assert p.exists(), f"expected source file missing: {rel}"
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1. CDP hardening (electron/main.js)
# ---------------------------------------------------------------------------
class TestCdpHardening:
    def test_debugging_port_still_enabled(self):
        """Port 9222 MUST stay enabled — it is a core product dependency."""
        src = _read("electron/main.js")
        assert "remote-debugging-port" in src
        assert "9222" in src

    def test_remote_allow_origins_is_not_wildcard(self):
        """`remote-allow-origins` must NOT be the wildcard `*`."""
        src = _read("electron/main.js")
        # Find the appendSwitch call for remote-allow-origins.
        m = re.search(
            r"appendSwitch\(\s*['\"]remote-allow-origins['\"]\s*,\s*([^)]+)\)",
            src,
        )
        assert m, "remote-allow-origins switch not found in electron/main.js"
        arg = m.group(1)
        assert "'*'" not in arg and '"*"' not in arg, (
            "remote-allow-origins must not be the wildcard '*'"
        )

    def test_remote_allow_origins_targets_local_server(self):
        src = _read("electron/main.js")
        assert "localhost:9090" in src or "127.0.0.1:9090" in src


# ---------------------------------------------------------------------------
# 2. install-update IPC hardening (electron/src/IpcHandlers.js)
# ---------------------------------------------------------------------------
class TestInstallUpdateIpcHardening:
    def test_validator_function_exists(self):
        src = _read("electron/src/IpcHandlers.js")
        assert "function validateInstallerPath" in src

    def test_handler_calls_validator_fail_closed(self):
        src = _read("electron/src/IpcHandlers.js")
        # The handler must call the validator and bail out on failure.
        assert "validateInstallerPath(installerPath)" in src
        assert "if (!check.ok)" in src
        # On rejection it must return WITHOUT quitting the app.
        assert "install-update rejected" in src

    def test_handler_uses_validated_path(self):
        """The cmd.exe string must use the validated path, not the raw input."""
        src = _read("electron/src/IpcHandlers.js")
        assert "safeInstallerPath" in src
        # The raw `installerPath` must not be interpolated into the command.
        assert '"${installerPath}" /S' not in src
        assert '"${safeInstallerPath}" /S' in src

    def test_validator_rejects_shell_metacharacters(self):
        src = _read("electron/src/IpcHandlers.js")
        # The metacharacter class must be present in the validator.
        assert "[&|<>^%!`" in src

    def test_validator_requires_exe_and_absolute(self):
        src = _read("electron/src/IpcHandlers.js")
        assert "path.isAbsolute" in src
        assert "'.exe'" in src

    def test_validator_uses_trusted_roots(self):
        src = _read("electron/src/IpcHandlers.js")
        assert "getPath('userData')" in src
        assert "getPath('temp')" in src
        assert "realpathSync" in src

    def test_open_system_browser_also_guarded(self):
        """Same exec-injection class — open-system-browser must validate too."""
        src = _read("electron/src/IpcHandlers.js")
        assert "open-system-browser rejected" in src


# ---------------------------------------------------------------------------
# 2b. Python mirror of the JS validator rules (behavioural check)
# ---------------------------------------------------------------------------
def _mirror_validate(installer_path, *, trusted_roots, exists=True, is_file=True):
    """Mirror of validateInstallerPath() rules, for behavioural assertions.

    Returns (ok, reason). This is intentionally a faithful re-implementation
    of the JS logic so we can test the *rules* without a Node runtime.
    """
    import os

    if not isinstance(installer_path, str) or installer_path.strip() == "":
        return False, "empty"
    if re.search(r"[&|<>^%!`\r\n]", installer_path):
        return False, "metacharacters"
    if not os.path.isabs(installer_path):
        return False, "not absolute"
    if os.path.splitext(installer_path)[1].lower() != ".exe":
        return False, "not exe"
    if not exists:
        return False, "missing"
    if not is_file:
        return False, "not a file"
    real = os.path.realpath(installer_path)
    in_root = False
    for root in trusted_roots:
        root = os.path.realpath(root)
        rel = os.path.relpath(real, root)
        if rel != "" and not rel.startswith("..") and not os.path.isabs(rel):
            in_root = True
            break
    if not in_root:
        return False, "outside trusted roots"
    return True, "ok"


class TestValidatorRulesMirror:
    TRUSTED = [r"C:\Users\test\AppData\Roaming\Daon", r"C:\Users\test\AppData\Local\Temp"]

    def test_accepts_valid_installer(self):
        ok, _ = _mirror_validate(
            r"C:\Users\test\AppData\Local\Temp\daon-setup.exe",
            trusted_roots=self.TRUSTED,
        )
        assert ok

    def test_rejects_empty(self):
        ok, _ = _mirror_validate("", trusted_roots=self.TRUSTED)
        assert not ok

    def test_rejects_metacharacters(self):
        ok, _ = _mirror_validate(
            r"C:\Users\test\AppData\Local\Temp\a.exe & calc.exe",
            trusted_roots=self.TRUSTED,
        )
        assert not ok

    def test_rejects_relative_path(self):
        ok, _ = _mirror_validate(r"setup.exe", trusted_roots=self.TRUSTED)
        assert not ok

    def test_rejects_non_exe(self):
        ok, _ = _mirror_validate(
            r"C:\Users\test\AppData\Local\Temp\evil.bat",
            trusted_roots=self.TRUSTED,
        )
        assert not ok

    def test_rejects_outside_trusted_roots(self):
        ok, _ = _mirror_validate(
            r"C:\Windows\System32\evil.exe",
            trusted_roots=self.TRUSTED,
        )
        assert not ok

    def test_rejects_missing_file(self):
        ok, _ = _mirror_validate(
            r"C:\Users\test\AppData\Local\Temp\ghost.exe",
            trusted_roots=self.TRUSTED,
            exists=False,
        )
        assert not ok

    def test_rejects_directory(self):
        ok, _ = _mirror_validate(
            r"C:\Users\test\AppData\Local\Temp\dir.exe",
            trusted_roots=self.TRUSTED,
            is_file=False,
        )
        assert not ok


# ---------------------------------------------------------------------------
# 3. Auth middleware wiring (server.py)
# ---------------------------------------------------------------------------
class TestAuthMiddlewareWiring:
    def test_do_get_calls_check_auth(self):
        src = _read("server.py")
        # Extract the do_GET body.
        m = re.search(r"def do_GET\(self\):(.*?)\n    def ", src, re.DOTALL)
        assert m, "do_GET not found"
        body = m.group(1)
        assert "check_auth" in body, "do_GET must call check_auth"
        assert "if not check_auth(self, parsed)" in body

    def test_do_post_calls_check_auth(self):
        src = _read("server.py")
        m = re.search(r"def do_POST\(self\):(.*?)\n    def ", src, re.DOTALL)
        assert m, "do_POST not found"
        body = m.group(1)
        assert "check_auth" in body, "do_POST must call check_auth"
        assert "if not check_auth(self, parsed)" in body

    def test_auth_gate_precedes_route_dispatch(self):
        """check_auth must run BEFORE handle_get/handle_post."""
        src = _read("server.py")
        m = re.search(r"def do_GET\(self\):(.*?)\n    def ", src, re.DOTALL)
        body = m.group(1)
        assert body.index("check_auth") < body.index("handle_get")


# ---------------------------------------------------------------------------
# 4. HOST default is loopback (api/api/config.py)
# ---------------------------------------------------------------------------
class TestHostDefaultLoopback:
    def test_host_defaults_to_loopback(self):
        src = _read("api/api/config.py")
        m = re.search(r"^HOST\s*=\s*(.+)$", src, re.MULTILINE)
        assert m, "HOST assignment not found"
        line = m.group(1)
        assert "127.0.0.1" in line, "HOST must default to loopback"
        assert "0.0.0.0" not in line, "HOST must not default to 0.0.0.0"

    def test_host_has_env_override(self):
        src = _read("api/api/config.py")
        m = re.search(r"^HOST\s*=\s*(.+)$", src, re.MULTILINE)
        line = m.group(1)
        assert "DAON_HOST" in line, "HOST must be overridable via DAON_HOST"

    def test_lan_exposure_is_opt_in(self):
        """The security comment must document that LAN exposure is opt-in."""
        src = _read("api/api/config.py")
        assert "opt-in" in src or "opt in" in src
