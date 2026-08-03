"""Optional GUI package.

PyQt5 is an optional dependency; importing the package must not require it.
"""


def run_ui(*args, **kwargs):
    from .desktop import run_ui as _run_ui

    return _run_ui(*args, **kwargs)


__all__ = ["run_ui"]
