"""Tests for Phase 1: onboarding, sessions, and wake-word engine dispatch."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from james.config import settings


def _make_assistant(tmp_path: Path, session: str | None = None):
    from james.core.assistant import Assistant

    return Assistant(session=session)


# --- sessions -------------------------------------------------------------
def test_session_slug_sanitizes() -> None:
    from james.core.assistant import _session_slug

    assert _session_slug("My Work!") == "my_work"
    assert _session_slug("") == "default"
    assert _session_slug("already-snake_case") == "already-snake_case"


def test_named_session_uses_separate_file(isolated_workspace: Path) -> None:
    from james.core.assistant import _session_path

    assert _session_path("work") == isolated_workspace / "sessions" / "work.enc"
    assert _session_path(None) == settings.assistant.history_file


def test_session_persists_and_reloads(isolated_workspace: Path) -> None:
    a = _make_assistant(isolated_workspace, session="work")
    a.history = [{"role": "user", "content": "hello work"}]
    a._save_history()
    assert (isolated_workspace / "sessions" / "work.enc").exists()

    b = _make_assistant(isolated_workspace, session="work")
    assert b.history == [{"role": "user", "content": "hello work"}]


def test_sessions_are_isolated(isolated_workspace: Path) -> None:
    a = _make_assistant(isolated_workspace, session="work")
    a.history = [{"role": "user", "content": "work msg"}]
    a._save_history()
    a.switch_session("personal")
    assert a.current_session() == "personal"
    assert a.history == []

    b = _make_assistant(isolated_workspace, session="work")
    assert b.history == [{"role": "user", "content": "work msg"}]


def test_list_sessions(isolated_workspace: Path) -> None:
    a = _make_assistant(isolated_workspace, session="alpha")
    a.history = [{"role": "user", "content": "x"}]
    a._save_history()
    a.switch_session("beta")
    a.history = [{"role": "user", "content": "y"}]
    a._save_history()

    assert a.list_sessions() == ["alpha", "beta"]


def test_handle_session_commands(isolated_workspace: Path) -> None:
    a = _make_assistant(isolated_workspace, session="work")
    a.history = [{"role": "user", "content": "old"}]
    a._save_history()

    assert a._handle_session_command("/sessions") is True
    assert a._handle_session_command("/resume") is True
    a._handle_session_command("/resume work")
    assert a.current_session() == "work"
    assert a.history == [{"role": "user", "content": "old"}]

    a._handle_session_command("/clear")
    assert a.history == []
    # reload from disk — must come back empty (not the old "old" message)
    b = _make_assistant(isolated_workspace, session="work")
    assert b.history == []

    assert a._handle_session_command("/new") is True
    assert a.current_session() != "work"

    assert a._handle_session_command("not a command") is False


def test_export_uses_active_session(isolated_workspace: Path) -> None:
    a = _make_assistant(isolated_workspace, session="work")
    a.history = [{"role": "user", "content": "export target"}]
    path = a.export_conversation("markdown")
    assert path and Path(path).read_text(encoding="utf-8").find("export target") >= 0


# --- onboarding -----------------------------------------------------------
def test_onboarding_writes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.onboarding as ob

    monkeypatch.setattr(ob, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ob, "env_exists", lambda: False)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("2\nclaude-3-test\nsk-test-123\nn\n")
    )

    env = ob.run_onboarding()
    content = env.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=anthropic" in content
    assert "LLM_MODEL=claude-3-test" in content
    assert "ANTHROPIC_API_KEY=sk-test-123" in content
    assert "VOICE_ENABLED=false" in content


def test_onboarding_custom_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.onboarding as ob

    monkeypatch.setattr(ob, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ob, "env_exists", lambda: False)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO("custom\nhttp://localhost:11434/v1\nllama3.2\n\nn\n"),
    )

    env = ob.run_onboarding()
    content = env.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=custom" in content
    assert "CUSTOM_BASE_URL=http://localhost:11434/v1" in content
    assert "CUSTOM_API_KEY=" in content


def test_setup_cli_runs_even_when_env_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import james.onboarding as ob

    monkeypatch.setattr(ob, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ob, "env_exists", lambda: True)
    called = {"n": 0}

    def _fake_run(**kw):
        called["n"] += 1

    monkeypatch.setattr(ob, "run_onboarding", _fake_run)
    assert ob.setup_cmd() == 0
    assert called["n"] == 1  # --setup forces a re-run for provider changes


# --- wake-word engine ----------------------------------------------------
def test_voice_loop_porcupine_falls_back_when_missing(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WAKE_ENGINE=porcupine with pvporcupine absent must not crash the loop."""
    import importlib.util

    monkeypatch.setattr(settings.assistant, "wake_engine", "porcupine")
    if importlib.util.find_spec("pvporcupine") is not None:
        pytest.skip("pvporcupine installed — cannot test fallback")


    a = _make_assistant(isolated_workspace)
    a.speak = lambda text: None
    a.log = type("_Log", (), {"warning": lambda self, *a, **k: None})()
    a._voice_loop_wake_word = lambda: None  # must be called instead of crashing
    a.voice_loop()  # returns None without raising


def _terminating_stt(*values):
    """Returns an STT whose listen() yields values then raises KeyboardInterrupt
    to break the infinite voice loop (KeyboardInterrupt is not caught by
    `except Exception` in the loop)."""

    class _Stt:
        def __init__(self, items):
            self._items = list(items)

        def listen(self) -> str:
            if self._items:
                return self._items.pop(0)
            raise KeyboardInterrupt

    return _Stt(values)


def test_wake_engine_none_listens_continuously(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.assistant, "wake_engine", "none")


    a = _make_assistant(isolated_workspace)
    a.speak = lambda text: None
    a.log = type("_Log", (), {"warning": lambda self, *x, **k: None})()
    handled = []
    a.handle_turn = lambda text: handled.append(text)
    a.stt = _terminating_stt("hello there", "stop")
    a.voice_loop()  # "none" engine exits naturally on the stop command
    assert handled == ["hello there"]


def test_wake_engine_always_requires_wake_word(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.assistant, "wake_engine", "always")


    a = _make_assistant(isolated_workspace)
    a.speak = lambda text: None
    a.log = type("_Log", (), {"warning": lambda self, *x, **k: None})()
    handled = []
    a.handle_turn = lambda text: handled.append(text)

    a.stt = _terminating_stt(
        f"{settings.assistant.wake_word} set a timer for 5 minutes"
    )
    with pytest.raises(KeyboardInterrupt):
        a._voice_loop_wake_word()
    assert handled == ["set a timer for 5 minutes"]
