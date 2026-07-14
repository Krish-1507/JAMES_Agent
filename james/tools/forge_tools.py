"""Skill Forge — JAMES teaches itself.

After a successful multi-step task the model can call ``save_skill`` with a
fully-formed plugin (a ``@tool``-decorated function). JAMES validates it, drops
it into ``plugins/`` (auto-discovered on every future startup) AND hot-registers
it into the live session so it works immediately. That's a stricter, more useful
self-improvement loop than generic "skills": the result is a directly executable,
typed, native tool — no re-prompting, no re-implementation.
"""
from __future__ import annotations

import importlib.util
import os
import py_compile
import re
from pathlib import Path

from .base import Tool, ToolResult, tool

_registry = {"reg": None}
_skill_tools: dict = {}  # file stem -> list of registered tool names
_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


def configure_forge(registry) -> None:
    _registry["reg"] = registry


def _safe_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")
    return n or "skill"


def _find_tools(module) -> list:
    from .base import Tool

    return [v for v in vars(module).values() if isinstance(v, Tool)]


@tool(
    "save_skill",
    "Persist a reusable capability as a native JAMES plugin. Provide a working @tool-decorated "
    "Python function as 'code'; JAMES validates, saves it to plugins/, and loads it immediately.",
    {
        "name": {"type": "string", "description": "Skill/tool name (letters, digits, underscores)."},
        "description": {"type": "string", "description": "What the skill does (becomes the tool description)."},
        "code": {
            "type": "string",
            "description": "Full Python source defining a @tool-decorated function (import tool from james.tools.base).",
        },
    },
    required=["name", "code"],
)
def save_skill(name: str, code: str, description: str = "") -> ToolResult:
    _PLUGINS_DIR.mkdir(exist_ok=True)
    fname = _safe_name(name)
    path = _PLUGINS_DIR / f"{fname}.py"

    # Always anchor the code with the required import so users can omit it.
    if "from james.tools.base import" not in code and "import tool" not in code:
        code = "from james.tools.base import tool\n" + code

    # Validate syntax first.
    try:
        tmp = path.with_suffix(".tmp.py")
        tmp.write_text(code, encoding="utf-8")
        py_compile.compile(str(tmp), doraise=True)
        tmp.unlink()
    except py_compile.PyCompileError as exc:
        return ToolResult(ok=False, output=f"Skill code did not compile:\n{exc}")

    path.write_text(code, encoding="utf-8")

    # Import and hot-register.
    try:
        spec = importlib.util.spec_from_file_location(f"james_skill_{fname}", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tools = _find_tools(module)
        if not tools:
            return ToolResult(ok=False, output="No @tool function found in the skill code.")
        reg = _registry["reg"]
        loaded = []
        for t in tools:
            if description and t is tools[0]:
                t.description = description
            if reg is not None:
                reg.register(t)
            loaded.append(t.name)
        _skill_tools[fname] = loaded
        return ToolResult(ok=True, output=f"Saved & loaded skill(s): {loaded} at {path}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Skill loaded with errors:\n{exc}")


@tool(
    "list_skills",
    "List all saved/learned skills currently available from the plugins folder.",
    {},
)
def list_skills() -> ToolResult:
    if not _PLUGINS_DIR.is_dir():
        return ToolResult(ok=True, output="No skills yet.")
    names = []
    for p in sorted(_PLUGINS_DIR.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"sk_{p.stem}", str(p))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for t in _find_tools(module):
                names.append(f"{t.name}: {t.description[:60]}")
        except Exception:
            continue
    return ToolResult(ok=True, output="\n".join(names) or "No skills yet.")


@tool(
    "forget_skill",
    "Delete a previously saved skill by its file/tool name.",
    {"name": {"type": "string", "description": "Skill name (filename without .py)."}},
    required=["name"],
)
def forget_skill(name: str) -> ToolResult:
    fname = _safe_name(name)
    path = _PLUGINS_DIR / f"{fname}.py"
    if not path.exists():
        return ToolResult(ok=False, output=f"No such skill: {fname}")
    # Remove from the live registry by recorded tool names.
    reg = _registry["reg"]
    for n in _skill_tools.get(fname, []):
        if reg is not None:
            reg._tools.pop(n, None)
    _skill_tools.pop(fname, None)
    path.unlink()
    return ToolResult(ok=True, output=f"Forgot skill '{fname}'.")
