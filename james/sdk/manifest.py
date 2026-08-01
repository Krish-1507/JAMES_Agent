"""Plugin manifest schema and helpers for the JAMES plugin SDK.

A manifest is the machine-readable contract a plugin declares about itself.
It lives as a block of ``# manifest-*`` comment lines directly under the
``# JAMES-GENERATED-SKILL v1`` header, so the constrained runtime (which only
permits imports and ``@tool`` functions at module scope) never sees it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MANIFEST_PREFIX = "# manifest-"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class PluginManifest:
    """Declared metadata for a JAMES plugin or skill."""

    name: str
    version: str = "1.0.0"
    author: str = "JAMES Community"
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "tags": list(self.tags),
        }


def validate_manifest(manifest: PluginManifest) -> list[str]:
    """Return a list of schema violations (empty when the manifest is valid)."""
    issues: list[str] = []
    if not _NAME_RE.match(manifest.name):
        issues.append(
            f"name '{manifest.name}' must match {_NAME_RE.pattern} (lowercase letters, digits, _ and -)"
        )
    if not _SEMVER_RE.match(manifest.version):
        issues.append(f"version '{manifest.version}' must be semantic (e.g. 1.2.3)")
    if not manifest.author.strip():
        issues.append("author must not be empty")
    if not isinstance(manifest.tags, list) or not all(isinstance(t, str) for t in manifest.tags):
        issues.append("tags must be a list of strings")
    return issues


def format_manifest(manifest: PluginManifest) -> str:
    """Render a manifest as the comment block written into a plugin file."""
    lines = [f"{MANIFEST_PREFIX}name: {manifest.name}"]
    lines.append(f"{MANIFEST_PREFIX}version: {manifest.version}")
    lines.append(f"{MANIFEST_PREFIX}author: {manifest.author}")
    if manifest.description:
        lines.append(f"{MANIFEST_PREFIX}description: {manifest.description}")
    if manifest.tags:
        lines.append(f"{MANIFEST_PREFIX}tags: {','.join(manifest.tags)}")
    return "\n".join(lines) + "\n"


def parse_manifest(source: str) -> PluginManifest | None:
    """Parse the manifest block from a plugin source string, or None if absent."""
    fields: dict[str, str] = {}
    for line in source.splitlines():
        if not line.startswith(MANIFEST_PREFIX):
            continue
        key, _, value = line[len(MANIFEST_PREFIX) :].partition(":")
        fields[key.strip()] = value.strip()
    if not fields:
        return None
    tags = [t.strip() for t in fields.get("tags", "").split(",") if t.strip()]
    return PluginManifest(
        name=fields.get("name", ""),
        version=fields.get("version", "1.0.0"),
        author=fields.get("author", "JAMES Community"),
        description=fields.get("description", ""),
        tags=tags,
    )
