"""Slack gateway — Socket Mode via the optional ``slack-sdk`` package.

Install with:  pip install "james-assistant[gateway]"
"""

from __future__ import annotations

import contextlib
import logging

from .base import GatewayChannel

log = logging.getLogger("james.gateway")


class SlackChannel(GatewayChannel):
    name = "slack"

    def __init__(self, manager, app_token: str, bot_token: str) -> None:
        super().__init__(manager)
        self.app_token = app_token
        self.bot_token = bot_token
        self._client = None
        self._web = None

    def _run(self) -> None:
        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.request import SocketModeRequest
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError as exc:
            self.error = "slack-sdk is not installed (pip install 'james-assistant[gateway]')"
            raise RuntimeError(self.error) from exc

        web = WebClient(token=self.bot_token)
        self._web = web
        client = SocketModeClient(app_token=self.app_token, web_client=web)
        self._client = client

        def handle(request: SocketModeRequest) -> None:
            if request.type != "events_api":
                return
            event = (request.payload or {}).get("event", {}) or {}
            if (
                event.get("type") == "message"
                and not event.get("bot_id")
                and not event.get("subtype")
                and event.get("text")
            ):
                self._dispatch(
                    str(event["text"]),
                    chat_id=str(event.get("channel", "")),
                    sender=str(event.get("user", "")),
                )
            with contextlib.suppress(Exception):
                client.send_socket_mode_response(
                    SocketModeResponse(envelope_id=request.envelope_id)
                )

        client.socket_mode_request_listeners.append(handle)
        log.info("slack gateway connecting in Socket Mode…")
        client.connect()  # blocks

    def stop(self) -> None:
        super().stop()
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.disconnect()

    def send(self, text: str, chat_id: str = "") -> bool:
        if self._web is None:
            return False
        target = chat_id or self.last_chat_id
        if not target:
            return False
        result = self._web.chat_postMessage(channel=target, text=text)
        return bool(result and result.get("ok"))
