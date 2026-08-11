"""Integrations — one-click connections to the user's apps.

Phase-4 scope: a curated catalog of default MCP servers (filesystem, fetch,
browser, GitHub, Slack, Notion, Gmail, sequential-thinking) that can be
enabled from the web UI with a single toggle. Enabling an integration writes
its server config into ``mcp.json`` (the file :func:`james.tools.mcp_tools.
load_mcp_configs` already reads), so the standard MCP discovery pipeline picks
it up — no new plumbing.
"""

from .manager import IntegrationManager, mcp_config_path

__all__ = ["IntegrationManager", "mcp_config_path"]
