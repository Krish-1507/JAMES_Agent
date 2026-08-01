"""Tests for Phase 2: the closed learning loop.

Covers skill auto-application (relevant skills are re-surfaced), cross-session
recall (summaries persist to long-term memory), and the marketplace loop
(publish a skill to the catalog, then install it back through the constrained
runtime).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from james.config import settings
from james.llm.base import LLMProvider, LLMResponse

_VALID_SKILL_CODE = '''
from james.tools.base import tool, ToolResult

@tool("double_number", "Double a number.", {"value": {"type": "integer"}}, required=["value"])
def double_number(value):
    return ToolResult(ok=True, output=str(value * 2))
'''


class _SummaryProvider(LLMProvider):
    name = "summary"

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        return LLMResponse(content="Summary: user wanted doubled numbers, files made none.")


class _NoopProvider(LLMProvider):
    name = "noop"

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        return LLMResponse(content="hi")


@pytest.fixture
def saved_skill(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from james.tools import forge_tools

    plugins_dir = isolated_workspace / "plugins"
    monkeypatch.setattr(forge_tools, "_PLUGINS_DIR", plugins_dir)
    result = forge_tools._persist_skill("double_number", _VALID_SKILL_CODE)
    assert result.ok
    return plugins_dir / "double_number.py"


# --- skill auto-application ------------------------------------------------
def test_get_relevant_skills_finds_match(saved_skill: Path) -> None:
    from james.tools.forge_tools import get_relevant_skills

    hits = get_relevant_skills("please double a number for me", top_k=3)
    assert "double_number" in hits
    assert "Double a number" in hits


def test_get_relevant_skills_ignores_irrelevant(saved_skill: Path) -> None:
    from james.tools.forge_tools import get_relevant_skills

    assert get_relevant_skills("what is the weather today", top_k=3) == ""


def test_think_injects_skill_hint(isolated_workspace: Path, saved_skill: Path) -> None:
    from james.core.assistant import Assistant

    a = Assistant(session=None)
    a.speak = lambda text: None
    a.llm = _NoopProvider()
    a.agent.llm = _NoopProvider()
    a.agent._nudge = False
    calls: list = []
    a.agent.run = lambda prompt, history=None: calls.append(prompt) or ("hi", history or [])
    a.think("double the number 21")
    assert any("double_number" in c for c in calls)


# --- cross-session recall --------------------------------------------------
def test_summary_persists_to_memory(isolated_workspace: Path) -> None:
    from james.core.assistant import Assistant

    a = Assistant(session=None)
    a.speak = lambda text: None
    a.llm = _SummaryProvider()
    a.history = [{"role": "user", "content": f"msg {i}"} for i in range(21)]
    a._summarize_history()

    entries = [
        json.loads(line)
        for line in settings.assistant.memory_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert entries, "summary should be persisted to long-term memory"
    assert "doubled numbers" in entries[0]["text"]


def test_summary_not_persisted_when_short(isolated_workspace: Path) -> None:
    from james.core.assistant import Assistant

    a = Assistant(session=None)
    a.speak = lambda text: None
    a.history = [{"role": "user", "content": "short"}]
    a._summarize_history()
    assert not settings.assistant.memory_file.exists()


def test_summary_persisted_when_memory_disabled(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.assistant, "memory_enabled", False)
    from james.core.assistant import Assistant

    a = Assistant(session=None)
    a.speak = lambda text: None
    a.llm = _SummaryProvider()
    a.history = [{"role": "user", "content": f"msg {i}"} for i in range(21)]
    a._summarize_history()
    assert not settings.assistant.memory_file.exists()


# --- marketplace loop ------------------------------------------------------
def test_publish_then_install_roundtrip(
    isolated_workspace: Path, saved_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.tools.marketplace import install_plugin, publish_skill

    marketplace_file = isolated_workspace / "marketplace.json"
    monkeypatch.setattr("james.tools.marketplace._MARKETPLACE_FILE", marketplace_file)

    result = publish_skill.run(name="double_number")
    assert result.ok, result.output
    catalog = json.loads(marketplace_file.read_text(encoding="utf-8"))
    entry = next(p for p in catalog if p["name"] == "double_number")
    assert entry["source"] == "local"
    assert "code" in entry and "from james.tools.base" in entry["code"]

    # remove the local skill, then install from the catalog
    assert saved_skill.exists()
    saved_skill.unlink()
    install = install_plugin.run(name="double_number")
    assert install.ok, install.output
    assert saved_skill.exists()


def test_install_plugin_requires_bundled_code(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.tools.marketplace import install_plugin

    marketplace_file = isolated_workspace / "marketplace.json"
    marketplace_file.write_text(
        json.dumps([{"name": "no-code-plugin", "description": "metadata only"}]), encoding="utf-8"
    )
    monkeypatch.setattr("james.tools.marketplace._MARKETPLACE_FILE", marketplace_file)

    result = install_plugin.run(name="no-code-plugin")
    assert not result.ok
    assert "no bundled code" in result.output
