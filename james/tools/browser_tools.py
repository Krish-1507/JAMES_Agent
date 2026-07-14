"""Browser automation tools (Playwright) — click-through web control.

These let JAMES actually *use* websites: navigate, click, fill forms and read
results back as text. Great for booking, shopping, filling web forms, etc.
"""
from __future__ import annotations

from typing import Optional

from ..config import settings
from .base import Tool, ToolResult, tool

_browser = None
_page = None


def _get_page():
    global _browser, _page
    if _page is not None:
        return _page
    from playwright.sync_api import sync_playwright

    _browser = sync_playwright().start().chromium.launch(headless=settings.assistant.browser_headless)
    _page = _browser.new_page()
    return _page


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
    "browser_close",
    "Close the automated browser session.",
    {},
)
def browser_close() -> ToolResult:
    _close()
    return ToolResult(ok=True, output="Browser closed.")
