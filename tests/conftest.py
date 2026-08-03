"""Shared test fixtures for the JAMES test suite.

Keeps the workspace-isolation fixture (previously copy-pasted across modules)
in one place, plus helpers for exercising the plugin SDK and marketplace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from james.config import settings


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every workspace-backed setting at an isolated temp directory."""
    monkeypatch.setattr(settings.assistant, "workspace_dir", tmp_path)
    monkeypatch.setattr(settings.assistant, "history_file", tmp_path / "history.enc")
    monkeypatch.setattr(settings.assistant, "audit_log", tmp_path / "audit.log")
    monkeypatch.setattr(settings.assistant, "egress_audit_log", tmp_path / "egress.log")
    monkeypatch.setattr(settings.assistant, "memory_file", tmp_path / "memory.jsonl")
    monkeypatch.setattr(settings.assistant, "memory_enabled", True)
    monkeypatch.setattr(settings.assistant, "offline_mode", False)
    return tmp_path


@pytest.fixture
def plugin_dir(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the Skill Forge plugins dir into the isolated workspace."""
    from james.tools import forge_tools

    directory = isolated_workspace / "plugins"
    monkeypatch.setattr(forge_tools, "_PLUGINS_DIR", directory)
    return directory


@pytest.fixture
def marketplace_file(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the marketplace catalog file into the isolated workspace."""
    path = isolated_workspace / "marketplace.json"
    monkeypatch.setattr("james.tools.marketplace._MARKETPLACE_FILE", path)
    return path
