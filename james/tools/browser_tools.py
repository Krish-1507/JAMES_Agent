"""Browser automation tools (Playwright) — click-through web control.

These let JAMES actually *use* websites: navigate, click, fill forms and read
results back as text. Great for booking, shopping, filling web forms, etc.
"""
from __future__ import annotations

import time
from typing import Optional

from ..config import settings
from .base import Tool, ToolResult, tool

_browser = None
_page = None
_browser_errors = 0
_MAX_BROWSER_ERRORS = 3
_RECOVERY_COOLDOWN = 5.0


def _get_page():
    global _browser, _page, _browser_errors
    if _page is not None:
        try:
            _page.title()
            return _page
        except Exception:
            _close()
    if _browser_errors >= _MAX_BROWSER_ERRORS:
        raise RuntimeError(
            f"Browser has failed {_browser_errors} times. "
            f"Please close and restart the browser with browser_close()."
        )
    from playwright.sync_api import sync_playwright

    try:
        _browser = sync_playwright().start().chromium.launch(
            headless=settings.assistant.browser_headless
        )
        _page = _browser.new_page()
        return _page
    except Exception as exc:
        _browser_errors += 1
        _browser = None
        _page = None
        raise RuntimeError(f"Browser launch failed ({_browser_errors}/{_MAX_BROWSER_ERRORS}): {exc}")


def _close():
    global _browser, _page, _browser_errors
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
        _page = None
        _browser_errors = 0


def _health_check() -> bool:
    """Check if the browser is healthy; attempt recovery if not."""
    global _browser_errors
    if _page is None and _browser is None:
        _browser_errors = 0
        return False
    try:
        if _page is not None:
            _page.title()
        return True
    except Exception:
        _close()
        _browser_errors = 0
        return False


def _close():
    global _browser, _page
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
        _browser = None
        _page = None


@tool(
    "browser_navigate",
    "Open a URL in the automated browser and wait for it to load.",
    {"url": {"type": "string", "description": "The full URL to open."}},
    required=["url"],
)
def browser_navigate(url: str) -> ToolResult:
    try:
        page = _get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return ToolResult(ok=True, output=f"Opened {url}\nTitle: {page.title()}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Navigation failed: {exc}")


@tool(
    "browser_click",
    "Click an element on the current page by CSS selector or visible text.",
    {"target": {"type": "string", "description": "CSS selector or link/button text to click."}},
    required=["target"],
)
def browser_click(target: str) -> ToolResult:
    try:
        page = _get_page()
        try:
            page.click(target, timeout=8000)
        except Exception:
            page.get_by_text(target, exact=False).first.click(timeout=8000)
        return ToolResult(ok=True, output=f"Clicked '{target}'.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Click failed: {exc}")


@tool(
    "browser_type",
    "Type text into an input field identified by CSS selector or placeholder.",
    {
        "selector": {"type": "string", "description": "CSS selector or placeholder of the field."},
        "text": {"type": "string", "description": "Text to type."},
    },
    required=["selector", "text"],
)
def browser_type(selector: str, text: str) -> ToolResult:
    try:
        page = _get_page()
        try:
            page.fill(selector, text)
        except Exception:
            page.get_by_placeholder(selector, exact=False).fill(text)
        return ToolResult(ok=True, output=f"Typed into '{selector}'.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Type failed: {exc}")


@tool(
    "browser_extract",
    "Extract the readable text content of the current page (or a CSS selector).",
    {"selector": {"type": "string", "description": "Optional CSS selector to limit extraction."}},
)
def browser_extract(selector: str = "") -> ToolResult:
    try:
        page = _get_page()
        if selector:
            text = page.locator(selector).inner_text()
        else:
            text = page.inner_text()
        return ToolResult(ok=True, output=text[:8000])
    except Exception as exc:
        return ToolResult(ok=False, output=f"Extract failed: {exc}")


@tool(
    "browser_screenshot",
    "Capture a screenshot of the current page to a PNG file.",
    {"filename": {"type": "string", "description": "Output file name (default page.png)."}},
)
def browser_screenshot(filename: str = "page.png") -> ToolResult:
    try:
        page = _get_page()
        p = settings.assistant.workspace_dir / filename
        page.screenshot(path=str(p))
        return ToolResult(ok=True, output=f"Screenshot saved to {p}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Screenshot failed: {exc}")


@tool(
    "browser_health",
    "Check if the browser session is healthy and attempt recovery if needed. Returns the browser status.",
    {},
)
def browser_health() -> ToolResult:
    try:
        healthy = _health_check()
        if healthy:
            return ToolResult(ok=True, output="Browser is healthy.")
        return ToolResult(ok=False, output="Browser is not running. Use browser_navigate to start a session.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Browser health check failed: {exc}")


@tool(
    "browser_close",
    "Close the automated browser session.",
    {},
)
def browser_close() -> ToolResult:
    _close()
    return ToolResult(ok=True, output="Browser closed.")
