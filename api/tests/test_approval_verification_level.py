"""Regression tests for P0-2 — Approval vs. Verification separation.

Audit R14 / mutual-IP spec v1.3 T08.

The core invariant under test:

    APPROVED  ≠  VERIFIED

A skill's *lifecycle* ("approved") is a human decision. Its *verification
level* (A~E) is evidence that some check actually ran. These are two separate
axes. A promoted skill must never silently acquire a verification grade it was
not explicitly given.

These tests exercise the real ``SkillRegistry`` code paths (no mocks of the
logic under test) so a regression in the separation is caught.
"""
import json
import sys
from pathlib import Path

import pytest

# The repo root is on sys.path, so the real importable module path is
# ``api.api.skill_registry`` (not ``api.skill_registry``).
from api.api import skill_registry as sr
from api.api.skill_registry import (
    SkillEntry,
    SkillRegistry,
    SKILL_APPROVED,
    SKILL_DRAFT,
    SKILL_REJECTED,
    DEFAULT_VERIFICATION_LEVEL,
    VERIFICATION_LEVELS,
    VERIFICATION_LEVEL_LIFECYCLE_ONLY,
    VERIFICATION_LEVEL_STATIC_VALIDATION,
    VERIFICATION_LEVEL_RUNTIME_SELF_TEST,
    VERIFICATION_LEVEL_EXPECTED_OUTPUT_VALIDATION,
    VERIFICATION_LEVEL_INDEPENDENT_VERIFICATION,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _entry(name: str, lifecycle: str = SKILL_APPROVED, source: str = "auto",
           verification_level: str = DEFAULT_VERIFICATION_LEVEL) -> SkillEntry:
    return SkillEntry(
        name=name,
        path=Path(f"/tmp/{name}/SKILL.md"),
        title=f"Skill {name}",
        source=source,
        content="# body",
        lifecycle=lifecycle,
        verification_level=verification_level,
    )


def _registry_with(*entries: SkillEntry) -> SkillRegistry:
    reg = SkillRegistry.__new__(SkillRegistry)
    reg._skills = {e.name: e for e in entries}
    return reg


@pytest.fixture
def auto_dir(tmp_path, monkeypatch):
    """Point the auto-skills dir at a temp dir so manifests are isolated."""
    auto = tmp_path / "auto"
    auto.mkdir()
    monkeypatch.setattr(sr, "_resolve_auto_skills_dir", lambda: auto)
    monkeypatch.setattr(sr, "_get_all_auto_skills_dirs", lambda: [auto])
    monkeypatch.setattr(sr, "_resolve_profile_auto_skills_dir", lambda: None)
    return auto


# --------------------------------------------------------------------------
# 1. The constant table itself
# --------------------------------------------------------------------------

class TestVerificationLevelConstants:
    def test_all_five_levels_defined(self):
        assert set(VERIFICATION_LEVELS.keys()) == {"A", "B", "C", "D", "E"}

    def test_level_names_are_stable(self):
        assert VERIFICATION_LEVELS["A"] == "LIFECYCLE_ONLY"
        assert VERIFICATION_LEVELS["B"] == "STATIC_VALIDATION"
        assert VERIFICATION_LEVELS["C"] == "RUNTIME_SELF_TEST"
        assert VERIFICATION_LEVELS["D"] == "EXPECTED_OUTPUT_VALIDATION"
        assert VERIFICATION_LEVELS["E"] == "INDEPENDENT_VERIFICATION"

    def test_default_is_lifecycle_only(self):
        # The default must be the WEAKEST grade — never a verified one.
        assert DEFAULT_VERIFICATION_LEVEL == VERIFICATION_LEVEL_LIFECYCLE_ONLY
        assert DEFAULT_VERIFICATION_LEVEL == "A"

    def test_named_constants_match_table(self):
        assert VERIFICATION_LEVEL_LIFECYCLE_ONLY == "A"
        assert VERIFICATION_LEVEL_STATIC_VALIDATION == "B"
        assert VERIFICATION_LEVEL_RUNTIME_SELF_TEST == "C"
        assert VERIFICATION_LEVEL_EXPECTED_OUTPUT_VALIDATION == "D"
        assert VERIFICATION_LEVEL_INDEPENDENT_VERIFICATION == "E"


# --------------------------------------------------------------------------
# 2. SkillEntry carries the field independently of lifecycle
# --------------------------------------------------------------------------

class TestSkillEntryVerificationField:
    def test_defaults_to_level_a(self):
        e = _entry("s1")
        assert e.verification_level == "A"

    def test_lifecycle_and_verification_are_independent(self):
        # An APPROVED skill can still be level A (unverified).
        e = _entry("s1", lifecycle=SKILL_APPROVED, verification_level="A")
        assert e.lifecycle == SKILL_APPROVED
        assert e.verification_level == "A"

    def test_explicit_level_is_stored(self):
        e = _entry("s1", verification_level="E")
        assert e.verification_level == "E"

    def test_none_falls_back_to_default(self):
        e = _entry("s1", verification_level=None)
        assert e.verification_level == DEFAULT_VERIFICATION_LEVEL

    def test_field_is_in_slots(self):
        # __slots__ must include the new field or assignment would raise.
        assert "verification_level" in SkillEntry.__slots__


# --------------------------------------------------------------------------
# 3. promote_skill records the level in the manifest
# --------------------------------------------------------------------------

class TestPromoteRecordsVerificationLevel:
    def test_promote_defaults_to_level_a(self, auto_dir):
        reg = _registry_with(_entry("auto_x", lifecycle=SKILL_DRAFT))
        assert reg.promote_skill("auto_x", SKILL_APPROVED) is True

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        rec = manifest["auto_x"]
        assert rec["status"] == SKILL_APPROVED
        assert rec["verification_level"] == "A"
        assert rec["verification_level_name"] == "LIFECYCLE_ONLY"

    def test_promote_with_explicit_level(self, auto_dir):
        reg = _registry_with(_entry("auto_x", lifecycle=SKILL_DRAFT))
        assert reg.promote_skill("auto_x", SKILL_APPROVED, "C") is True

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        rec = manifest["auto_x"]
        assert rec["verification_level"] == "C"
        assert rec["verification_level_name"] == "RUNTIME_SELF_TEST"

    def test_promote_updates_entry_in_memory(self, auto_dir):
        e = _entry("auto_x", lifecycle=SKILL_DRAFT)
        reg = _registry_with(e)
        reg.promote_skill("auto_x", SKILL_APPROVED, "D")
        assert e.lifecycle == SKILL_APPROVED
        assert e.verification_level == "D"

    def test_unknown_level_does_not_inflate_grade(self, auto_dir):
        # A typo must never silently upgrade the grade.
        e = _entry("auto_x", lifecycle=SKILL_DRAFT, verification_level="A")
        reg = _registry_with(e)
        assert reg.promote_skill("auto_x", SKILL_APPROVED, "Z") is True

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        assert manifest["auto_x"]["verification_level"] == "A"
        assert e.verification_level == "A"

    def test_promote_keeps_existing_level_when_omitted(self, auto_dir):
        e = _entry("auto_x", lifecycle=SKILL_DRAFT, verification_level="B")
        reg = _registry_with(e)
        reg.promote_skill("auto_x", SKILL_APPROVED)
        assert e.verification_level == "B"

    def test_reject_also_records_level(self, auto_dir):
        reg = _registry_with(_entry("auto_x", lifecycle=SKILL_DRAFT))
        assert reg.reject_skill("auto_x") is True
        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        assert manifest["auto_x"]["status"] == SKILL_REJECTED
        assert "verification_level" in manifest["auto_x"]


# --------------------------------------------------------------------------
# 4. register_new_auto_skill records the level
# --------------------------------------------------------------------------

class TestRegisterRecordsVerificationLevel:
    def test_register_defaults_to_level_a(self, auto_dir):
        skill_file = auto_dir / "auto_new" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# body", encoding="utf-8")

        SkillRegistry.register_new_auto_skill(skill_file, lifecycle=SKILL_APPROVED)

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        rec = manifest["auto_new"]
        assert rec["status"] == SKILL_APPROVED
        assert rec["verification_level"] == "A"
        assert rec["verification_level_name"] == "LIFECYCLE_ONLY"

    def test_register_with_explicit_level(self, auto_dir):
        skill_file = auto_dir / "auto_new" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# body", encoding="utf-8")

        SkillRegistry.register_new_auto_skill(
            skill_file, lifecycle=SKILL_APPROVED, verification_level="E"
        )

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        assert manifest["auto_new"]["verification_level"] == "E"
        assert manifest["auto_new"]["verification_level_name"] == "INDEPENDENT_VERIFICATION"

    def test_register_unknown_level_falls_back_to_a(self, auto_dir):
        skill_file = auto_dir / "auto_new" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# body", encoding="utf-8")

        SkillRegistry.register_new_auto_skill(
            skill_file, lifecycle=SKILL_APPROVED, verification_level="Q"
        )

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        assert manifest["auto_new"]["verification_level"] == "A"


# --------------------------------------------------------------------------
# 5. The audit scenario — the whole point of P0-2
# --------------------------------------------------------------------------

class TestAuditScenarioApprovedIsNotVerified:
    def test_user_approved_save_is_not_verified(self, auto_dir):
        """A user clicking 'save as skill' produces APPROVED + level A.

        This is the exact false-PASS the audit flagged: the product must not
        present an approved-but-unverified skill as if it were verified.
        """
        skill_file = auto_dir / "auto_user_save" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# body", encoding="utf-8")

        # This mirrors api/dynamic/skill_extractor.py's call site.
        SkillRegistry.register_new_auto_skill(
            skill_file,
            lifecycle=SKILL_APPROVED,
            verification_level=VERIFICATION_LEVEL_LIFECYCLE_ONLY,
        )

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        rec = manifest["auto_user_save"]

        # Lifecycle says approved...
        assert rec["status"] == SKILL_APPROVED
        # ...but verification says "no check ran".
        assert rec["verification_level"] == "A"
        assert rec["verification_level_name"] == "LIFECYCLE_ONLY"
        # The two axes are distinguishable in the record.
        assert rec["status"] != rec["verification_level"]

    def test_manifest_distinguishes_approved_from_verified(self, auto_dir):
        """Two skills can both be APPROVED yet carry different grades."""
        for name, level in (("auto_a", "A"), ("auto_e", "E")):
            skill_file = auto_dir / name / "SKILL.md"
            skill_file.parent.mkdir(parents=True)
            skill_file.write_text("# body", encoding="utf-8")
            SkillRegistry.register_new_auto_skill(
                skill_file, lifecycle=SKILL_APPROVED, verification_level=level
            )

        manifest = json.loads((auto_dir / sr._MANIFEST_FILE).read_text(encoding="utf-8"))
        assert manifest["auto_a"]["status"] == manifest["auto_e"]["status"] == SKILL_APPROVED
        assert manifest["auto_a"]["verification_level"] == "A"
        assert manifest["auto_e"]["verification_level"] == "E"
