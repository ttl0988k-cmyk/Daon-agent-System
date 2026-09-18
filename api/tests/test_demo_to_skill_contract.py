"""P0-1 regression tests — Demo-to-Skill output contract hardening.

Audit R14 (FAIL - FINAL_GENERATION_CONTRACT_FAILURE) root cause:
    SkillAnalyzer.analyze() only checked for the *existence* of the
    "frontmatter" key, and SkillWriter.write() did
        content = "\\n".join(yaml_lines) + "\\n\\n" + body
    assuming ``body`` is a ``str``. When the LLM returned a dict/list/int/null
    body, the writer raised ``TypeError`` and the whole skill generation failed.

Contract after P0-1:
    * ``SkillWriter.write()`` NEVER raises on a bad body type — it coerces.
    * The ONLY permitted failure is real disk I/O.
    * ``_normalize_skill_data()`` records the violation in ``_meta`` so a
      downstream receipt (spec v1.3 T01) can prove what happened.

These tests are pure-unit: they do not call an LLM and do not require the
hermes-agent runtime. They exercise the coercion + writer path directly.
"""

import sys
from pathlib import Path

import pytest

# Make the repo root importable so ``api.api.demo_to_skill`` resolves.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api.api.demo_to_skill import (  # noqa: E402
    ANALYZER_OUTPUT_SCHEMA_VERSION,
    SkillWriter,
    _coerce_body_to_text,
    _normalize_skill_data,
)


# ---------------------------------------------------------------------------
# _coerce_body_to_text — the defensive layer
# ---------------------------------------------------------------------------
class TestCoerceBodyToText:
    def test_str_passthrough(self):
        assert _coerce_body_to_text("# Hello\n\nworld") == "# Hello\n\nworld"

    def test_none_becomes_empty_string(self):
        assert _coerce_body_to_text(None) == ""

    def test_int_becomes_string(self):
        assert _coerce_body_to_text(42) == "42"

    def test_bool_becomes_string(self):
        assert _coerce_body_to_text(True) == "True"

    def test_list_becomes_bullets(self):
        out = _coerce_body_to_text(["alpha", "beta"])
        assert "- alpha" in out
        assert "- beta" in out

    def test_list_of_dicts_is_json_encoded(self):
        out = _coerce_body_to_text([{"step": 1}])
        assert "step" in out

    def test_dict_with_markdown_key_extracts_markdown(self):
        out = _coerce_body_to_text({"markdown": "# Real Body"})
        assert out == "# Real Body"

    def test_dict_without_markdown_renders_sections(self):
        out = _coerce_body_to_text({"purpose": "do a thing", "steps": ["a", "b"]})
        assert "## Purpose" in out
        assert "do a thing" in out
        assert "- a" in out

    def test_never_raises_on_arbitrary_object(self):
        class Weird:
            def __str__(self):
                return "weird"

        assert _coerce_body_to_text(Weird()) == "weird"


# ---------------------------------------------------------------------------
# _normalize_skill_data — the envelope
# ---------------------------------------------------------------------------
class TestNormalizeSkillData:
    def test_valid_input_is_noop(self):
        raw = {"frontmatter": {"name": "x"}, "body": "# body"}
        out = _normalize_skill_data(raw)
        assert out["frontmatter"] == {"name": "x"}
        assert out["body"] == "# body"
        assert out["_meta"]["normalization_result"] == "noop"
        assert out["_meta"]["schema_validation_result"] == "pass"
        assert out["_meta"]["schema_version"] == ANALYZER_OUTPUT_SCHEMA_VERSION

    def test_dict_body_is_coerced_and_recorded(self):
        raw = {"frontmatter": {"name": "x"}, "body": {"markdown": "# md"}}
        out = _normalize_skill_data(raw)
        assert out["body"] == "# md"
        assert out["_meta"]["normalization_result"] == "coerced"
        assert out["_meta"]["parsed_body_type"] == "dict"
        assert out["_meta"]["schema_validation_result"] == "fail_recovered"

    def test_missing_body_is_recovered(self):
        out = _normalize_skill_data({"frontmatter": {"name": "x"}})
        assert out["body"] == ""
        assert out["_meta"]["parsed_body_type"] == "NoneType"

    def test_non_dict_input_does_not_raise(self):
        out = _normalize_skill_data("not a dict")
        assert isinstance(out["frontmatter"], dict)
        assert isinstance(out["body"], str)

    def test_meta_records_lengths(self):
        out = _normalize_skill_data({"frontmatter": {}, "body": "abc"})
        assert out["_meta"]["body_length_before"] == 3
        assert out["_meta"]["body_length_after"] == 3


# ---------------------------------------------------------------------------
# SkillWriter.write — the contract that must never break
# ---------------------------------------------------------------------------
BODY_CASES = [
    ("str", "# Title\n\nSome markdown body."),
    ("dict", {"markdown": "# From Dict\n\nbody"}),
    ("list", ["step one", "step two"]),
    ("null", None),
    ("int", 12345),
]


def _isolate_writer(monkeypatch, tmp_path):
    """Redirect SkillWriter's lazy imports into tmp_path.

    ``SkillWriter.write()`` does ``from api.skill_registry import ...`` at call
    time. The real module lives at ``api.api.skill_registry`` (the repo root is
    on sys.path, so ``api.skill_registry`` is NOT importable). We therefore
    patch the *real* module's functions, which the lazy import will pick up.
    """
    import api.api.skill_registry as reg

    monkeypatch.setattr(reg, "_resolve_profile_auto_skills_dir", lambda: None)
    monkeypatch.setattr(reg, "_resolve_auto_skills_dir", lambda: tmp_path)
    # Registration is best-effort; keep it from touching the real manifest.
    monkeypatch.setattr(
        reg.SkillRegistry, "register_new_auto_skill", staticmethod(lambda *a, **k: None)
    )


@pytest.mark.parametrize("label,body", BODY_CASES, ids=[c[0] for c in BODY_CASES])
def test_writer_generates_file_for_every_body_type(tmp_path, monkeypatch, label, body):
    """The 5-type regression: every body type must produce a SKILL.md."""
    _isolate_writer(monkeypatch, tmp_path)

    skill_data = {"frontmatter": {"name": f"contract-{label}"}, "body": body}
    path = SkillWriter.write(skill_data, skill_name=f"contract-{label}")

    assert path.exists(), f"[{label}] SKILL.md was not created"
    content = path.read_text(encoding="utf-8")
    assert content.startswith("---"), f"[{label}] missing frontmatter fence"
    assert "name:" in content, f"[{label}] missing name field"
    # The body must be present as text (never a raw Python repr of a dict).
    assert "{" not in content.split("---", 2)[-1] or label != "dict"


def test_writer_does_not_raise_on_dict_body(tmp_path, monkeypatch):
    """Direct reproduction of the R14 TypeError, now fixed."""
    _isolate_writer(monkeypatch, tmp_path)

    # This exact shape used to raise: TypeError: can only concatenate str
    # (not "dict") to str
    path = SkillWriter.write(
        {"frontmatter": {"name": "r14-repro"}, "body": {"markdown": "# ok"}},
        skill_name="r14-repro",
    )
    assert path.exists()
    assert "# ok" in path.read_text(encoding="utf-8")
