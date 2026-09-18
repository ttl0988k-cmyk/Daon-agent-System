# -*- coding: utf-8 -*-
"""Regression tests for the T01~T13 Evidence Receipt emitter (audit R14).

These lock in the receipt contract:
  * the T01~T13 registry is complete and stable,
  * a receipt records status + evidence + a build fingerprint,
  * the LEVEL B Evidence ZIP is deterministic and self-verifying
    (manifest lists every member's SHA-256; a sidecar carries the bundle digest),
  * a partial run still produces evidence (missing tests are reported, not fatal).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

# Repo root is on sys.path so `api.api.evidence_receipt` is importable.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.api.evidence_receipt import (  # noqa: E402
    EVIDENCE_LEVEL_B,
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    TEST_REGISTRY,
    VALID_STATUSES,
    BuildFingerprint,
    EvidenceReceiptEmitter,
    Receipt,
    emit_evidence_bundle,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class TestTestRegistry:
    def test_all_thirteen_tests_present(self):
        expected = {f"T{i:02d}" for i in range(1, 14)}
        assert set(TEST_REGISTRY) == expected

    def test_every_test_has_a_title(self):
        for tid, title in TEST_REGISTRY.items():
            assert title.strip(), f"{tid} has an empty title"

    def test_status_vocabulary(self):
        assert VALID_STATUSES == {STATUS_PASS, STATUS_FAIL, STATUS_SKIP, STATUS_ERROR}


# ---------------------------------------------------------------------------
# Build fingerprint
# ---------------------------------------------------------------------------
class TestBuildFingerprint:
    def test_capture_never_raises(self):
        fp = BuildFingerprint.capture()
        assert isinstance(fp, BuildFingerprint)
        assert fp.captured_at  # always stamped

    def test_capture_hashes_artifact(self, tmp_path):
        artifact = tmp_path / "server.exe"
        artifact.write_bytes(b"fake-binary")
        fp = BuildFingerprint.capture(artifact_path=str(artifact))
        assert fp.artifact_sha256
        assert len(fp.artifact_sha256) == 64  # sha256 hex
        assert fp.artifact_path == str(artifact)

    def test_missing_artifact_leaves_hash_empty(self, tmp_path):
        fp = BuildFingerprint.capture(artifact_path=str(tmp_path / "nope.exe"))
        assert fp.artifact_sha256 == ""


# ---------------------------------------------------------------------------
# Receipt recording
# ---------------------------------------------------------------------------
class TestReceiptRecording:
    def test_record_pass(self):
        em = EvidenceReceiptEmitter()
        r = em.record("T01", STATUS_PASS, evidence={"skill_file_created": True})
        assert r.test_id == "T01"
        assert r.status == STATUS_PASS
        assert r.evidence["skill_file_created"] is True
        assert r.title == TEST_REGISTRY["T01"]
        assert r.emitted_at

    def test_receipt_carries_fingerprint(self):
        em = EvidenceReceiptEmitter()
        r = em.record("T02", STATUS_PASS)
        assert "captured_at" in r.fingerprint

    def test_invalid_status_coerced_to_error(self):
        em = EvidenceReceiptEmitter()
        r = em.record("T03", "banana")
        assert r.status == STATUS_ERROR
        assert "invalid status" in r.notes

    def test_record_error_helper(self):
        em = EvidenceReceiptEmitter()
        try:
            raise ValueError("boom")
        except ValueError as e:
            r = em.record_error("T04", e)
        assert r.status == STATUS_ERROR
        assert r.evidence["error_type"] == "ValueError"
        assert "boom" in r.evidence["error"]

    def test_record_overwrites_same_id(self):
        em = EvidenceReceiptEmitter()
        em.record("T05", STATUS_FAIL)
        em.record("T05", STATUS_PASS)
        assert len(em.receipts) == 1
        assert em.receipts[0].status == STATUS_PASS

    def test_unknown_test_id_still_recorded(self):
        em = EvidenceReceiptEmitter()
        r = em.record("T99", STATUS_PASS)
        assert r.test_id == "T99"
        assert r.title == ""

    def test_receipts_sorted_by_id(self):
        em = EvidenceReceiptEmitter()
        em.record("T03", STATUS_PASS)
        em.record("T01", STATUS_PASS)
        em.record("T02", STATUS_PASS)
        assert [r.test_id for r in em.receipts] == ["T01", "T02", "T03"]


# ---------------------------------------------------------------------------
# Summary / completeness
# ---------------------------------------------------------------------------
class TestSummary:
    def test_missing_tests_reported(self):
        em = EvidenceReceiptEmitter()
        em.record("T01", STATUS_PASS)
        missing = em.missing_tests()
        assert "T01" not in missing
        assert "T02" in missing
        assert len(missing) == 12

    def test_complete_when_all_recorded(self):
        em = EvidenceReceiptEmitter()
        for tid in TEST_REGISTRY:
            em.record(tid, STATUS_PASS)
        s = em.summary()
        assert s["complete"] is True
        assert s["missing"] == []
        assert s["total"] == 13

    def test_by_status_counts(self):
        em = EvidenceReceiptEmitter()
        em.record("T01", STATUS_PASS)
        em.record("T02", STATUS_PASS)
        em.record("T03", STATUS_FAIL)
        s = em.summary()
        assert s["by_status"][STATUS_PASS] == 2
        assert s["by_status"][STATUS_FAIL] == 1


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------
class TestEvidenceBundle:
    def _full_emitter(self):
        em = EvidenceReceiptEmitter()
        for tid in TEST_REGISTRY:
            em.record(tid, STATUS_PASS, evidence={"ran": True})
        return em

    def test_bundle_created(self, tmp_path):
        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "evidence.zip")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_bundle_contains_manifest_and_receipts(self, tmp_path):
        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "evidence.zip")
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "manifest.json" in names
        for tid in TEST_REGISTRY:
            assert f"receipts/{tid}.json" in names

    def test_manifest_lists_member_hashes(self, tmp_path):
        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "evidence.zip")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["evidence_level"] == EVIDENCE_LEVEL_B
        assert manifest["summary"]["complete"] is True
        # Every receipt member must have a 64-char sha256.
        for name, digest in manifest["members"].items():
            assert len(digest) == 64, name

    def test_member_hashes_match_actual_content(self, tmp_path):
        """The manifest hashes must match the bytes actually in the zip."""
        import hashlib

        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "evidence.zip")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            for name, digest in manifest["members"].items():
                actual = hashlib.sha256(zf.read(name)).hexdigest()
                assert actual == digest, f"hash mismatch for {name}"

    def test_sidecar_digest_written(self, tmp_path):
        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "evidence.zip")
        sidecar = out.with_suffix(out.suffix + ".sha256")
        assert sidecar.exists()
        content = sidecar.read_text(encoding="utf-8")
        assert out.name in content
        assert len(content.split()[0]) == 64

    def test_bundle_is_deterministic(self, tmp_path):
        """Same receipts -> identical member hashes (reproducible evidence)."""
        em1 = self._full_emitter()
        em2 = self._full_emitter()
        # Freeze fingerprints so only content differs.
        fp = BuildFingerprint(captured_at="FIXED")
        em1.fingerprint = fp
        em2.fingerprint = fp
        out1 = em1.write_bundle(tmp_path / "a.zip", deterministic=True)
        out2 = em2.write_bundle(tmp_path / "b.zip", deterministic=True)
        with zipfile.ZipFile(out1) as z1, zipfile.ZipFile(out2) as z2:
            m1 = json.loads(z1.read("manifest.json"))
            m2 = json.loads(z2.read("manifest.json"))
        assert m1["members"] == m2["members"]
        # The whole manifest (incl. generated_at) must match too.
        assert m1 == m2

    def test_deterministic_bundle_is_byte_identical(self, tmp_path):
        """deterministic=True -> the two zips are byte-for-byte identical."""
        em1 = self._full_emitter()
        em2 = self._full_emitter()
        fp = BuildFingerprint(captured_at="FIXED")
        em1.fingerprint = fp
        em2.fingerprint = fp
        out1 = em1.write_bundle(tmp_path / "a.zip", deterministic=True)
        out2 = em2.write_bundle(tmp_path / "b.zip", deterministic=True)
        assert out1.read_bytes() == out2.read_bytes()

    def test_deterministic_pins_fingerprint_captured_at(self, tmp_path):
        """Regression: differing fingerprint.captured_at must not leak into the
        deterministic bundle.

        The fingerprint's wall-clock `captured_at` is embedded in every receipt
        AND in the manifest. If write_bundle does not pin it, two runs with
        different capture times produce different bytes even though the logical
        inputs are identical. This test uses *differing* captured_at values
        (unlike the other determinism tests, which pre-freeze the fingerprint)
        so the pinning is actually exercised.
        """
        em1 = self._full_emitter()
        em2 = self._full_emitter()
        em1.fingerprint = BuildFingerprint(captured_at="2026-01-01T00:00:00+0000")
        em2.fingerprint = BuildFingerprint(captured_at="2026-09-18T23:59:59+0900")
        out1 = em1.write_bundle(tmp_path / "a.zip", deterministic=True)
        out2 = em2.write_bundle(tmp_path / "b.zip", deterministic=True)
        # Byte-identical despite the differing capture times.
        assert out1.read_bytes() == out2.read_bytes()
        with zipfile.ZipFile(out1) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            t01 = json.loads(zf.read("receipts/T01.json"))
        assert manifest["fingerprint"]["captured_at"] == "1970-01-01T00:00:00+0000"
        assert t01["fingerprint"]["captured_at"] == "1970-01-01T00:00:00+0000"

    def test_non_deterministic_keeps_wall_clock(self, tmp_path):
        """Default (deterministic=False) keeps a real generated_at timestamp."""
        em = self._full_emitter()
        out = em.write_bundle(tmp_path / "live.zip")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["generated_at"] != "1970-01-01T00:00:00+0000"

    def test_partial_run_still_produces_bundle(self, tmp_path):
        em = EvidenceReceiptEmitter()
        em.record("T01", STATUS_PASS)
        out = em.write_bundle(tmp_path / "partial.zip")
        with zipfile.ZipFile(out) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["summary"]["complete"] is False
        assert "T02" in manifest["summary"]["missing"]


# ---------------------------------------------------------------------------
# One-shot helper
# ---------------------------------------------------------------------------
class TestEmitHelper:
    def test_emit_from_dicts(self, tmp_path):
        receipts = [
            {"test_id": "T01", "status": STATUS_PASS, "evidence": {"ok": True}},
            {"test_id": "T07", "status": STATUS_PASS, "evidence": {"blocked": True}},
        ]
        out = emit_evidence_bundle(receipts, tmp_path / "bundle.zip")
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert "receipts/T01.json" in zf.namelist()
            assert "receipts/T07.json" in zf.namelist()

    def test_emit_defaults_missing_status_to_skip(self, tmp_path):
        out = emit_evidence_bundle([{"test_id": "T01"}], tmp_path / "b.zip")
        with zipfile.ZipFile(out) as zf:
            r = json.loads(zf.read("receipts/T01.json"))
        assert r["status"] == STATUS_SKIP


# ---------------------------------------------------------------------------
# Receipt serialization
# ---------------------------------------------------------------------------
class TestReceiptSerialization:
    def test_to_dict_roundtrip(self):
        r = Receipt(test_id="T01", status=STATUS_PASS, evidence={"a": 1})
        d = r.to_dict()
        assert d["test_id"] == "T01"
        assert d["evidence"] == {"a": 1}

    def test_to_json_is_valid_json(self):
        r = Receipt(test_id="T01", status=STATUS_PASS)
        parsed = json.loads(r.to_json())
        assert parsed["test_id"] == "T01"
