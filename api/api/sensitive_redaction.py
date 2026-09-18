"""Sensitive field redaction for the Demo-to-Skill pipeline.

Audit R14 / mutual-IP spec v1.3 (T10) requires that secrets captured during a
user demonstration (passwords, OTPs, tokens, API keys, emails) NEVER reach the
model prompt, the persisted skill, or any log/receipt.

Design decision (see docs/PATCH_PLAN_AUDIT_R14_20260918.md §P0-4):
    Redaction happens at a SINGLE CHOKE POINT — immediately before the prompt is
    assembled — not at capture time. This keeps the raw capture intact for local
    debugging while guaranteeing the model never sees a secret.

The module is intentionally dependency-free (stdlib only) so it can be imported
from any layer without creating import cycles.

Public API:
    redact_events(events) -> (redacted_events, report)
    redact_text(text) -> (redacted_text, hits)
    build_trust_flags(...) -> dict[str, bool]
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Placeholder format. The class name is preserved so downstream tooling can
#: still reason about *what kind* of value was removed without seeing it.
REDACTION_TEMPLATE = "«REDACTED:{cls}»"

#: Field names (case-insensitive substring match) that mark a value as secret.
SENSITIVE_FIELD_TOKENS = (
    "password",
    "passwd",
    "pwd",
    "otp",
    "token",
    "secret",
    "cvv",
    "cvc",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "passphrase",
    "pin",
)

#: Regex patterns applied to free text. Order matters: the first match wins.
_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Explicit test/placeholder markers used by the audit harness.
    ("FAKE", re.compile(r"FAKE_[A-Za-z0-9_\-]+")),
    # OpenAI-style secret keys.
    ("APIKEY", re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")),
    # Bearer tokens.
    ("BEARER", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)),
    # JWT (three base64url segments).
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    # key=value / key: value assignments for sensitive keys.
    (
        "ASSIGNMENT",
        re.compile(
            r"(?i)\b(password|passwd|pwd|otp|token|secret|api_key|apikey|"
            r"access_key|private_key|cvv|cvc|pin)\b\s*[:=]\s*"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
        ),
    ),
    # Email addresses.
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # 6-8 digit standalone OTP-like numbers (only when labelled nearby is hard;
    # keep this conservative to avoid nuking coordinates/timestamps).
    ("OTP", re.compile(r"(?i)\botp\b\D{0,4}(\d{4,8})\b")),
)

#: Keys whose *values* are always redacted regardless of content.
_ALWAYS_REDACT_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "otp",
        "token",
        "secret",
        "cvv",
        "cvc",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "passphrase",
        "pin",
    }
)

#: Keys that carry a value whose sensitivity is determined by a SIBLING key
#: (e.g. ``{"name": "password", "value": "..."}``). We only redact ``value``
#: when a sibling ``name``/``id``/``field``/``label`` is itself sensitive —
#: otherwise we would destroy every legitimate input value.
_CONTEXT_VALUE_KEYS = frozenset({"value", "text", "input"})
_CONTEXT_NAME_KEYS = ("name", "id", "field", "label", "type", "selector")


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _classify_key(key: str) -> Optional[str]:
    """Return a redaction class for a dict key, or None if not sensitive."""
    if not isinstance(key, str):
        return None
    low = key.lower()
    if low in _ALWAYS_REDACT_KEYS:
        return low.upper()
    for tok in SENSITIVE_FIELD_TOKENS:
        if tok in low:
            return tok.upper()
    return None


def redact_text(text: str) -> tuple[str, list[str]]:
    """Redact secrets from a free-text string.

    Returns the redacted string and the list of redaction classes that hit.
    """
    if not isinstance(text, str) or not text:
        return text, []
    hits: list[str] = []
    out = text
    for cls, pattern in _TEXT_PATTERNS:
        def _sub(match: re.Match[str], _cls: str = cls) -> str:
            hits.append(_cls)
            return REDACTION_TEMPLATE.format(cls=_cls)

        out = pattern.sub(_sub, out)
    return out, hits


def _sibling_sensitive_class(obj: dict) -> Optional[str]:
    """If a dict has a sensitive sibling name/id, return its redaction class."""
    for nk in _CONTEXT_NAME_KEYS:
        cls = _classify_key(obj.get(nk)) if isinstance(obj.get(nk), str) else None
        if cls:
            return cls
    return None


def _redact_value(value: Any, key: Optional[str], hits: list[str]) -> Any:
    """Recursively redact a single value, honouring its key context."""
    key_cls = _classify_key(key) if key else None

    if isinstance(value, dict):
        # A ``value``/``text``/``input`` key is only a secret when a sibling
        # name/id marks the field as sensitive (e.g. {"name":"password",...}).
        sibling_cls = _sibling_sensitive_class(value)
        out: dict = {}
        for k, v in value.items():
            if (
                isinstance(k, str)
                and k.lower() in _CONTEXT_VALUE_KEYS
                and sibling_cls
                and isinstance(v, str)
                and v
            ):
                hits.append(sibling_cls)
                out[k] = REDACTION_TEMPLATE.format(cls=sibling_cls)
            else:
                out[k] = _redact_value(v, k, hits)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, key, hits) for v in value]

    if isinstance(value, str):
        # A sensitive key means the whole value is a secret — do not even try
        # to pattern-match it, just replace it wholesale.
        if key_cls:
            hits.append(key_cls)
            return REDACTION_TEMPLATE.format(cls=key_cls)
        redacted, text_hits = redact_text(value)
        hits.extend(text_hits)
        return redacted

    # Non-string scalars under a sensitive key are still secrets.
    if key_cls and value is not None:
        hits.append(key_cls)
        return REDACTION_TEMPLATE.format(cls=key_cls)

    return value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def redact_events(events: Iterable[dict]) -> tuple[list[dict], dict]:
    """Redact a list of captured event dicts.

    Returns ``(redacted_events, report)`` where ``report`` is a JSON-safe dict
    describing what was removed — WITHOUT ever containing the original values.
    """
    redacted: list[dict] = []
    all_hits: list[str] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            redacted.append(ev)
            continue
        redacted.append(_redact_value(ev, None, all_hits))

    counts: dict[str, int] = {}
    for cls in all_hits:
        counts[cls] = counts.get(cls, 0) + 1

    report = {
        "redaction_applied": bool(all_hits),
        "redaction_hit_count": len(all_hits),
        "redaction_classes": sorted(counts.keys()),
        "redaction_class_counts": counts,
    }
    return redacted, report


def build_trust_flags(
    *,
    dom_captured: bool = False,
    local_bridge: bool = False,
    model_prompt: bool = False,
    persisted_skill: bool = False,
    logged: bool = False,
    external_provider_sent: bool = False,
) -> dict[str, bool]:
    """Emit the spec v1.3 T10 trust-boundary flags (existence only, no body).

    These flags answer "did the captured data cross this boundary?" so a
    verifier can assert e.g. ``MODEL_PROMPT=false`` for a redacted secret.
    """
    return {
        "DOM_CAPTURED": bool(dom_captured),
        "LOCAL_BRIDGE": bool(local_bridge),
        "MODEL_PROMPT": bool(model_prompt),
        "PERSISTED_SKILL": bool(persisted_skill),
        "LOGGED": bool(logged),
        "EXTERNAL_PROVIDER_SENT": bool(external_provider_sent),
    }
