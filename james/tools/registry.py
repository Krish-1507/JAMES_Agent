"""Tool registry — the single place the agent looks to discover capabilities.

It loads the built-in tools, then automatically discovers community plugins
from the ``james.plugins`` package and any local ``plugins/`` folder, so new
superpowers appear without touching core code.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import pkgutil
import time
from pathlib import Path
from types import ModuleType
from typing import Dict, List

from ..core.secrets import load_or_create_secret
from .base import Tool, ToolResult, tool
from .browser_tools import (
    browser_click,
    browser_close,
    browser_extract,
    browser_navigate,
    browser_screenshot,
    browser_type,
)
from .desktop_tools import (
    click_at,
    computer_use,
    press_key,
    screenshot_save,
    type_text,
)
from .research_tools import learn_skill, research
from .background_tools import (
    background_task,
    get_background_result,
    list_background_tasks,
    task_dependency_graph,
)
from .delegate_tool import delegate
from .document_tools import create_pdf, create_powerpoint, create_word_document
from .file_manager_tools import list_file_manager_tasks, manage_files, stop_file_manager
from .forge_tools import _GENERATED_SKILL_HEADER, forget_skill, list_skills, load_generated_skill, save_skill
from .mcp_tools import discover_mcp_tools
from .marketplace import install_plugin, list_plugins, remove_plugin, search_plugins
from .file_tools import (
    create_directory,
    copy_file,
    delete_file,
    directory_tree,
    list_directory,
    move_file,
    read_file,
    rename_file,
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


@tool(
    "help",
    "List all available JAMES capabilities with a short description of each.",
    {},
)
def help_command() -> ToolResult:
    lines = []
    for t in ALL_TOOLS:
        if settings.assistant.mode == "standard" and t.name in DANGEROUS_TOOLS:
            continue
        lines.append(f"- {t.name}: {t.description[:100]}")
    return ToolResult(ok=True, output="Available tools:\n" + "\n".join(lines))

def _audit_hmac_key() -> bytes:
    path = settings.assistant.workspace_dir / ".james_audit_hmac.key"
    return load_or_create_secret("JAMES_AUDIT_HMAC_KEY", path)

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
    computer_use, click_at, type_text, press_key, screenshot_save,
    create_directory, copy_file, move_file, rename_file, directory_tree,
    research, learn_skill,
    background_task, list_background_tasks, get_background_result,
    manage_files, list_file_manager_tasks, stop_file_manager,
    help_command, task_dependency_graph,
]

# Tools that mutate the system and should ask for confirmation when enabled.
DANGEROUS_TOOLS = {
    "run_shell_command",
    "delete_file",
    "open_application",
    "computer_use",
    "click_at",
    "type_text",
    "press_key",
    "move_file",
    "rename_file",
    "manage_files",
    "save_skill",
}


def is_dangerous_tool_call(name: str, arguments: dict | None = None) -> bool:
    if name == "schedule_task":
        return bool((arguments or {}).get("command"))
    return name in DANGEROUS_TOOLS


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
    for path in ext.glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            if source.startswith(_GENERATED_SKILL_HEADER):
                module = load_generated_skill(path)
            elif settings.assistant.external_plugins_enabled:
                spec = importlib.util.spec_from_file_location(path.stem, str(path))
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            else:
                continue
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
        self._call_times: list[float] = []
        self._max_calls_per_minute = 60

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> List[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> List[str]:
        return list(self._tools.keys())

    def _check_rate_limit(self) -> bool:
        now = time.time()
        self._call_times = [t for t in self._call_times if now - t < 60]
        if len(self._call_times) >= self._max_calls_per_minute:
            return False
        self._call_times.append(now)
        return True

    def execute(self, name: str, arguments: dict) -> ToolResult:
        t = self._tools.get(name)
        if not t:
            return ToolResult(ok=False, output=f"Unknown tool: {name}")

        if not self._check_rate_limit():
            return ToolResult(
                ok=False,
                output=f"Rate limit exceeded: too many tool calls. Please wait before trying again.",
            )

        # Per-tool permission granularity.
        if settings.assistant.allowed_tools and name not in settings.assistant.allowed_tools:
            return ToolResult(
                ok=False,
                output=f"Tool '{name}' is not in the allowed tools list.",
            )
        if name in settings.assistant.denied_tools:
            return ToolResult(
                ok=False,
                output=f"Tool '{name}' is explicitly denied.",
            )

        mode = settings.assistant.mode
        # Permission tiers: in "standard" mode, block mutating tools entirely.
        if mode == "standard" and is_dangerous_tool_call(name, arguments):
            result = ToolResult(
                ok=False,
                output=f"Tool '{name}' is disabled in 'standard' mode. Set JAMES_MODE=full to enable.",
            )
            self._audit(name, arguments, result)
            return result

        # Dry-run: simulate dangerous actions instead of executing them.
        if settings.assistant.dry_run and is_dangerous_tool_call(name, arguments):
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
            entry = line.encode("utf-8")
            digest = hmac.new(
                _audit_hmac_key(),
                entry,
                hashlib.sha256,
            ).hexdigest()
            signed = f"{digest} | {line}"
            with open(settings.assistant.audit_log, "a", encoding="utf-8") as f:
                f.write(signed)
        except Exception:
            pass

    @staticmethod
    def verify_audit_integrity(log_path: str) -> bool:
        try:
            path = Path(log_path)
            if not path.exists():
                return True
            for line in path.read_text(encoding="utf-8").splitlines():
                if " | " not in line:
                    continue
                digest_str, _, rest = line.partition(" | ")
                entry = (rest + "\n").encode("utf-8")
                expected = hmac.new(
                    _audit_hmac_key(),
                    entry,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(digest_str, expected):
                    return False
            return True
        except Exception:
            return False

    def __contains__(self, name: str) -> bool:
        return name in self._tools
