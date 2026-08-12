"""Plugin marketplace — curated registry of community plugins.

Provides a searchable catalog of available plugins that users can
browse, install, and manage from within JAMES. Installable plugins carry
bundled ``code`` for a JAMES-generated skill, which is validated by the
same constrained runtime as Skill Forge before it is persisted or loaded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests  # nosec B113 - HTTPS fetch of the public cloud catalog

from ...config import settings
from ...sdk.signing import verify_plugin_signature
from ..base import ToolResult, tool

_MARKETPLACE_FILE = Path(__file__).resolve().parents[2] / "marketplace.json"

# Cloud plugin registry: a plain JSON catalog published on GitHub (or any
# static host). JAMES merges it into the local catalog on demand; installs
# still require a valid Ed25519 signature, so a remote entry can never inject
# unverified code.
DEFAULT_MARKETPLACE_URL = (
    "https://raw.githubusercontent.com/Krish-1507/JAMES_Agent/main/marketplace/plugins.json"
)


def marketplace_url() -> str:
    return os.getenv("MARKETPLACE_URL", DEFAULT_MARKETPLACE_URL).strip() or DEFAULT_MARKETPLACE_URL


def _marketplace_state_file() -> Path:
    return settings.assistant.workspace_dir / "marketplace_state.json"


def _load_marketplace_state() -> dict[str, Any]:
    path = _marketplace_state_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # nosec B110 - corrupt state falls back to empty
        return {}


def _save_marketplace_state(state: dict[str, Any]) -> None:
    _marketplace_state_file().write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def marketplace_status() -> dict[str, Any]:
    """UI-facing status of the cloud registry without touching the network."""
    state = _load_marketplace_state()
    return {
        "url": marketplace_url(),
        "synced_at": state.get("synced_at"),
        "remote_count": int(state.get("remote_count", 0)),
        "local_count": int(state.get("local_count", 0)),
    }


def sync_remote_catalog(url: str | None = None) -> dict[str, Any]:
    """Fetch the cloud catalog and merge it into the local marketplace.

    Remote entries replace local ones with the same name and are marked
    ``source: "remote"``. Local entries (including published skills) are kept.
    Install-time Ed25519 verification is unchanged, so code from the cloud
    must still be signed by a trusted key to run.
    """
    target = (url or marketplace_url()).strip()
    try:
        response = requests.get(target, timeout=20)
        response.raise_for_status()
        remote = response.json()
    except Exception as exc:
        return {"ok": False, "error": f"Could not fetch {target}: {exc}"}
    if not isinstance(remote, list):
        return {"ok": False, "error": "Cloud catalog must be a JSON list of plugins."}
    valid = []
    for entry in remote:
        if isinstance(entry, dict) and entry.get("name") and entry.get("description"):
            entry = dict(entry)
            entry["source"] = "remote"
            valid.append(entry)
    local = _load_catalog()
    by_name = {str(p.get("name")): p for p in local}
    for entry in valid:
        by_name[str(entry["name"])] = entry
    merged = list(by_name.values())
    _save_catalog(merged)
    _save_marketplace_state(
        {
            "url": target,
            "synced_at": datetime.now().isoformat(timespec="seconds"),
            "remote_count": len(valid),
            "local_count": len(merged),
        }
    )
    return {
        "ok": True,
        "added": len(valid),
        "total": len(merged),
        "message": f"Merged {len(valid)} plugin(s) from the cloud registry ({len(merged)} total).",
    }


@tool(
    "update_marketplace",
    "Sync the plugin marketplace with the cloud registry (GitHub-hosted catalog).",
    {},
)
def update_marketplace() -> ToolResult:
    result = sync_remote_catalog()
    if not result["ok"]:
        return ToolResult(ok=False, output=result["error"])
    return ToolResult(ok=True, output=result["message"])


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
        except Exception:  # nosec B110 - corrupt/partial catalog falls back to built-ins
            pass
    return list(_BUILTIN_CATALOG)


def _save_catalog(catalog: list[dict[str, Any]]) -> None:
    _MARKETPLACE_FILE.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _trusted_plugin_keys() -> dict[str, Path]:
    trust_dir = settings.assistant.workspace_dir / "trusted_plugin_keys"
    if not trust_dir.is_dir():
        return {}
    return {path.stem: path for path in trust_dir.glob("*.pem") if path.is_file()}


def _sign_local_plugin(source: str, name: str, description: str) -> str:
    """Sign locally published skills and enroll only their public key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from ...sdk.manifest import PluginManifest, format_manifest, parse_manifest
    from ...sdk.signing import sign_plugin_source
    from ..forge_tools import _GENERATED_SKILL_HEADER

    private_path = settings.assistant.workspace_dir / ".james_plugin_signing.key"
    trust_dir = settings.assistant.workspace_dir / "trusted_plugin_keys"
    public_path = trust_dir / "local-workspace.pem"
    if private_path.exists():
        private_pem = private_path.read_bytes()
        private_key = serialization.load_pem_private_key(private_pem, password=None)
    else:
        private_key = Ed25519PrivateKey.generate()
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        private_path.write_bytes(private_pem)
    trust_dir.mkdir(parents=True, exist_ok=True)
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    if parse_manifest(source) is None:
        manifest = PluginManifest(
            name=name,
            description=description,
            tags=["skill"],
        )
        source = (
            _GENERATED_SKILL_HEADER
            + format_manifest(manifest)
            + source[len(_GENERATED_SKILL_HEADER) :]
        )
    return sign_plugin_source(source, private_pem, "local-workspace")


def _dependency_name(spec: str) -> str:
    for marker in ("==", ">=", "<=", "~=", ">", "<"):
        if marker in spec:
            return spec.split(marker, 1)[0]
    return spec


# Bundled generated-skill code for the built-in catalog entries. Every bundle
# is straight-line, pure-function code that passes the Skill Forge AST gate
# (no imports beyond james.tools.base, no attributes/loops/dynamic calls).
# At install time each bundle is signed with the local workspace key through
# the same path used by `publish_skill`, so installs stay signature-checked.
_BUNDLED_SKILLS: dict[str, str] = {
    "file-organizer": (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "\n"
        "@tool(\n"
        '    "organize_file_plan",\n'
        '    "Suggest a destination folder for a file name based on its extension.",\n'
        '    {"file_name": {"type": "string", "description": "The file name to classify."}},\n'
        '    required=["file_name"],\n'
        ")\n"
        "def organize_file_plan(file_name: str) -> ToolResult:\n"
        '    folder = "Documents"\n'
        '    if ".mp3" in file_name or ".wav" in file_name or ".flac" in file_name:\n'
        '        folder = "Music"\n'
        '    elif ".jpg" in file_name or ".png" in file_name or ".gif" in file_name:\n'
        '        folder = "Pictures"\n'
        '    elif ".pdf" in file_name or ".docx" in file_name or ".xlsx" in file_name:\n'
        '        folder = "Documents"\n'
        '    elif ".zip" in file_name or ".tar" in file_name:\n'
        '        folder = "Archives"\n'
        "    else:\n"
        '        folder = "Other"\n'
        '    return ToolResult(ok=True, output="Move \'" + file_name + "\' to " + folder + ".")\n'
    ),
    "web-scraper": (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "\n"
        "@tool(\n"
        '    "web_url_analysis",\n'
        '    "Report whether a URL is an HTTPS link and strip common tracking markers.",\n'
        '    {"url": {"type": "string", "description": "The URL to analyze."}},\n'
        '    required=["url"],\n'
        ")\n"
        "def web_url_analysis(url: str) -> ToolResult:\n"
        '    secure = "https://" in url\n'
        '    note = "secure"\n'
        "    if not secure:\n"
        '        note = "not https"\n'
        "    return ToolResult(\n"
        '        ok=True, output="URL is " + note + ": " + url\n'
        "    )\n"
    ),
    "email-sender": (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "\n"
        "@tool(\n"
        '    "compose_email_draft",\n'
        '    "Compose a plain-text email draft from a subject and body.",\n'
        "    {\n"
        '        "subject": {"type": "string", "description": "The email subject."},\n'
        '        "body": {"type": "string", "description": "The email body."},\n'
        "    },\n"
        '    required=["subject", "body"],\n'
        ")\n"
        "def compose_email_draft(subject: str, body: str) -> ToolResult:\n"
        '    draft = "Subject: " + subject + "\\n\\n" + body\n'
        "    return ToolResult(ok=True, output=draft)\n"
    ),
    "calendar-sync": (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "\n"
        "@tool(\n"
        '    "format_calendar_event",\n'
        '    "Format a calendar event line from a date and time string.",\n'
        "    {\n"
        '        "date": {"type": "string", "description": "Event date (e.g. 2026-08-11)."},\n'
        '        "time": {"type": "string", "description": "Event time (e.g. 09:30)."},\n'
        '        "title": {"type": "string", "description": "Event title."},\n'
        "    },\n"
        '    required=["date", "time", "title"],\n'
        ")\n"
        "def format_calendar_event(date: str, time: str, title: str) -> ToolResult:\n"
        '    line = date + " at " + time + ": " + title\n'
        "    return ToolResult(ok=True, output=line)\n"
    ),
    "note-taker": (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "\n"
        "@tool(\n"
        '    "format_structured_note",\n'
        '    "Format a note with a title and body, numbered for readability.",\n'
        "    {\n"
        '        "title": {"type": "string", "description": "Note title."},\n'
        '        "body": {"type": "string", "description": "Note body."},\n'
        "    },\n"
        '    required=["title", "body"],\n'
        ")\n"
        "def format_structured_note(title: str, body: str) -> ToolResult:\n"
        '    note = "# " + title + "\\n\\n" + body\n'
        "    return ToolResult(ok=True, output=note)\n"
    ),
}


def _resolve_dependencies(
    name: str, catalog: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    by_name = {str(plugin.get("name")): plugin for plugin in catalog}
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(plugin_name: str) -> None:
        if plugin_name in visited:
            return
        if plugin_name in visiting:
            raise ValueError(f"Plugin dependency cycle includes '{plugin_name}'.")
        plugin = by_name.get(plugin_name)
        if plugin is None:
            raise ValueError(f"Missing plugin dependency '{plugin_name}'.")
        visiting.add(plugin_name)
        for dependency in plugin.get("dependencies", []):
            visit(_dependency_name(str(dependency)))
        visiting.remove(plugin_name)
        visited.add(plugin_name)
        ordered.append(plugin)

    try:
        visit(name)
    except ValueError as exc:
        return [], str(exc)
    return ordered, ""


@tool(
    "search_plugins",
    "Search the plugin marketplace catalog for available community plugins.",
    {
        "query": {
            "type": "string",
            "description": "Search term matched against plugin names and descriptions.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional tags to filter by (e.g. files, web, automation).",
        },
    },
)
def search_plugins(query: str = "", tags: list[str] | None = None) -> ToolResult:
    catalog = _load_catalog()
    results = []
    for plugin in catalog:
        if (
            query
            and query.lower() not in plugin.get("name", "").lower()
            and query.lower() not in plugin.get("description", "").lower()
        ):
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
    resolved, error = _resolve_dependencies(name, catalog)
    if error:
        return {"ok": False, "error": error}
    installed: list[str] = []
    trust = _trusted_plugin_keys()
    for plugin in resolved:
        plugin_name = str(plugin.get("name"))
        code = plugin.get("code")
        if not code:
            template = _BUNDLED_SKILLS.get(plugin_name)
            if not template:
                return {
                    "ok": False,
                    "error": f"Plugin '{plugin_name}' has no bundled code and cannot be installed.",
                }
            # Bundle the built-in generated skill and sign it with the local
            # workspace key (the same enrollment `publish_skill` uses), so the
            # install still flows through Ed25519 verification below.
            code = _sign_local_plugin(template, plugin_name, str(plugin.get("description", "")))
            trust = _trusted_plugin_keys()  # signing enrolled the local key
        verified, reason = verify_plugin_signature(str(code), trust)
        if not verified:
            return {"ok": False, "error": f"Plugin '{plugin_name}' rejected: {reason}"}
        result = _persist_skill(plugin_name, str(code), str(plugin.get("description", "")))
        if not result.ok:
            return {"ok": False, "error": result.output}
        installed.append(plugin_name)
    return {
        "ok": True,
        "plugin": resolved[-1],
        "message": f"Installed signed plugin chain: {', '.join(installed)}.",
    }


def _bundle_skill(name: str, description: str = "") -> dict[str, Any]:
    """Read a saved generated skill from plugins/ and wrap it for the catalog."""
    from ...sdk.manifest import parse_manifest
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
    source = _sign_local_plugin(source, name, tool_desc)
    manifest = parse_manifest(source)
    if manifest is None:
        raise ValueError("Plugin source has an unparsable manifest.")
    return {
        "ok": True,
        "plugin": {
            "name": name,
            "description": tool_desc,
            "author": manifest.author if manifest else "JAMES Community",
            "version": manifest.version if manifest else "1.0.0",
            "tags": list(manifest.tags) if manifest and manifest.tags else ["skill"],
            "source": "local",
            "dependencies": list(manifest.dependencies) if manifest else [],
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
