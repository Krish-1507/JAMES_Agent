"""Delegation tool — spawn child agents to handle subtasks in isolation/parallel.

This is JAMES's answer to Hermes's subagent delegation, with a twist: a single
call can fan out *multiple* independent subtasks across threads and combine the
results, so one request can parallelize real work.
"""

from __future__ import annotations

import concurrent.futures

from .base import ToolResult, tool

_context = {"llm": None, "on_tool": None, "on_tool_start": None}


def configure_delegate(llm, on_tool=None, on_tool_start=None) -> None:
    """Called by the Assistant so the tool can build child agents.

    ``on_tool`` / ``on_tool_start`` are forwarded to every child agent so that
    tool calls made by delegated sub-agents stream into the same live canvas
    as the parent's.
    """
    _context["llm"] = llm
    _context["on_tool"] = on_tool
    _context["on_tool_start"] = on_tool_start


def _run_one(task: str) -> str:
    from ..core.agent import Agent
    from .registry import ALL_TOOLS, ToolRegistry

    # Child gets every tool EXCEPT delegate, to avoid infinite recursion.
    child_tools = [t for t in ALL_TOOLS if t.name != "delegate"]
    child = ToolRegistry(tools=child_tools, discover_plugins=False)
    agent = Agent(_context["llm"], child, max_iterations=8)
    # Stream the child's tool activity to the live canvas / logs.
    agent.on_tool = _context.get("on_tool")
    agent.on_tool_start = _context.get("on_tool_start")
    reply, _ = agent.run(task)
    return reply


@tool(
    "delegate",
    "Delegate a self-contained subtask to an isolated sub-agent and return its result. "
    "Accepts a single 'task' or a list of 'tasks' which are run in parallel and combined.",
    {
        "task": {"type": "string", "description": "A single subtask to delegate."},
        "tasks": {
            "type": "array",
            "description": "Optional list of independent subtasks to run in parallel.",
            "items": {"type": "string"},
        },
    },
)
def delegate(task: str = "", tasks: list[str] | None = None) -> ToolResult:
    llm = _context["llm"]
    if llm is None:
        return ToolResult(ok=False, output="Delegate is not configured (no LLM provider).")

    jobs = list(tasks) if tasks else []
    if task:
        jobs.append(task)
    if not jobs:
        return ToolResult(ok=False, output="Provide 'task' or 'tasks'.")

    if len(jobs) == 1:
        try:
            return ToolResult(ok=True, output=_run_one(jobs[0]))
        except Exception as exc:
            return ToolResult(ok=False, output=f"Delegation failed: {exc}")

    # Parallel fan-out across threads.
    results: list[str] = [""] * len(jobs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(jobs), 6)) as ex:
        fut_to_idx = {ex.submit(_run_one, j): i for i, j in enumerate(jobs)}
        for fut in concurrent.futures.as_completed(fut_to_idx):
            i = fut_to_idx[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:
                results[i] = f"(error: {exc})"

    combined = "\n\n".join(f"[Subtask {i + 1}] {r}" for i, r in enumerate(results))
    return ToolResult(ok=True, output=combined)
