"""Temporary CI diagnostic: log process metrics at failure + every 25th test (delete later)."""

from __future__ import annotations

import os

import pytest

_LOG = "/tmp/diag.log"
_COUNT = 0


def _metrics() -> str:
    rss = vsz = threads = -1
    with open("/proc/self/status") as fh:
        for ln in fh:
            if ln.startswith("VmRSS:"):
                rss = int(ln.split()[1])
            elif ln.startswith("VmSize:"):
                vsz = int(ln.split()[1])
            elif ln.startswith("Threads:"):
                threads = int(ln.split()[1])
    return (
        f"pid={os.getpid()} RSS={rss}KB VSZ={vsz}KB threads={threads} "
        f"fds={len(os.listdir('/proc/self/fd'))}"
    )


def _maps() -> int:
    with open("/proc/self/maps") as fh:
        return sum(1 for _ in fh)


def _log(msg: str) -> None:
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def pytest_runtest_protocol(item: "pytest.Item", nextitem: "pytest.Item | None") -> None:
    global _COUNT
    _COUNT += 1
    nodeid = item.nodeid
    if _COUNT % 25 == 0 or "test_phase5_server_ui" in nodeid or "TestAgentConfirmation" in nodeid:
        _log(f"[DIAG] {nodeid} {_metrics()} maps={_maps()}")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.failed or report.error:
        _log(f"[DIAGFAIL] {report.nodeid} {_metrics()} maps={_maps()}")