"""Temporary CI diagnostic: track RSS / fd / map growth during the suite (delete later)."""

from __future__ import annotations

import os

import pytest


def _rss_kb() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _vmaps() -> int:
    return sum(1 for _ in open("/proc/self/maps"))


_AT = {25, 50, 75, 100, 150, 200, 250, 300, 330, 350, 360, 370}


def pytest_runtest_protocol(item: "pytest.Item", nextitem: "pytest.Item | None") -> None:
    nodeid = item.nodeid
    if "test_phase5_server_ui" in nodeid and nodeid not in _AT:
        return
    _AT.discard(nodeid)
    if nodeid not in _AT and "server_ui" not in nodeid:
        return
    print(
        f"[DIAG] {nodeid} RSS={_rss_kb()}KB fds={_fds()} maps={_vmaps()}",
        flush=True,
    )
