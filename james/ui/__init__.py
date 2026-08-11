"""Optional GUI package.

PyQt5 is an optional dependency; importing the package must not require it.
The default ``run_ui`` delegates to the Qt shell and falls back to browser
mode when Qt is not installed.
"""

from __future__ import annotations


def run_ui(port: int = 8124) -> int:
    from .shell import run_ui as _run_ui

    return _run_ui(port=port)


__all__ = ["run_ui"]
