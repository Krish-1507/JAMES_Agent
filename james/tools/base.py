"""Tool framework.

Every capability JAMES has — reading a file, making a PowerPoint, browsing the
web — is expressed as a :class:`Tool`. The agent loop discovers tools via the
registry, exposes their JSON schemas to the LLM, and executes whatever the
model decides to call.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: Any = None


class Tool(ABC):
    name: str = "tool"
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }

    def to_openai(self) -> Dict[str, Any]:
        return self.schema()


class FunctionTool(Tool):
    """Wrap a plain function as a tool (keeps the framework ergonomic)."""

    def __init__(self, func, name, description, parameters, required):
        self._func = func
        self.name = name
        self.description = description
        self.parameters = parameters
        self.required = required

    def run(self, **kwargs) -> ToolResult:
        try:
            result = self._func(**kwargs)
        except Exception as exc:  # surface errors back to the model
            return ToolResult(ok=False, output=f"Error: {exc}")
        if isinstance(result, ToolResult):
            return result
        return ToolResult(ok=True, output=str(result))


def tool(name: str, description: str, parameters: Dict[str, Any], required: List[str] | None = None):
    def decorator(func):
        return FunctionTool(func, name, description, parameters, required or [])

    return decorator
