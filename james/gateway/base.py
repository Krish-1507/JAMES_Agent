"""Channel abstractions shared by every messaging gateway backend."""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from .manager import GatewayManager

log = logging.getLogger("james.gateway")


@dataclass
class IncomingMessage:
    text: str
    channel: str
    chat_id: str = ""
    sender: str = ""


class GatewayChannel(ABC):
    """One connected messaging service (Telegram, WhatsApp, ...)."""

    name: str = "base"

    def __init__(self, manager: GatewayManager) -> None:
        self.manager = manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_chat_id: str | None = None
        self.error: str | None = None

    @abstractmethod
    def _run(self) -> None:
        """Blocking loop; runs in a daemon thread after :meth:`start`."""

    @abstractmethod
    def send(self, text: str, chat_id: str = "") -> bool:
        """Send *text* to a chat (or the last chat that messaged us)."""

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"gateway-{self.name}", daemon=True)
        self._thread.start()
        log.info("gateway channel '%s' started", self.name)

    def stop(self) -> None:
        self._stop.set()

    # ---- inbound routing ---------------------------------------------------

    def _dispatch(self, text: str, chat_id: str = "", sender: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        if chat_id:
            self.last_chat_id = chat_id
        self.manager.handle_inbound(
            IncomingMessage(text=text, channel=self.name, chat_id=chat_id, sender=sender)
        )


def make_channel(name: str, manager: GatewayManager, **kwargs) -> GatewayChannel:
    """Build a channel by name so config/manager stay import-light."""
    if name == "telegram":
        from .telegram import TelegramChannel

        return TelegramChannel(manager, token=str(kwargs.get("token", "")))
    if name == "discord":
        from .discord import DiscordChannel

        return DiscordChannel(manager, token=str(kwargs.get("token", "")))
    if name == "slack":
        from .slack import SlackChannel

        return SlackChannel(
            manager,
            app_token=str(kwargs.get("app_token", "")),
            bot_token=str(kwargs.get("bot_token", "")),
        )
    if name == "whatsapp":
        from .whatsapp import WhatsAppChannel

        return WhatsAppChannel(
            manager,
            account_sid=str(kwargs.get("account_sid", "")),
            auth_token=str(kwargs.get("auth_token", "")),
            from_number=str(kwargs.get("from_number", "")),
        )
    raise ValueError(f"unknown gateway channel: {name}")
