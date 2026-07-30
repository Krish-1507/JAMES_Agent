"""Scheduler — reminders and delayed/recurring tasks that fire in the background."""
from __future__ import annotations

import re
import json
import shlex
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ..config import settings

_SHELL_METACHAR_RE = re.compile(r'[;&|`$(){}[\]<>!#]')


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
    command: Optional[str] = None
    message: Optional[str] = None
    repeat: Optional[str] = None  # "daily" | "hourly" | None
    done: bool = False


class Scheduler:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (settings.assistant.workspace_dir / "schedule.json")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- persistence ----
    def _load(self) -> List[Job]:
        if not self.path.exists():
            return []
        try:
            return [Job(**j) for j in json.loads(self.path.read_text(encoding="utf-8"))]
        except Exception:
            return []

    def _save(self, jobs: List[Job]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(j) for j in jobs], indent=2), encoding="utf-8")

    # ---- API ----
    def add(self, at: datetime, *, command: str = None, message: str = None, repeat: str = None) -> str:
        jobs = self._load()
        job = Job(id=f"job{int(time.time()*1000)}", at=at.isoformat(timespec="seconds"),
                  command=command, message=message, repeat=repeat)
        jobs.append(job)
        self._save(jobs)
        return job.id

    def list_jobs(self) -> List[Job]:
        return [j for j in self._load() if not j.done]

    def cancel(self, job_id: str) -> bool:
        jobs = self._load()
        new = [j for j in jobs if j.id != job_id]
        self._save(new)
        return len(new) != len(jobs)

def _validate_command(command: str) -> bool:
    if _SHELL_METACHAR_RE.search(command):
        return False
    return True


def _fire(self, job: Job) -> None:
    if job.message:
        _notify(f"{settings.assistant.name} reminder", job.message)
    if job.command:
        if not _validate_command(job.command):
            _notify("Scheduled task blocked", f"Command contains unsafe characters: {job.command[:80]}")
            return
        try:
            args = shlex.split(job.command)
            subprocess.run(args, shell=False, capture_output=True, text=True, timeout=120)
        except Exception as exc:
            _notify("Scheduled task failed", str(exc))

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
        delta = {"minute": timedelta(minutes=n), "minutes": timedelta(minutes=n),
                 "hour": timedelta(hours=n), "hours": timedelta(hours=n),
                 "day": timedelta(days=n), "days": timedelta(days=n)}.get(unit, timedelta(minutes=n))
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
