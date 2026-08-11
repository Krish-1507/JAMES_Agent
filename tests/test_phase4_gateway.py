"""Phase-4 tests: messaging gateway (manager, Telegram, WhatsApp, tools, API)."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from james.config import settings
from james.gateway.base import GatewayChannel, IncomingMessage, make_channel
from james.gateway.manager import GatewayManager
from james.ui.server import ServerRuntime, create_app


class FakeAssistant:
    def __init__(self) -> None:
        self.turns: list[str] = []
        self.registry = None
        self.recipe_engine = None
        self.gateway = None
        self.on_event = None

    def handle_turn(self, text: str) -> None:
        self.turns.append(text)
        if self.on_event:
            self.on_event({"type": "user", "text": text})
            self.on_event({"type": "reply", "text": f"echo: {text}"})

    def current_session(self) -> str:
        return "default"

    def list_sessions(self) -> list[str]:
        return ["default"]

    def switch_model(self, provider: str, model: str) -> bool:
        return True


class MemoryChannel(GatewayChannel):
    """GatewayChannel implementation that never touches the network."""

    name = "memory"

    def __init__(self, manager: GatewayManager, label: str = "memory") -> None:
        super().__init__(manager)
        self.label = label
        self.sent: list[tuple[str, str]] = []
        self._stop = threading.Event()

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._stop.set()

    def _run(self) -> None:
        self._stop.wait(30)

    def send(self, text: str, chat_id: str = "") -> bool:
        self.sent.append((text, chat_id))
        return True


@pytest.fixture
def assistant() -> FakeAssistant:
    return FakeAssistant()


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------


def test_make_channel_unknown(assistant: FakeAssistant) -> None:
    with pytest.raises(ValueError):
        make_channel("irc", GatewayManager(assistant))


def test_make_channel_known_backends(assistant: FakeAssistant) -> None:
    gateway = GatewayManager(assistant)
    for name in ("telegram", "whatsapp", "discord", "slack"):
        channel = make_channel(name, gateway, token="x", app_token="a", bot_token="b")
        assert channel.name == name


def test_handle_inbound_forwards_turn_and_reply(assistant: FakeAssistant) -> None:
    gateway = GatewayManager(assistant)
    channel = MemoryChannel(gateway)
    gateway.channels = [channel]
    ok = gateway.handle_inbound(IncomingMessage(text="hello", channel="memory", chat_id="42"))
    assert ok
    assert assistant.turns == ["hello"]
    deadline = time.monotonic() + 5
    while not channel.sent and time.monotonic() < deadline:
        time.sleep(0.02)
    assert channel.sent[0] == ("echo: hello", "")  # reply routed to originating channel


def test_handle_inbound_allowlist(
    assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.gateway, "allow_from", ["trusted-user"])
    gateway = GatewayManager(assistant)
    channel = MemoryChannel(gateway)
    gateway.channels = [channel]
    blocked = gateway.handle_inbound(
        IncomingMessage(text="spam", channel="memory", chat_id="1", sender="stranger")
    )
    assert blocked is False
    assert assistant.turns == []
    allowed = gateway.handle_inbound(
        IncomingMessage(text="hi", channel="memory", chat_id="2", sender="trusted-user")
    )
    assert allowed is True
    assert assistant.turns == ["hi"]


def test_send_routes_to_channel(assistant: FakeAssistant) -> None:
    gateway = GatewayManager(assistant)
    channel = MemoryChannel(gateway)
    gateway.channels = [channel]
    assert gateway.send("memory", "proactive ping", "9") is True
    assert channel.sent == [("proactive ping", "9")]
    assert gateway.send("no-such-channel", "x", "1") is False


def test_status_lists_channels(assistant: FakeAssistant) -> None:
    gateway = GatewayManager(assistant)
    channel = MemoryChannel(gateway)
    gateway.channels = [channel]
    rows = gateway.status()
    assert rows == [{"name": "memory", "running": False, "last_chat": None, "error": None}]
    channel.last_chat_id = "42"
    rows = gateway.status()
    assert rows[0]["last_chat"] == "42"


def test_start_builds_channels_from_settings(
    assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.gateway import manager as manager_module

    built: list[str] = []

    def _fake_make(name: str, manager, **kwargs):
        channel = MemoryChannel(manager, label=name)
        channel.name = name
        built.append(name)
        return channel

    monkeypatch.setattr(manager_module, "make_channel", _fake_make)
    monkeypatch.setattr(settings.gateway, "telegram_token", "tg-token")
    monkeypatch.setattr(settings.gateway, "slack_app_token", "xapp-x")
    monkeypatch.setattr(settings.gateway, "slack_bot_token", "xoxb-x")
    gateway = GatewayManager(assistant)
    gateway.start()
    try:
        names = {c.name for c in gateway.channels}
        assert "telegram" in names and "slack" in names
    finally:
        gateway.stop()


# ---------------------------------------------------------------------------
# telegram (network mocked)
# ---------------------------------------------------------------------------


def _fake_post(responses: list[dict]):
    calls = []

    def _post(url: str, **kwargs):
        calls.append(url)
        return type(
            "R",
            (),
            {
                "status_code": 200,
                "raise_for_status": lambda self: None,
                "json": lambda self: responses.pop(0),
            },
        )()

    _post.calls = calls  # type: ignore[attr-defined]
    return _post


def test_telegram_send_and_poll(assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    from james.gateway.telegram import TelegramChannel

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "text": "hey there",
            "chat": {"id": 123},
            "from": {"username": "tester"},
        },
    }
    post = _fake_post([{"ok": True, "result": [update]}, {"ok": True, "result": []}])
    monkeypatch.setattr("james.gateway.telegram.requests.post", post)
    monkeypatch.setattr(settings.gateway, "allow_from", [])  # accept all

    gateway = GatewayManager(assistant)
    channel = TelegramChannel(gateway, token="TEST:TOKEN")
    channel._process_update(update)
    assert channel.last_chat_id == "123"
    assert assistant.turns == ["hey there"]
    assert channel.send("echo back", "123") is True
    assert "sendMessage" in post.calls[-1]


def test_telegram_bad_token(assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    from james.gateway.telegram import TelegramChannel

    def _fail(url: str, **kwargs):
        return type("R", (), {"status_code": 401, "json": lambda: {"ok": False}})()  # type: ignore[call-arg]

    monkeypatch.setattr("james.gateway.telegram.requests.post", _fail)
    gateway = GatewayManager(assistant)
    channel = TelegramChannel(gateway, token="BAD")
    with pytest.raises(RuntimeError):
        channel._call("getMe")


def test_telegram_missing_text_ignored(
    assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.gateway.telegram import TelegramChannel

    gateway = GatewayManager(assistant)
    channel = TelegramChannel(gateway, token="TEST:TOKEN")
    channel._process_update({"update_id": 2, "message": {"chat": {"id": 5}}})  # no text
    assert assistant.turns == []
    channel._process_update({"update_id": 3, "channel_post": {}})  # no message key
    assert assistant.turns == []


# ---------------------------------------------------------------------------
# whatsapp
# ---------------------------------------------------------------------------


def test_whatsapp_send(assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    from james.gateway.whatsapp import WhatsAppChannel

    calls = []

    def _post(url: str, **kwargs):
        calls.append((url, kwargs))
        return type("R", (), {"status_code": 201})()

    monkeypatch.setattr("james.gateway.whatsapp.requests.post", _post)
    gateway = GatewayManager(assistant)
    channel = WhatsAppChannel(
        gateway, account_sid="SID", auth_token="AUTH", from_number="whatsapp:+14155552671"
    )
    assert channel.send("hello", "whatsapp:+15551234567") is True
    url, kwargs = calls[0]
    assert "Accounts/SID/Messages.json" in url
    assert kwargs["data"]["To"] == "whatsapp:+15551234567"
    assert kwargs["auth"] == ("SID", "AUTH")


def test_whatsapp_send_failure(assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    from james.gateway.whatsapp import WhatsAppChannel

    monkeypatch.setattr(
        "james.gateway.whatsapp.requests.post",
        lambda *a, **k: type("R", (), {"status_code": 400})(),
    )
    gateway = GatewayManager(assistant)
    channel = WhatsAppChannel(
        gateway, account_sid="SID", auth_token="AUTH", from_number="whatsapp:+1"
    )
    assert channel.send("nope") is False


def test_whatsapp_webhook_dispatches(
    assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.gateway.whatsapp import WhatsAppChannel

    monkeypatch.setattr(settings.gateway, "allow_from", [])
    gateway = GatewayManager(assistant)
    channel = WhatsAppChannel(gateway, account_sid="S", auth_token="A", from_number="whatsapp:+1")
    ok = channel.handle_webhook({"Body": "hello from twilio", "From": "whatsapp:+15551234567"})
    assert ok is True
    assert assistant.turns == ["hello from twilio"]
    assert channel.last_chat_id == "whatsapp:+15551234567"
    assert channel.handle_webhook({"From": "whatsapp:+1"}) is False  # no Body
    assert channel.handle_webhook({}) is False


# ---------------------------------------------------------------------------
# send_message tool
# ---------------------------------------------------------------------------


def test_send_message_without_gateway() -> None:
    from james.tools.gateway_tools import configure_gateway, send_message

    configure_gateway(None)
    result = send_message.run(text="hi", channel="telegram")
    assert not result.ok and "gateway" in result.output.lower()


def test_send_message_routes(assistant: FakeAssistant) -> None:
    from james.tools.gateway_tools import configure_gateway, send_message

    gateway = GatewayManager(assistant)
    channel = MemoryChannel(gateway)
    gateway.channels = [channel]
    configure_gateway(gateway)
    result = send_message.run(text="agent message", channel="memory")
    assert result.ok
    assert channel.sent == [("agent message", "")]
    result = send_message.run(text="x", channel="missing")
    assert not result.ok


# ---------------------------------------------------------------------------
# server API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(assistant: FakeAssistant) -> TestClient:
    gateway = GatewayManager(assistant)
    gateway.channels = [MemoryChannel(gateway)]
    assistant.gateway = gateway
    runtime = ServerRuntime(assistant_factory=lambda: assistant)
    runtime._assistant = assistant
    return TestClient(create_app(runtime))


def test_gateway_status_endpoint(client: TestClient) -> None:
    response = client.get("/api/gateway")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["channels"][0]["name"] == "memory"


def test_gateway_send_endpoint(client: TestClient) -> None:
    response = client.post("/api/gateway/send", json={"channel": "memory", "text": "from ui"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    missing = client.post("/api/gateway/send", json={"channel": "ghost", "text": "x"})
    assert missing.status_code == 400


def test_gateway_whatsapp_webhook_endpoint(client: TestClient, assistant: FakeAssistant) -> None:
    from james.gateway.whatsapp import WhatsAppChannel

    assistant.gateway.channels = [
        WhatsAppChannel(
            assistant.gateway, account_sid="S", auth_token="A", from_number="whatsapp:+1"
        )
    ]
    response = client.post(
        "/api/gateway/whatsapp", data={"Body": "webhook turn", "From": "whatsapp:+15551234567"}
    )
    assert response.status_code == 200
    assert assistant.turns == ["webhook turn"]


def test_gateway_webhook_when_gateway_disabled(
    assistant: FakeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.gateway, "enabled", False)
    runtime = ServerRuntime(assistant_factory=lambda: assistant)
    runtime._assistant = assistant
    client = TestClient(create_app(runtime))
    response = client.post("/api/gateway/whatsapp", data={"Body": "x", "From": "y"})
    assert response.status_code == 503  # gateway is not running
