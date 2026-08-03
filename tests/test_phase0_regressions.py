"""Regression tests for the Phase 0 bug-fix pass (see CHANGELOG)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from james.config import settings
from james.llm.base import LLMProvider, LLMResponse
from james.tools.base import ToolResult, tool


class _NoopProvider(LLMProvider):
    name = "noop"

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        return LLMResponse(content="hi")


# --- Bug 1: `--eval` used json without importing it -------------------------
def test_eval_cli_runs_without_nameerror(isolated_workspace: Path) -> None:
    from james.__main__ import main

    assert main(["--eval", "smoke"]) == 0


# --- Bug 2: TOOL_* permissions in .env silently did nothing -----------------
def test_tool_env_permissions_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.config import _load_tool_permissions

    settings.assistant.denied_tools = []
    monkeypatch.setenv("TOOL_delete_file", "false")
    _load_tool_permissions()
    assert "delete_file" in settings.assistant.denied_tools

    monkeypatch.setenv("TOOL_delete_file", "true")
    _load_tool_permissions()
    assert "delete_file" not in settings.assistant.denied_tools


# --- Bug 3: agent loop duplicated the system prompt into history ------------
def test_agent_history_excludes_injected_system_prompt() -> None:
    from james.core.agent import Agent
    from james.tools.registry import ToolRegistry

    agent = Agent(
        _NoopProvider(),
        ToolRegistry(discover_plugins=False),
        confirm_dangerous=False,
    )
    reply, history = agent.run("hello", history=[{"role": "user", "content": "previous"}])
    assert reply == "hi"
    assert all(m["role"] != "system" for m in history)


def test_agent_history_stays_clean_across_turns() -> None:
    from james.core.agent import Agent
    from james.tools.registry import ToolRegistry

    agent = Agent(
        _NoopProvider(),
        ToolRegistry(discover_plugins=False),
        confirm_dangerous=False,
    )
    _, history = agent.run("first turn")
    _, history = agent.run("second turn", history=history)
    assert all(m["role"] != "system" for m in history)
    assert history[0]["role"] == "user"


# --- Bug 5: dashboard read the dead plaintext history format ----------------
def test_dashboard_reads_encrypted_history(isolated_workspace: Path) -> None:
    from james.core.assistant import encrypt_history
    from james.ui.dashboard import _DashboardHandler

    (settings.assistant.history_file).write_bytes(
        encrypt_history([{"role": "user", "content": "secret"}])
    )
    handler = _DashboardHandler.__new__(_DashboardHandler)
    assert handler._get_history()["messages"] == [{"role": "user", "content": "secret"}]


def test_dashboard_export_uses_encrypted_history(isolated_workspace: Path) -> None:
    from james.core.assistant import encrypt_history
    from james.ui.dashboard import _DashboardHandler

    (settings.assistant.history_file).write_bytes(
        encrypt_history([{"role": "user", "content": "export me"}])
    )
    handler = _DashboardHandler.__new__(_DashboardHandler)
    assert handler._load_history_messages() == [{"role": "user", "content": "export me"}]


# --- Bug 6: duplicate stream() definition on FunctionTool -------------------
def test_function_tool_stream_yields_single_chunk() -> None:
    @tool("tick", "noop", {})
    def tick() -> ToolResult:
        return ToolResult(ok=True, output="data")

    chunks = list(tick.stream())
    assert len(chunks) == 1
    assert chunks[0].output == "data"


# --- Bug 7: previously unreachable tools are now registered -----------------
def test_phase0_tools_are_registered() -> None:
    from james.tools.registry import ALL_TOOLS

    names = [t.name for t in ALL_TOOLS]
    for expected in ("browser_health", "upload_image", "search_plugins", "list_plugins"):
        assert expected in names, f"{expected} should be registered"
    # Phase 0 removed the misleading stub install_plugin/remove_plugin; Phase 2
    # re-added a *real* install_plugin (constrained runtime) plus publish_skill.
    assert "install_plugin" in names, "install_plugin should now be a real tool"
    assert "publish_skill" in names, "publish_skill should be registered"
    assert "remove_plugin" not in names, "remove_plugin is internal, not a tool"


def test_upload_image_returns_base64_data_uri(isolated_workspace: Path) -> None:
    from james.tools.system_tools import upload_image

    img = isolated_workspace / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    result = upload_image.run(path=str(img))
    assert result.ok
    assert result.data.startswith("data:image/png;base64,")


# --- Bug 8: rate limiter was not thread-safe --------------------------------
def test_rate_limit_is_thread_safe(isolated_workspace: Path) -> None:
    from james.tools.registry import ToolRegistry

    @tool("tick", "noop", {})
    def tick() -> ToolResult:
        return ToolResult(ok=True, output="ok")

    registry = ToolRegistry(tools=[tick], discover_plugins=False)
    registry._max_calls_per_minute = 50
    results: list = []

    def worker() -> None:
        for _ in range(20):
            results.append(registry.execute("tick", {}).ok)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r) == 50


# --- Bug 10: orb model switcher updates live settings -----------------------
def test_orb_model_change_updates_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        from james.ui.orb import OrbWindow
    except ImportError:
        pytest.skip("PyQt5 not installed")

    monkeypatch.setattr("james.llm.catalog.save_llm_config", lambda provider, model: None)

    window = OrbWindow.__new__(OrbWindow)
    window.log = type("_Log", (), {"appendPlainText": lambda self, s: None})()
    window._on_model_change("groq:llama-3.3-70b")
    assert settings.llm.provider == "groq"
    assert settings.llm.model == "llama-3.3-70b"
