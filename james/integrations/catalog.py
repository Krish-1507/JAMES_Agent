"""Default MCP server catalog for one-click Integrations.

Each entry describes a well-known MCP server. ``command``/``args``/``url``
mirror what ``mcp.json`` expects; ``env`` lists the environment variables the
server needs (they are read from the process environment at runtime, never
stored in the config file). Entries marked ``community`` are third-party
servers whose invocation can change upstream — the command is editable by
hand in ``mcp.json``.
"""

from __future__ import annotations

from typing import Any

MCP_CATALOG: list[dict[str, Any]] = [
    {
        "name": "filesystem",
        "title": "Filesystem",
        "description": "Read, write and organize files on this machine (scoped to the JAMES workspace).",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "<workspace>"],
        "env": [],
        "community": False,
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "name": "fetch",
        "title": "Fetch (web pages)",
        "description": "Fetch a URL and convert its content to markdown for the agent.",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "env": [],
        "community": True,
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "name": "browser_use",
        "title": "Browser automation (Playwright)",
        "description": "Drive a real browser: navigate, click, type and extract from JavaScript-heavy sites.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "env": [],
        "community": False,
        "docs": "https://github.com/microsoft/playwright-mcp",
    },
    {
        "name": "github",
        "title": "GitHub",
        "description": "Repositories, issues, pull requests and code search — needs a GitHub token.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "community": False,
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    },
    {
        "name": "slack",
        "title": "Slack",
        "description": "Read and post to Slack workspaces — needs a Slack bot token and team id.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        "community": False,
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    },
    {
        "name": "notion",
        "title": "Notion",
        "description": "Pages, databases and notes — needs a Notion integration token.",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-notion"],
        "env": ["NOTION_TOKEN"],
        "community": True,
        "docs": "https://github.com/mcp-servers/mcp-notion",
    },
    {
        "name": "gmail",
        "title": "Gmail",
        "description": "Search, read and draft Gmail messages via a community MCP server.",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-gmail"],
        "env": ["GMAIL_API_KEY", "GMAIL_SENDER_EMAIL"],
        "community": True,
        "docs": "https://github.com/staticnoodl/mcp-gmail",
    },
    {
        "name": "sequential_thinking",
        "title": "Sequential thinking",
        "description": "Structured step-by-step reasoning for hard multi-step problems.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env": [],
        "community": False,
        "docs": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    },
]

# Names of the bundled default integrations (used to distinguish catalog
# entries from user-defined servers when enabling/disabling).
CATALOG_NAMES = {str(entry["name"]) for entry in MCP_CATALOG}
