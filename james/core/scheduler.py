"""Scheduler — reminders and delayed/recurring tasks that fire in the background."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..config import settings
from .command_policy import is_safe_command, parse_safe_command
from .isolation import run_isolated
from .workspace import workspace_root


def _notify(title: str, message: str) -> None:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=10)
    except Exception:
        print(f"[reminder] {title}: {message}")


@dataclass
class Job:
    id: str
    at: str  # ISO datetime
    command: str | None = None
    message: str | None = None
    repeat: str | None = None  # "daily" | "hourly" | None
    done: bool = False


def _validate_command(command: str) -> bool:
    return is_safe_command(command)


class Scheduler:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings.assistant.workspace_dir / "schedule.json")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- persistence ----
    def _load(self) -> list[Job]:
        if not self.path.exists():
            return []
        try:
            return [Job(**j) for j in json.loads(self.path.read_text(encoding="utf-8"))]
        except Exception:
            return []

    def _save(self, jobs: list[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps([asdict(j) for j in jobs], indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self.path)

    # ---- API ----
    def add(
        self,
        at: datetime,
        *,
        command: str | None = None,
        message: str | None = None,
        repeat: str | None = None,
    ) -> str:
        if command and not _validate_command(command):
            raise ValueError("Scheduled command is not in the read-only command allowlist.")
        if repeat not in (None, "daily", "hourly"):
            raise ValueError("Repeat must be 'daily', 'hourly', or empty.")
        with self._lock:
            jobs = self._load()
            job = Job(
                id=f"job{time.time_ns()}",
                at=at.isoformat(timespec="seconds"),
                command=command,
                message=message,
                repeat=repeat,
            )
            jobs.append(job)
            self._save(jobs)
        return job.id

    def list_jobs(self) -> list[Job]:
        return [j for j in self._load() if not j.done]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._load()
            new = [j for j in jobs if j.id != job_id]
            self._save(new)
        return len(new) != len(jobs)

    def _fire(self, job: Job) -> None:
        if job.message:
            _notify(f"{settings.assistant.name} reminder", job.message)
        if job.command:
            args, reason = parse_safe_command(job.command)
            if args is None:
                _notify("Scheduled task blocked", reason)
                return
            result = run_isolated(
                "command",
                {"args": args, "timeout": 120, "workspace": str(workspace_root())},
                timeout=125,
            )
            if not result.get("ok"):
                _notify("Scheduled task failed", str(result.get("output", "Unknown error")))

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            jobs = self._load()
            changed = False
            for job in jobs:
                if job.done:
                    continue
                try:
                    due = datetime.fromisoformat(job.at)
                except ValueError:
                    continue
                if now >= due:
                    self._fire(job)
                    if job.repeat == "daily":
                        job.at = (due + timedelta(days=1)).isoformat(timespec="seconds")
                        changed = True
                    elif job.repeat == "hourly":
                        job.at = (due + timedelta(hours=1)).isoformat(timespec="seconds")
                        changed = True
                    else:
                        job.done = True
                        changed = True
            if changed:
                self._save(jobs)
            self._stop.wait(10)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


scheduler = Scheduler()


def parse_when(when: str) -> datetime:
    """Parse 'HH:MM' or 'in N minutes|hours|days' into a datetime."""
    when = when.strip().lower()
    if when.startswith("in "):
        parts = when[3:].split()
        try:
            n = int(parts[0])
        except ValueError:
            n = 1
        unit = parts[1] if len(parts) > 1 else "minutes"
        delta = {
            "minute": timedelta(minutes=n),
            "minutes": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "hours": timedelta(hours=n),
            "day": timedelta(days=n),
            "days": timedelta(days=n),
        }.get(unit, timedelta(minutes=n))
        return datetime.now() + delta
    try:
        today = datetime.now().replace(microsecond=0)
        candidate = datetime.strptime(when, "%H:%M").replace(
            year=today.year, month=today.month, day=today.day
        )
        if candidate <= today:
            candidate += timedelta(days=1)
        return candidate
    except ValueError:
        return datetime.now() + timedelta(minutes=1)
