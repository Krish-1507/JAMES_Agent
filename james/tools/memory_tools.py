"""Memory tools — JAMES remembers facts across sessions.

A lightweight, dependency-free store (JSONL) with keyword/embedding retrieval.
If ``sentence-transformers`` is installed it uses semantic search; otherwise it
falls back to TF-style keyword overlap. Swap in Chroma/FAISS later if you want.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..config import settings
from .base import ToolResult, tool

_EMBED = None


def _embedder():
    global _EMBED
    if _EMBED is not None:
        return _EMBED
    # Opt-in by default: only load when MEMORY_EMBEDDING is not explicitly "false".
    if os.getenv("MEMORY_EMBEDDING", "true").lower() in ("false", "0", "no", "off"):
        _EMBED = False
        return _EMBED
    try:
        from sentence_transformers import SentenceTransformer

        _EMBED = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _EMBED = False
    return _EMBED


def _load() -> list[dict]:
    path: Path = settings.assistant.memory_file
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _save(entries: list[dict]) -> None:
    settings.assistant.memory_file.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8"
    )


def _score(query: str, text: str) -> float:
    embedder = _embedder()
    if embedder:
        from numpy import dot
        from numpy.linalg import norm

        a = embedder.encode([query])[0]
        b = embedder.encode([text])[0]
        denom = norm(a) * norm(b)
        return float(dot(a, b) / denom) if denom else 0.0
    q = set(query.lower().split())
    t = set(text.lower().split())
    return len(q & t) / (len(q | t) + 1e-9)


@tool(
    "remember",
    "Store a fact, preference or piece of info into JAMES's long-term memory for later recall.",
    {"text": {"type": "string", "description": "The information to remember."}},
    required=["text"],
)
def remember(text: str) -> ToolResult:
    if not settings.assistant.memory_enabled:
        return ToolResult(ok=False, output="Memory is disabled (MEMORY_ENABLED=false).")
    entries = _load()
    entries.append({"ts": datetime.now().isoformat(timespec="seconds"), "text": text})
    _save(entries)
    return ToolResult(ok=True, output=f"Remembered ({len(entries)} entries stored).")


@tool(
    "recall",
    "Search JAMES's long-term memory for relevant info related to a query.",
    {
        "query": {"type": "string", "description": "What to recall."},
        "top_k": {"type": "integer", "description": "Number of results (default 3)."},
    },
    required=["query"],
)
def recall(query: str, top_k: int = 3) -> ToolResult:
    if not settings.assistant.memory_enabled:
        return ToolResult(ok=False, output="Memory is disabled (MEMORY_ENABLED=false).")
    entries = _load()
    if not entries:
        return ToolResult(ok=True, output="Memory is empty.")
    ranked = sorted(entries, key=lambda e: _score(query, e["text"]), reverse=True)[:top_k]
    body = "\n".join(f"- {e['text']}" for e in ranked)
    return ToolResult(ok=True, output=body)


def get_relevant_memories(query: str, top_k: int = 4) -> str:
    """Internal helper: return relevant memories as a block of text (or '')."""
    if not settings.assistant.memory_enabled:
        return ""
    entries = _load()
    if not entries:
        return ""
    ranked = sorted(entries, key=lambda e: _score(query, e["text"]), reverse=True)[:top_k]
    return "\n".join(f"- {e['text']}" for e in ranked)
