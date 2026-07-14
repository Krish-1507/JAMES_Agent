"""LLM provider abstraction.

JAMES talks to language models through a single, normalized interface so the
rest of the system never needs to know which provider is behind it. Every
provider returns a :class:`LLMResponse` containing text content and (optionally)
structured tool calls.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

Message = Dict[str, Any]
Tool = Dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Any = None
    finish_reason: Optional[str] = None


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """Send a conversation to the model and return a normalized response."""
        raise NotImplementedError

    def validate(self) -> None:
        """Raise if the provider is not configured (e.g. missing API key)."""
        return None
