"""Phase-5 release-engineering tests: auto-updater, eval metrics, CLI exit codes.

Covers ``james.updater`` (offline handling, version comparison, pip upgrade
path, frozen-install hint), the new ``Evaluator.summary`` metrics, and the
`--eval`/`--update` CLI exit-code contracts used by CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from james import __version__
from james.evaluation import Evaluator, TaskResult
from james.updater import _parse_version, apply_update, check_for_updates, update_url

# ---------------------------------------------------------------------------
# version comparison
# ---------------------------------------------------------------------------


def test_parse_version_strips_prefixes_and_suffixes() -> None:
    assert _parse_version("v1.2.3") == (1, 2, 3)
    assert _parse_version("1.2.3-rc1") == (1, 2, 3)
    assert _parse_version("garbage") == (0, 0, 0)
    assert _parse_version("") == (0, 0, 0)


def test_update_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_URL", "https://example.com/feed.json")
    assert update_url() == "https://example.com/feed.json"
    monkeypatch.delenv("UPDATE_URL")
    assert "api.github.com" in update_url()


# ---------------------------------------------------------------------------
# check_for_updates
# ---------------------------------------------------------------------------


def _release(tag: str) -> dict:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/Krish-1507/JAMES_Agent/releases/tag/{tag}",
        "published_at": "2026-08-01T00:00:00Z",
    }


def test_check_updates_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {"status_code": 200, "json.return_value": _release("v99.0.0")}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return fake["json.return_value"]

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    result = check_for_updates(url="https://example.com")
    assert result["ok"] is True
    assert result["up_to_date"] is False
    assert result["latest_version"] == "99.0.0"
    assert result["current_version"] == __version__


def test_check_updates_current(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return _release(f"v{__version__}")

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    assert check_for_updates(url="https://example.com")["up_to_date"] is True


def test_check_updates_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("james.updater.requests.get", boom)
    result = check_for_updates(url="https://example.com")
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_check_updates_offline_mode_skips_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.config import settings

    monkeypatch.setattr(settings.assistant, "offline_mode", True)

    def should_not_run(*a, **k):
        raise AssertionError("network touched in offline mode")

    monkeypatch.setattr("james.updater.requests.get", should_not_run)
    result = check_for_updates(url="https://example.com")
    assert result == {"ok": True, "up_to_date": True, "reason": "offline_mode"}


# ---------------------------------------------------------------------------
# apply_update
# ---------------------------------------------------------------------------


def test_apply_update_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return _release(f"v{__version__}")

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    result = apply_update(url="https://example.com")
    assert result["ok"] is True
    assert result["up_to_date"] is True


def test_apply_update_frozen_prints_download_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return _release("v99.0.0")

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("james.updater.is_frozen", lambda: True)
    result = apply_update(url="https://example.com")
    assert result["ok"] is True
    assert "download the installer" in result["message"].lower()


def test_apply_update_pip_path(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return _release("v99.0.0")

    class FakeProc:
        returncode = 0
        stdout = "installed"
        stderr = ""

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("james.updater.is_frozen", lambda: False)
    monkeypatch.setattr("james.updater.subprocess.run", lambda *a, **k: FakeProc())
    result = apply_update(url="https://example.com")
    assert result["ok"] is True
    assert "upgraded" in result["message"].lower()


def test_apply_update_pip_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return _release("v99.0.0")

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "Permission denied\n"

    monkeypatch.setattr("james.updater.requests.get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr("james.updater.is_frozen", lambda: False)
    monkeypatch.setattr("james.updater.subprocess.run", lambda *a, **k: FakeProc())
    result = apply_update(url="https://example.com")
    assert result["ok"] is False
    assert "Permission denied" in result["error"]


# ---------------------------------------------------------------------------
# TaskResult metrics
# ---------------------------------------------------------------------------


def test_evaluator_summary_includes_metrics() -> None:
    evaluator = Evaluator(output_dir=Path("."))
    evaluator._results = [
        TaskResult(
            task_id="t1",
            task_description="a",
            success=True,
            tool_calls=4,
            iterations=2,
            duration_seconds=1.0,
        ),
        TaskResult(
            task_id="t2",
            task_description="b",
            success=False,
            tool_calls=0,
            iterations=8,
            duration_seconds=3.0,
        ),
    ]
    summary = evaluator.summary()
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["pass_rate"] == 0.5
    assert summary["avg_tool_calls"] == 2.0
    assert summary["avg_iterations"] == 5.0
    assert summary["avg_duration"] == 2.0


def test_evaluator_summary_empty() -> None:
    evaluator = Evaluator(output_dir=Path("."))
    summary = evaluator.summary()
    assert summary["total"] == 0
    assert summary["passed"] == 0
    assert summary["pass_rate"] == 0.0


# ---------------------------------------------------------------------------
# CLI exit-code contracts
# ---------------------------------------------------------------------------


def _run_cli(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "james", *args],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        check=False,
    )


def test_cli_version_flag() -> None:
    proc = _run_cli("--version")
    assert proc.returncode == 0
    assert __version__ in proc.stdout


def test_cli_eval_smoke_exit_zero() -> None:
    proc = _run_cli("--eval", "smoke")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = json.loads(proc.stdout.strip())
    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert summary["avg_tool_calls"] > 0


def test_cli_update_check_exit_zero_when_current() -> None:
    # Hermetic: point the updater at a local fake release feed instead of the
    # live GitHub API (shared runner IPs hit the anonymous rate limit).
    import http.server
    import threading

    release = json.dumps({"tag_name": f"v{__version__}", "html_url": "https://example.invalid/release"})

    class _FakeReleases(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(release.encode())

        def log_message(self, *args) -> None:  # silence request logging
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeReleases)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        proc = _run_cli(
            "--update-check",
            env_extra={"UPDATE_URL": f"http://127.0.0.1:{server.server_port}/releases/latest"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_unknown_eval_suite_exit_two() -> None:
    proc = _run_cli("--eval", "nope")
    assert proc.returncode == 2
