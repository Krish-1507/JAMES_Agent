"""Web tools: search the internet and read pages — JAMES's eyes on the web.

Phase-1 upgrades:
- Multi-engine search: ``engine="auto"`` prefers Tavily / Brave when an API
  key is configured, and always falls back to DuckDuckGo (no key needed).
- Main-content extraction: strip nav/ads/sidebars from pages before reading,
  so articles and docs come back clean.
- Link discovery: ``extract_links`` and ``fetch_url(include_links=True)`` so
  the agent can explore a site's structure instead of guessing URLs.
"""

from __future__ import annotations

import os
import re
from contextlib import suppress
from html import unescape
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from ..config import settings
from .base import ToolResult, tool

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Tags that never carry main content.
_JUNK_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
_NAV_TAGS = {"nav", "header", "footer", "aside", "form", "iframe"}
_NAV_CLASS_RE = re.compile(
    r"\b(nav|menu|sidebar|ad|ads|advert|banner|cookie|modal|popup|footer|header|comment|social|share|related|recommend)\b",
    re.IGNORECASE,
)


def _offline_blocked() -> ToolResult | None:
    if settings.assistant.offline_mode:
        return ToolResult(
            ok=False,
            output="Offline mode is ON — web access is disabled and audited. "
            "Use a local model / local data instead.",
        )
    return None


def _render_with_playwright(url: str, timeout_ms: int = 25000) -> str:
    """Render a JS-heavy page with headless Chromium and return its HTML.

    Needed for single-page apps and JS-gated sites where a plain HTTP GET
    only returns an empty shell. Playwright is an optional dependency.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=settings.assistant.browser_headless, timeout=timeout_ms
        )
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = page.content()
        finally:
            browser.close()
    return html


def _looks_like_js_shell(html: str, text: str) -> bool:
    """Heuristic: a page that rendered almost no text but loads scripts is
    probably a JS shell (SPA) — worth a headless-browser pass."""
    if len(text.strip()) > 200:
        return False
    lowered = html.lower()
    return "<script" in lowered and (
        'id="app"' in lowered
        or 'id="root"' in lowered
        or 'id="__next"' in lowered
        or "webpack" in lowered
        or "vue" in lowered
        or "react" in lowered
    )


def _get(url: str, timeout: int = 25) -> requests.Response:
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


def _strip_junk(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(_JUNK_TAGS):
        tag.decompose()


def _is_nav_el(tag: Tag) -> bool:
    if tag.name in _NAV_TAGS:
        return True
    cls = tag.get("class") or []
    id_ = tag.get("id") or ""
    return bool(_NAV_CLASS_RE.search(" ".join(cls) + " " + id_))


def _select_main(soup: BeautifulSoup) -> Tag | None:
    """Prefer semantic containers (article, [role=main], main), else the
    div with the most paragraph text."""
    candidates = []
    for sel in ("article", "main", "[role='main']", "[role='article']"):
        candidates.extend(soup.select(sel))
    if candidates:
        return max(candidates, key=lambda c: len(c.get_text(" ", strip=True)))
    best, best_score = None, 0
    for div in soup.find_all("div"):
        score = len(div.find_all("p"))
        if score > best_score:
            best, best_score = div, score
    return best


def extract_main_text(html: str) -> str:
    """Extract the readable main text of a page, minus nav/ads/sidebars."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_junk(soup)
    main = _select_main(soup)
    if main is None:
        main = soup.body or soup
    if isinstance(main, Tag):
        for tag in main.find_all(_NAV_TAGS):
            tag.decompose()
        for tag in main.find_all(True):
            cls = " ".join(tag.get("class") or [])
            if tag.name == "div" and _NAV_CLASS_RE.search(cls + " " + (tag.get("id") or "")):
                tag.decompose()
    text = re.sub(r"\n\s*\n", "\n", main.get_text(" ", strip=True))
    return text.strip()


def extract_links(html: str, base_url: str, limit: int = 30) -> list[str]:
    """Return deduplicated absolute URLs found in a page, in document order."""
    soup = BeautifulSoup(html, "html.parser")
    seen, links = set(), []
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        href = href.strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
        if len(links) >= limit:
            break
    return links


# ---------------------------------------------------------------------------
# Multi-engine search
# ---------------------------------------------------------------------------


def _search_tavily(query: str, max_results: int) -> list[dict] | None:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return None
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": query, "max_results": max_results},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in resp.json().get("results", [])
        ]
    except Exception as exc:
        raise RuntimeError(f"Tavily error: {exc}") from exc


def _search_brave(query: str, max_results: int) -> list[dict] | None:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        return None
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in resp.json().get("web", {}).get("results", [])
        ]
    except Exception as exc:
        raise RuntimeError(f"Brave error: {exc}") from exc


def _search_ddg(query: str, max_results: int) -> list[dict]:
    resp = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.select(".result__a")[:max_results]:
        results.append(
            {
                "title": unescape(a.get_text(strip=True)),
                "url": a.get("href", ""),
                "snippet": "",
            }
        )
    return results


def _format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        title = r.get("title") or "(untitled)"
        url = r.get("url") or ""
        snippet = r.get("snippet") or ""
        block = f"• {title}\n  {url}"
        if snippet:
            block += f"\n  {snippet[:300]}"
        lines.append(block)
    return "\n".join(lines) or "No results."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(
    "web_search",
    "Search the web (auto-selects Tavily, Brave, or DuckDuckGo) and return "
    "result titles, URLs and snippets.",
    {
        "query": {"type": "string", "description": "Search query."},
        "max_results": {"type": "integer", "description": "Max results to return (default 5)."},
        "engine": {
            "type": "string",
            "enum": ["auto", "tavily", "brave", "duckduckgo"],
            "description": "Search engine (default auto: API key if set, else DuckDuckGo).",
        },
    },
    required=["query"],
)
def web_search(query: str, max_results: int = 5, engine: str = "auto") -> ToolResult:
    blocked = _offline_blocked()
    if blocked:
        return blocked
    try:
        if engine in ("auto", "tavily"):
            results = _search_tavily(query, max_results)
            if results is not None and engine == "tavily":
                return ToolResult(ok=True, output=_format_results(results))
            if results:
                return ToolResult(ok=True, output=_format_results(results))
        if engine in ("auto", "brave"):
            results = _search_brave(query, max_results)
            if results is not None and engine == "brave":
                return ToolResult(ok=True, output=_format_results(results))
            if results:
                return ToolResult(ok=True, output=_format_results(results))
        results = _search_ddg(query, max_results)
        return ToolResult(ok=True, output=_format_results(results))
    except Exception as exc:
        return ToolResult(ok=False, output=f"Search failed: {exc}")


@tool(
    "fetch_url",
    "Fetch a web page and extract its readable main text (strips navigation, "
    "ads and sidebars). Optionally list the links found on the page. Pages "
    "that render via JavaScript (SPAs) are automatically re-rendered with a "
    "headless browser when they contain almost no plain text.",
    {
        "url": {"type": "string", "description": "Fully-qualified URL to read."},
        "max_chars": {
            "type": "integer",
            "description": "Cap on returned characters (default 8000).",
        },
        "include_links": {
            "type": "boolean",
            "description": "Also return up to 30 links found on the page (default false).",
        },
        "links_limit": {
            "type": "integer",
            "description": "Max links when include_links is true (default 30).",
        },
    },
    required=["url"],
)
def fetch_url(
    url: str, max_chars: int = 8000, include_links: bool = False, links_limit: int = 30
) -> ToolResult:
    blocked = _offline_blocked()
    if blocked:
        return blocked
    try:
        resp = _get(url)
        text = extract_main_text(resp.text)
        if _looks_like_js_shell(resp.text, text):
            with suppress(Exception):  # headless rendering is best-effort
                rendered = _render_with_playwright(url)
                text = extract_main_text(rendered) or text
        output = text[:max_chars]
        if include_links:
            links = extract_links(resp.text, resp.url, limit=links_limit)
            output += "\n\n[Links on this page]\n" + "\n".join(f"- {u}" for u in links)
        return ToolResult(ok=True, output=output)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Fetch failed: {exc}")


@tool(
    "extract_links",
    "List the hyperlinks found on a web page — useful for site exploration "
    "and link discovery before fetching deeper pages.",
    {
        "url": {"type": "string", "description": "Fully-qualified URL to scan."},
        "max_links": {
            "type": "integer",
            "description": "Max links to return (default 30).",
        },
        "same_domain_only": {
            "type": "boolean",
            "description": "Only return links pointing at the same domain (default true).",
        },
    },
    required=["url"],
)
def extract_links_tool(url: str, max_links: int = 30, same_domain_only: bool = True) -> ToolResult:
    blocked = _offline_blocked()
    if blocked:
        return blocked
    try:
        resp = _get(url)
        links = extract_links(resp.text, resp.url, limit=max_links)
        if same_domain_only:
            host = urlparse(resp.url).netloc
            links = [u for u in links if urlparse(u).netloc == host]
        output = "\n".join(f"- {u}" for u in links) or "No links found."
        return ToolResult(ok=True, output=output)
    except Exception as exc:
        return ToolResult(ok=False, output=f"Fetch failed: {exc}")
