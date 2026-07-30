"""Research and self-learning tools.

JAMES can look things up on the web, synthesise an answer, and — crucially —
*learn new skills*: it researches a goal, asks the model to write a native
@tool plugin that implements what it learned, and persists it to plugins/ so the
capability is available directly next time. This is the self-improving loop:
research -> understand -> turn it into executable code.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..config import settings
from ..llm.base import LLMProvider
from .base import Tool, ToolResult, tool
from .web_tools import fetch_url, web_search

_context = {"llm": None}


def configure_research(llm: LLMProvider) -> None:
    _context["llm"] = llm


def _gather_sources(query: str, max_sources: int) -> List[str]:
    """Run a web search and fetch the top result pages; return raw text snippets."""
    parts: List[str] = []
    search = web_search(query, max_results=max(3, min(max_sources, 8)))
    if not search.ok:
        return parts
    urls = _extract_urls(search.output)
    for url in urls[:max_sources]:
        try:
            res = fetch_url(url, max_chars=4000)
            if res.ok:
                parts.append(f"[source: {url}]\n{res.output}")
        except Exception:
            continue
    return parts


_DDG_REDIRECT_RE = re.compile(r'^/l/\?uddg=(.+)$', re.IGNORECASE)
_URL_RE = re.compile(r'https?://[^\s<>"]+')


def _extract_urls(text: str) -> List[str]:
    urls: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("•"):
            continue
        m = _DDG_REDIRECT_RE.match(line)
        if m:
            from urllib.parse import unquote

            decoded = unquote(m.group(1))
            if decoded.startswith("http://") or decoded.startswith("https://"):
                urls.append(decoded)
            continue
        if line.startswith("http://") or line.startswith("https://"):
            urls.append(line)
            continue
        for candidate in _URL_RE.findall(line):
            urls.append(candidate)
    seen: set[str] = set()
    unique: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


@tool(
    "research",
    "Look up a topic on the web, read the top sources, and return a concise synthesized answer "
    "with citations. Use this when you need current facts, how-tos, or context before acting.",
    {
        "query": {"type": "string", "description": "What to research."},
        "max_sources": {"type": "integer", "description": "How many pages to read (default 3)."},
    },
    required=["query"],
)
def research(query: str, max_sources: int = 3) -> ToolResult:
    if _context["llm"] is None:
        return ToolResult(ok=False, output="Research is not configured (no LLM provider).")
    if settings.assistant.offline_mode:
        return ToolResult(ok=False, output="Research needs the web; offline mode is on.")
    sources = _gather_sources(query, max_sources)
    if not sources:
        return ToolResult(ok=False, output="No sources could be retrieved for that query.")
    prompt = (
        "You are JAMES's research analyst. Using the sources below, answer the user's question "
        "clearly and concisely. Cite sources by their URL where relevant.\n\n"
        f"QUESTION: {query}\n\nSOURCES:\n" + "\n\n".join(sources)
    )
    try:
        resp = _context["llm"].chat([{"role": "user", "content": prompt}])
        return ToolResult(ok=True, output=resp.content or "(no answer)")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Research synthesis failed: {exc}")


@tool(
    "learn_skill",
    "Research a goal on the web, then autonomously write and save a native JAMES @tool plugin that "
    "implements it. The new skill is hot-loaded and available immediately — JAMES teaches itself new "
    "capabilities instead of re-prompting. Returns the saved skill name.",
    {
        "goal": {"type": "string", "description": "What the new skill should be able to do."},
        "skill_name": {
            "type": "string",
            "description": "Optional skill name (letters/digits/underscores). Derived from goal if omitted.",
        },
    },
    required=["goal"],
)
def learn_skill(goal: str, skill_name: str = "") -> ToolResult:
    if _context["llm"] is None:
        return ToolResult(ok=False, output="Learning is not configured (no LLM provider).")
    # 1) Research the goal for context (skipped gracefully in offline mode).
    context_blob = ""
    if not settings.assistant.offline_mode:
        sources = _gather_sources(goal, 3)
        if sources:
            context_blob = "\n\nReference material found on the web:\n" + "\n\n".join(sources[:3])

    # 2) Ask the model to write a @tool plugin implementing the goal.
    prompt = (
        "You are JAMES's self-improvement engine. Write ONE reusable JAMES tool — a @tool-decorated "
        "Python function with clear JSON-schema parameters — that fulfils this goal:\n"
        f'"""{goal}"""\n'
        "Import only from james.tools.base (the `tool` decorator). Keep it safe, typed and "
        "self-contained. If the goal needs external data, prefer reading it via the existing tools "
        "(web_search/fetch_url) or local files. Return ONLY the Python code, no explanation.\n"
        f"{context_blob}"
    )
    try:
        resp = _context["llm"].chat([{"role": "user", "content": prompt}])
    except Exception as exc:
        return ToolResult(ok=False, output=f"Learning failed while generating code: {exc}")

    import re

    code = resp.content
    m = re.search(r"```(?:python)?\s*(.*?)```", code, re.DOTALL)
    if m:
        code = m.group(1).strip()
    elif "@tool" not in code and "def " not in code:
        return ToolResult(ok=False, output="Learning produced no usable code.")

    # 3) Persist + hot-load via the shared Skill Forge helper.
    from .forge_tools import _persist_skill

    name = skill_name or "_".join(re.findall(r"[a-z0-9]+", goal.lower())[:4]) or "skill"
    return _persist_skill(name, code, description=f"Learned from goal: {goal[:80]}")
