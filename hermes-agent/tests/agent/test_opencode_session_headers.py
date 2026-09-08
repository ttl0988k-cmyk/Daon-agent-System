"""Tests for OpenCode session and User-Agent headers.

Validates that x-opencode-session and User-Agent are automatically injected
into OpenAI and Anthropic clients when communicating with OpenCode Go and OpenCode Zen.
"""
from unittest.mock import patch, MagicMock
import pytest


def test_opencode_openai_client_headers():
    """Verify AIAgent injects x-opencode-session and User-Agent for OpenCode Go/Zen."""
    from run_agent import AIAgent

    with patch("agent.openai_client_lifecycle.build_keepalive_http_client", return_value=None), \
         patch("openai.OpenAI") as mock_openai:
        agent = AIAgent(
            base_url="https://opencode.ai/zen/go/v1",
            api_key="test-opencode-key",
            provider="opencode-go",
            model="glm-5.3-flash",
            session_id="session-test-abc-123",
            quiet_mode=True,
        )

        headers = agent._client_kwargs.get("default_headers") or {}
        assert headers.get("x-opencode-session") == "session-test-abc-123"
        assert headers.get("User-Agent") == "daon-agent/1.0"
        assert mock_openai.called
        call_kwargs = mock_openai.call_args.kwargs
        assert call_kwargs.get("default_headers", {}).get("x-opencode-session") == "session-test-abc-123"


def test_opencode_anthropic_client_headers():
    """Verify build_anthropic_client injects x-opencode-session and User-Agent for OpenCode."""
    from agent.anthropic_adapter import build_anthropic_client

    with patch("agent.anthropic_adapter._anthropic_sdk") as mock_sdk:
        build_anthropic_client(
            api_key="test-key",
            base_url="https://opencode.ai/zen/go",
            session_id="sess-anthropic-xyz",
        )
        assert mock_sdk.Anthropic.called
        call_kwargs = mock_sdk.Anthropic.call_args.kwargs
        default_headers = call_kwargs.get("default_headers", {})
        assert default_headers.get("x-opencode-session") == "sess-anthropic-xyz"
        assert default_headers.get("User-Agent") == "daon-agent/1.0"


def test_opencode_auxiliary_client_headers(monkeypatch):
    """Verify auxiliary client resolution injects headers for OpenCode."""
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    from agent.auxiliary_client import resolve_provider_client

    with patch("agent.auxiliary_client.OpenAI") as mock_openai:
        client, model = resolve_provider_client(
            provider="opencode-go",
        )
        assert mock_openai.called
        call_kwargs = mock_openai.call_args.kwargs
        default_headers = call_kwargs.get("default_headers", {})
        assert default_headers.get("x-opencode-session") == "daon-auxiliary-session"
        assert default_headers.get("User-Agent") == "daon-agent/1.0"
