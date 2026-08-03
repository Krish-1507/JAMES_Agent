"""JAMES Plugin SDK.

A stable, documented surface for authoring JAMES plugins and skills.
Third-party code should import from here, never from internal modules.

Two authoring tiers are supported:

* **Generated skills** (constrained, safe-by-default). Pure ``@tool`` functions
  validated by the Skill Forge runtime; this is what the marketplace installs.
* **Trusted external plugins** (opt-in, arbitrary Python). Loaded only when
  ``ENABLE_TRUSTED_EXTERNAL_PLUGINS=true``.

Basic usage:

    from james.sdk import tool, ToolResult

    @tool("hello", "Say hello.", {"name": {"type": "string"}}, required=["name"])
    def hello(name: str) -> ToolResult:
        return ToolResult(ok=True, output=f"Hello, {name}!")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..tools.base import FunctionTool, Tool, ToolResult, tool
from .manifest import (
    MANIFEST_PREFIX,
    PluginManifest,
    format_manifest,
    parse_manifest,
    validate_manifest,
)
from .signing import (
    canonical_plugin_bytes,
    plugin_digest,
    sign_plugin_source,
    verify_plugin_signature,
)

GENERATED_HEADER = "# JAMES-GENERATED-SKILL v1\n"

__all__ = [
    "GENERATED_HEADER",
    "MANIFEST_PREFIX",
    "FunctionTool",
    "PluginManifest",
    "Tool",
    "ToolResult",
    "canonical_plugin_bytes",
    "create_plugin",
    "format_manifest",
    "load_plugin",
    "parse_manifest",
    "plugin_digest",
    "sign_plugin_source",
    "tool",
    "validate_manifest",
    "validate_plugin",
    "verify_plugin_signature",
]

# Kept in sync with james.tools.forge_tools so plugins can be authored with one
# canonical header constant.
GENERATED_SKILL_HEADER = GENERATED_HEADER

_TEMPLATE = '''"""{description}"""
from james.sdk import tool, ToolResult


@tool(
    "{name}",
    "{description}",
    {{{params}}},
    required={required},
)
def {name}({args}):
    return ToolResult(ok=True, output=f"Processed input")
'''


def _clean_name(name: str) -> str:
    import re

    cleaned = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    return cleaned or "my_plugin"


def _plugin_dir(directory: str | Path | None) -> Path:
    if directory is not None:
        return Path(directory)
    from ..tools.forge_tools import _PLUGINS_DIR

    return _PLUGINS_DIR


def validate_plugin(source: str) -> list[str]:
    """Validate plugin source against the constrained runtime rules.

    Returns an empty list when the source is safe and loadable, otherwise a
    list of human-readable violations.
    """
    from ..tools.forge_tools import _validate_skill_ast

    if not source.startswith(GENERATED_HEADER):
        return ["Plugin source must start with the JAMES generated-skill header."]
    manifest = parse_manifest(source)
    issues: list[str] = []
    if manifest is not None:
        issues = validate_manifest(manifest)
    return issues + _validate_skill_ast(source)


def create_plugin(
    name: str,
    description: str = "",
    author: str = "JAMES Community",
    version: str = "1.0.0",
    tags: list[str] | None = None,
    directory: str | Path | None = None,
    dependencies: list[str] | None = None,
    overwrite: bool = False,
) -> Path:
    """Scaffold a new generated-skill plugin file in the plugins directory.

    The generated file carries a manifest header and a stub ``@tool`` function
    that passes the constrained runtime validation. Returns the written path.
    """
    clean = _clean_name(name)
    manifest = PluginManifest(
        name=clean,
        version=version,
        author=author,
        description=description,
        tags=tags or [],
        dependencies=dependencies or [],
    )
    path = _plugin_dir(directory) / f"{clean}.py"
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists (pass overwrite=True to replace it)")

    header = GENERATED_HEADER + format_manifest(manifest)
    params = '"value": {"type": "string", "description": "Describe the input."}'
    required = '["value"]'
    args = "value: str"
    source = header + _TEMPLATE.format(
        description=description or f"{clean} tool",
        name=clean,
        params=params,
        required=required,
        args=args,
    )

    issues = validate_plugin(source)
    if issues:
        raise ValueError("Scaffold failed validation: " + "; ".join(issues))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def load_plugin(path: str | Path, trusted: bool = False) -> Any:
    """Load a plugin file into a module and return it.

    ``trusted=False`` (default) loads through the constrained generated-skill
    runtime. ``trusted=True`` loads arbitrary Python and should only be used
    for plugins the user has explicitly enabled.
    """
    plugin_path = Path(path)
    if trusted:
        import importlib.util

        spec = importlib.util.spec_from_file_location(plugin_path.stem, str(plugin_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load plugin module: {plugin_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    from ..tools.forge_tools import load_generated_skill

    return load_generated_skill(plugin_path)
