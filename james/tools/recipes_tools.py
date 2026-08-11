"""Recipe tools — create, compose, run and manage automations from the agent loop.

``compose_recipe`` is the auto-compose entry: it asks the LLM to turn a
natural-language request ("every morning summarize my emails and post to
WhatsApp") into a structured recipe (trigger + tool steps), validates the
steps against the live tool registry, and persists it — scheduler + tools +
skills wired together in one sentence.
"""

from __future__ import annotations

import json

from ..core.recipes import Recipe, RecipeEngine, RecipeStep, steps_from_json
from .base import ToolResult, tool

_context = {"engine": None, "llm": None}

_COMPOSE_PROMPT = """\
You are the automation composer for JAMES, an AI agent with a tool registry.

The user wants an automation built. Choose tools ONLY from this list of
available tool names:
{available}

Return a single JSON object (no markdown, no commentary) with this exact shape:
{{
  "name": "short-kebab-case-name",
  "description": "one sentence about what this automation does",
  "steps": [
    {{"tool": "tool_name", "args": {{"arg_name": "value"}}}}
  ]
}}

Rules:
- One step per action. For recurring greetings/summaries prefer existing
  tools like outlook_read_inbox, web_search, send_message, notify.
- Use concrete values for args (real email addresses, channel names, times).
- Do not invent tool names outside the provided list.
"""


def configure_recipes(engine: RecipeEngine | None, llm=None) -> None:
    _context["engine"] = engine
    _context["llm"] = llm


def _engine() -> RecipeEngine:
    if _context["engine"] is None:
        raise RuntimeError("Recipe engine is not configured.")
    return _context["engine"]


@tool(
    "create_recipe",
    "Create a recipe: a named, repeatable multi-step automation with a trigger "
    "('daily 09:00', 'hourly', 'every 30 minutes', or an ISO datetime).",
    {
        "name": {"type": "string", "description": "Recipe name (kebab-case)."},
        "description": {"type": "string", "description": "What the automation does."},
        "trigger": {
            "type": "string",
            "description": "When to run: 'daily HH:MM', 'hourly', 'every N minutes', ISO datetime, or empty for manual.",
        },
        "steps": {
            "type": "string",
            "description": 'JSON list of steps: [{"tool": "name", "args": {...}}].',
        },
        "stop_on_error": {
            "type": "boolean",
            "description": "Stop at the first failed step (default true).",
        },
    },
    required=["name", "steps"],
)
def create_recipe(
    name: str, steps: str, description: str = "", trigger: str = "", stop_on_error: bool = True
) -> ToolResult:
    try:
        recipe_steps = steps_from_json(steps)
        recipe = Recipe(
            name=name.strip().lower().replace(" ", "-"),
            description=description,
            trigger=trigger or None,
            steps=recipe_steps,
            stop_on_error=bool(stop_on_error),
        )
        ok, message = _engine().add(recipe)
        return ToolResult(ok=ok, output=message)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Recipe creation failed: {exc}")


@tool(
    "compose_recipe",
    "Auto-compose an automation from a natural-language request, e.g. 'every morning "
    "summarize my emails and notify me'. Requires a configured LLM.",
    {
        "request": {
            "type": "string",
            "description": "What the automation should do, in plain language.",
        },
        "trigger": {
            "type": "string",
            "description": "Optional schedule; guessed from the request when omitted.",
        },
    },
    required=["request"],
)
def compose_recipe(request: str, trigger: str = "") -> ToolResult:
    llm = _context["llm"]
    if llm is None:
        return ToolResult(ok=False, output="Recipe composition needs an LLM provider.")
    engine = _engine()
    try:
        available = "\n".join(sorted(n for n in engine.registry.names() if n != "compose_recipe"))
        prompt = _COMPOSE_PROMPT.format(available=available or "(none)")
        prompt += f"\nUser request: {request}\n"
        if trigger:
            prompt += f"User-specified schedule: {trigger}\n"
        reply = llm.chat([{"role": "user", "content": prompt}])
        text = (reply.content or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return ToolResult(ok=False, output="The model did not return a recipe JSON.")
        parsed = json.loads(text[start : end + 1])
        name = str(parsed.get("name", "")).strip().lower().replace(" ", "-") or "automation"
        description = str(parsed.get("description", ""))
        recipe_steps = [
            RecipeStep(tool=str(s["tool"]), args=dict(s.get("args") or {}))
            for s in (parsed.get("steps") or [])
        ]
        recipe = Recipe(
            name=name,
            description=description or request[:200],
            trigger=(trigger.strip() or str(parsed.get("trigger") or "") or None),
            steps=recipe_steps,
        )
        ok, message = engine.add(recipe)
        if not ok:
            return ToolResult(ok=False, output=message)
        detail = "\n".join(f"- {s.tool}({s.args})" for s in recipe_steps)
        return ToolResult(
            ok=True,
            output=f"{message}\nSchedule: {recipe.trigger or 'manual'}\n{detail}",
        )
    except Exception as exc:
        return ToolResult(ok=False, output=f"Recipe composition failed: {exc}")


@tool(
    "list_recipes",
    "List all saved automation recipes with their trigger and step count.",
    {},
)
def list_recipes() -> ToolResult:
    recipes = _engine().list()
    if not recipes:
        return ToolResult(ok=True, output="No recipes yet.")
    lines = []
    for recipe in recipes:
        state = "enabled" if recipe.enabled else "paused"
        lines.append(
            f"- {recipe.name} [{state}] trigger={recipe.trigger or 'manual'} "
            f"steps={len(recipe.steps)} runs={recipe.runs}"
            + (f" last={recipe.last_run}" if recipe.last_run else "")
        )
    return ToolResult(ok=True, output="\n".join(lines))


@tool(
    "delete_recipe",
    "Delete an automation recipe by name.",
    {"name": {"type": "string", "description": "Recipe name to delete."}},
    required=["name"],
)
def delete_recipe(name: str) -> ToolResult:
    ok = _engine().remove(name)
    return ToolResult(ok=ok, output=f"Deleted '{name}'." if ok else f"Recipe '{name}' not found.")


@tool(
    "run_recipe_now",
    "Execute a saved recipe immediately, step by step.",
    {"name": {"type": "string", "description": "Recipe name to run."}},
    required=["name"],
)
def run_recipe_now(name: str) -> ToolResult:
    ok, output = _engine().run_now(name)
    return ToolResult(ok=ok, output=output)
