"""Auto-update support for JAMES (Phase 5 release engineering).

Checks the GitHub releases API (or ``UPDATE_URL``) for the latest release
and compares it with the installed version. ``james --update-check`` prints
the result and exits 0/1/2; ``james --update`` applies the update — pip
upgrade for pip installs, or a download hint for bundled desktop installs.

All network access is HTTPS. In offline mode (``--offline``) the check is
skipped and reports up-to-date so nothing egresses.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 - runs the user's own pip to upgrade james-assistant
import sys
from typing import Any

import requests  # nosec B113 - HTTPS release lookup only

from . import __version__
from .config import settings

DEFAULT_UPDATE_URL = "https://api.github.com/repos/Krish-1507/JAMES_Agent/releases/latest"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def update_url() -> str:
    return os.getenv("UPDATE_URL", DEFAULT_UPDATE_URL).strip() or DEFAULT_UPDATE_URL


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle rather than a pip install."""
    return bool(getattr(sys, "frozen", False))


def _parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_RE.search(version or "")
    if not match:
        return (0, 0, 0)
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def check_for_updates(url: str | None = None, timeout: int = 15) -> dict[str, Any]:
    """Query the release feed; never raises. ``ok`` is False only on network errors."""
    if settings.assistant.offline_mode:
        return {"ok": True, "up_to_date": True, "reason": "offline_mode"}
    target = url or update_url()
    try:
        response = requests.get(target, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {"ok": False, "error": f"Could not check for updates: {exc}"}
    latest = str(data.get("tag_name", "")).lstrip("v")
    current = __version__
    behind = _parse_version(latest) > _parse_version(current)
    return {
        "ok": True,
        "current_version": current,
        "latest_version": latest,
        "up_to_date": not behind,
        "release_url": data.get("html_url") or target,
        "published_at": data.get("published_at"),
    }


def _pip_upgrade() -> tuple[bool, str]:
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell; installs our own package
        [sys.executable, "-m", "pip", "install", "--upgrade", "james-assistant"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        return False, tail[-1] if tail else f"pip exited {proc.returncode}"
    return True, "Upgraded james-assistant to the latest release."


def apply_update(url: str | None = None, timeout: int = 30) -> dict[str, Any]:
    """Upgrade the installed package (pip) or point at the release download."""
    check = check_for_updates(url=url, timeout=timeout)
    if not check.get("ok"):
        return {"ok": False, "error": check.get("error", "update check failed")}
    if check.get("up_to_date"):
        return {
            "ok": True,
            "message": f"Already on the latest version ({__version__}).",
            "up_to_date": True,
        }
    if is_frozen():
        return {
            "ok": True,
            "up_to_date": False,
            "message": (
                f"JAMES {check['latest_version']} is available — download the installer at "
                f"{check['release_url']} and reinstall."
            ),
        }
    try:
        ok, message = _pip_upgrade()
    except Exception as exc:
        return {"ok": False, "error": f"pip upgrade failed: {exc}"}
    if not ok:
        return {"ok": False, "error": message}
    return {"ok": True, "up_to_date": False, "message": message}
