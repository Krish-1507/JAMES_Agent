"""Computer-use vision loop — screenshot -> describe -> act, fully local.

A tight agentic loop: capture the screen, ask a vision model what to do next,
execute the action with pyautogui, repeat. No cloud browser required — the
model can be a local Ollama vision model (e.g. llava) in offline mode. This is
JAMES's "computer-use": it can operate any desktop app the way a human would.
"""
from __future__ import annotations

import base64
import io
import json
import re

from ..llm.base import LLMProvider

_STEP_PROMPT = """You are operating the user's computer through screenshots.
GOAL: {instruction}
PREVIOUS ACTIONS: {history}
Study the screenshot and choose the SINGLE next action. Reply with ONLY one JSON object, no prose:
  {{"action":"click","x":<int>,"y":<int>}}
  {{"action":"type","text":"<text to type>"}}
  {{"action":"keypress","key":"enter"}}
  {{"action":"scroll","dx":0,"dy":-300}}
  {{"action":"wait"}}
  {{"action":"done","result":"<what was accomplished>"}}
Coordinates are screen pixels (origin top-left). When the goal is complete, return action "done".
"""


def _parse_action(text: str) -> dict:
    # Tolerate models that wrap the JSON in prose or code fences.
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if not match:
        return {"action": "wait"}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"action": "wait"}
    if not isinstance(obj, dict) or "action" not in obj:
        return {"action": "wait"}
    return obj


def _act(action: dict) -> str:
    import pyautogui

    pyautogui.FAILSAFE = True
    kind = action.get("action")
    if kind == "click":
        pyautogui.click(int(action.get("x", 0)), int(action.get("y", 0)))
        return f"clicked ({action.get('x')},{action.get('y')})"
    if kind == "type":
        pyautogui.write(str(action.get("text", "")), interval=0.01)
        return f"typed {len(str(action.get('text', '')))} chars"
    if kind == "keypress":
        pyautogui.press(str(action.get("key", "enter")))
        return f"pressed {action.get('key')}"
    if kind == "scroll":
        pyautogui.scroll(int(action.get("dy", 0)), x=int(action.get("x", 0)), y=int(action.get("y", 0)))
        return f"scrolled {action.get('dy')}"
    if kind == "wait":
        import time

        time.sleep(1)
        return "waited"
    if kind == "done":
        return f"done: {action.get('result', '')}"
    return f"unknown action {kind}"


def run_computer_use(
    provider: LLMProvider,
    instruction: str,
    max_steps: int = 12,
    model: str | None = None,
) -> str:
    import pyautogui

    history: list[str] = []
    for step in range(1, max_steps + 1):
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        prompt = _STEP_PROMPT.format(
            instruction=instruction,
            history="; ".join(history) or "(none yet)",
        )
        try:
            resp = provider.chat(
                [{"role": "user", "content": prompt}],
                images=[b64],
                model=model,
            )
            action = _parse_action(resp.content)
        except Exception as exc:  # vision/connection error — stop safely
            return f"Computer-use stopped at step {step}: {exc}"

        note = _act(action)
        history.append(f"step {step}: {action.get('action')} -> {note}")
        if action.get("action") == "done":
            return action.get("result", "Task complete.") + f" (in {step} steps)"
    return "Reached max steps without finishing. Last actions: " + "; ".join(history[-3:])
