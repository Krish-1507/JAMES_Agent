"""Discord gateway — uses the optional ``discord.py`` package.

Install with:  pip install "james-assistant[gateway]"
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .base import GatewayChannel

log = logging.getLogger("james.gateway")


class DiscordChannel(GatewayChannel):
    name = "discord"

    def __init__(self, manager, token: str) -> None:
        super().__init__(manager)
        self.token = token
        self._client = None

    def _run(self) -> None:
        try:
            import discord
        except ImportError as exc:
            self.error = "discord.py is not installed (pip install 'james-assistant[gateway]')"
            raise RuntimeError(self.error) from exc

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:
            log.info("discord gateway online as %s", client.user)

        @client.event
        async def on_message(message) -> None:
            if message.author.bot:
                return
            if message.content and message.channel is not None:
                self._dispatch(
                    message.content,
                    chat_id=str(message.channel.id),
                    sender=str(message.author),
                )

        client.run(self.token, log_handler=None)

    def stop(self) -> None:
        super().stop()
        if self._client is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self._client.close(), self._client.loop)

    def send(self, text: str, chat_id: str = "") -> bool:
        if self._client is None:
            return False
        target = chat_id or self.last_chat_id
        if not target:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._send_to_channel(int(target), text), self._client.loop
        )
        return bool(future.result(timeout=20))

    async def _send_to_channel(self, channel_id: int, text: str) -> bool:
        channel = self._client.get_channel(channel_id)
        if channel is None:
            return False
        await channel.send(text)
        return True
