"""Tool registry — the single place the agent looks to discover capabilities.

It loads the built-in tools, then automatically discovers community plugins
from the ``james.plugins`` package and any local ``plugins/`` folder, so new
superpowers appear without touching core code.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict, List

from .base import Tool, ToolResult
from .browser_tools import (
    browser_click,
    browser_close,
    browser_extract,
    browser_navigate,
    browser_screenshot,
    browser_type,
)
from .delegate_tool import delegate
from .document_tools import create_pdf, create_powerpoint, create_word_document
from .forge_tools import forget_skill, list_skills, save_skill
from .mcp_tools import discover_mcp_tools
from .file_tools import (
    delete_file,
    list_directory,
    read_file,
    search_files,
    write_file,
)
from .memory_tools import recall, remember
from .scheduler_tools import cancel_task, list_scheduled, schedule_task
from .system_tools import (
    clipboard,
    control_media,
    get_system_info,
    open_application,
    run_shell_command,
    take_screenshot,
)
from .web_tools import fetch_url, web_search
from ..config import settings

# Built-in registry.
ALL_TOOLS: List[Tool] = [
    read_file, write_file, list_directory, search_files, delete_file,
    create_word_document, create_powerpoint, create_pdf,
    web_search, fetch_url,
    browser_navigate, browser_click, browser_type, browser_extract,
    browser_screenshot, browser_close,
    remember, recall,
    schedule_task, list_scheduled, cancel_task,
    delegate, save_skill, list_skills, forget_skill,
    run_shell_command, open_application, take_screenshot,
    get_system_info, control_media, clipboard,
]

# Tools that mutate the system and should ask for confirmation when enabled.
DANGEROUS_TOOLS = {"run_shell_command", "delete_file", "open_application"}


def _register_module(registry: "ToolRegistry", module: ModuleType) -> None:
    for value in vars(module).values():
        if isinstance(value, Tool):
            registry.register(value)


def _discover_builtin_plugins(registry: "ToolRegistry") -> None:
    try:
        import james.plugins as pkg

        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            try:
                _register_module(registry, importlib.import_module(f"james.plugins.{mod.name}"))
            except Exception as exc:  # a bad plugin must not crash JAMES
                print(f"[plugins] failed to load james.plugins.{mod.name}: {exc}")
    except Exception:
        pass


def _discover_external_plugins(registry: "ToolRegistry") -> None:
    ext = Path(__file__).resolve().parents[2] / "plugins"
    if not ext.is_dir():
        return
    if str(ext) not in sys.path:
        sys.path.insert(0, str(ext))
    for path in ext.glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            module = importlib.machinery.SourceFileLoader(path.stem, str(path)).load_module()
            _register_module(registry, module)
        except Exception as exc:
            print(f"[plugins] failed to load {path.name}: {exc}")


class ToolRegistry:
    def __init__(self, tools: List[Tool] | None = None, discover_plugins: bool = True):
        self._tools: Dict[str, Tool] = {}
        for t in tools or ALL_TOOLS:
            self._tools[t.name] = t
        if discover_plugins:
            _discover_builtin_plugins(self)
            _discover_external_plugins(self)
            try:
                for t in discover_mcp_tools():
                    self._tools[t.name] = t
            except Exception as exc:
                print(f"[plugins] MCP discovery failed: {exc}")

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> List[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def execute(self, name: str, arguments: dict) -> ToolResult:
        t = self._tools.get(name)
        if not t:
            return ToolResult(ok=False, output=f"Unknown tool: {name}")

        mode = settings.assistant.mode
        # Permission tiers: in "standard" mode, block mutating tools entirely.
        if mode == "standard" and name in DANGEROUS_TOOLS:
            result = ToolResult(
                ok=False,
                output=f"Tool '{name}' is disabled in 'standard' mode. Set JAMES_MODE=full to enable.",
            )
            self._audit(name, arguments, result)
            return result

        # Dry-run: simulate dangerous actions instead of executing them.
        if settings.assistant.dry_run and name in DANGEROUS_TOOLS:
            result = ToolResult(ok=True, output=f"[DRY RUN] Would have executed '{name}' with {arguments}")
            self._audit(name, arguments, result)
            return result

        result = t.run(**arguments)
        self._audit(name, arguments, result)
        return result

    def _audit(self, name: str, arguments: dict, result: ToolResult) -> None:
        try:
            from datetime import datetime

            line = (
                f"{datetime.now().isoformat(timespec='seconds')} | "
                f"tool={name} ok={result.ok} args={arguments} -> {result.output[:200]}\n"
            )
            with open(settings.assistant.audit_log, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def __contains__(self, name: str) -> bool:
        return name in self._tools
