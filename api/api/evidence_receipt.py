# -*- coding: utf-8 -*-
"""Evidence Receipt emitter (audit R14 / mutual-IP spec v1.3 T01~T13).

The audit's core complaint is that DAON *self-reports* success without
producing independently checkable artifacts. This module closes that gap: it
emits a machine-readable **receipt** for each verification test (T01~T13) and
bundles them into a **LEVEL B Evidence ZIP** whose every file carries a
SHA-256 so a third party can verify the bundle offline.

Design rules (spec v1.3 §15):
  * A receipt is EVIDENCE, not a claim. It records what actually ran, the
    observed result, and the exact build it ran against.
  * Every receipt carries a build fingerprint (product_version, build_id,
    artifact SHA-256) so a receipt can never be replayed against a different
    binary.
  * The bundle is deterministic: same inputs -> same file set. A manifest
    lists every member with its SHA-256 and the bundle's own digest.
  * Nothing here raises on the happy path; failures are recorded as receipts
    with ``status="error"`` so a partial run still produces evidence.

This module is stdlib-only (json / hashlib / zipfile / time / pathlib) so it
can run inside the frozen server.exe without extra dependencies.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Test registry (spec v1.3 T01~T13)
# ---------------------------------------------------------------------------
# Each id maps to a human title. The emitter does not run the tests itself —
# callers record results — but the registry fixes the vocabulary so a bundle
# can be checked for completeness (all 13 present).
TEST_REGISTRY: dict[str, str] = {
    "T01": "Demo-to-Skill schema pass + SKILL.md actually created",
    "T02": "REVIEW->APPROVED->consumer->tool executor->task artifact chain",
    "T03": "baseline(A) vs approved(B) same-condition repeated AB/BA",
    "T04": "unseen task generalization",
    "T05": "regression (no capability loss)",
    "T06": "restart persistence",
    "T07": "REJECTED skill blocked on normal path",
    "T08": "approval verification level recorded",
    "T09": "browser E2E on localhost synthetic page",
    "T10": "synthetic marker + EXTERNAL_PROVIDER_SENT=false",
    "T11": "autonomous self-evolution claim scope",
    "T12": "auth / CDP / IPC boundary",
    "T13": "public-site claim accuracy (banned-phrase scan; NOT Mobile/RLS/E2EE/P2P)",
}

# Receipt status vocabulary.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"
STATUS_ERROR = "error"
VALID_STATUSES = frozenset({STATUS_PASS, STATUS_FAIL, STATUS_SKIP, STATUS_ERROR})

# Evidence level (spec v1.3). LEVEL_B = automated, reproducible, hashed.
EVIDENCE_LEVEL_B = "B"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Build fingerprint
# ---------------------------------------------------------------------------
@dataclass
class BuildFingerprint:
    """Identity of the exact build a receipt was produced against."""

    product_version: str = ""
    build_id: str = ""
    artifact_path: str = ""
    artifact_sha256: str = ""
    git_commit: str = ""
    python: str = ""
    frozen: bool = False
    captured_at: str = ""

    @classmethod
    def capture(cls, artifact_path: Optional[str] = None) -> "BuildFingerprint":
        """Best-effort capture of the running build's identity.

        Never raises — missing fields are left empty so a receipt is still
        emitted (an incomplete fingerprint is itself evidence).
        """
        import os
        import sys

        fp = cls()
        fp.captured_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            fp.python = sys.version.split()[0]
            fp.frozen = bool(getattr(sys, "frozen", False))
        except Exception:
            pass

        # build_id / version. The real importable paths in the source tree are
        # ``api.api.routes.system_routes`` and ``api.api.config`` (the repo root
        # is on sys.path, so ``api.routes`` / ``api.config`` do NOT resolve).
        # We try both spellings so the fingerprint is populated in the source
        # tree AND inside the frozen server.exe (where the layout differs).
        for mod_name in ("api.api.routes.system_routes", "api.routes.system_routes"):
            try:
                mod = __import__(mod_name, fromlist=["BUILD_ID"])
                fp.build_id = str(getattr(mod, "BUILD_ID", "") or "")
                if fp.build_id:
                    break
            except Exception:
                continue
        for mod_name in ("api.api.config", "api.config"):
            try:
                mod = __import__(mod_name, fromlist=["PRODUCT_VERSION"])
                fp.product_version = str(getattr(mod, "PRODUCT_VERSION", "") or "")
                if fp.product_version:
                    break
            except Exception:
                continue

        # Fallbacks so the fingerprint is never silently empty: read the
        # product version from package.json and the backend build id from the
        # server.py banner. These are the identifiers a third party compares
        # against the previous public revision.
        if not fp.product_version:
            try:
                pkg = Path(__file__).resolve().parents[2] / "package.json"
                fp.product_version = str(json.loads(pkg.read_text(encoding="utf-8")).get("version", "") or "")
            except Exception:
                pass
        if not fp.build_id:
            try:
                import re as _re
                srv = Path(__file__).resolve().parents[2] / "server.py"
                for line in srv.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "[BUILD ID]:" in line:
                        # The banner is: print("[BUILD ID]: server-v5-...", flush=True)
                        # The value sits BETWEEN the marker and the closing quote,
                        # so capture up to the first quote (no leading quote).
                        m = _re.search(r"\[BUILD ID\]:\s*([^'\"]+)", line)
                        if m:
                            fp.build_id = m.group(1).strip()
                        break
            except Exception:
                pass

        # git commit (best effort).
        try:
            import subprocess
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root, capture_output=True, text=True, timeout=3,
            )
            if out.returncode == 0:
                fp.git_commit = out.stdout.strip()
        except Exception:
            pass

        # artifact hash: the frozen exe, or this source file when not frozen.
        try:
            if artifact_path:
                target = Path(artifact_path)
            elif fp.frozen:
                target = Path(sys.executable)
            else:
                target = Path(__file__)
            fp.artifact_path = str(target)
            fp.artifact_sha256 = _sha256_file(target) or ""
        except Exception:
            pass

        return fp


# ---------------------------------------------------------------------------
# Receipt
# ---------------------------------------------------------------------------
@dataclass
class Receipt:
    """A single verification receipt (one test id)."""

    test_id: str
    title: str = ""
    status: str = STATUS_SKIP
    evidence: dict = field(default_factory=dict)
    notes: str = ""
    fingerprint: dict = field(default_factory=dict)
    emitted_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------
class EvidenceReceiptEmitter:
    """Collects receipts and writes a LEVEL B Evidence ZIP.

    Usage::

        em = EvidenceReceiptEmitter()
        em.record("T01", STATUS_PASS, evidence={"skill_file_created": True})
        em.record("T07", STATUS_PASS, evidence={"normal_path_blocked": True})
        bundle = em.write_bundle(Path("evidence/daon_evidence.zip"))
    """

    def __init__(self, fingerprint: Optional[BuildFingerprint] = None):
        self.fingerprint = fingerprint or BuildFingerprint.capture()
        self._receipts: dict[str, Receipt] = {}

    # -- recording ---------------------------------------------------------
    def record(
        self,
        test_id: str,
        status: str,
        *,
        evidence: Optional[dict] = None,
        notes: str = "",
    ) -> Receipt:
        """Record (or overwrite) a receipt for ``test_id``.

        Unknown test ids are still recorded (with an empty title) so a caller
        can attach ad-hoc evidence without being blocked by the registry.
        """
        if status not in VALID_STATUSES:
            status = STATUS_ERROR
            notes = (notes + f" [invalid status coerced]").strip()
        r = Receipt(
            test_id=test_id,
            title=TEST_REGISTRY.get(test_id, ""),
            status=status,
            evidence=dict(evidence or {}),
            notes=notes,
            fingerprint=asdict(self.fingerprint),
            emitted_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        self._receipts[test_id] = r
        return r

    def record_error(self, test_id: str, exc: BaseException, *, notes: str = "") -> Receipt:
        """Convenience: record a test that raised."""
        return self.record(
            test_id,
            STATUS_ERROR,
            evidence={"error_type": type(exc).__name__, "error": str(exc)},
            notes=notes,
        )

    # -- introspection -----------------------------------------------------
    @property
    def receipts(self) -> list[Receipt]:
        return [self._receipts[k] for k in sorted(self._receipts)]

    def missing_tests(self) -> list[str]:
        """Registry test ids that have no receipt yet."""
        return [t for t in sorted(TEST_REGISTRY) if t not in self._receipts]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for r in self._receipts.values():
            counts[r.status] = counts.get(r.status, 0) + 1
        return {
            "total": len(self._receipts),
            "by_status": counts,
            "missing": self.missing_tests(),
            "complete": not self.missing_tests(),
        }

    # -- bundle ------------------------------------------------------------
    def write_bundle(self, out_path: Path, *, deterministic: bool = False) -> Path:
        """Write a LEVEL B Evidence ZIP and return its path.

        Layout::

            manifest.json          # fingerprint + per-member SHA-256 + bundle digest
            receipts/T01.json      # one file per receipt
            receipts/T02.json
            ...

        When ``deterministic=True`` every wall-clock field (each receipt's
        ``emitted_at`` and the manifest's ``generated_at``) is replaced with a
        fixed sentinel so that the same logical inputs always produce
        byte-identical members. This is what makes the bundle independently
        reproducible: a verifier can re-run the suite and diff the hashes.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Fixed sentinel used when determinism is requested.
        fixed_ts = "1970-01-01T00:00:00+0000"

        # Build member payloads in memory (deterministic ordering).
        members: dict[str, bytes] = {}
        for r in self.receipts:
            if deterministic:
                r.emitted_at = fixed_ts
                # The fingerprint carries its own wall-clock `captured_at`,
                # which is embedded in every receipt. It must be pinned too,
                # otherwise the bundle is not byte-reproducible.
                if isinstance(r.fingerprint, dict) and "captured_at" in r.fingerprint:
                    r.fingerprint["captured_at"] = fixed_ts
            members[f"receipts/{r.test_id}.json"] = r.to_json().encode("utf-8")

        member_hashes = {name: _sha256_bytes(data) for name, data in members.items()}

        # The manifest embeds the fingerprint too; its wall-clock `captured_at`
        # must be pinned under determinism or the manifest is not reproducible.
        manifest_fingerprint = asdict(self.fingerprint)
        if deterministic and "captured_at" in manifest_fingerprint:
            manifest_fingerprint["captured_at"] = fixed_ts

        manifest = {
            "evidence_level": EVIDENCE_LEVEL_B,
            "generated_at": fixed_ts if deterministic else time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "fingerprint": manifest_fingerprint,
            "summary": self.summary(),
            "test_registry": TEST_REGISTRY,
            "members": member_hashes,
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")

        # Bundle digest = sha256 over the sorted (name, hash) pairs + manifest.
        digest_input = "".join(
            f"{name}:{member_hashes[name]}\n" for name in sorted(member_hashes)
        ).encode("utf-8") + manifest_bytes
        bundle_digest = _sha256_bytes(digest_input)

        # Write the zip. Fixed timestamps keep the archive reproducible.
        fixed_date = (1980, 1, 1, 0, 0, 0)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, date_time=fixed_date)
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, members[name])
            info = zipfile.ZipInfo("manifest.json", date_time=fixed_date)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, manifest_bytes)

        # Sidecar digest file so the bundle can be verified without unzipping.
        digest_path = out_path.with_suffix(out_path.suffix + ".sha256")
        digest_path.write_text(f"{bundle_digest}  {out_path.name}\n", encoding="utf-8")

        return out_path


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------
def emit_evidence_bundle(
    receipts: list[dict],
    out_path: Path,
    *,
    fingerprint: Optional[BuildFingerprint] = None,
) -> Path:
    """One-shot helper: build a bundle from a list of receipt dicts.

    Each dict must have at least ``test_id`` and ``status``; ``evidence`` and
    ``notes`` are optional.
    """
    em = EvidenceReceiptEmitter(fingerprint=fingerprint)
    for item in receipts:
        em.record(
            str(item.get("test_id", "")),
            str(item.get("status", STATUS_SKIP)),
            evidence=item.get("evidence") or {},
            notes=str(item.get("notes", "")),
        )
    return em.write_bundle(Path(out_path))
