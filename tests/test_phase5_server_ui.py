"""Tests for the Phase-3 desktop-app v2 server sidecar (``james.ui.server``).

Covers the FastAPI control surface (turns, sessions, model switching,
settings, tools, voice, approvals, onboarding), the SSE event bus, and the
approval round-trip that used to live in the Qt desktop shell.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from james.ui import server as server_module
from james.ui.server import EventBus, ServerRuntime, _redact_args, create_app

SECRET = "sk-test-secret-value"


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeRegistry:
    def schemas(self) -> list[dict]:
        return [
            {"name": "web_search", "description": "Search the web."},
            {"name": "delete_file", "description": "Delete a file."},
            {"name": "run_shell_command", "description": "Run a shell command."},
        ]


class FakeAssistant:
    """Stands in for the real Assistant: records calls, returns canned data."""

    def __init__(self) -> None:
        self.history: list[dict] = []
        self.registry = FakeRegistry()
        self.sessions = ["default"]
        self.session = "default"
        self.texts: list[str] = []
        self.models: list[tuple[str, str]] = []
        self.events: list[dict] = []

    def current_session(self) -> str:
        return self.session

    def list_sessions(self) -> list[str]:
        return list(self.sessions)

    def new_session(self) -> str:
        self.session = f"session-{len(self.sessions) + 1}"
        self.sessions.append(self.session)
        return self.session

    def switch_session(self, name: str) -> None:
        if name in self.sessions:
            self.session = name

    def clear_history(self) -> None:
        self.history = []

    def send_voice_text(self, text: str) -> None:
        self.texts.append(text)

    def switch_model(self, provider: str, model: str) -> bool:
        self.models.append((provider, model))
        return True

    def mute_voice(self, muted: bool) -> None:
        self.events.append(("mute", muted))

    def interrupt_voice(self) -> None:
        self.events.append(("interrupt",))

    def set_voice_only(self, enabled: bool) -> None:
        self.events.append(("voice_only", enabled))


@pytest.fixture
def runtime() -> ServerRuntime:
    rt = ServerRuntime(assistant_factory=FakeAssistant)
    rt._assistant = rt._assistant_factory()  # build without starting the loop thread
    rt._assistant.on_event = rt._on_event
    return rt


@pytest.fixture
def client(runtime: ServerRuntime) -> TestClient:
    return TestClient(create_app(runtime))


# ---------------------------------------------------------------------------
# event bus
# ---------------------------------------------------------------------------


def test_event_bus_watermarks_and_bounds() -> None:
    bus = EventBus(maxlen=3)
    ids = [bus.publish({"type": "user", "text": str(i)}) for i in range(5)]
    assert ids == [1, 2, 3, 4, 5]

    items, last = bus.drain(0)
    assert [e["id"] for e in items] == [3, 4, 5]  # bounded to maxlen
    assert last == 5

    items, last = bus.drain(5)
    assert items == []
    assert last == 5

    assert bus.subscribers == 0
    bus.connect()
    assert bus.subscribers == 1
    bus.disconnect()
    bus.disconnect()  # clamps at zero
    assert bus.subscribers == 0


# ---------------------------------------------------------------------------
# web assets
# ---------------------------------------------------------------------------


def test_index_and_static_assets_served(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "JAMES" in index.text
    assert "/static/style.css" in index.text
    assert "/static/app.js" in index.text

    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")

    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "EventSource" in js.text


def test_static_path_traversal_blocked(client: TestClient) -> None:
    for path in (
        "/static/../server.py",
        "/static/..%2F..%2Fjames%2F__init__.py",
        "/static/%2e%2e/server.py",
    ):
        resp = client.get(path)
        assert resp.status_code == 404, path


# ---------------------------------------------------------------------------
# status / turns
# ---------------------------------------------------------------------------


def test_status_reports_ready_and_history(runtime: ServerRuntime, client: TestClient) -> None:
    runtime.assistant.history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "tool", "content": "skip me"},
    ]
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["session"] == "default"
    assert data["sessions"] == ["default"]
    assert [m["text"] for m in data["history"]] == ["hello", "hi there"]
    assert data["name"]
    assert data["providers"]
    assert data["version"]


def test_status_not_ready_before_assistant_boots() -> None:
    rt = ServerRuntime()
    resp = TestClient(create_app(rt)).get("/api/status")
    assert resp.json() == {"ready": False}


def test_turn_submits_text(runtime: ServerRuntime, client: TestClient) -> None:
    resp = client.post("/api/turn", json={"text": "  tell me a joke  "})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert runtime.assistant.texts == ["tell me a joke"]


def test_turn_rejects_empty(runtime: ServerRuntime, client: TestClient) -> None:
    assert client.post("/api/turn", json={"text": "   "}).status_code == 400
    assert client.post("/api/turn", json={}).status_code == 400


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def test_sessions_lifecycle(runtime: ServerRuntime, client: TestClient) -> None:
    got = client.get("/api/sessions").json()
    assert got == {"sessions": ["default"], "current": "default"}

    new = client.post("/api/sessions/new").json()
    assert new["ok"] and new["name"] == "session-2"
    assert runtime.assistant.current_session() == "session-2"

    switched = client.post("/api/sessions/switch", json={"name": "default"})
    assert switched.status_code == 200
    assert runtime.assistant.current_session() == "default"

    cleared = client.post("/api/sessions/clear")
    assert cleared.status_code == 200
    assert runtime.assistant.history == []


def test_switch_session_rejects_missing_name(client: TestClient) -> None:
    assert client.post("/api/sessions/switch", json={}).status_code == 400


# ---------------------------------------------------------------------------
# model switching
# ---------------------------------------------------------------------------


def test_model_switch_delegates_and_publishes(runtime: ServerRuntime, client: TestClient) -> None:
    before = len(runtime.bus.drain(0)[0])
    resp = client.post("/api/model", json={"provider": "openai", "model": "gpt-4o"})
    assert resp.status_code == 200
    assert runtime.assistant.models == [("openai", "gpt-4o")]
    events = runtime.bus.drain(before)[0]
    assert events[-1]["payload"] == {
        "type": "model_changed",
        "provider": "openai",
        "model": "gpt-4o",
    }


def test_model_switch_requires_fields(client: TestClient) -> None:
    assert client.post("/api/model", json={"provider": "openai"}).status_code == 400
    assert client.post("/api/model", json={"model": "gpt-4o"}).status_code == 400


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def test_tools_list_marks_dangerous(client: TestClient) -> None:
    data = client.get("/api/tools").json()["tools"]
    by_name = {t["name"]: t for t in data}
    assert by_name["web_search"]["dangerous"] is False
    assert by_name["delete_file"]["dangerous"] is True
    assert by_name["run_shell_command"]["dangerous"] is True


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_settings() -> None:
    """Restore the live settings singleton after endpoints mutate it."""
    from james.config import settings

    a, v = settings.assistant, settings.voice
    saved = {
        "mode": a.mode,
        "dry_run": a.dry_run,
        "confirm": a.confirm_dangerous_actions,
        "offline": a.offline_mode,
        "wake_engine": a.wake_engine,
        "wake_word": a.wake_word,
        "name": a.name,
        "user_name": a.user_name,
        "stt": v.stt_provider,
        "tts": v.tts_provider,
        "duplex": v.duplex_mode,
        "allowed": list(a.allowed_tools or []),
        "denied": list(a.denied_tools or []),
        "voice_enabled": v.enabled,
    }
    yield None
    a.mode = saved["mode"]
    a.dry_run = saved["dry_run"]
    a.confirm_dangerous_actions = saved["confirm"]
    a.offline_mode = saved["offline"]
    a.wake_engine = saved["wake_engine"]
    a.wake_word = saved["wake_word"]
    a.name = saved["name"]
    a.user_name = saved["user_name"]
    v.stt_provider = saved["stt"]
    v.tts_provider = saved["tts"]
    v.duplex_mode = saved["duplex"]
    a.allowed_tools = saved["allowed"]
    a.denied_tools = saved["denied"]
    v.enabled = saved["voice_enabled"]


def test_settings_roundtrip(
    runtime: ServerRuntime, client: TestClient, restore_settings: None
) -> None:
    snap = client.get("/api/settings").json()
    assert snap["mode"] in ("standard", "full")
    assert isinstance(snap["dry_run"], bool)

    resp = client.post(
        "/api/settings",
        json={"updates": {"mode": "full", "assistant_name": "Spark", "dry_run": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "full"
    assert resp.json()["assistant_name"] == "Spark"
    assert resp.json()["dry_run"] is True


def test_settings_rejects_bad_values(client: TestClient, restore_settings: None) -> None:
    resp = client.post("/api/settings", json={"updates": {"mode": "chaotic"}})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]

    resp = client.post("/api/settings", json={"updates": {"mode": "standard", "mystery_key": 1}})
    assert resp.status_code == 400
    assert "unknown setting" in resp.json()["detail"]

    resp = client.post("/api/settings", json={"updates": "not-a-dict"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------


def test_voice_status_and_controls(runtime: ServerRuntime, client: TestClient) -> None:
    status = client.get("/api/voice").json()
    assert status["state"] == "idle"
    assert status["level"] == 0.0
    assert status["muted"] is False

    assert client.post("/api/voice/mute", json={"muted": True}).status_code == 200
    assert runtime.assistant.events[0] == ("mute", True)
    assert client.get("/api/voice").json()["muted"] is True

    assert client.post("/api/voice/interrupt").status_code == 200
    assert ("interrupt",) in runtime.assistant.events

    assert client.post("/api/voice/voice_only", json={"enabled": True}).status_code == 200
    assert ("voice_only", True) in runtime.assistant.events


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_redact_args_hides_secrets_and_oversized_values() -> None:
    redacted = _redact_args(
        "run_shell_command",
        {"command": "echo ok", "api_key": SECRET, "token": "abc", "long": "x" * 500, "safe": "hi"},
    )
    assert redacted["api_key"] == "***"
    assert redacted["token"] == "***"
    assert redacted["long"] == "***"
    assert SECRET not in json.dumps(redacted)
    assert redacted["safe"] == "hi"


def test_approval_allow_once_via_http(runtime: ServerRuntime, client: TestClient) -> None:
    before = len(runtime.bus.drain(0)[0])
    req = runtime.approvals.request("delete_file", {"path": "/tmp/x"})
    assert not req.resolved

    resp = client.post(f"/api/approvals/{req.req_id}", json={"allowed": True})
    assert resp.status_code == 200
    assert req.resolved and req.allowed is True

    events = runtime.bus.drain(before)[0]
    kinds = [e["payload"]["type"] for e in events]
    assert "approval_requested" in kinds
    assert "approval_resolved" in kinds

    assert client.post(f"/api/approvals/{req.req_id}", json={"allowed": False}).status_code == 404


def test_confirm_defaults_to_deny_without_clients(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_NO_CLIENT_GRACE", 0.05)
    monkeypatch.setattr(server_module, "_APPROVAL_TIMEOUT", 5.0)

    allowed = runtime._confirm("run_shell_command", {"command": "rm -rf /", "api_key": SECRET})
    assert allowed is False

    pending_events = [
        e["payload"]
        for e in runtime.bus.drain(0)[0]
        if e["payload"]["type"] == "approval_requested"
    ]
    assert pending_events
    assert SECRET not in json.dumps(pending_events)
    assert pending_events[-1]["args"]["api_key"] == "***"


def test_confirm_allow_once_from_connected_client(
    runtime: ServerRuntime, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "_APPROVAL_TIMEOUT", 15.0)
    runtime.bus.connect()  # a UI client is watching

    results: list = []

    def run_confirm() -> None:
        results.append(runtime._confirm("delete_file", {"path": "notes.txt"}))

    thread = threading.Thread(target=run_confirm)
    thread.start()
    try:
        pending = [
            e for e in runtime.bus.drain(0)[0] if e["payload"]["type"] == "approval_requested"
        ]
        deadline = 100
        while not pending and deadline > 0:
            pending = [
                e for e in runtime.bus.drain(0)[0] if e["payload"]["type"] == "approval_requested"
            ]
            deadline -= 1
        assert pending, "approval request never surfaced"
        req_id = pending[-1]["payload"]["id"]
        resp = client.post(f"/api/approvals/{req_id}", json={"allowed": True})
        assert resp.status_code == 200
    finally:
        thread.join(timeout=10)

    assert results == [True]


# ---------------------------------------------------------------------------
# onboarding
# ---------------------------------------------------------------------------


def test_onboarding_writes_env_and_applies_live(
    runtime: ServerRuntime, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written: list[dict] = []

    def fake_configure(provider, model, api_key="", *, voice_enabled=False, base_url=""):
        written.append(
            {
                "provider": provider,
                "model": model,
                "api_key": api_key,
                "voice_enabled": voice_enabled,
            }
        )
        return tmp_path / ".env"

    monkeypatch.setattr("james.onboarding.configure", fake_configure)
    monkeypatch.setattr("james.onboarding.env_exists", lambda: False)

    from james.config import settings

    voice_was_enabled = settings.voice.enabled

    resp = client.post(
        "/api/onboarding",
        json={"provider": "openai", "model": "gpt-4o", "api_key": "", "voice_enabled": True},
    )
    assert resp.status_code == 200
    assert written == [
        {"provider": "openai", "model": "gpt-4o", "api_key": "", "voice_enabled": True}
    ]
    assert runtime.assistant.models[-1] == ("openai", "gpt-4o")

    settings.voice.enabled = voice_was_enabled


def test_onboarding_requires_provider_and_model(client: TestClient) -> None:
    resp = client.post("/api/onboarding", json={"model": "gpt-4o"})
    assert resp.status_code == 400
