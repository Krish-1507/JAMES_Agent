"""Autonomous File Explorer Manager.

JAMES can take full, 100% agentic control of a part of the filesystem and run the
job in the background: explore the tree, organise files by type or date, remove or
quarantine duplicates, tidy names, and report back — without interrupting the user.

A persistent daemon mode (``AUTO_FILE_MANAGER``) keeps the user's main folders
(Desktop, Documents, Downloads, ...) tidy on a fixed interval, so the file explorer
manages itself.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from ..config import settings
from ..core.workspace import resolve_workspace_path, workspace_root
from ..llm.base import LLMProvider
from .base import ToolResult, tool

# A dedicated, fully autonomous system prompt for the file-manager sub-agent. It is
# deliberately 100% agentic: it must finish the whole job on its own and never ask
# the user anything (the parent assistant may, but the background manager must not).
_FILE_MANAGER_PROMPT = """You are the autonomous File Explorer Manager running inside JAMES. You have full \
control of the filesystem within the assigned scope and you are 100% agentic: complete the entire job on your \
own. Never ask the user anything. Never stop early.

Your mission for the scope below:
- Explore the full directory tree with directory_tree and list_directory.
- Organise files into clear folders (by type, project, or date) so the folder is tidy and predictable.
- Move loose, misnamed, or orphaned files into a sensible structure; fix messy names.
- Detect duplicates and consolidate them (keep one copy, move extras into a "Duplicates" folder rather than deleting).
- Prefer moving into a "Review" or "Unsorted" folder over deleting; only delete when it is clearly safe junk (e.g. empty temp files).
- If you discover you lack a capability, use research or learn_skill to acquire it, then continue.
- When the job is genuinely done, produce a short report: what you organised, how many files moved, duplicates found, and the final top-level structure.

Scope: {scope}
Goal: {goal}

Begin now and keep working until the scope is fully organised.
"""


def _resolve_scope(scope: str) -> str:
    """Resolve a scope while enforcing the configured workspace capability."""
    s = (scope or "").strip()
    if not s or s.lower() == "workspace":
        return str(workspace_root())
    return str(resolve_workspace_path(s))


class FileManager:
    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._llm: LLMProvider | None = None
        self._file = settings.assistant.workspace_dir / "file_manager_tasks.jsonl"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self._daemon: threading.Thread | None = None
        self._stop = threading.Event()

    def configure(self, llm: LLMProvider) -> None:
        self._llm = llm

    # ---- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not self._file.exists():
            return
        for line in self._file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                if t.get("status") == "running":
                    t["status"] = "interrupted"
                self._tasks[t["id"]] = t
            except json.JSONDecodeError:
                continue

    def _save(self, task: dict) -> None:
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")

    # ---- submission --------------------------------------------------------
    def submit(self, scope: str, goal: str = "") -> str:
        if self._llm is None:
            raise RuntimeError("File manager not configured (no LLM provider).")
        from . import file_tools, forge_tools, research_tools
        from .registry import ToolRegistry

        child_tools = [
            file_tools.read_file, file_tools.write_file, file_tools.list_directory,
            file_tools.search_files, file_tools.delete_file, file_tools.create_directory,
            file_tools.copy_file, file_tools.move_file, file_tools.rename_file, file_tools.directory_tree,
            research_tools.research, research_tools.learn_skill,
            forge_tools.save_skill, forge_tools.list_skills, forge_tools.forget_skill,
        ]
        child = ToolRegistry(tools=child_tools, discover_plugins=False)

        from ..core.agent import Agent

        resolved = _resolve_scope(scope)
        prompt = _FILE_MANAGER_PROMPT.format(scope=resolved, goal=goal or "Organise and tidy this location completely.")
        # The background manager respects the global confirmation setting.
        agent = Agent(self._llm, child, max_iterations=50, confirm_dangerous=settings.assistant.confirm_dangerous_actions, nudge=False, system_prompt=prompt)

        tid = uuid.uuid4().hex[:8]
        record = {
            "id": tid,
            "scope": resolved,
            "goal": goal,
            "status": "running",
            "submitted": time.time(),
            "result": "",
        }
        with self._lock:
            self._tasks[tid] = record
        self._save(record)

        def _run() -> None:
            try:
                reply, _ = agent.run(
                    f"Take full control of {resolved} and organise it completely. Goal: {goal or 'tidy everything'}."
                )
                record["result"] = reply
                record["status"] = "done"
            except Exception as exc:
                record["result"] = f"File manager task failed: {exc}"
                record["status"] = "error"
            with self._lock:
                self._save(record)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return tid

    def get(self, tid: str) -> dict | None:
        return self._tasks.get(tid)

    def stop(self, tid: str) -> bool:
        t = self._tasks.get(tid)
        if not t:
            return False
        t["status"] = "cancelled"
        with self._lock:
            self._save(t)
        return True

    def list_tasks(self) -> list[dict]:
        return [
            {"id": t["id"], "scope": t.get("scope", ""), "status": t["status"]}
            for t in self._tasks.values()
        ]

    # ---- daemon ------------------------------------------------------------
    def start_daemon(self, interval: int, scopes: list[str]) -> None:
        if self._daemon and self._daemon.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            first = True
            while not self._stop.is_set():
                if not first and self._stop.wait(interval):
                    break
                first = False
                if self._stop.is_set():
                    break
                if self._llm is None:
                    continue
                for scope in scopes:
                    if self._stop.is_set():
                        break
                    try:
                        self.submit(scope, "Keep this location organised and tidy.")
                    except Exception:
                        continue

        self._daemon = threading.Thread(target=_loop, daemon=True)
        self._daemon.start()

    def stop_daemon(self) -> None:
        self._stop.set()


_manager = FileManager()


def configure_file_manager(llm: LLMProvider) -> None:
    _manager.configure(llm)


def start_file_manager_daemon() -> None:
    _manager.start_daemon(
        settings.assistant.file_manager_interval,
        settings.assistant.file_manager_scopes,
    )


def stop_file_manager_daemon() -> None:
    _manager.stop_daemon()


@tool(
    "manage_files",
    "Take 100% agentic control of a directory (the file explorer) and organise it in the background. "
    "JAMES explores the tree, sorts files, removes duplicates, tidies names and reports back without "
    "interrupting you. Scope can be 'desktop', 'documents', 'downloads', 'workspace', 'home', 'whole', or a path.",
    {
        "scope": {
            "type": "string",
            "description": "What to manage: desktop|documents|downloads|workspace|home|whole, or an absolute path.",
        },
        "goal": {
            "type": "string",
            "description": "Optional instruction, e.g. 'sort by project and date'. Defaults to a full tidy.",
        },
    },
    required=["scope"],
)
def manage_files(scope: str, goal: str = "") -> ToolResult:
    try:
        tid = _manager.submit(scope, goal)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Could not start file manager: {exc}")
    return ToolResult(
        ok=True,
        output=f"JAMES is now managing {_resolve_scope(scope)} in the background (task {tid}). "
        f"Check with list_file_manager_tasks; results appear when done.",
    )


@tool(
    "list_file_manager_tasks",
    "List all autonomous file-manager tasks and their current status.",
    {},
)
def list_file_manager_tasks() -> ToolResult:
    tasks = _manager.list_tasks()
    if not tasks:
        return ToolResult(ok=True, output="No file-manager tasks.")
    return ToolResult(
        ok=True,
        output="\n".join(f"[{t['status']}] {t['id']}: {t['scope']}" for t in tasks),
    )


@tool(
    "stop_file_manager",
    "Stop an autonomous file-manager task by its id.",
    {"id": {"type": "string", "description": "Task id returned by manage_files."}},
    required=["id"],
)
def stop_file_manager(id: str) -> ToolResult:
    if _manager.stop(id):
        return ToolResult(ok=True, output=f"Stopped file-manager task {id}.")
    return ToolResult(ok=False, output=f"No such file-manager task: {id}")
