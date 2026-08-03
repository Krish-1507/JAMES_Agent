"""Scheduler tools exposed to the agent."""

from __future__ import annotations

from ..core.scheduler import parse_when, scheduler
from .base import ToolResult, tool


@tool(
    "schedule_task",
    "Schedule a reminder or a shell command to run later. Supports 'HH:MM' or 'in N minutes|hours|days'.",
    {
        "when": {"type": "string", "description": "When to run, e.g. '09:30' or 'in 30 minutes'."},
        "message": {
            "type": "string",
            "description": "Reminder text to show (omit for silent command).",
        },
        "command": {
            "type": "string",
            "description": "Optional shell command to execute at that time.",
        },
        "repeat": {"type": "string", "description": "Optional: 'daily' or 'hourly'."},
    },
    required=["when"],
)
def schedule_task(when: str, message: str = "", command: str = "", repeat: str = "") -> ToolResult:
    try:
        at = parse_when(when)
        job_id = scheduler.add(
            at, command=command or None, message=message or None, repeat=repeat or None
        )
        return ToolResult(ok=True, output=f"Scheduled (id={job_id}) for {at:%Y-%m-%d %H:%M}.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Schedule failed: {exc}")


@tool(
    "list_scheduled",
    "List all pending scheduled tasks/reminders.",
    {},
)
def list_scheduled() -> ToolResult:
    jobs = scheduler.list_jobs()
    if not jobs:
        return ToolResult(ok=True, output="No pending tasks.")
    return ToolResult(
        ok=True,
        output="\n".join(f"- {j.id}: {j.at} | msg={j.message} cmd={j.command}" for j in jobs),
    )


@tool(
    "cancel_task",
    "Cancel a scheduled task by its id (get the id from list_scheduled).",
    {"job_id": {"type": "string", "description": "Job id to cancel."}},
    required=["job_id"],
)
def cancel_task(job_id: str) -> ToolResult:
    ok = scheduler.cancel(job_id)
    return ToolResult(ok=ok, output="Cancelled." if ok else "Job not found.")
