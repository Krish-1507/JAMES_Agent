"""GatewayManager — owns channels and routes messages to/from the assistant.

Inbound messages run as assistant turns in dedicated threads; the assistant's
``reply`` event is delivered back to the channel that sent the message (and
only that channel, so a busy multi-channel session never cross-posts).
"""

from __future__ import annotations

import logging
import threading
from contextlib import suppress

from ..config import settings
from .base import GatewayChannel, IncomingMessage, make_channel

log = logging.getLogger("james.gateway")


class GatewayManager:
    def __init__(self, assistant) -> None:
        self.assistant = assistant
        self.channels: list[GatewayChannel] = []
        self.started = False
        self._reply_channel: GatewayChannel | None = None
        self._turn_threads: set[threading.Thread] = set()
        self._lock = threading.Lock()
        # Chain the assistant's event hook so existing UIs keep receiving
        # events while the gateway also watches for replies.
        self._previous_on_event = getattr(assistant, "on_event", None)
        assistant.on_event = self._on_event

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.started:
            return
        g = settings.gateway
        specs = []
        if g.telegram_token:
            specs.append(("telegram", {"token": g.telegram_token}))
        if g.discord_token:
            specs.append(("discord", {"token": g.discord_token}))
        if g.slack_app_token and g.slack_bot_token:
            specs.append(
                ("slack", {"app_token": g.slack_app_token, "bot_token": g.slack_bot_token})
            )
        if g.twilio_account_sid and g.twilio_auth_token and g.twilio_whatsapp_from:
            specs.append(
                (
                    "whatsapp",
                    {
                        "account_sid": g.twilio_account_sid,
                        "auth_token": g.twilio_auth_token,
                        "from_number": g.twilio_whatsapp_from,
                    },
                )
            )
        for name, kwargs in specs:
            try:
                self.channels.append(make_channel(name, self, **kwargs))
            except Exception as exc:
                log.warning("gateway channel '%s' failed to build: %s", name, exc)
        for channel in self.channels:
            with suppress(Exception):
                channel.start()
        self.started = True
        if not self.channels:
            log.info("gateway enabled but no channel configured (add tokens to .env)")

    def stop(self) -> None:
        for channel in self.channels:
            with suppress(Exception):
                channel.stop()
        self.started = False
        if self._previous_on_event is not None:
            self.assistant.on_event = self._previous_on_event

    # ---- event chain -------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        event = dict(event or {})
        if event.get("type") == "reply" and self._reply_channel is not None:
            text = str(event.get("text", "")).strip()
            if text:
                with suppress(Exception):
                    self._reply_channel.send(text)
        if self._previous_on_event is not None:
            with suppress(Exception):
                self._previous_on_event(event)

    # ---- inbound -----------------------------------------------------------

    def _allowed(self, message: IncomingMessage) -> bool:
        allow = [str(x).strip().lower() for x in (settings.gateway.allow_from or [])]
        if not allow:
            return True
        from_id = (message.sender or message.chat_id or "").lower()
        chat = (message.chat_id or "").lower()
        return from_id in allow or chat in allow

    def handle_inbound(self, message: IncomingMessage) -> bool:
        if not self._allowed(message):
            log.warning("gateway: blocked message from %s on %s", message.sender, message.channel)
            return False
        channel = next((c for c in self.channels if c.name == message.channel), None)
        self._reply_channel = channel

        def _run() -> None:
            try:
                self.assistant.handle_turn(message.text)
            finally:
                self._reply_channel = None
                with self._lock:
                    self._turn_threads.discard(threading.current_thread())

        thread = threading.Thread(target=_run, name=f"gateway-turn-{message.channel}", daemon=True)
        with self._lock:
            self._turn_threads.add(thread)
        thread.start()
        return True

    # ---- proactive send ----------------------------------------------------

    def send(self, channel: str, text: str, chat_id: str = "") -> bool:
        text = (text or "").strip()
        if not text:
            return False
        target = next((c for c in self.channels if c.name == channel.lower()), None)
        if target is None:
            return False
        try:
            return target.send(text, chat_id=chat_id)
        except Exception as exc:
            log.warning("gateway send to '%s' failed: %s", channel, exc)
            return False

    def status(self) -> list[dict]:
        rows = []
        for channel in self.channels:
            rows.append(
                {
                    "name": channel.name,
                    "running": bool(channel._thread and channel._thread.is_alive()),
                    "last_chat": channel.last_chat_id,
                    "error": channel.error,
                }
            )
        return rows
