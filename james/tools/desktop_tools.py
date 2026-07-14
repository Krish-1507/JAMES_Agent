"""Desktop / computer-use tools — direct pyautogui control + the vision loop.

These power JAMES's "computer-use": it can click, type and drive any desktop
application the way a human would, and the ``computer_use`` tool runs a full
screenshot -> describe -> act loop using a vision model (local-friendly).
"""
from __future__ import annotations

from typing import Optional

from .base import Tool, ToolResult, tool

_context = {"llm": None}


def configure_computer_use(llm) -> None:
    _context["llm"] = llm


@tool(
    "computer_use",
    "Autonomously operate the desktop: repeatedly screenshot the screen, ask a vision model "
    "what to do next, and act (click/type/scroll) until the instruction is complete. Fully "
    "local when paired with a local vision model. Bounded by max_steps for safety.",
    {
        "instruction": {"type": "string", "description": "What to accomplish on the screen."},
        "max_steps": {"type": "integer", "description": "Max action iterations (default 12)."},
    },
    required=["instruction"],
)
def computer_use(instruction: str, max_steps: int = 12) -> ToolResult:
    from ..config import settings
    from ..core.computeruse import run_computer_use

    if _context["llm"] is None:
        return ToolResult(ok=False, output="Computer-use is not configured (no LLM provider).")
    model = settings.assistant.vision_model or None
    try:
        result = run_computer_use(_context["llm"], instruction, max_steps=max(1, int(max_steps)), model=model)
        return ToolResult(ok=True, output=result)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Computer-use failed: {exc}")


@tool(
    "screenshot_save",
    "Capture the screen and save it to a PNG file. Returns the path.",
    {"path": {"type": "string", "description": "Destination PNG path (default workspace/screenshot.png)."}},
)
def screenshot_save(path: str = "") -> ToolResult:
    import pyautogui
    from pathlib import Path

    from ..config import settings

    dest = Path(path) if path else settings.assistant.workspace_dir / "screenshot.png"
    try:
        img = pyautogui.screenshot()
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(dest))
        return ToolResult(ok=True, output=f"Saved screenshot to {dest}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Screenshot failed: {exc}")


@tool(
    "click_at",
    "Click at screen coordinates (pixels, origin top-left).",
    {
        "x": {"type": "integer", "description": "X pixel."},
        "y": {"type": "integer", "description": "Y pixel."},
    },
    required=["x", "y"],
)
def click_at(x: int, y: int) -> ToolResult:
    import pyautogui

    try:
        pyautogui.click(int(x), int(y))
        return ToolResult(ok=True, output=f"Clicked at ({x},{y})")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Click failed: {exc}")


@tool(
    "type_text",
    "Type text at the current cursor position.",
    {"text": {"type": "string", "description": "Text to type."}},
    required=["text"],
)
def type_text(text: str) -> ToolResult:
    import pyautogui

    try:
        pyautogui.write(str(text), interval=0.01)
        return ToolResult(ok=True, output=f"Typed {len(text)} characters")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Type failed: {exc}")


@tool(
    "press_key",
    "Press a keyboard key (e.g. 'enter', 'tab', 'esc', 'ctrl+a').",
    {"key": {"type": "string", "description": "Key name understood by pyautogui."}},
    required=["key"],
)
def press_key(key: str) -> ToolResult:
    import pyautogui

    try:
        pyautogui.press(str(key))
        return ToolResult(ok=True, output=f"Pressed {key}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Key press failed: {exc}")
