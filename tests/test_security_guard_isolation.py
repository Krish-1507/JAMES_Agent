"""Tests for the security-critical paths that previously had little or no
coverage: the offline egress guard, the isolated worker broker, agent
confirmation handling, computer-use, the wake-word engine, and `james doctor`.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from james.config import settings

# ---------------------------------------------------------------------------
# Offline egress guard
# ---------------------------------------------------------------------------


class TestEgressGuard:
    def test_loopback_hosts_allowed(self) -> None:
        from james.core.guard import _is_loopback

        assert _is_loopback("127.0.0.1") is True
        assert _is_loopback("::1") is True
        assert _is_loopback("localhost") is True
        assert _is_loopback("LOCALHOST") is True
        assert _is_loopback("0.0.0.0") is True
        assert _is_loopback("::") is True
        assert _is_loopback(None) is False

    def test_external_hosts_blocked(self) -> None:
        from james.core.guard import _is_loopback

        assert _is_loopback("8.8.8.8") is False
        assert _is_loopback("example.com") is False
        assert _is_loopback("not-an-ip") is False

    def test_host_of_normalises_sockaddr(self) -> None:
        from james.core.guard import _host_of

        assert _host_of(("127.0.0.1", 80)) == ("127.0.0.1", 80)
        assert _host_of("1.2.3.4") == ("1.2.3.4", 0)

    def test_guarded_getaddrinfo_blocks_external(self, isolated_workspace: Path) -> None:
        import james.core.guard as guard

        with pytest.raises(guard.BlockedEgress):
            guard._guarded_getaddrinfo("example.com", 443)
        log = isolated_workspace / "egress.log"
        assert "BLOCK" in log.read_text(encoding="utf-8")

    def test_guarded_connect_blocks_external_and_allows_loopback(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import james.core.guard as guard

        calls: list = []
        monkeypatch.setattr(guard, "_orig_connect", lambda *a, **k: calls.append(a))
        with pytest.raises(guard.BlockedEgress):
            guard._guarded_connect(object(), ("8.8.8.8", 443))
        assert calls == []
        guard._guarded_connect(object(), ("127.0.0.1", 8123))
        assert len(calls) == 1

    def test_install_offline_guard_is_idempotent(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket

        import james.core.guard as guard

        monkeypatch.setattr(guard, "_INSTALLED", False)
        # Capture the true originals so teardown always restores the socket layer.
        monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
        monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
        monkeypatch.setattr(socket, "create_connection", socket.create_connection)
        for mod_name, path in [
            ("requests", "Session.request"),
        ]:
            try:
                mod = importlib.import_module(mod_name)
                obj = mod
                for part in path.split(".")[:-1]:
                    obj = getattr(obj, part)
                monkeypatch.setattr(obj, path.split(".")[-1], getattr(obj, path.split(".")[-1]))
            except Exception:
                pass

        guard.install_offline_guard()
        assert socket.getaddrinfo is guard._guarded_getaddrinfo
        guard.install_offline_guard()  # second call must be a no-op
        assert socket.getaddrinfo is guard._guarded_getaddrinfo
        assert guard.is_offline() is False

    def test_is_offline_reflects_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from james.core.guard import is_offline

        monkeypatch.setattr(settings.assistant, "offline_mode", True)
        assert is_offline() is True
        monkeypatch.setattr(settings.assistant, "offline_mode", False)
        assert is_offline() is False


# ---------------------------------------------------------------------------
# Isolated worker broker
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_inside_accepts_contained_paths(self, isolated_workspace: Path) -> None:
        from james.core.isolation import _inside

        root = str(isolated_workspace)
        assert (
            _inside(root, str(isolated_workspace / "notes" / "today.txt"))
            == (isolated_workspace / "notes" / "today.txt").resolve()
        )
        assert (
            _inside(root, str(isolated_workspace / "ok.txt"))
            == (isolated_workspace / "ok.txt").resolve()
        )

    def test_inside_rejects_escapes(self, isolated_workspace: Path) -> None:
        from james.core.isolation import _inside

        root = str(isolated_workspace)
        with pytest.raises(ValueError, match="escaped"):
            _inside(root, "../escape.txt")
        with pytest.raises(ValueError, match="Refusing"):
            _inside(root, str(isolated_workspace))

    def test_execute_command(self, isolated_workspace: Path) -> None:
        from james.core.isolation import _execute

        result = _execute(
            "command",
            {"args": [sys.executable, "-c", "print('hi')"], "workspace": str(isolated_workspace)},
        )
        assert result["ok"] is True
        assert "hi" in result["output"]

    def test_execute_unknown_operation_raises(self, isolated_workspace: Path) -> None:
        from james.core.isolation import _execute

        with pytest.raises(ValueError, match="Unknown isolated operation"):
            _execute("nope", {})

    def test_trash_and_restore_roundtrip(self, isolated_workspace: Path) -> None:
        from james.core.isolation import _execute

        target = isolated_workspace / "keep.txt"
        target.write_text("data", encoding="utf-8")
        trash = isolated_workspace / ".james_trash"
        trashed = _execute(
            "trash",
            {"workspace": str(isolated_workspace), "path": str(target), "trash": str(trash)},
        )
        assert trashed["ok"] is True
        assert not target.exists()
        restored = _execute(
            "restore",
            {
                "workspace": str(isolated_workspace),
                "trashed": trashed["data"]["trashed"],
                "original": trashed["data"]["original"],
            },
        )
        assert restored["ok"] is True
        assert target.read_text(encoding="utf-8") == "data"

    def test_run_isolated_end_to_end(self, isolated_workspace: Path) -> None:
        from james.core.isolation import run_isolated

        result = run_isolated(
            "command",
            {
                "args": [sys.executable, "-c", "print('isolated')"],
                "workspace": str(isolated_workspace),
            },
            timeout=30,
        )
        assert result.get("ok") is True
        assert "isolated" in result.get("output", "")

    def test_run_isolated_timeout(self, isolated_workspace: Path) -> None:
        from james.core.isolation import run_isolated

        result = run_isolated(
            "command",
            {
                "args": [sys.executable, "-c", "import time; time.sleep(30)"],
                "timeout": 1,
                "workspace": str(isolated_workspace),
            },
            timeout=2,
        )
        assert result.get("ok") is False


# ---------------------------------------------------------------------------
# Agent confirmation handling
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        self.calls.append(list(messages))
        return self._responses.pop(0)


class _RaisingOnceLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._failed = False

    def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated API outage")
        return self._responses.pop(0)


class TestAgentConfirmation:
    def _agent(self, llm, confirm, registry):
        from james.core.agent import Agent

        return Agent(llm=llm, registry=registry, confirm=confirm, max_iterations=5)

    def test_dangerous_tool_denied_without_execution(self, isolated_workspace: Path) -> None:
        from james.llm.base import LLMResponse, ToolCall
        from james.tools.file_tools import delete_file
        from james.tools.registry import ToolRegistry

        registry = ToolRegistry(tools=[delete_file], discover_plugins=False)
        llm = _FakeLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="delete_file", arguments={"path": "notes.txt"})
                    ],
                ),
                LLMResponse(content="fine, kept it", tool_calls=[]),
            ]
        )
        denied: list[str] = []

        def confirm(name, args):
            denied.append(name)
            return False

        agent = self._agent(llm, confirm, registry)
        reply, history = agent.run("remove notes.txt")
        assert reply == "fine, kept it"
        assert denied == ["delete_file"]
        tool_msgs = [m for m in history if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "denied" in tool_msgs[0]["content"]

    def test_dangerous_tool_allowed_executes(
        self, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from james.llm.base import LLMResponse, ToolCall
        from james.tools.file_tools import delete_file
        from james.tools.registry import ToolRegistry

        monkeypatch.setattr(settings.assistant, "mode", "full")
        monkeypatch.setattr(settings.assistant, "allowed_tools", [])
        monkeypatch.setattr(settings.assistant, "denied_tools", [])
        target = isolated_workspace / "bye.txt"
        target.write_text("x", encoding="utf-8")
        registry = ToolRegistry(tools=[delete_file], discover_plugins=False)
        llm = _FakeLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="delete_file", arguments={"path": "bye.txt"})
                    ],
                ),
                LLMResponse(content="removed", tool_calls=[]),
            ]
        )
        agent = self._agent(llm, lambda name, args: True, registry)
        reply, history = agent.run("delete bye.txt")
        assert reply == "removed"
        assert not target.exists()
        tool_msgs = [m for m in history if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "trash" in tool_msgs[0]["content"]

    def test_non_dangerous_tool_runs_without_confirmation(self, isolated_workspace: Path) -> None:
        from james.llm.base import LLMResponse, ToolCall
        from james.tools.base import ToolResult, tool
        from james.tools.registry import ToolRegistry

        @tool("echo_tool", "Echo text back.", {"text": {"type": "string"}})
        def echo_tool(text: str) -> ToolResult:
            return ToolResult(ok=True, output=text)

        called: list[str] = []
        registry = ToolRegistry(tools=[echo_tool], discover_plugins=False)
        llm = _FakeLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="1", name="echo_tool", arguments={"text": "hello"})],
                ),
                LLMResponse(content="done", tool_calls=[]),
            ]
        )

        def confirm(name, args):
            called.append(name)
            return False

        agent = self._agent(llm, confirm, registry)
        reply, history = agent.run("say hello")
        assert reply == "done"
        assert called == []  # non-dangerous tools never prompt
        assert any(m.get("content") == "hello" for m in history if m["role"] == "tool")

    def test_llm_error_retries_when_confirmed(self, isolated_workspace: Path) -> None:
        from james.llm.base import LLMResponse
        from james.tools.registry import ToolRegistry

        retries = []
        registry = ToolRegistry(tools=[], discover_plugins=False)
        llm = _RaisingOnceLLM([LLMResponse(content="recovered", tool_calls=[])])

        def confirm(name, args):
            retries.append(name)
            return True

        agent = self._agent(llm, confirm, registry)
        reply, _history = agent.run("hello")
        assert reply == "recovered"
        assert retries == ["retry_llm"]

    def test_annotate_builds_tool_call_messages(self, isolated_workspace: Path) -> None:
        import json

        from james.core.agent import Agent
        from james.llm.base import LLMResponse, ToolCall
        from james.tools.registry import ToolRegistry

        agent = Agent(llm=_FakeLLM([]), registry=ToolRegistry(tools=[], discover_plugins=False))
        msg = agent._annotate(
            LLMResponse(content="", tool_calls=[ToolCall(id="9", name="foo", arguments={"a": 1})])
        )
        assert msg["role"] == "assistant"
        assert msg["tool_calls"][0]["function"]["name"] == "foo"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"a": 1}

    def test_confirm_request_wait_and_respond(self) -> None:
        from james.core.agent import _ConfirmRequest

        req = _ConfirmRequest("delete_file", {"path": "x"})
        assert req.wait(timeout=0.1) is False  # times out as denied
        req.respond(True)
        assert req.wait(timeout=1.0) is True


# ---------------------------------------------------------------------------
# Computer-use vision loop
# ---------------------------------------------------------------------------


def _fake_pyautogui(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"click": [], "write": [], "press": [], "scroll": []}

    class FakeImage:
        def save(self, buf, *args, **kwargs) -> None:
            buf.write(b"fakepng")

    class FakePyAutoGUI:
        FAILSAFE = True

        def screenshot(self, *a, **k):
            calls["screenshot"] = True
            return FakeImage()

        def click(self, x, y):
            calls["click"].append((x, y))

        def write(self, text, interval=0):
            calls["write"].append(text)

        def press(self, key):
            calls["press"].append(key)

        def scroll(self, dy, x=0, y=0):
            calls["scroll"].append((dy, x, y))

    monkeypatch.setitem(sys.modules, "pyautogui", FakePyAutoGUI())
    return calls


class TestComputerUse:
    def test_parse_action_plain_json(self) -> None:
        from james.core.computeruse import _parse_action

        assert _parse_action('{"action":"click","x":10,"y":20}') == {
            "action": "click",
            "x": 10,
            "y": 20,
        }

    def test_parse_action_tolerates_prose(self) -> None:
        from james.core.computeruse import _parse_action

        parsed = _parse_action('I will click. {"action":"keypress","key":"enter"} Done.')
        assert parsed == {"action": "keypress", "key": "enter"}

    def test_parse_action_returns_wait_on_garbage(self) -> None:
        from james.core.computeruse import _parse_action

        assert _parse_action("no json here") == {"action": "wait"}
        assert _parse_action("{broken json") == {"action": "wait"}
        assert _parse_action('{"no_action": 1}') == {"action": "wait"}

    def test_act_dispatches_to_pyautogui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from james.core.computeruse import _act

        calls = _fake_pyautogui(monkeypatch)
        assert "clicked" in _act({"action": "click", "x": 5, "y": 6})
        assert _act({"action": "type", "text": "hi"}) == "typed 2 chars"
        assert _act({"action": "keypress", "key": "enter"}) == "pressed enter"
        assert _act({"action": "done", "result": "finished"}) == "done: finished"
        assert calls["click"] == [(5, 6)]
        assert calls["write"] == ["hi"]
        assert calls["press"] == ["enter"]

    def test_run_computer_use_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from james.core.computeruse import run_computer_use
        from james.llm.base import LLMResponse

        _fake_pyautogui(monkeypatch)

        class Vision:
            def chat(self, messages, tools=None, tool_choice="auto", images=None, model=None):
                return LLMResponse(content='{"action":"done","result":"opened the app"}')

        result = run_computer_use(Vision(), "open the app", max_steps=5)
        assert result.startswith("opened the app")

    def test_run_computer_use_stops_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from james.core.computeruse import run_computer_use

        _fake_pyautogui(monkeypatch)

        class Vision:
            def chat(self, *args, **kwargs):
                raise RuntimeError("vision model unreachable")

        result = run_computer_use(Vision(), "do a thing", max_steps=3)
        assert "Computer-use stopped" in result


# ---------------------------------------------------------------------------
# Wake-word engine
# ---------------------------------------------------------------------------


class TestPorcupineEngine:
    def test_init_raises_import_error_when_deps_missing(self) -> None:
        import james.core.porcupine_engine as engine

        if importlib.util.find_spec("pvporcupine") and importlib.util.find_spec("sounddevice"):
            pytest.skip("porcupine deps installed — cannot test missing-dep path")
        with pytest.raises(ImportError):
            engine.PorcupineWakeEngine(access_key="key", keyword="jarvis")

    def test_listen_and_close_with_fake_deps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import james.core.porcupine_engine as engine

        class FakePorcupine:
            sample_rate = 16000
            frame_length = 512

            def __init__(self):
                self.deleted = False

            def process(self, pcm):
                return -1

            def delete(self):
                self.deleted = True

        class FakeStream:
            def __init__(self, engine_impl, blocks):
                self._engine = engine_impl
                self._blocks = iter(blocks)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n, dtype="int16"):
                try:
                    return next(self._blocks)
                except StopIteration:
                    raise TimeoutError("stream closed") from None

        fake_pv = FakePorcupine()
        monkeypatch.setitem(sys.modules, "pvporcupine", types.ModuleType("pvporcupine"))
        monkeypatch.setattr(
            sys.modules["pvporcupine"], "create", lambda **kw: fake_pv, raising=False
        )
        monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))
        monkeypatch.setattr(
            sys.modules["sounddevice"],
            "InputStream",
            lambda **kw: FakeStream(fake_pv, [None, None]),
            raising=False,
        )

        wake = engine.PorcupineWakeEngine(access_key="key", keyword="jarvis")
        assert wake.listen(timeout=0.5) is False
        wake.close()
        assert fake_pv.deleted is True


# ---------------------------------------------------------------------------
# Doctor diagnostics
# ---------------------------------------------------------------------------


class TestDoctor:
    def test_run_diagnostics_returns_report(self, isolated_workspace: Path) -> None:
        from james.core.doctor import run_diagnostics

        out = run_diagnostics()
        assert "JAMES doctor" in out
        assert "[PASS]" in out
        assert "Python" in out

    def test_line_helper(self) -> None:
        from james.core.doctor import _line

        assert _line("PASS", "ok") == "[PASS] ok"
        assert _line("WARN", "w", "detail") == "[WARN] w — detail"
