"""System automation tools: run commands, open apps, screen, media control, multimodal input."""
from __future__ import annotations

import base64
import re
import subprocess
import webbrowser
from pathlib import Path

from ..config import settings
from .base import Tool, ToolResult, tool
from ..core.command_policy import is_safe_command, parse_safe_command

_SHELL_METACHAR_RE = re.compile(r'[;&|`$(){}[\]<>!#]')

_URL_SCHEME_RE = re.compile(r'^https?://', re.IGNORECASE)



def _is_safe_command(command: str) -> bool:
    return is_safe_command(command)


@tool(
    "run_shell_command",
    "Run a shell command on the host machine and return its output. Powerful — only use when asked.",
    {
        "command": {"type": "string", "description": "The shell command to execute."},
        "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)."},
    },
    required=["command"],
)
def run_shell_command(command: str, timeout: int = 60) -> ToolResult:
    args, reason = parse_safe_command(command)
    if args is None:
        return ToolResult(ok=False, output=f"Command blocked: {reason}")
    try:
        proc = subprocess.run(
            args, shell=False, capture_output=True, text=True, timeout=timeout
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return ToolResult(ok=proc.returncode == 0, output=out[:8000])
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output="Command timed out.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Error: {exc}")


@tool(
    "open_application",
    "Open a website in the default browser or launch a desktop application by name/path.",
    {
        "target": {"type": "string", "description": "A URL, app name (e.g. 'notepad') or full path to an executable."},
    },
    required=["target"],
)
def open_application(target: str) -> ToolResult:
    if _SHELL_METACHAR_RE.search(target):
        return ToolResult(ok=False, output=f"Target contains unsafe characters: {target[:80]}")
    try:
        if _URL_SCHEME_RE.match(target):
            webbrowser.open(target)
            return ToolResult(ok=True, output=f"Opened {target} in browser.")
        subprocess.Popen([target], shell=False)
        return ToolResult(ok=True, output=f"Launched {target}.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Failed to open: {exc}")


@tool(
    "take_screenshot",
    "Capture the current screen and save it as a PNG file. Returns the saved path.",
    {"filename": {"type": "string", "description": "Output file name (default screenshot.png)."}},
)
def take_screenshot(filename: str = "screenshot.png") -> ToolResult:
    try:
        import pyautogui

        p = settings.assistant.workspace_dir / filename
        pyautogui.screenshot(str(p))
        return ToolResult(ok=True, output=f"Screenshot saved to {p}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Screenshot failed: {exc}")


@tool(
    "get_system_info",
    "Return live system info: battery %, CPU and memory usage, and running platform.",
    {},
)
def get_system_info() -> ToolResult:
    try:
        import platform

        import psutil

        info = {
            "platform": f"{platform.system()} {platform.release()}",
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_percent": psutil.virtual_memory().percent,
            "battery": None,
        }
        bat = psutil.sensors_battery()
        if bat:
            info["battery"] = f"{bat.percent}% ({'plugged in' if bat.power_plugged else 'on battery'})"
        return ToolResult(ok=True, output=str(info))
    except Exception as exc:
        return ToolResult(ok=False, output=f"Failed: {exc}")


@tool(
    "control_media",
    "Control media playback with a keyboard shortcut: play_pause, next, previous, volume_up, volume_down, mute.",
    {"action": {"type": "string", "description": "One of play_pause|next|previous|volume_up|volume_down|mute."}},
    required=["action"],
)
def control_media(action: str) -> ToolResult:
    try:
        import pyautogui

        mapping = {
            "play_pause": "playpause",
            "next": "nexttrack",
            "previous": "prevtrack",
            "volume_up": "volumeup",
            "volume_down": "volumedown",
            "mute": "volumemute",
        }
        key = mapping.get(action)
        if not key:
            return ToolResult(ok=False, output=f"Unknown action: {action}")
        pyautogui.press(key)
        return ToolResult(ok=True, output=f"Sent {action}.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Failed: {exc}")


@tool(
    "clipboard",
    "Get or set the system clipboard. Set 'text' to write, omit it to read.",
    {"text": {"type": "string", "description": "Text to copy (omit to read current clipboard)."}},
)
def clipboard(text: str = "") -> ToolResult:
    try:
        import pyperclip

        if text:
            pyperclip.copy(text)
            return ToolResult(ok=True, output="Copied to clipboard.")
        return ToolResult(ok=True, output=pyperclip.paste())
    except Exception as exc:
        return ToolResult(ok=False, output=f"Clipboard error: {exc}")


@tool(
    "upload_image",
    "Upload an image file for analysis. Returns a base64-encoded description of the image.",
    {
        "path": {"type": "string", "description": "Path to the image file."},
        "description": {"type": "string", "description": "Optional description of what to look for in the image."},
    },
    required=["path"],
)
def upload_image(path: str, description: str = "") -> ToolResult:
    try:
        p = Path(path)
        if not p.exists():
            return ToolResult(ok=False, output=f"File not found: {path}")
        if p.stat().st_size > 10_000_000:
            return ToolResult(ok=False, output=f"File too large: {p.stat().st_size} bytes (max 10MB)")
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        return ToolResult(
            ok=True,
            output=f"Image uploaded: {p.name} ({len(data)} bytes, base64 length {len(b64)}). "
            f"Pass the base64 data to a vision-capable LLM for analysis.",
        )
    except Exception as exc:
        return ToolResult(ok=False, output=f"Image upload failed: {exc}")
