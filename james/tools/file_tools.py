"""File-system tools: read, write, search and explore the user's files."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import settings
from ..core.isolation import run_isolated
from ..core.workspace import resolve_workspace_path, workspace_root
from .base import ToolResult, tool


def _resolve(path: str) -> Path:
    return resolve_workspace_path(path)


def _trash_receipt() -> Path:
    return workspace_root() / ".james_trash" / "last.json"


@tool(
    "read_file",
    "Read the full text content of a file on the user's computer. Supports txt, md, json, csv, py, code and most text files.",
    {
        "path": {
            "type": "string",
            "description": "Path to the file (absolute or relative to the workspace).",
        },
        "max_lines": {
            "type": "integer",
            "description": "Optional cap on number of lines returned.",
        },
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
        "path": {
            "type": "string",
            "description": "Directory to search in (defaults to workspace).",
        },
    },
    required=["pattern"],
)
def search_files(pattern: str, path: str = "") -> ToolResult:
    import re

    base = _resolve(path) if path else settings.assistant.workspace_dir
    regex = re.compile(pattern, re.IGNORECASE)
    hits: list[str] = []
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".pdf",
            ".docx",
            ".pptx",
            ".exe",
            ".dll",
        }:
            continue
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{f}:{i}: {line.strip()}")
                    if len(hits) >= 50:
                        break
        except Exception:  # nosec B112 - unreadable file must not abort the whole search
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
    p = resolve_workspace_path(path, allow_root=False)
    receipt = _trash_receipt()
    result = run_isolated(
        "trash",
        {"workspace": str(workspace_root()), "path": str(p), "trash": str(receipt.parent)},
        timeout=30,
    )
    if result.get("ok") and result.get("data"):
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result["data"], indent=2), encoding="utf-8")
    return ToolResult(
        ok=bool(result.get("ok")),
        output=str(result.get("output", "Delete operation failed.")),
        data=result.get("data"),
    )


@tool(
    "restore_last_deleted",
    "Restore the most recent item moved to JAMES' recoverable workspace trash.",
    {},
)
def restore_last_deleted() -> ToolResult:
    receipt = _trash_receipt()
    if not receipt.exists():
        return ToolResult(ok=False, output="There is no recent deleted item to restore.")
    try:
        record = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return ToolResult(ok=False, output=f"Recovery record is unreadable: {exc}")
    result = run_isolated(
        "restore",
        {
            "workspace": str(workspace_root()),
            "trashed": record["trashed"],
            "original": record["original"],
        },
        timeout=30,
    )
    if result.get("ok"):
        receipt.unlink(missing_ok=True)
    return ToolResult(
        ok=bool(result.get("ok")), output=str(result.get("output", "Restore failed."))
    )


@tool(
    "create_directory",
    "Create a directory and any missing parent directories.",
    {"path": {"type": "string", "description": "Directory path to create."}},
    required=["path"],
)
def create_directory(path: str) -> ToolResult:
    p = _resolve(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return ToolResult(ok=True, output=f"Created directory {p}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Could not create directory: {exc}")


@tool(
    "copy_file",
    "Copy a file or directory to a new location (directories are copied recursively).",
    {
        "src": {"type": "string", "description": "Source path."},
        "dst": {"type": "string", "description": "Destination path."},
    },
    required=["src", "dst"],
)
def copy_file(src: str, dst: str) -> ToolResult:
    import shutil

    s, d = _resolve(src), _resolve(dst)
    if not s.exists():
        return ToolResult(ok=False, output=f"Source not found: {s}")
    try:
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
        return ToolResult(ok=True, output=f"Copied {s} -> {d}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Copy failed: {exc}")


@tool(
    "move_file",
    "Move or rename a file/directory to a new location. Destructive if it overwrites.",
    {
        "src": {"type": "string", "description": "Source path."},
        "dst": {"type": "string", "description": "Destination path."},
    },
    required=["src", "dst"],
)
def move_file(src: str, dst: str) -> ToolResult:
    import shutil

    s, d = _resolve(src), _resolve(dst)
    if not s.exists():
        return ToolResult(ok=False, output=f"Source not found: {s}")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
        return ToolResult(ok=True, output=f"Moved {s} -> {d}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Move failed: {exc}")


@tool(
    "rename_file",
    "Rename a file or directory (within the same parent folder).",
    {
        "path": {"type": "string", "description": "Current path."},
        "new_name": {"type": "string", "description": "New name (not a full path)."},
    },
    required=["path", "new_name"],
)
def rename_file(path: str, new_name: str) -> ToolResult:
    p = _resolve(path)
    if not p.exists():
        return ToolResult(ok=False, output="Path does not exist.")
    dst = p.parent / new_name
    try:
        p.rename(dst)
        return ToolResult(ok=True, output=f"Renamed {p.name} -> {new_name}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Rename failed: {exc}")


@tool(
    "directory_tree",
    "Show a tree view of a directory's contents (files and folders), up to a max depth.",
    {
        "path": {"type": "string", "description": "Directory path (defaults to workspace)."},
        "max_depth": {
            "type": "integer",
            "description": "Maximum folder depth to display (default 3).",
        },
    },
)
def directory_tree(path: str = "", max_depth: int = 3) -> ToolResult:
    base = _resolve(path) if path else settings.assistant.workspace_dir
    if not base.exists():
        return ToolResult(ok=False, output=f"Directory not found: {base}")
    lines: list[str] = []

    def _walk(d: Path, depth: int, prefix: str):
        if depth > max_depth:
            return
        try:
            items = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return
        for i, item in enumerate(items):
            last = i == len(items) - 1
            branch = "+-- " if last else "|-- "
            lines.append(f"{prefix}{branch}{item.name}{'/' if item.is_dir() else ''}")
            if item.is_dir():
                _walk(item, depth + 1, prefix + ("    " if last else "|   "))

    lines.append(base.name + "/")
    _walk(base, 1, "")
    return ToolResult(ok=True, output="\n".join(lines) or "(empty)")
