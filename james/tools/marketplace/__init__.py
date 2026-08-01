"""Plugin marketplace — curated registry of community plugins.

Provides a searchable catalog of available plugins that users can
browse, install, and manage from within JAMES. Installable plugins carry
bundled ``code`` for a JAMES-generated skill, which is validated by the
same constrained runtime as Skill Forge before it is persisted or loaded.
"""
from __future__ import annotations

import json
from datetime import datetime
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
    """Install a plugin from the catalog into plugins/ via the constrained runtime.

    Only bundles that carry generated-skill ``code`` are installable — the same
    sandbox that guards Skill Forge applies. Trusted arbitrary Python plugins
    remain a separate, explicit opt-in.
    """
    from ..forge_tools import _persist_skill

    catalog = _load_catalog()
    plugin = next((p for p in catalog if p.get("name") == name), None)
    if plugin is None:
        return {"ok": False, "error": f"Plugin '{name}' not found in marketplace."}
    code = plugin.get("code")
    if not code:
        return {
            "ok": False,
            "error": f"Plugin '{name}' has no bundled code and cannot be installed "
            "from the marketplace yet.",
        }
    description = plugin.get("description", "")
    result = _persist_skill(name, code, description)
    if not result.ok:
        return {"ok": False, "error": result.output}
    return {"ok": True, "plugin": plugin, "message": f"Plugin '{name}' installed."}


def _bundle_skill(name: str, description: str = "") -> dict[str, Any]:
    """Read a saved generated skill from plugins/ and wrap it for the catalog."""
    from ..forge_tools import (
        _GENERATED_SKILL_HEADER,
        _PLUGINS_DIR,
        _find_tools,
        load_generated_skill,
    )

    path = _PLUGINS_DIR / f"{name}.py"
    if not path.exists():
        return {"ok": False, "error": f"Skill '{name}' does not exist in plugins/."}
    source = path.read_text(encoding="utf-8")
    if not source.startswith(_GENERATED_SKILL_HEADER):
        return {"ok": False, "error": f"'{name}' is not a generated skill."}
    try:
        module = load_generated_skill(path)
        tools = _find_tools(module)
    except Exception as exc:
        return {"ok": False, "error": f"Could not load skill: {exc}"}
    if not tools:
        return {"ok": False, "error": "No @tool found in the skill."}
    tool_desc = description or tools[0].description or ""
    return {
        "ok": True,
        "plugin": {
            "name": name,
            "description": tool_desc,
            "author": "JAMES Community",
            "version": "1.0.0",
            "tags": ["skill"],
            "source": "local",
            "code": source,
            "installed": True,
            "added": datetime.now().isoformat(timespec="seconds"),
        },
    }


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


@tool(
    "install_plugin",
    "Install a plugin from the marketplace. Installs bundled generated-skill code "
    "through the constrained Skill Forge runtime.",
    {"name": {"type": "string", "description": "Plugin name from the marketplace catalog."}},
    required=["name"],
)
def install_plugin(name: str) -> ToolResult:
    result = _install_plugin(name)
    if not result["ok"]:
        return ToolResult(ok=False, output=result["error"])
    return ToolResult(ok=True, output=result["message"])


@tool(
    "publish_skill",
    "Publish a saved generated skill to the local marketplace catalog so it can be "
    "installed again later or shared.",
    {
        "name": {"type": "string", "description": "Skill name (file in plugins/ without .py)."},
        "description": {"type": "string", "description": "Short description for the catalog."},
    },
    required=["name"],
)
def publish_skill(name: str, description: str = "") -> ToolResult:
    bundle = _bundle_skill(name, description)
    if not bundle["ok"]:
        return ToolResult(ok=False, output=bundle["error"])
    catalog = _load_catalog()
    existing = next((p for p in catalog if p.get("name") == name), None)
    if existing is not None:
        catalog.remove(existing)
    catalog.append(bundle["plugin"])
    _save_catalog(catalog)
    return ToolResult(
        ok=True,
        output=f"Published '{name}' to the marketplace ({len(catalog)} plugins).",
        data=bundle["plugin"],
    )
