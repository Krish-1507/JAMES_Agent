"""Web tools: search the internet and read pages — JAMES's eyes on the web."""

from __future__ import annotations

import re
from html import unescape

import requests
from bs4 import BeautifulSoup

from ..config import settings
from .base import ToolResult, tool

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _offline_blocked() -> ToolResult | None:
    if settings.assistant.offline_mode:
        return ToolResult(
            ok=False,
            output="Offline mode is ON — web access is disabled and audited. "
            "Use a local model / local data instead.",
        )
    return None


@tool(
    "web_search",
    "Search the web (DuckDuckGo) and return a list of result titles, snippets and URLs.",
    {
        "query": {"type": "string", "description": "Search query."},
        "max_results": {"type": "integer", "description": "Max results to return (default 5)."},
    },
    required=["query"],
)
def web_search(query: str, max_results: int = 5) -> ToolResult:
    blocked = _offline_blocked()
    if blocked:
        return blocked
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=_HEADERS,
            timeout=20,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for a in soup.select(".result__a")[:max_results]:
            title = unescape(a.get_text(strip=True))
            href = a.get("href", "")
            results.append(f"• {title}\n  {href}")
        return ToolResult(ok=True, output="\n".join(results) or "No results.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Search failed: {exc}")


@tool(
    "fetch_url",
    "Fetch a web page and extract its readable text content (articles, docs, etc.).",
    {
        "url": {"type": "string", "description": "Fully-qualified URL to read."},
        "max_chars": {
            "type": "integer",
            "description": "Cap on returned characters (default 8000).",
        },
    },
    required=["url"],
)
def fetch_url(url: str, max_chars: int = 8000) -> ToolResult:
    blocked = _offline_blocked()
    if blocked:
        return blocked
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=25)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = re.sub(r"\n\s*\n", "\n", soup.get_text(" ", strip=True))
        return ToolResult(ok=True, output=text[:max_chars])
    except Exception as exc:
        return ToolResult(ok=False, output=f"Fetch failed: {exc}")
