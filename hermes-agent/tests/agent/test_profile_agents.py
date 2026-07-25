"""Tests for profile-specific AGENTS.md loading in agent/prompt_builder.py"""

import pytest
from pathlib import Path
from agent.prompt_builder import _load_profile_agents_md, build_context_files_prompt


class TestProfileAgentsMd:
    def test_missing_profile_agents_md_returns_empty(self, tmp_path, monkeypatch):
        # Set HERMES_HOME to a clean temp dir
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        result = _load_profile_agents_md()
        assert result == ""

    def test_loads_profile_agents_md_successfully(self, tmp_path, monkeypatch):
        # Set HERMES_HOME and write AGENTS.md
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "AGENTS.md").write_text("Profile specific rule: Always greet with Hello.", encoding="utf-8")
        
        result = _load_profile_agents_md()
        assert "Profile-Specific Operating Rules" in result
        assert "Profile specific rule: Always greet with Hello." in result

    def test_loads_profile_agents_md_lowercase(self, tmp_path, monkeypatch):
        # Set HERMES_HOME and write agents.md
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "agents.md").write_text("Profile specific rule: Lowercase rules.", encoding="utf-8")
        
        result = _load_profile_agents_md()
        assert "Profile-Specific Operating Rules" in result
        assert "Profile specific rule: Lowercase rules." in result

    def test_build_context_files_prompt_includes_profile_agents(self, tmp_path, monkeypatch):
        # Set HERMES_HOME and write AGENTS.md
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "AGENTS.md").write_text("Greeting: Hello.", encoding="utf-8")
        
        # We also need a clean workspace to avoid loading workspace context
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        
        result = build_context_files_prompt(cwd=str(workspace_dir))
        assert "Greeting: Hello." in result
        assert "Profile-Specific Operating Rules" in result
