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

_DANGEROUS_IMPORTS = {
    "os", "subprocess", "sys", "shutil", "socket", "threading",
    "multiprocessing", "importlib", "exec", "eval", "compile",
    "open", "pathlib", "tempfile", "glob", "fnmatch",
}


def _scan_for_dangerous_imports(code: str) -> list[str]:
    dangerous = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            for imp in _DANGEROUS_IMPORTS:
                if imp in stripped.split()[1].split(".")[0].split(",")[0]:
                    dangerous.append(stripped)
                    break
    return dangerous
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


_RESTRICTED_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "hasattr": hasattr,
    "getattr": getattr,
    "setattr": setattr,
    "delattr": delattr,
    "print": print,
    "super": super,
    "property": property,
    "classmethod": classmethod,
    "staticmethod": staticmethod,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "ImportError": ImportError,
    "RuntimeError": RuntimeError,
}


def _persist_skill(name: str, code: str, description: str = "") -> ToolResult:
    """Validate, save to plugins/, and hot-load a @tool plugin. Shared by save_skill + auto-forge."""
    _PLUGINS_DIR.mkdir(exist_ok=True)
    fname = _safe_name(name)
    path = _PLUGINS_DIR / f"{fname}.py"

    # Always anchor the code with the required import so users/models can omit it.
    if "from james.tools.base import" not in code and "import tool" not in code:
        code = "from james.tools.base import tool\n" + code

    try:
        tmp = path.with_suffix(".tmp.py")
        tmp.write_text(code, encoding="utf-8")
        py_compile.compile(str(tmp), doraise=True)
        tmp.unlink()
    except py_compile.PyCompileError as exc:
        return ToolResult(ok=False, output=f"Skill code did not compile:\n{exc}")

    dangerous = _scan_for_dangerous_imports(code)
    if dangerous:
        return ToolResult(
            ok=False,
            output="Skill code contains disallowed imports: " + ", ".join(dangerous),
        )

    path.write_text(code, encoding="utf-8")

    try:
        spec = importlib.util.spec_from_file_location(f"james_skill_{fname}", str(path))
        module = importlib.util.module_from_spec(spec)
        module.__builtins__ = _RESTRICTED_BUILTINS
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
    return _persist_skill(name, code, description)


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


# ---------------------------------------------------------------------------
# Self-improving Skill Forge: turn a completed multi-tool task into a native tool
# ---------------------------------------------------------------------------

def _extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    if "@tool" in text or "def " in text:
        return text.strip()
    return ""


def _derive_name(user_msg: str) -> str:
    words = re.findall(r"[a-z0-9]+", user_msg.lower())
    return "_".join(words[:4]) or "skill"


def auto_forge_from_history(llm, history: list, max_steps: int = 8) -> ToolResult:
    """Generate a native @tool plugin from the last multi-tool task and persist it.

    Unlike generic 'skills' (free-text recipes), the output is a directly
    executable, typed JAMES tool — no re-implementation and no re-prompting next
    time the capability is needed.
    """
    user_msg = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
    steps = []
    for m in history:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                steps.append(f"call {fn.get('name')}({fn.get('arguments')})")
        elif m.get("role") == "tool":
            steps.append(f"result: {str(m.get('content', ''))[:240]}")
    if len(steps) < 2:
        return ToolResult(ok=False, output="Not enough tool activity to forge a skill.")

    transcript = "\n".join(steps[-max_steps * 2:])
    prompt = (
        "You are JAMES's self-improvement engine. A user asked:\n"
        f'"""{user_msg}"""\n'
        "JAMES solved it by chaining these tool calls:\n"
        f"{transcript}\n\n"
        "Write ONE reusable JAMES tool — a @tool-decorated Python function with clear JSON-schema "
        "parameters — that encapsulates this workflow so it can be invoked directly next time. "
        "Import only from james.tools.base (the `tool` decorator). Keep it safe, typed and "
        "self-contained. Return ONLY the Python code, no explanation, no markdown outside the code."
    )
    try:
        resp = llm.chat([{"role": "user", "content": prompt}])
        code = _extract_code(resp.content)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Auto-forge generation failed: {exc}")
    if not code:
        return ToolResult(ok=False, output="Auto-forge produced no usable code.")
    return _persist_skill(_derive_name(user_msg), code, description=f"Auto-generated from: {user_msg[:80]}")
