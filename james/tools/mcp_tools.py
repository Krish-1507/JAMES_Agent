"""MCP client — inherit ANY Model Context Protocol server's tools.

Point JAMES at an MCP server (stdio or HTTP/SSE) and its tools appear in the
registry like native ones — zero glue code per integration. This is the
extensibility wedge: one client unlocks the entire MCP ecosystem (GitHub,
browser-use, databases, thousands of community servers).

Requires the optional `mcp` package:  pip install mcp
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .base import Tool, ToolResult


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


@dataclass
class MCPServerSpec:
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = None
    env: dict | None = None
    url: str | None = None


async def _with_stdio(spec: MCPServerSpec, async_fn):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=spec.command, args=spec.args or [], env=spec.env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        return await async_fn(session)


async def _with_http(spec: MCPServerSpec, async_fn):
    from mcp import ClientSession

    try:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(spec.url) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            return await async_fn(session)
    except ImportError:
        from mcp.client.sse import sse_client

        async with sse_client(spec.url) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            return await async_fn(session)


async def _with_session(spec: MCPServerSpec, async_fn):
    if spec.transport == "http" or spec.url:
        return await _with_http(spec, async_fn)
    return await _with_stdio(spec, async_fn)


def _extract_text(result) -> str:
    parts = []
    for c in getattr(result, "content", []) or []:
        if getattr(c, "type", None) == "text":
            parts.append(c.text)
        else:
            parts.append(str(getattr(c, "data", c)))
    return "\n".join(parts)


_MAX_MCP_ARGS_SIZE = 65536

_SENSITIVE_KEY_PATTERNS = re.compile(r'api[_-]?key|secret|token|password|passwd|auth|credential', re.IGNORECASE)


def _validate_mcp_arguments(arguments: dict, tool_name: str) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError(f"Arguments for '{tool_name}' must be a dict, got {type(arguments).__name__}")
    if len(str(arguments)) > _MAX_MCP_ARGS_SIZE:
        raise ValueError(f"Arguments for '{tool_name}' exceed maximum size of {_MAX_MCP_ARGS_SIZE} bytes")
    sanitized = {}
    for key, value in arguments.items():
        if not isinstance(key, str):
            raise ValueError(f"Argument key must be a string, got {type(key).__name__}")
        if _SENSITIVE_KEY_PATTERNS.search(key) and isinstance(value, str):
            sanitized[key] = "***REDACTED***"
        elif isinstance(value, str) and len(value) > 10000:
            sanitized[key] = value[:10000]
        else:
            sanitized[key] = value
    return sanitized


def call_mcp(spec: MCPServerSpec, tool_name: str, arguments: dict) -> str:
    validated = _validate_mcp_arguments(arguments or {}, tool_name)

    async def _call(session):
        res = await session.call_tool(tool_name, validated)
        return _extract_text(res)

    return _run_async(_with_session(spec, _call))


def _spec_from_dict(d: dict) -> MCPServerSpec:
    return MCPServerSpec(
        name=d.get("name", "server"),
        transport=d.get("transport", "stdio"),
        command=d.get("command"),
        args=d.get("args", []) or [],
        env=d.get("env"),
        url=d.get("url"),
    )


def load_mcp_configs() -> list[MCPServerSpec]:
    configs: list[MCPServerSpec] = []
    p = Path(__file__).resolve().parents[2] / "mcp.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = list(data.values())
            configs += [_spec_from_dict(e) for e in data]
        except Exception as exc:
            print(f"[mcp] bad mcp.json: {exc}")
    env = os.getenv("MCP_SERVERS")
    if env:
        try:
            data = json.loads(env)
            if isinstance(data, dict):
                data = list(data.values())
            configs += [_spec_from_dict(e) for e in data]
        except Exception as exc:
            print(f"[mcp] bad MCP_SERVERS: {exc}")
    return configs


class MCPTool(Tool):
    def __init__(self, spec: MCPServerSpec, mcp_tool):
        self.spec = spec
        self.mcp_name = mcp_tool.name
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in mcp_tool.name)
        self.name = f"mcp_{spec.name}_{safe}"[:64]
        self.description = f"[MCP:{spec.name}] {mcp_tool.description or ''}"
        schema = mcp_tool.inputSchema or {"type": "object", "properties": {}}
        self.parameters = schema.get("properties", {})
        self.required = schema.get("required", [])

    def run(self, **kwargs) -> ToolResult:
        try:
            out = call_mcp(self.spec, self.mcp_name, kwargs)
            return ToolResult(ok=True, output=out)
        except Exception as exc:
            return ToolResult(ok=False, output=f"MCP tool '{self.mcp_name}' failed: {exc}")


def discover_mcp_tools() -> list[Tool]:
    tools: list[Tool] = []
    for spec in load_mcp_configs():
        try:
            async def _list(session):
                return (await session.list_tools()).tools

            mcp_tools = _run_async(asyncio.wait_for(_with_session(spec, _list), timeout=20))
            for mt in mcp_tools:
                tools.append(MCPTool(spec, mt))
            print(f"[mcp] loaded {len(mcp_tools)} tool(s) from '{spec.name}'")
        except Exception as exc:
            print(f"[mcp] could not load server '{spec.name}': {exc}")
    return tools
