"""Workspace capability boundary for every agent-controlled filesystem path."""

from __future__ import annotations

import os
from pathlib import Path

from ..config import settings


class WorkspaceViolation(ValueError):
    """Raised when a requested path escapes the configured workspace."""


def workspace_root() -> Path:
    """Return the canonical workspace root."""
    return settings.assistant.workspace_dir.expanduser().resolve()


def resolve_workspace_path(path: str | os.PathLike[str], *, allow_root: bool = True) -> Path:
    """Resolve *path* and reject absolute, ``..`` and symlink escapes.

    Absolute paths are accepted only when they already point inside the configured
    workspace. This keeps existing callers working while making the workspace a
    real capability boundary instead of merely a default directory.
    """
    root = workspace_root()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise WorkspaceViolation(f"Path is outside the JAMES workspace ({root}): {path}") from exc
    if not allow_root and resolved == root:
        raise WorkspaceViolation("The workspace root cannot be used for this operation.")
    return resolved


def require_workspace_path(path: Path, *, allow_root: bool = True) -> Path:
    """Validate an already-created :class:`Path` against the workspace."""
    return resolve_workspace_path(str(path), allow_root=allow_root)
