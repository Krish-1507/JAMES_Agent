"""LLM provider abstraction.

JAMES talks to language models through a single, normalized interface so the
rest of the system never needs to know which provider is behind it. Every
provider returns a :class:`LLMResponse` containing text content and (optionally)
structured tool calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, Any]
Tool = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    finish_reason: str | None = None


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        tool_choice: str = "auto",
        images: list[str] | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a conversation to the model and return a normalized response.

        ``images`` is an optional list of base64-encoded PNGs (or http(s) URLs)
        attached to the last user message for vision tasks (computer-use).
        ``model`` optionally overrides the provider's default model for a call.
        """
        raise NotImplementedError

    def validate(self) -> None:
        """Raise if the provider is not configured (e.g. missing API key)."""
        return None
