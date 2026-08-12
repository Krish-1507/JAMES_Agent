"""Temporary CI diagnostic: track RSS / fd / map growth during the suite (delete later)."""

from __future__ import annotations

import os

import pytest

_LOG = "/tmp/diag.log"
_PREV = {}


def _line() -> str:
    rss = -1
    threads = -1
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith("VmRSS:"):
                rss = int(ln.split()[1])
            elif ln.startswith("Threads:"):
                threads = int(ln.split()[1])
    return f"RSS={rss}KB threads={threads} fds={len(os.listdir('/proc/self/fd'))}"


def _maps() -> int:
    with open("/proc/self/maps") as fh:
        return sum(1 for _ in fh)


def _log(msg: str) -> None:
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def pytest_runtest_protocol(item: "pytest.Item", nextitem: "pytest.Item | None") -> None:
    nodeid = item.nodeid
    key = _line()
    if key in _PREV:
        _PREV.pop(key)
    _PREV[nodeid] = key
    if len(_PREV) >= 25:
        _PREV.clear()
        _log(f"[DIAG25] {nodeid} {_line()} maps={_maps()}")
    if "test_phase5_server_ui" in nodeid:
        _log(f"[DIAGSERVER] {nodeid} {_line()} maps={_maps()}")
