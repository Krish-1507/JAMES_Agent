"""System automation tools: run commands, open apps, screen, media control, multimodal input."""

from __future__ import annotations

import base64
import re
import subprocess  # nosec B404 - required to launch desktop applications
import webbrowser

from ..core.command_policy import is_safe_command, parse_safe_command
from ..core.isolation import run_isolated
from ..core.workspace import resolve_workspace_path, workspace_root
from .base import ToolResult, tool

_SHELL_METACHAR_RE = re.compile(r"[;&|`$(){}[\]<>!#]")

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


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
    result = run_isolated(
        "command",
        {"args": args, "timeout": timeout, "workspace": str(workspace_root())},
        timeout=timeout + 5,
    )
    return ToolResult(
        ok=bool(result.get("ok")), output=str(result.get("output", "Command failed."))
    )


@tool(
    "open_application",
    "Open a website in the default browser or launch a desktop application by name/path.",
    {
        "target": {
            "type": "string",
            "description": "A URL, app name (e.g. 'notepad') or full path to an executable.",
        },
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
        subprocess.Popen([target], shell=False)  # nosec B603 - argv list, no shell; gated by mode/confirmation
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

        p = resolve_workspace_path(filename, allow_root=False)
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
    import platform

    info = {
        "platform": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }
    try:
        import psutil

        info.update(
            {
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "memory_percent": psutil.virtual_memory().percent,
                "battery": None,
            }
        )
        bat = psutil.sensors_battery()
        if bat:
            info["battery"] = (
                f"{bat.percent}% ({'plugged in' if bat.power_plugged else 'on battery'})"
            )
    except ImportError:
        info["battery"] = "psutil not installed"
    return ToolResult(ok=True, output=str(info))


@tool(
    "control_media",
    "Control media playback with a keyboard shortcut: play_pause, next, previous, volume_up, volume_down, mute.",
    {
        "action": {
            "type": "string",
            "description": "One of play_pause|next|previous|volume_up|volume_down|mute.",
        }
    },
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
    "notify",
    "Show a system notification (Windows toast, macOS banner, Linux notification).",
    {
        "title": {"type": "string", "description": "Notification title."},
        "message": {"type": "string", "description": "Notification body."},
    },
    required=["title", "message"],
)
def notify(title: str, message: str) -> ToolResult:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=10)
        return ToolResult(ok=True, output=f"Notification shown: {title}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Notification failed: {exc}")


@tool(
    "upload_image",
    "Encode a local image as a base64 data URI for vision-capable models. Returns the data URI.",
    {
        "path": {"type": "string", "description": "Path to the image file."},
        "description": {
            "type": "string",
            "description": "Optional description of what to look for in the image.",
        },
    },
    required=["path"],
)
def upload_image(path: str, description: str = "") -> ToolResult:
    try:
        p = resolve_workspace_path(path, allow_root=False)
        if not p.exists():
            return ToolResult(ok=False, output=f"File not found: {path}")
        if p.stat().st_size > 10_000_000:
            return ToolResult(
                ok=False, output=f"File too large: {p.stat().st_size} bytes (max 10MB)"
            )
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(p.suffix.lower(), "application/octet-stream")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        uri = f"data:{mime};base64,{b64}"
        preview = f"{uri[:80]}…({len(uri)} chars total)" if len(uri) > 80 else uri
        return ToolResult(
            ok=True,
            output=f"Image {p.name} encoded. Data URI ({len(uri)} chars): {preview}",
            data=uri,
        )
    except Exception as exc:
        return ToolResult(ok=False, output=f"Image upload failed: {exc}")
