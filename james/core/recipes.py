"""Recipes — named multi-step automations the agent can compose and run.

A recipe is a list of tool invocations with a trigger ("daily 09:00",
"hourly", "every 30 minutes", or an absolute ISO datetime). The engine
persists them to ``workspace/recipes.json`` and fires due recipes on a
background thread, executing every step through the :class:`ToolRegistry` so
permissions, mode tiers, dry-run and HMAC audit all still apply.

Recipes are the Phase-4 "connect to the user's apps" primitive: the agent
auto-composes them from natural language (see ``james.tools.recipes_tools``),
so "every morning summarize my emails and post to WhatsApp" becomes a
persistent automation in one sentence.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..config import settings

_TRIGGER_DAILY = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_TRIGGER_EVERY = re.compile(r"^every\s+(\d+)\s*minutes?$", re.IGNORECASE)


@dataclass
class RecipeStep:
    tool: str
    args: dict = field(default_factory=dict)


@dataclass
class Recipe:
    name: str
    description: str = ""
    # None (manual only) | "daily HH:MM" | "hourly" | "every N minutes" | ISO datetime
    trigger: str | None = None
    steps: list[RecipeStep] = field(default_factory=list)
    enabled: bool = True
    stop_on_error: bool = True
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_run: str | None = None
    last_error: str | None = None
    runs: int = 0


def parse_trigger(trigger: str | None) -> datetime | None:
    """Return the next fire time for a trigger, or None when unparseable."""
    if not trigger:
        return None
    text = trigger.strip()
    match = _TRIGGER_DAILY.match(text)
    if match:
        now = datetime.now().replace(microsecond=0)
        try:
            candidate = now.replace(hour=int(match.group(1)), minute=int(match.group(2)))
        except ValueError:  # invalid clock time such as 25:00 or 09:61
            return None
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if text.lower() == "hourly":
        return datetime.now().replace(microsecond=0) + timedelta(hours=1)
    match = _TRIGGER_EVERY.match(text)
    if match:
        minutes = int(match.group(1))
        if minutes < 1:
            return None
        return datetime.now() + timedelta(minutes=minutes)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class RecipeEngine:
    """Persist, schedule and execute recipes through a ToolRegistry."""

    def __init__(self, registry=None, path: Path | None = None, poll_seconds: float = 15.0):
        self.registry = registry
        self.path = path or (settings.assistant.workspace_dir / "recipes.json")
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- persistence ------------------------------------------------------

    def _load(self) -> list[Recipe]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        recipes = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                steps = [RecipeStep(**s) for s in (entry.get("steps") or [])]
                recipes.append(
                    Recipe(**{k: v for k, v in entry.items() if k != "steps"}, steps=steps)
                )
            except Exception:
                continue
        return recipes

    def _save(self, recipes: list[Recipe]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                [{**asdict(r), "steps": [asdict(s) for s in r.steps]} for r in recipes],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    # ---- validation -------------------------------------------------------

    def _validate_steps(self, steps: list[RecipeStep]) -> list[str]:
        errors = []
        for step in steps:
            if not step.tool:
                errors.append("every step needs a 'tool' name")
            elif self.registry is not None and step.tool not in self.registry:
                errors.append(f"unknown tool '{step.tool}'")
            if not isinstance(step.args, dict):
                errors.append(f"step '{step.tool}' args must be an object")
        return errors

    # ---- API --------------------------------------------------------------

    def add(self, recipe: Recipe) -> tuple[bool, str]:
        errors = self._validate_steps(recipe.steps)
        if errors:
            return False, "; ".join(errors)
        if parse_trigger(recipe.trigger) is None and recipe.trigger:
            return (
                False,
                f"unsupported trigger '{recipe.trigger}' (use daily HH:MM, hourly, every N minutes, or an ISO datetime)",
            )
        with self._lock:
            recipes = self._load()
            recipes = [r for r in recipes if r.name != recipe.name]
            recipes.append(recipe)
            self._save(recipes)
        return True, f"Recipe '{recipe.name}' saved ({len(recipe.steps)} step(s))."

    def remove(self, name: str) -> bool:
        with self._lock:
            recipes = self._load()
            kept = [r for r in recipes if r.name != name]
            if len(kept) == len(recipes):
                return False
            self._save(kept)
        return True

    def list(self) -> list[Recipe]:
        return self._load()

    def get(self, name: str) -> Recipe | None:
        return next((r for r in self._load() if r.name == name), None)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            recipes = self._load()
            recipe = next((r for r in recipes if r.name == name), None)
            if recipe is None:
                return False
            recipe.enabled = bool(enabled)
            self._save(recipes)
        return True

    # ---- execution --------------------------------------------------------

    def run_now(self, name: str) -> tuple[bool, str]:
        """Execute a recipe's steps sequentially through the registry."""
        recipe = self.get(name)
        if recipe is None:
            return False, f"Recipe '{name}' not found."
        if self.registry is None:
            return False, "Recipe engine has no tool registry."
        outcome = [f"Recipe '{name}':"]
        failed = False
        for index, step in enumerate(recipe.steps, start=1):
            try:
                result = self.registry.execute(step.tool, dict(step.args or {}))
            except Exception as exc:
                result = ToolResultShim(ok=False, output=f"Error: {exc}")
            if result.ok:
                outcome.append(f"  [{index}] {step.tool}: ok — {str(result.output)[:120]}")
            else:
                failed = True
                outcome.append(f"  [{index}] {step.tool}: FAILED — {str(result.output)[:200]}")
                if recipe.stop_on_error:
                    break
        recipe.last_run = datetime.now().isoformat(timespec="seconds")
        recipe.runs += 1
        recipe.last_error = str(result.output)[:300] if failed else None
        self._save([*[r for r in self._load() if r.name != name], recipe])
        status = "failed" if failed else "completed"
        return not failed, "\n".join(outcome) + f"\nStatus: {status}"

    # ---- background loop --------------------------------------------------

    def _due(self, recipe: Recipe) -> bool:
        now = datetime.now().replace(microsecond=0)
        last = None
        if recipe.last_run is not None:
            try:
                last = datetime.fromisoformat(recipe.last_run)
            except ValueError:
                last = None
        # Interval triggers fire when the interval has elapsed since the last run.
        text = (recipe.trigger or "").strip()
        every_match = _TRIGGER_EVERY.match(text)
        if every_match:
            minutes = int(every_match.group(1))
            if last is None:
                return True  # never ran -> due now
            return now - last >= timedelta(minutes=minutes)
        if text.lower() == "hourly":
            if last is None:
                return True
            return now - last >= timedelta(hours=1)
        # Absolute triggers (daily HH:MM / ISO) fire at the next occurrence,
        # unless that occurrence has already fired.
        next_fire = parse_trigger(recipe.trigger)
        if next_fire is None:
            return False
        if last is not None and last >= next_fire - timedelta(seconds=1):
            return False
        return now >= next_fire

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for recipe in self._load():
                    if recipe.enabled and self._due(recipe):
                        self.run_now(recipe.name)
            except Exception:
                pass
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="james-recipes", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class ToolResultShim:
    """Minimal stand-in so failed step execution reads like a ToolResult."""

    def __init__(self, ok: bool, output: str) -> None:
        self.ok = ok
        self.output = output


def steps_from_json(steps_json: str) -> list[RecipeStep]:
    """Parse a JSON string of [{"tool": str, "args": {...}}, ...] into steps."""
    try:
        raw = json.loads(steps_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"steps must be valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("steps must be a JSON list")
    steps = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str):
            raise ValueError("every step needs a 'tool' name")
        args = entry.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError(f"step '{entry['tool']}' args must be an object")
        steps.append(RecipeStep(tool=entry["tool"], args=args))
    return steps


def steps_to_json(steps: list[RecipeStep]) -> str:
    return json.dumps([asdict(s) for s in steps], ensure_ascii=False)


recipe_engine = RecipeEngine()
