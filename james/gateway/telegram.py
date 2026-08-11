"""Telegram gateway — pure long-polling Bot API via ``requests`` (no extra deps)."""

from __future__ import annotations

import requests  # nosec B113 - API client; tokens are sent to api.telegram.org only

from .base import GatewayChannel

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramChannel(GatewayChannel):
    name = "telegram"

    def __init__(self, manager, token: str) -> None:
        super().__init__(manager)
        self.token = token
        self._offset = 0

    def _call(self, method: str, **params) -> dict:
        response = requests.post(
            _API.format(token=self.token, method=method), data=params, timeout=35
        )
        if response.status_code == 401:
            self.error = "invalid bot token (HTTP 401)"
            raise RuntimeError(self.error)
        response.raise_for_status()
        return response.json()

    def _process_update(self, update: dict) -> None:
        """Handle one Bot API update: advance the offset and dispatch text messages."""
        self._offset = max(self._offset, int(update.get("update_id", 0)))
        message = update.get("message") or {}
        text = message.get("text")
        chat = message.get("chat") or {}
        if text and chat.get("id") is not None:
            self._dispatch(
                text,
                chat_id=str(chat["id"]),
                sender=str((message.get("from") or {}).get("username", "")),
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._call("getUpdates", offset=self._offset + 1, timeout=25)
                for update in payload.get("result", []) or []:
                    self._process_update(update)
            except requests.RequestException as exc:
                self.error = str(exc)[:200]
                if self._stop.wait(5):  # wait is interruptible by stop()
                    return
            except Exception:
                if self._stop.wait(5):
                    return

    def send(self, text: str, chat_id: str = "") -> bool:
        target = chat_id or self.last_chat_id
        if not target:
            return False
        self._call("sendMessage", chat_id=target, text=text)
        return True
