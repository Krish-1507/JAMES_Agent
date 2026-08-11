"""Manage the ``mcp.json`` config file from the Integrations catalog.

The web UI and the agent both talk to this module: enabling an integration
appends its server spec to ``mcp.json``, disabling removes it. User-defined
servers (anything not in the default catalog) are preserved untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import settings
from .catalog import MCP_CATALOG


def mcp_config_path() -> Path:
    """Where the MCP server config lives (env override, else project root)."""
    override = os.getenv("MCP_CONFIG_PATH", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2] / "mcp.json"


def _catalog_env_status(entry: dict[str, Any]) -> dict[str, bool]:
    return {str(var): bool(os.getenv(var, "").strip()) for var in (entry.get("env") or [])}


class IntegrationManager:
    """Read/write the enabled set of MCP integrations."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or mcp_config_path()

    # ---- config file IO ---------------------------------------------------

    def load_config(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # corrupt config must not crash the UI
            raise ValueError(f"bad mcp.json: {exc}") from exc
        if isinstance(data, dict):
            data = list(data.values())
        if not isinstance(data, list):
            raise ValueError("mcp.json must be a list of server objects")
        return [e for e in data if isinstance(e, dict)]

    def save_config(self, servers: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(servers, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _enabled_names(self) -> set[str]:
        return {str(e.get("name")) for e in self.load_config()}

    # ---- status -----------------------------------------------------------

    def status(self) -> list[dict[str, Any]]:
        """Merge the catalog with the current config; one row per integration."""
        enabled = self._enabled_names()
        rows = []
        for entry in MCP_CATALOG:
            name = str(entry["name"])
            rows.append(
                {
                    "name": name,
                    "title": str(entry.get("title", name)),
                    "description": str(entry.get("description", "")),
                    "transport": str(entry.get("transport", "stdio")),
                    "command": str(entry.get("command", "")),
                    "args": list(entry.get("args") or []),
                    "url": entry.get("url") or "",
                    "env": {k: {"set": v} for k, v in _catalog_env_status(entry).items()},
                    "community": bool(entry.get("community", False)),
                    "enabled": name in enabled,
                    "docs": str(entry.get("docs", "")),
                }
            )
        return rows

    def enabled_count(self) -> int:
        return sum(1 for row in self.status() if row["enabled"])

    def enabled_servers(self) -> list[dict[str, Any]]:
        enabled = self._enabled_names()
        return [entry for entry in MCP_CATALOG if entry["name"] in enabled]

    # ---- mutations --------------------------------------------------------

    def _server_entry(self, name: str) -> dict[str, Any]:
        for entry in MCP_CATALOG:
            if entry["name"] == name:
                args = list(entry.get("args") or [])
                # Substitute the workspace placeholder with the real path.
                if entry.get("command") == "npx" and "<workspace>" in args:
                    args = [
                        str(settings.assistant.workspace_dir) if a == "<workspace>" else a
                        for a in args
                    ]
                # Catalog env may be a dict {"VAR": "hint"} or a list of VAR names;
                # mcp.json always stores a mapping of VAR -> value.
                raw_env = entry.get("env") or {}
                if isinstance(raw_env, list):
                    raw_env = {str(v): "" for v in raw_env}
                return {
                    "name": name,
                    "transport": str(entry.get("transport", "stdio")),
                    "command": str(entry.get("command", "")),
                    "args": args,
                    "env": {str(k): str(v) for k, v in dict(raw_env).items()},
                }
        raise KeyError(f"Unknown integration: {name}")

    def enable(self, name: str) -> tuple[bool, str]:
        servers = self.load_config()
        if any(str(e.get("name")) == name for e in servers):
            return False, f"'{name}' is already enabled"
        try:
            entry = self._server_entry(name)
        except KeyError as exc:
            return False, str(exc)
        servers.append(entry)
        try:
            self.save_config(servers)
        except Exception as exc:  # pragma: no cover - filesystem errors
            return False, f"could not write {self.path}: {exc}"
        return True, f"Enabled '{name}'. Reload the tool registry to pick it up."

    def disable(self, name: str) -> tuple[bool, str]:
        servers = self.load_config()
        kept = [e for e in servers if str(e.get("name")) != name]
        if len(kept) == len(servers):
            return False, f"'{name}' is not enabled"
        try:
            self.save_config(kept)
        except Exception as exc:  # pragma: no cover - filesystem errors
            return False, f"could not write {self.path}: {exc}"
        return True, f"Disabled '{name}'. Reload the tool registry to drop its tools."


manager = IntegrationManager()
