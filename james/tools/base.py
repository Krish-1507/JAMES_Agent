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

    def _validate_args(self, **kwargs) -> list[str]:
        errors = []
        for param_name, param_schema in self.parameters.items():
            value = kwargs.get(param_name)
            if param_name in self.required and value is None:
                errors.append(f"Missing required parameter: {param_name}")
                continue
            if value is None:
                continue
            param_type = param_schema.get("type", "string")
            if param_type == "integer":
                if not isinstance(value, int):
                    errors.append(f"Parameter '{param_name}' must be an integer, got {type(value).__name__}")
            elif param_type == "number":
                if not isinstance(value, (int, float)):
                    errors.append(f"Parameter '{param_name}' must be a number, got {type(value).__name__}")
            elif param_type == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"Parameter '{param_name}' must be a boolean, got {type(value).__name__}")
            elif param_type == "string":
                if not isinstance(value, str):
                    errors.append(f"Parameter '{param_name}' must be a string, got {type(value).__name__}")
            elif param_type == "array":
                if not isinstance(value, list):
                    errors.append(f"Parameter '{param_name}' must be an array, got {type(value).__name__}")
            elif param_type == "object":
                if not isinstance(value, dict):
                    errors.append(f"Parameter '{param_name}' must be an object, got {type(value).__name__}")
            max_length = param_schema.get("maxLength")
            if max_length is not None and isinstance(value, str) and len(value) > max_length:
                errors.append(f"Parameter '{param_name}' exceeds maximum length of {max_length}")
            minimum = param_schema.get("minimum")
            if minimum is not None and isinstance(value, (int, float)) and value < minimum:
                errors.append(f"Parameter '{param_name}' is below minimum of {minimum}")
            maximum = param_schema.get("maximum")
            if maximum is not None and isinstance(value, (int, float)) and value > maximum:
                errors.append(f"Parameter '{param_name}' exceeds maximum of {maximum}")
        return errors

    def run(self, **kwargs) -> ToolResult:
        validation_errors = self._validate_args(**kwargs)
        if validation_errors:
            return ToolResult(ok=False, output="Invalid arguments: " + "; ".join(validation_errors))
        try:
            result = self._func(**kwargs)
        except Exception as exc:  # surface errors back to the model
            return ToolResult(ok=False, output=f"Error: {exc}")
        if isinstance(result, ToolResult):
            return result
        return ToolResult(ok=True, output=str(result))

    def stream(self, **kwargs):
        """Stream tool output in chunks for long-running operations.

        Override this in subclasses that produce incremental output.
        By default, yields a single chunk with the full result.
        """
        result = self.run(**kwargs)
        yield result

    def stream(self, **kwargs):
        """Stream tool output in chunks for long-running operations.

        Override this in subclasses that produce incremental output.
        By default, yields a single chunk with the full result.
        """
        result = self.run(**kwargs)
        yield result


def tool(name: str, description: str, parameters: Dict[str, Any], required: List[str] | None = None):
    def decorator(func):
        return FunctionTool(func, name, description, parameters, required or [])

    return decorator
