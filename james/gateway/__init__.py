"""Messaging gateway — bridge Telegram, WhatsApp, Discord and Slack to the agent core.

A :class:`GatewayManager` owns the enabled channels. Inbound messages from any
channel are routed into the same :class:`Assistant` turn pipeline as the web
UI and CLI, and the assistant's reply is delivered back to the originating
channel. The agent can also push messages proactively via the ``send_message``
tool.
"""

from .manager import GatewayManager

__all__ = ["GatewayManager"]
