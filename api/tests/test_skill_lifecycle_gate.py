"""P0-3 regression tests — REJECTED lifecycle enforcement (audit R14 / spec v1.3 T07).

The audit's A3 finding: a REJECTED skill could still be loaded via the
direct/router/forced paths. These tests prove the NORMAL_USER_PATH blocks
REJECTED (and DRAFT) skills, while the diagnostic/forced path can still reach
them via an explicit opt-in — and that the two paths are distinguishable.
"""

from pathlib import Path

import pytest

from api.api.skill_registry import (
    SKILL_APPROVED,
    SKILL_DRAFT,
    SKILL_REJECTED,
    SKILL_REVIEW,
    SkillEntry,
    SkillRegistry,
)


def _entry(name: str, lifecycle: str, source: str = "auto") -> SkillEntry:
    return SkillEntry(
        name=name,
        path=Path(f"/tmp/{name}/SKILL.md"),
        title=f"{name} title",
        source=source,
        content=f"# {name}\n\nbody of {name}",
        lifecycle=lifecycle,
    )


def _registry_with(*entries: SkillEntry) -> SkillRegistry:
    reg = SkillRegistry.__new__(SkillRegistry)
    reg._skills = {e.name: e for e in entries}
    return reg


# ---------------------------------------------------------------------------
# load_skills — normal path vs forced path
# ---------------------------------------------------------------------------

class TestLoadSkillsGate:
    def test_rejected_blocked_on_normal_path(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        out = reg.load_skills(["bad-skill"])
        assert out == ""
        assert "bad-skill" not in out

    def test_draft_blocked_on_normal_path(self):
        reg = _registry_with(_entry("draft-skill", SKILL_DRAFT))
        out = reg.load_skills(["draft-skill"])
        assert out == ""

    def test_approved_loaded_on_normal_path(self):
        reg = _registry_with(_entry("good-skill", SKILL_APPROVED))
        out = reg.load_skills(["good-skill"])
        assert "good-skill" in out
        assert "body of good-skill" in out

    def test_rejected_loaded_on_forced_path(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        out = reg.load_skills(["bad-skill"], include_rejected=True)
        assert "bad-skill" in out
        assert "body of bad-skill" in out

    def test_mixed_list_filters_only_rejected(self):
        reg = _registry_with(
            _entry("good-skill", SKILL_APPROVED),
            _entry("bad-skill", SKILL_REJECTED),
        )
        out = reg.load_skills(["good-skill", "bad-skill"])
        assert "good-skill" in out
        assert "bad-skill" not in out

    def test_curated_always_loaded(self):
        reg = _registry_with(_entry("curated-skill", SKILL_APPROVED, source="curated"))
        out = reg.load_skills(["curated-skill"])
        assert "curated-skill" in out


# ---------------------------------------------------------------------------
# load_skills_for_reviewer
# ---------------------------------------------------------------------------

class TestReviewerGate:
    def test_rejected_blocked_for_reviewer(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        out = reg.load_skills_for_reviewer(["bad-skill"])
        assert out == ""

    def test_rejected_visible_for_reviewer_when_opted_in(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        out = reg.load_skills_for_reviewer(["bad-skill"], include_rejected=True)
        assert "bad-skill" in out


# ---------------------------------------------------------------------------
# get_skill
# ---------------------------------------------------------------------------

class TestGetSkillGate:
    def test_rejected_returns_none_on_normal_path(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        assert reg.get_skill("bad-skill") is None

    def test_rejected_returned_on_diagnostic_path(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        entry = reg.get_skill("bad-skill", include_rejected=True)
        assert entry is not None
        assert entry.lifecycle == SKILL_REJECTED

    def test_approved_returned_normally(self):
        reg = _registry_with(_entry("good-skill", SKILL_APPROVED))
        assert reg.get_skill("good-skill") is not None

    def test_review_state_reachable_but_not_in_default_catalog(self):
        # REVIEW is "under evaluation" — it is NOT a hard block (only REJECTED
        # and DRAFT are gated), but it must never appear in the default catalog.
        reg = _registry_with(_entry("review-skill", SKILL_REVIEW))
        assert reg.get_skill("review-skill") is not None
        assert "review-skill" not in reg.get_catalog_text()


# ---------------------------------------------------------------------------
# get_catalog_text
# ---------------------------------------------------------------------------

class TestCatalogGate:
    def test_rejected_absent_from_default_catalog(self):
        reg = _registry_with(
            _entry("good-skill", SKILL_APPROVED),
            _entry("bad-skill", SKILL_REJECTED),
        )
        catalog = reg.get_catalog_text()
        assert "bad-skill" not in catalog
        assert "good-skill" in catalog

    def test_rejected_in_diagnostic_catalog_labelled(self):
        reg = _registry_with(_entry("bad-skill", SKILL_REJECTED))
        catalog = reg.get_catalog_text(include_rejected=True)
        assert "bad-skill" in catalog
        assert "REJECTED" in catalog
        assert "NOT selectable" in catalog


# ---------------------------------------------------------------------------
# End-to-end: the audit's exact scenario (T07 acceptance matrix)
# ---------------------------------------------------------------------------

class TestAuditScenario:
    def test_normal_and_forced_paths_are_distinguishable(self):
        """Audit A3: a REJECTED skill must yield MODEL_CONTEXT_INCLUDED=false on
        the normal path, but remain reachable on the forced/diagnostic path."""
        reg = _registry_with(_entry("rejected-skill", SKILL_REJECTED))

        # NORMAL_USER_PATH
        normal_out = reg.load_skills(["rejected-skill"])
        normal_included = "rejected-skill" in normal_out
        assert normal_included is False

        # INTERNAL_FORCED_PATH / DEBUG_PATH
        forced_out = reg.load_skills(["rejected-skill"], include_rejected=True)
        forced_included = "rejected-skill" in forced_out
        assert forced_included is True

        # The two paths must NOT be summed into one verdict.
        assert normal_included != forced_included
