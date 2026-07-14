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
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .base import Tool, ToolResult


@dataclass
class MCPServerSpec:
    name: str
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = None
    env: Optional[dict] = None
    url: Optional[str] = None


async def _with_stdio(spec: MCPServerSpec, async_fn):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=spec.command, args=spec.args or [], env=spec.env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return await async_fn(session)


async def _with_http(spec: MCPServerSpec, async_fn):
    from mcp import ClientSession

    try:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(spec.url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                return await async_fn(session)
    except ImportError:
        from mcp.client.sse import sse_client

        async with sse_client(spec.url) as (r, w):
            async with ClientSession(r, w) as session:
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


def call_mcp(spec: MCPServerSpec, tool_name: str, arguments: dict) -> str:
    async def _call(session):
        res = await session.call_tool(tool_name, arguments or {})
        return _extract_text(res)

    return asyncio.run(_with_session(spec, _call))


def _spec_from_dict(d: dict) -> MCPServerSpec:
    return MCPServerSpec(
        name=d.get("name", "server"),
        transport=d.get("transport", "stdio"),
        command=d.get("command"),
        args=d.get("args", []) or [],
        env=d.get("env"),
        url=d.get("url"),
    )


def load_mcp_configs() -> List[MCPServerSpec]:
    configs: List[MCPServerSpec] = []
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


def discover_mcp_tools() -> List[Tool]:
    tools: List[Tool] = []
    for spec in load_mcp_configs():
        try:
            async def _list(session):
                return (await session.list_tools()).tools

            mcp_tools = asyncio.run(asyncio.wait_for(_with_session(spec, _list), timeout=20))
            for mt in mcp_tools:
                tools.append(MCPTool(spec, mt))
            print(f"[mcp] loaded {len(mcp_tools)} tool(s) from '{spec.name}'")
        except Exception as exc:
            print(f"[mcp] could not load server '{spec.name}': {exc}")
    return tools
