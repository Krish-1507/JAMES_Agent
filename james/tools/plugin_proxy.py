"""Out-of-process plugin discovery proxies."""

from __future__ import annotations

import ast
from pathlib import Path

from ..core.isolation import run_isolated
from .base import Tool, ToolResult


class IsolatedPluginTool(Tool):
    def __init__(
        self,
        path: Path,
        name: str,
        description: str,
        parameters: dict,
        required: list,
        *,
        trusted: bool,
    ):
        self.path = path
        self.name = name
        self.description = description
        self.parameters = parameters
        self.required = required
        self.trusted = trusted

    def run(self, **kwargs) -> ToolResult:
        result = run_isolated(
            "plugin",
            {
                "path": str(self.path),
                "name": self.name,
                "arguments": kwargs,
                "trusted": self.trusted,
            },
            timeout=120,
        )
        return ToolResult(
            ok=bool(result.get("ok")),
            output=str(result.get("output", "Plugin failed.")),
            data=result.get("data"),
        )


def discover_plugin_tools(path: Path, *, trusted: bool) -> list[IsolatedPluginTool]:
    """Read literal ``@tool`` declarations without importing plugin code."""
    source = path.read_text(encoding="utf-8")
    if trusted:
        from ..config import settings
        from ..sdk.signing import verify_plugin_signature

        trust_dir = settings.assistant.workspace_dir / "trusted_plugin_keys"
        keys = {key.stem: key for key in trust_dir.glob("*.pem")} if trust_dir.is_dir() else {}
        verified, reason = verify_plugin_signature(source, keys)
        if not verified:
            raise ValueError(reason)
    if not trusted:
        from .forge_tools import _GENERATED_SKILL_HEADER, _validate_skill_ast

        if not source.startswith(_GENERATED_SKILL_HEADER):
            raise ValueError("Generated plugin header is missing.")
        issues = _validate_skill_ast(source)
        if issues:
            raise ValueError("; ".join(issues))
    tree = ast.parse(source, filename=str(path))
    tools: list[IsolatedPluginTool] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name):
                continue
            if decorator.func.id != "tool" or len(decorator.args) < 3:
                continue
            name = ast.literal_eval(decorator.args[0])
            description = ast.literal_eval(decorator.args[1])
            parameters = ast.literal_eval(decorator.args[2])
            required = []
            for keyword in decorator.keywords:
                if keyword.arg == "required":
                    required = ast.literal_eval(keyword.value)
            tools.append(
                IsolatedPluginTool(
                    path,
                    name,
                    description,
                    parameters,
                    required,
                    trusted=trusted,
                )
            )
    return tools
