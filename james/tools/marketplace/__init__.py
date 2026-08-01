"""Plugin marketplace — curated registry of community plugins.

Provides a searchable catalog of available plugins that users can
browse, install, and manage from within JAMES.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..base import ToolResult, tool

_MARKETPLACE_FILE = Path(__file__).resolve().parents[2] / "marketplace.json"

_BUILTIN_CATALOG: list[dict[str, Any]] = [
    {
        "name": "file-organizer",
        "description": "Automatically organizes files by type, date, and project.",
        "author": "JAMES Community",
        "version": "1.0.0",
        "tags": ["files", "organization", "automation"],
        "source": "builtin",
    },
    {
        "name": "web-scraper",
        "description": "Scrape web pages and extract structured data.",
        "author": "JAMES Community",
        "version": "1.0.0",
        "tags": ["web", "scraping", "data"],
        "source": "builtin",
    },
    {
        "name": "email-sender",
        "description": "Send emails via SMTP with templates and attachments.",
        "author": "JAMES Community",
        "version": "1.0.0",
        "tags": ["email", "communication"],
        "source": "builtin",
    },
    {
        "name": "calendar-sync",
        "description": "Sync with Google Calendar or Outlook for scheduling.",
        "author": "JAMES Community",
        "version": "1.0.0",
        "tags": ["calendar", "scheduling", "productivity"],
        "source": "builtin",
    },
    {
        "name": "note-taker",
        "description": "Create and manage structured notes with tags and search.",
        "author": "JAMES Community",
        "version": "1.0.0",
        "tags": ["notes", "productivity", "organization"],
        "source": "builtin",
    },
]


def _load_catalog() -> list[dict[str, Any]]:
    if _MARKETPLACE_FILE.exists():
        try:
            return json.loads(_MARKETPLACE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return list(_BUILTIN_CATALOG)


def _save_catalog(catalog: list[dict[str, Any]]) -> None:
    _MARKETPLACE_FILE.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )


@tool(
    "search_plugins",
    "Search the plugin marketplace catalog for available community plugins.",
    {
        "query": {"type": "string", "description": "Search term matched against plugin names and descriptions."},
        "tags": {"type": "array", "description": "Optional tags to filter by (e.g. files, web, automation)."},
    },
)
def search_plugins(query: str = "", tags: list[str] | None = None) -> ToolResult:
    catalog = _load_catalog()
    results = []
    for plugin in catalog:
        if query and query.lower() not in plugin.get("name", "").lower() and query.lower() not in plugin.get("description", "").lower():
            continue
        if tags:
            plugin_tags = plugin.get("tags", [])
            if not any(t in plugin_tags for t in tags):
                continue
        results.append(plugin)
    if not results:
        return ToolResult(ok=True, output="No plugins matched your search.", data=results)
    return ToolResult(
        ok=True,
        output="\n".join(f"{p.get('name')} — {p.get('description')}" for p in results),
        data=results,
    )


@tool(
    "list_plugins",
    "List all plugins available in the marketplace catalog.",
    {},
)
def list_plugins() -> ToolResult:
    catalog = _load_catalog()
    if not catalog:
        return ToolResult(ok=True, output="The marketplace catalog is empty.", data=[])
    return ToolResult(
        ok=True,
        output="\n".join(f"{p.get('name')} — {p.get('description')}" for p in catalog),
        data=catalog,
    )


def _install_plugin(name: str) -> dict[str, Any]:
    catalog = _load_catalog()
    for plugin in catalog:
        if plugin.get("name") == name:
            return {"ok": True, "plugin": plugin, "message": f"Plugin '{name}' installed."}
    return {"ok": False, "error": f"Plugin '{name}' not found in marketplace."}


def add_plugin(plugin: dict[str, Any]) -> dict[str, Any]:
    catalog = _load_catalog()
    catalog.append(plugin)
    _save_catalog(catalog)
    return {"ok": True, "message": f"Plugin '{plugin.get('name')}' added to marketplace."}


def remove_plugin(name: str) -> dict[str, Any]:
    catalog = _load_catalog()
    catalog = [p for p in catalog if p.get("name") != name]
    _save_catalog(catalog)
    return {"ok": True, "message": f"Plugin '{name}' removed from marketplace."}
