"""Tests for Phase 3: the plugin SDK.

Covers the manifest schema, scaffolding, validation, loading, marketplace
metadata flow, and the public authoring surface (imports from ``james.sdk``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from james.sdk import (
    GENERATED_HEADER,
    PluginManifest,
    create_plugin,
    format_manifest,
    load_plugin,
    parse_manifest,
    tool,
    validate_plugin,
)
from james.sdk.manifest import validate_manifest as _validate_manifest


# --- manifest schema -------------------------------------------------------
def test_manifest_roundtrips_through_format_and_parse() -> None:
    manifest = PluginManifest(
        name="double_number",
        version="1.2.0",
        author="Alice",
        description="Doubles a number.",
        tags=["math", "numbers"],
    )
    block = format_manifest(manifest)
    assert block.startswith("# manifest-name: double_number")
    assert "# manifest-tags: math,numbers" in block

    parsed = parse_manifest(GENERATED_HEADER + block)
    assert parsed is not None
    assert parsed.name == "double_number"
    assert parsed.version == "1.2.0"
    assert parsed.author == "Alice"
    assert parsed.tags == ["math", "numbers"]


def test_manifest_validation_rejects_bad_schema() -> None:
    assert _validate_manifest(PluginManifest(name="good_name")) == []
    bad_name = _validate_manifest(PluginManifest(name="Bad Name"))
    assert any("name" in issue for issue in bad_name)
    bad_version = _validate_manifest(PluginManifest(name="x", version="latest"))
    assert any("version" in issue for issue in bad_version)


def test_parse_manifest_returns_none_without_block() -> None:
    assert parse_manifest("# just a comment\nfrom james.sdk import tool") is None


# --- scaffolding -----------------------------------------------------------
def test_create_plugin_writes_valid_manifest_skill(plugin_dir: Path) -> None:
    path = create_plugin(
        "hello world",
        description="Say hello.",
        tags=["greetings"],
        directory=plugin_dir,
    )
    assert path.name == "hello_world.py"
    assert path.exists()
    source = path.read_text(encoding="utf-8")
    assert source.startswith(GENERATED_HEADER)
    assert validate_plugin(source) == []


def test_create_plugin_refuses_overwrite(plugin_dir: Path) -> None:
    create_plugin("dup", directory=plugin_dir)
    with pytest.raises(FileExistsError):
        create_plugin("dup", directory=plugin_dir)
    create_plugin("dup", directory=plugin_dir, overwrite=True)


# --- loading ---------------------------------------------------------------
def test_load_plugin_returns_registered_tool(plugin_dir: Path) -> None:
    path = create_plugin("greeter", description="Greet someone.", directory=plugin_dir)
    module = load_plugin(path)
    found = [
        value.name for value in vars(module).values() if type(value).__name__ == "FunctionTool"
    ]
    assert found == ["greeter"]


# --- validation ------------------------------------------------------------
def test_validate_plugin_rejects_missing_header() -> None:
    issues = validate_plugin(
        "from james.sdk import tool\n@tool('x','x',{},required=[])\ndef x():\n    pass\n"
    )
    assert any("header" in issue for issue in issues)


def test_validate_plugin_rejects_dangerous_code() -> None:
    source = (
        GENERATED_HEADER
        + "from james.sdk import tool, ToolResult\n"
        + "import os\n"
        + "@tool('evil','evil',{},required=[])\n"
        + "def evil():\n"
        + "    return ToolResult(ok=True, output=str(len(os.listdir('.'))))\n"
    )
    issues = validate_plugin(source)
    assert any("import os" in issue for issue in issues)


def test_validate_plugin_rejects_invalid_manifest(plugin_dir: Path) -> None:
    source = (
        GENERATED_HEADER
        + "# manifest-name: Bad Name\n"
        + "# manifest-version: nope\n"
        + "from james.sdk import tool, ToolResult\n"
        + "@tool('ok','ok',{},required=[])\n"
        + "def ok():\n"
        + "    return ToolResult(ok=True, output='fine')\n"
    )
    issues = validate_plugin(source)
    assert any("name" in issue for issue in issues)
    assert any("version" in issue for issue in issues)


# --- public surface --------------------------------------------------------
def test_sdk_re_exports_authoring_surface() -> None:
    from james.sdk import FunctionTool, ToolResult
    from james.sdk import tool as sdk_tool

    assert sdk_tool is tool
    assert FunctionTool.__name__ == "FunctionTool"
    assert ToolResult(ok=True, output="x").output == "x"


def test_sdk_skill_loads_through_constrained_runtime(plugin_dir: Path) -> None:
    path = create_plugin("calc", description="Add two numbers.", directory=plugin_dir)
    module = load_plugin(path)
    value_tool = next(v for v in vars(module).values() if type(v).__name__ == "FunctionTool")
    result = value_tool.run(value="hello")
    assert result.ok


# --- marketplace metadata flow ---------------------------------------------
def test_marketplace_bundle_uses_manifest_metadata(
    plugin_dir: Path, marketplace_file: Path
) -> None:
    create_plugin(
        "meta",
        description="From manifest.",
        author="Bob",
        version="2.3.4",
        tags=["x"],
        directory=plugin_dir,
    )
    from james.tools.marketplace import publish_skill

    result = publish_skill.run(name="meta")
    assert result.ok, result.output
    catalog = __import__("json").loads(marketplace_file.read_text(encoding="utf-8"))
    entry = next(p for p in catalog if p["name"] == "meta")
    assert entry["author"] == "Bob"
    assert entry["version"] == "2.3.4"
    assert "x" in entry["tags"]
