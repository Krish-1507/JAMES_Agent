"""Background task execution.

Lets JAMES run long or independent work in the background and keep operating. A
background task spins up an isolated sub-agent (in a daemon thread) that works on
the request autonomously; the caller gets a task id immediately and can check
progress later. Tasks are persisted so they survive restarts.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from ..config import settings
from ..llm.base import LLMProvider


class BackgroundManager:
    def __init__(self):
        self._tasks: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._llm: Optional[LLMProvider] = None
        self._file = settings.assistant.workspace_dir / "background_tasks.jsonl"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def configure(self, llm: LLMProvider) -> None:
        self._llm = llm

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

    def submit(self, task: str) -> str:
        if self._llm is None:
            raise RuntimeError("Background manager not configured (no LLM provider).")
        # Exclude the background tools themselves to avoid recursion.
        from ..tools.registry import ToolRegistry

        child = ToolRegistry(
            tools=[t for t in self._all_tools() if t.name not in _EXCLUDE],
            discover_plugins=False,
        )
        from ..core.agent import Agent

        agent = Agent(self._llm, child, max_iterations=20)
        tid = uuid.uuid4().hex[:8]
        record = {
            "id": tid,
            "task": task,
            "status": "running",
            "submitted": time.time(),
            "result": "",
        }
        with self._lock:
            self._tasks[tid] = record
        self._save(record)

        def _run():
            try:
                reply, _ = agent.run(task)
                record["result"] = reply
                record["status"] = "done"
            except Exception as exc:
                record["result"] = f"Background task failed: {exc}"
                record["status"] = "error"
            with self._lock:
                self._save(record)
            try:
                from plyer import notification

                if record["status"] == "done":
                    notification.notify(
                        title="Background task complete",
                        message=record["task"][:100],
                        timeout=5,
                    )
                else:
                    notification.notify(
                        title="Background task failed",
                        message=record["result"][:100],
                        timeout=5,
                    )
            except Exception:
                pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return tid

    def get(self, tid: str) -> Optional[dict]:
        return self._tasks.get(tid)

    def list_tasks(self) -> List[dict]:
        return [
            {"id": t["id"], "task": t["task"], "status": t["status"]}
            for t in self._tasks.values()
        ]

    def _all_tools(self):
        from .registry import ALL_TOOLS

        return list(ALL_TOOLS)


_EXCLUDE = {"background_task", "list_background_tasks", "get_background_result"}


_manager = BackgroundManager()


def configure_background(llm: LLMProvider) -> None:
    _manager.configure(llm)


from .base import Tool, ToolResult, tool


@tool(
    "background_task",
    "Run a task autonomously in the background and keep working. Returns a task id immediately; "
    "check it later with get_background_result. Good for long or independent jobs.",
    {"task": {"type": "string", "description": "The instruction to run in the background."}},
    required=["task"],
)
def background_task(task: str) -> ToolResult:
    try:
        tid = _manager.submit(task)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Could not start background task: {exc}")
    return ToolResult(ok=True, output=f"Started background task {tid}. Check with get_background_result({tid}).")


@tool(
    "list_background_tasks",
    "List all background tasks and their current status.",
    {},
)
def list_background_tasks() -> ToolResult:
    tasks = _manager.list_tasks()
    if not tasks:
        return ToolResult(ok=True, output="No background tasks.")
    return ToolResult(ok=True, output="\n".join(f"[{t['status']}] {t['id']}: {t['task']}" for t in tasks))


@tool(
    "get_background_result",
    "Get the result (or current status) of a background task by its id.",
    {"id": {"type": "string", "description": "Task id returned by background_task."}},
    required=["id"],
)
def get_background_result(id: str) -> ToolResult:
    t = _manager.get(id)
    if not t:
        return ToolResult(ok=False, output=f"No such background task: {id}")
    if t["status"] in ("running",):
        return ToolResult(ok=True, output=f"[{t['status']}] still working on: {t['task']}")
    return ToolResult(ok=True, output=f"[{t['status']}] {t['result']}")


@tool(
    "task_dependency_graph",
    "Generate a visual dependency graph of tool calls from the current session. "
    "Returns a DOT-format graph string that can be rendered by Graphviz or online tools.",
    {"task_id": {"type": "string", "description": "Optional background task id to visualize."}},
)
def task_dependency_graph(task_id: str = "") -> ToolResult:
    try:
        history_path = settings.assistant.workspace_dir / "conversation_history.jsonl"
        if not history_path.exists():
            return ToolResult(ok=False, output="No conversation history found.")
        lines = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        fn = tc.get("function", {})
                        lines.append(f'  "{fn.get("name", "?")}" [label="{fn.get("name", "?")}"];')
            except json.JSONDecodeError:
                continue
        if not lines:
            return ToolResult(ok=True, output="No tool calls found in history.")
        dot = "digraph ToolCalls {\n  rankdir=LR;\n  node [shape=box];\n" + "\n".join(lines) + "\n}"
        return ToolResult(ok=True, output=dot)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Graph generation failed: {exc}")
