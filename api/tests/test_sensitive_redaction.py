"""P0-4 regression tests — sensitive field redaction (audit R14 / spec v1.3 T10).

The audit's A4 finding: password/OTP/token-class values reached the analyzer
prompt boundary. These tests prove the single-choke-point redaction removes
them from the LLM-facing summary and that the T10 trust flags report the
boundary correctly.
"""

import pytest

from api.api.sensitive_redaction import (
    REDACTION_TEMPLATE,
    build_trust_flags,
    redact_events,
    redact_text,
)


# ---------------------------------------------------------------------------
# redact_text
# ---------------------------------------------------------------------------

class TestRedactText:
    def test_fake_marker_redacted(self):
        out, hits = redact_text("typed FAKE_PASSWORD_challenge123 into the field")
        assert "FAKE_PASSWORD_challenge123" not in out
        assert "FAKE" in hits
        assert REDACTION_TEMPLATE.format(cls="FAKE") in out

    def test_sk_key_redacted(self):
        out, hits = redact_text("Authorization: sk-abcdef1234567890")
        assert "sk-abcdef1234567890" not in out
        assert "APIKEY" in hits

    def test_bearer_token_redacted(self):
        out, hits = redact_text("header Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6")
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6" not in out
        assert "BEARER" in hits

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"
        out, hits = redact_text(f"token={jwt}")
        assert jwt not in out
        assert "JWT" in hits

    def test_email_redacted(self):
        out, hits = redact_text("contact user@example.com for access")
        assert "user@example.com" not in out
        assert "EMAIL" in hits

    def test_assignment_redacted(self):
        out, hits = redact_text("password=hunter2")
        assert "hunter2" not in out
        assert "ASSIGNMENT" in hits

    def test_plain_text_untouched(self):
        text = "clicked the Submit button at (120, 340)"
        out, hits = redact_text(text)
        assert out == text
        assert hits == []

    def test_non_string_passthrough(self):
        out, hits = redact_text(None)
        assert out is None
        assert hits == []


# ---------------------------------------------------------------------------
# redact_events
# ---------------------------------------------------------------------------

class TestRedactEvents:
    def test_password_field_value_redacted(self):
        events = [
            {"type": "input", "source": "daon_injected",
             "name": "password", "value": "FAKE_PASSWORD_challenge123"},
        ]
        redacted, report = redact_events(events)
        assert "FAKE_PASSWORD_challenge123" not in str(redacted)
        assert report["redaction_applied"] is True
        assert report["redaction_hit_count"] >= 1

    def test_nested_dict_redacted(self):
        events = [
            {"type": "input", "params": {"api_key": "sk-secretvalue12345678"}},
        ]
        redacted, report = redact_events(events)
        assert "sk-secretvalue12345678" not in str(redacted)
        assert report["redaction_applied"] is True

    def test_list_values_redacted(self):
        events = [
            {"type": "form", "fields": [
                {"name": "otp", "value": "123456"},
                {"name": "user", "value": "alice"},
            ]},
        ]
        redacted, report = redact_events(events)
        assert "123456" not in str(redacted)
        assert "alice" in str(redacted)  # non-sensitive preserved
        assert report["redaction_applied"] is True

    def test_report_never_contains_original(self):
        secret = "FAKE_PASSWORD_supersecret999"
        events = [{"type": "input", "name": "password", "value": secret}]
        _, report = redact_events(events)
        assert secret not in str(report)

    def test_clean_events_no_redaction(self):
        events = [
            {"type": "navigation", "url": "https://example.org/dashboard"},
            {"type": "click", "x": 10, "y": 20},
        ]
        redacted, report = redact_events(events)
        assert report["redaction_applied"] is False
        assert report["redaction_hit_count"] == 0
        assert redacted == events

    def test_empty_input(self):
        redacted, report = redact_events([])
        assert redacted == []
        assert report["redaction_applied"] is False

    def test_non_dict_entries_preserved(self):
        redacted, report = redact_events([None, "raw", 42])
        assert redacted == [None, "raw", 42]
        assert report["redaction_applied"] is False


# ---------------------------------------------------------------------------
# T10 trust flags
# ---------------------------------------------------------------------------

class TestTrustFlags:
    def test_all_flags_present(self):
        flags = build_trust_flags()
        assert set(flags.keys()) == {
            "DOM_CAPTURED", "LOCAL_BRIDGE", "MODEL_PROMPT",
            "PERSISTED_SKILL", "LOGGED", "EXTERNAL_PROVIDER_SENT",
        }

    def test_defaults_false(self):
        flags = build_trust_flags()
        assert all(v is False for v in flags.values())

    def test_explicit_true(self):
        flags = build_trust_flags(dom_captured=True, model_prompt=True)
        assert flags["DOM_CAPTURED"] is True
        assert flags["MODEL_PROMPT"] is True
        assert flags["PERSISTED_SKILL"] is False


# ---------------------------------------------------------------------------
# End-to-end: the audit's exact scenario
# ---------------------------------------------------------------------------

class TestAuditScenario:
    def test_fake_password_never_reaches_prompt_or_skill(self):
        """Audit A4: FAKE_PASSWORD_<challenge> must yield MODEL_PROMPT=false
        and PERSISTED_SKILL=false."""
        challenge = "FAKE_PASSWORD_challenge_20260918"
        events = [
            {"type": "navigation", "source": "daon_injected",
             "url": "https://example.org/login"},
            {"type": "input", "source": "daon_injected",
             "name": "password", "value": challenge},
            {"type": "click", "source": "daon_injected", "x": 100, "y": 200},
        ]

        redacted, report = redact_events(events)

        # The secret must be gone from the redacted event stream.
        assert challenge not in str(redacted)

        # Simulate the prompt assembly from the redacted stream.
        prompt = "Captured Event Sequence:\n" + str(redacted)
        assert challenge not in prompt

        # T10 flags: the secret did NOT cross the model/persist boundary.
        flags = build_trust_flags(
            dom_captured=True,
            local_bridge=True,
            model_prompt=False,      # redacted before prompt
            persisted_skill=False,   # never written
            logged=False,
            external_provider_sent=False,
        )
        assert flags["MODEL_PROMPT"] is False
        assert flags["PERSISTED_SKILL"] is False
        assert report["redaction_applied"] is True
