"""Tool registry — the single place the agent looks to discover capabilities.

It loads the built-in tools, then automatically discovers community plugins
from the ``james.plugins`` package and any local ``plugins/`` folder, so new
superpowers appear without touching core code.

Security boundary: :meth:`ToolRegistry.execute` is the *only* gated entry
point (permissions, mode tiers, dry-run, rate limit, and HMAC audit). The
agent loop always goes through it. Individual tool functions can also be
invoked directly in Python (e.g. for tests or from other plugins), which is
intentionally not gated here — treat that as trusted code. Untrusted input
must always arrive through ``execute``.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import pkgutil
import threading
import time
from pathlib import Path
from types import ModuleType

from ..config import settings
from ..core.secrets import load_or_create_secret
from .background_tools import (
    background_task,
    get_background_result,
    list_background_tasks,
    task_dependency_graph,
)
from .base import Tool, ToolResult, tool
from .browser_tools import (
    browser_click,
    browser_close,
    browser_extract,
    browser_health,
    browser_navigate,
    browser_screenshot,
    browser_type,
)
from .compute_tools import calculate
from .delegate_tool import delegate
from .desktop_tools import (
    click_at,
    computer_use,
    press_key,
    screenshot_save,
    type_text,
)
from .document_tools import create_pdf, create_powerpoint, create_word_document
from .file_manager_tools import list_file_manager_tasks, manage_files, stop_file_manager
from .file_tools import (
    copy_file,
    create_directory,
    delete_file,
    directory_tree,
    list_directory,
    move_file,
    read_file,
    rename_file,
    restore_last_deleted,
    search_files,
    unzip_archive,
    write_file,
)
from .forge_tools import (
    _GENERATED_SKILL_HEADER,
    forget_skill,
    list_skills,
    save_skill,
)
from .marketplace import install_plugin, list_plugins, publish_skill, search_plugins
from .mcp_tools import discover_mcp_tools
from .memory_tools import recall, remember
from .plugin_proxy import discover_plugin_tools
from .reading_tools import describe_image, extract_audio_text, read_document, read_pdf
from .research_tools import learn_skill, research
from .scheduler_tools import cancel_task, list_scheduled, schedule_task
from .system_tools import (
    clipboard,
    control_media,
    get_system_info,
    open_application,
    run_shell_command,
    take_screenshot,
    upload_image,
)
from .web_tools import fetch_url, web_search


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
ALL_TOOLS: list[Tool] = [
    read_file,
    write_file,
    list_directory,
    search_files,
    delete_file,
    read_pdf,
    read_document,
    extract_audio_text,
    describe_image,
    unzip_archive,
    calculate,
    create_word_document,
    create_powerpoint,
    create_pdf,
    web_search,
    fetch_url,
    browser_navigate,
    browser_click,
    browser_type,
    browser_extract,
    browser_screenshot,
    browser_close,
    browser_health,
    remember,
    recall,
    schedule_task,
    list_scheduled,
    cancel_task,
    delegate,
    save_skill,
    list_skills,
    forget_skill,
    run_shell_command,
    open_application,
    take_screenshot,
    upload_image,
    get_system_info,
    control_media,
    clipboard,
    computer_use,
    click_at,
    type_text,
    press_key,
    screenshot_save,
    create_directory,
    copy_file,
    move_file,
    rename_file,
    restore_last_deleted,
    directory_tree,
    research,
    learn_skill,
    background_task,
    list_background_tasks,
    get_background_result,
    manage_files,
    list_file_manager_tasks,
    stop_file_manager,
    help_command,
    task_dependency_graph,
    list_plugins,
    search_plugins,
    install_plugin,
    publish_skill,
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
    "forget_skill",
    "install_plugin",
    "publish_skill",
}


def is_dangerous_tool_call(name: str, arguments: dict | None = None) -> bool:
    if name == "schedule_task":
        return bool((arguments or {}).get("command"))
    return name in DANGEROUS_TOOLS


def _register_module(registry: ToolRegistry, module: ModuleType) -> None:
    for value in vars(module).values():
        if isinstance(value, Tool):
            registry.register(value)


def _discover_builtin_plugins(registry: ToolRegistry) -> None:
    try:
        import james.plugins as pkg

        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            try:
                _register_module(registry, importlib.import_module(f"james.plugins.{mod.name}"))
            except Exception as exc:  # a bad plugin must not crash JAMES
                print(f"[plugins] failed to load james.plugins.{mod.name}: {exc}")
    except Exception:  # nosec B110 - plugin discovery must not crash startup
        pass


def _discover_external_plugins(registry: ToolRegistry) -> None:
    ext = Path(__file__).resolve().parents[2] / "plugins"
    if not ext.is_dir():
        return
    for path in ext.glob("*.py"):
        if path.name.startswith("_"):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            if source.startswith(_GENERATED_SKILL_HEADER):
                for plugin_tool in discover_plugin_tools(path, trusted=False):
                    registry.register(plugin_tool)
            elif settings.assistant.external_plugins_enabled:
                for plugin_tool in discover_plugin_tools(path, trusted=True):
                    registry.register(plugin_tool)
            else:
                continue
        except Exception as exc:
            print(f"[plugins] failed to load {path.name}: {exc}")


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, discover_plugins: bool = True):
        self._tools: dict[str, Tool] = {}
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
        self._rate_lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def _check_rate_limit(self) -> bool:
        with self._rate_lock:
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
                output="Rate limit exceeded: too many tool calls. Please wait before trying again.",
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
            result = ToolResult(
                ok=True, output=f"[DRY RUN] Would have executed '{name}' with {arguments}"
            )
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
        except Exception:  # nosec B110 - a write failure must not break the tool call
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
