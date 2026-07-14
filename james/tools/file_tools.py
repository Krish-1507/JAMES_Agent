"""File-system tools: read, write, search and explore the user's files."""
from __future__ import annotations

from pathlib import Path
from typing import List

from ..config import settings
from .base import Tool, ToolResult, tool


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = settings.assistant.workspace_dir / p
    return p


@tool(
    "read_file",
    "Read the full text content of a file on the user's computer. Supports txt, md, json, csv, py, code and most text files.",
    {
        "path": {"type": "string", "description": "Path to the file (absolute or relative to the workspace)."},
        "max_lines": {"type": "integer", "description": "Optional cap on number of lines returned."},
    },
    required=["path"],
)
def read_file(path: str, max_lines: int = 0) -> ToolResult:
    p = _resolve(path)
    if not p.exists():
        return ToolResult(ok=False, output=f"File not found: {p}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Cannot read file: {exc}")
    if max_lines:
        text = "\n".join(text.splitlines()[:max_lines])
    return ToolResult(ok=True, output=text[:20000])


@tool(
    "write_file",
    "Write text content to a file, creating directories as needed. Use this to save documents, code, notes or any text.",
    {
        "path": {"type": "string", "description": "Destination path."},
        "content": {"type": "string", "description": "Full text content to write."},
    },
    required=["path", "content"],
)
def write_file(path: str, content: str) -> ToolResult:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return ToolResult(ok=True, output=f"Wrote {len(content)} chars to {p}")


@tool(
    "list_directory",
    "List the files and folders inside a directory.",
    {"path": {"type": "string", "description": "Directory path (defaults to the workspace)."}},
)
def list_directory(path: str = "") -> ToolResult:
    p = _resolve(path) if path else settings.assistant.workspace_dir
    if not p.exists():
        return ToolResult(ok=False, output=f"Directory not found: {p}")
    entries = sorted((f.name + ("/" if f.is_dir() else "")) for f in p.iterdir())
    return ToolResult(ok=True, output="\n".join(entries) or "(empty)")


@tool(
    "search_files",
    "Recursively search file contents for a keyword and return matching lines with file paths.",
    {
        "pattern": {"type": "string", "description": "Text or regex pattern to search for."},
        "path": {"type": "string", "description": "Directory to search in (defaults to workspace)."},
    },
    required=["pattern"],
)
def search_files(pattern: str, path: str = "") -> ToolResult:
    import re

    base = _resolve(path) if path else settings.assistant.workspace_dir
    regex = re.compile(pattern, re.IGNORECASE)
    hits: List[str] = []
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".docx", ".pptx", ".exe", ".dll"}:
            continue
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{f}:{i}: {line.strip()}")
                    if len(hits) >= 50:
                        break
        except Exception:
            continue
        if len(hits) >= 50:
            break
    return ToolResult(ok=True, output="\n".join(hits) or "No matches found.")


@tool(
    "delete_file",
    "Permanently delete a file or empty directory. Destructive — only use when the user explicitly asks.",
    {"path": {"type": "string", "description": "Path of the file/directory to delete."}},
    required=["path"],
)
def delete_file(path: str) -> ToolResult:
    p = _resolve(path)
    if not p.exists():
        return ToolResult(ok=False, output="Path does not exist.")
    if p.is_dir():
        p.rmdir()
    else:
        p.unlink()
    return ToolResult(ok=True, output=f"Deleted {p}")
