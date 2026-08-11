"""Gateway tools — let the agent send messages to connected channels."""

from __future__ import annotations

from .base import ToolResult, tool

_context = {"manager": None}


def configure_gateway(manager) -> None:
    _context["manager"] = manager


@tool(
    "send_message",
    "Send a message to a connected messaging channel (telegram, whatsapp, discord, slack). "
    "Use this to notify the user or post results to their apps.",
    {
        "channel": {
            "type": "string",
            "description": "Channel name: telegram, whatsapp, discord, or slack.",
        },
        "text": {"type": "string", "description": "Message text to send."},
        "chat_id": {
            "type": "string",
            "description": "Optional chat id (falls back to the last chat that messaged us).",
        },
    },
    required=["channel", "text"],
)
def send_message(channel: str, text: str, chat_id: str = "") -> ToolResult:
    manager = _context["manager"]
    if manager is None:
        return ToolResult(
            ok=False,
            output="No messaging gateway is connected. Configure tokens in .env and restart.",
        )
    ok = manager.send(channel, text, chat_id=chat_id)
    if ok:
        return ToolResult(ok=True, output=f"Message sent to {channel}.")
    return ToolResult(
        ok=False,
        output=f"Could not send to '{channel}'. Check that the channel is enabled and connected.",
    )
