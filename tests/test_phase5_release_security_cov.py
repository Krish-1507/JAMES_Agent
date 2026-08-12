"""Phase-5 coverage expansion for security-critical modules.

Targets branches in the offline egress guard (socket/httpx/urllib/urllib3/
requests patches), the isolation broker (all operation types, timeout and
invalid-output paths), secret-key handling, the tool registry's permission
logic, and the MCP argument sanitizer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# offline egress guard: guard.py
# ---------------------------------------------------------------------------


def test_guard_host_parse_and_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    assert guard._host_of(("127.0.0.1", 80)) == ("127.0.0.1", 80)
    assert guard._host_of("127.0.0.1") == ("127.0.0.1", 0)
    assert guard._is_loopback("127.0.0.1") is True
    assert guard._is_loopback("localhost") is True
    assert guard._is_loopback("::1") is True
    assert guard._is_loopback("8.8.8.8") is False

    # hostname that fails ipaddress parsing but resolves to loopback
    monkeypatch.setattr(
        guard, "_orig_getaddrinfo", lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    assert guard._is_loopback("myhost") is True
    monkeypatch.setattr(
        guard,
        "_orig_getaddrinfo",
        lambda host, *a, **k: [(_ for _ in ()).throw(RuntimeError("no dns"))],
    )
    assert guard._is_loopback("myhost") is False


def test_guard_getaddrinfo_blocks_non_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    loopback_info = [(2, 1, 6, "", ("127.0.0.1", 0))]

    def fake_getaddrinfo(host, *a, **k):
        return loopback_info if host == "localhost" else [("evil",)]

    monkeypatch.setattr(guard, "_orig_getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(guard, "_audit", lambda host, port, allowed: None)
    assert guard._guarded_getaddrinfo("localhost", 80) == loopback_info
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_getaddrinfo("evil.com", 443)
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_getaddrinfo("evil.com", None)


def test_guard_connect_allow_loopback_and_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    class FakeSocket:
        def __init__(self):
            self.calls = 0

        def connect(self, address, *a, **k):
            self.calls += 1
            return address

    monkeypatch.setattr(
        guard, "_orig_connect", lambda self, address, *a, **k: self.connect(address)
    )
    sock = FakeSocket()
    assert guard._guarded_connect(sock, ("127.0.0.1", 8000)) == ("127.0.0.1", 8000)
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_connect(sock, ("1.2.3.4", 80))


def test_guard_create_connection_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    monkeypatch.setattr(guard, "_orig_create", lambda *a, **k: "connected")
    assert guard._guarded_create(("127.0.0.1", 8000)) == "connected"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_create(("1.2.3.4", 80))
    # no args -> pass through
    assert guard._guarded_create() == "connected"


def test_guard_httpx_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    monkeypatch.setattr(guard, "_orig_httpx_request", lambda self, url, **k: "ok")
    assert guard._guarded_httpx_request(object(), "http://localhost/x") == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_httpx_request(object(), "https://evil.com/x")

    class FakeRequest:
        url = type("U", (), {"host": "localhost", "port": 443})()

    class FakeRequestBad:
        url = type("U", (), {"host": "evil.com", "port": 443})()

    monkeypatch.setattr(guard, "_orig_httpx_send", lambda self, request, **k: "ok")
    assert guard._guarded_httpx_send(object(), FakeRequest()) == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_httpx_send(object(), FakeRequestBad())


def test_guard_http_client_urllib_urllib3_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    monkeypatch.setattr(
        guard,
        "_orig_http_client_request",
        lambda self, method, url, body=None, headers=None, **k: "ok",
    )
    assert guard._guarded_http_client_request(object(), "GET", "http://localhost/") == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_http_client_request(object(), "GET", "http://evil.com/")

    monkeypatch.setattr(guard, "_orig_urllib_request", lambda url, **k: "ok")
    assert guard._guarded_urllib_request("http://localhost/") == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_urllib_request("http://evil.com/")

    monkeypatch.setattr(guard, "_orig_urllib3_request", lambda self, url, **k: "ok")
    assert guard._guarded_urllib3_request(object(), "http://localhost/") == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_urllib3_request(object(), "http://evil.com/")

    monkeypatch.setattr(guard, "_orig_requests_request", lambda self, url, **k: "ok")
    assert guard._guarded_requests_request(object(), "http://localhost/") == "ok"
    with pytest.raises(guard.BlockedEgress):
        guard._guarded_requests_request(object(), "http://evil.com/")


def test_guard_audit_writes_log(monkeypatch: pytest.MonkeyPatch, isolated_workspace: Path) -> None:
    from james.core import guard

    monkeypatch.setattr(
        guard.settings.assistant, "egress_audit_log", isolated_workspace / "egress.log"
    )
    guard._audit("example.com", 443, False)
    guard._audit("127.0.0.1", 8000, True)
    lines = (isolated_workspace / "egress.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "BLOCK | example.com:443" in lines[0]
    assert "ALLOW | 127.0.0.1:8000" in lines[1]


def test_guard_audit_survives_write_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import guard

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    guard._audit("example.com", 443, False)  # must not raise


def test_guard_install_is_idempotent_and_restores(monkeypatch: pytest.MonkeyPatch) -> None:
    """install_offline_guard() patches and, on repeat calls, is a no-op."""
    import socket

    from james.core import guard

    orig_connect = socket.socket.connect
    orig_getaddrinfo = socket.getaddrinfo
    try:
        monkeypatch.setattr(guard, "_INSTALLED", False)
        monkeypatch.setattr(guard, "_LOCK", guard.threading.RLock())
        guard.install_offline_guard()
        assert guard._INSTALLED is True
        assert socket.socket.connect is guard._guarded_connect
        # idempotent second install leaves patches in place
        guard.install_offline_guard()
        assert socket.socket.connect is guard._guarded_connect
        with pytest.raises(guard.BlockedEgress):
            guard._guarded_connect(object(), ("8.8.8.8", 53))
    finally:
        socket.socket.connect = orig_connect
        socket.getaddrinfo = orig_getaddrinfo
        monkeypatch.setattr(guard, "_INSTALLED", False)


def test_guard_install_skips_missing_optional_libs(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from james.core import guard

    real_import = builtins.__import__
    requested: list[str] = []

    def fake_import(name, *a, **k):
        requested.append(name)
        if name in ("httpx", "http.client", "urllib.request", "urllib3"):
            raise ImportError(f"no {name}")
        if name == "requests":
            return real_import("requests")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(guard, "_INSTALLED", False)
    monkeypatch.setattr(guard, "_LOCK", guard.threading.RLock())
    guard.install_offline_guard()
    assert guard._INSTALLED is True
    assert "requests" in requested
    monkeypatch.setattr(guard, "_INSTALLED", False)


def test_guard_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.config import settings
    from james.core import guard

    monkeypatch.setattr(settings.assistant, "offline_mode", True)
    assert guard.is_offline() is True
    monkeypatch.setattr(settings.assistant, "offline_mode", False)
    assert guard.is_offline() is False


# ---------------------------------------------------------------------------
# isolation broker: isolation.py
# ---------------------------------------------------------------------------


def test_isolation_inside_path_checks() -> None:
    from james.core.isolation import _inside

    root = str(Path("/ws").resolve())
    assert _inside(root, "/ws/file.txt") == Path("/ws/file.txt").resolve()
    with pytest.raises(ValueError):
        _inside(root, "/etc/passwd")
    with pytest.raises(ValueError):
        _inside(root, "/ws")  # root itself refused


def test_isolation_execute_unknown_operation() -> None:
    from james.core.isolation import _execute

    with pytest.raises(ValueError, match="Unknown isolated operation"):
        _execute("hack", {})


def test_isolation_execute_command(tmp_path: Path) -> None:
    from james.core.isolation import _execute

    script = tmp_path / "hello.py"
    script.write_text("print('hi')", encoding="utf-8")
    result = _execute("command", {"args": ["python", str(script)], "workspace": str(tmp_path)})
    assert result["ok"] is True
    assert "hi" in result["output"]

    result = _execute(
        "command", {"args": ["python", "-c", "raise SystemExit(3)"], "workspace": str(tmp_path)}
    )
    assert result["ok"] is False


def test_isolation_execute_trash_restore_roundtrip(tmp_path: Path) -> None:
    from james.core.isolation import _execute

    source = tmp_path / "doc.txt"
    source.write_text("x", encoding="utf-8")
    trash = tmp_path / ".trash"
    result = _execute(
        "trash", {"workspace": str(tmp_path), "path": str(source), "trash": str(trash)}
    )
    assert result["ok"] is True
    assert not source.exists()
    trashed = Path(result["data"]["trashed"])
    assert trashed.exists()

    result = _execute(
        "restore",
        {"workspace": str(tmp_path), "trashed": str(trashed), "original": str(source)},
    )
    assert result["ok"] is True
    assert source.exists()

    # restore when source missing -> error
    trashed2 = tmp_path / ".trash" / "gone.txt"
    result = _execute(
        "restore",
        {
            "workspace": str(tmp_path),
            "trashed": str(trashed2),
            "original": str(tmp_path / "gone.txt"),
        },
    )
    assert result["ok"] is False

    # restore when destination exists -> error
    result = _execute(
        "restore",
        {"workspace": str(tmp_path), "trashed": str(trashed), "original": str(source)},
    )
    assert result["ok"] is False


def test_isolation_execute_trash_missing_source(tmp_path: Path) -> None:
    from james.core.isolation import _execute

    result = _execute(
        "trash",
        {
            "workspace": str(tmp_path),
            "path": str(tmp_path / "missing.txt"),
            "trash": str(tmp_path / ".trash"),
        },
    )
    assert result["ok"] is False
    assert "does not exist" in result["output"]


def test_isolation_execute_plugin_generated(tmp_path: Path, plugin_dir: Path) -> None:
    from james.core.isolation import _execute
    from james.tools.forge_tools import _persist_skill

    skill_source = (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "@tool('triple', 'Triple a number.', {'n': {'type': 'integer'}}, required=['n'])\n"
        "def triple(n: int) -> ToolResult:\n"
        "    return ToolResult(ok=True, output=str(n * 3))\n"
    )
    assert _persist_skill("triple", skill_source).ok is True
    plugin_path = plugin_dir / "triple.py"

    result = _execute(
        "plugin",
        {"path": str(plugin_path), "name": "triple", "arguments": {"n": 4}, "trusted": False},
    )
    assert result["ok"] is True
    assert result["output"] == "12"


def test_isolation_execute_plugin_trusted_missing(tmp_path: Path) -> None:
    from james.core.isolation import _execute

    with pytest.raises(OSError):
        _execute("plugin", {"path": str(tmp_path / "nope.py"), "name": "x", "trusted": True})


def test_isolation_execute_plugin_unknown_tool(tmp_path: Path, plugin_dir: Path) -> None:
    from james.core.isolation import _execute
    from james.tools.forge_tools import _persist_skill

    skill_source = (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "@tool('only_one', 'Does one thing.', {})\n"
        "def only_one() -> ToolResult:\n"
        "    return ToolResult(ok=True, output='done')\n"
    )
    assert _persist_skill("only_one", skill_source).ok is True
    with pytest.raises(ValueError, match="was not found"):
        _execute(
            "plugin",
            {
                "path": str(plugin_dir / "only_one.py"),
                "name": "missing_tool",
                "arguments": {},
                "trusted": False,
            },
        )


def test_isolation_execute_plugin_delete(tmp_path: Path, plugin_dir: Path) -> None:
    from james.core.isolation import _execute
    from james.tools.forge_tools import _persist_skill

    skill_source = (
        "# JAMES-GENERATED-SKILL v1\n"
        "from james.tools.base import tool, ToolResult\n"
        "@tool('todel', 'X.', {})\n"
        "def todel() -> ToolResult:\n"
        "    return ToolResult(ok=True, output='x')\n"
    )
    assert _persist_skill("todel", skill_source).ok is True
    target = plugin_dir / "todel.py"
    result = _execute("plugin_delete", {"plugin_root": str(plugin_dir), "path": str(target)})
    assert result["ok"] is True
    assert not target.exists()

    with pytest.raises(ValueError, match="Only Python plugin files"):
        _execute(
            "plugin_delete",
            {"plugin_root": str(plugin_dir), "path": str(plugin_dir / "evil.txt")},
        )
    with pytest.raises(ValueError, match="escaped"):
        _execute(
            "plugin_delete",
            {"plugin_root": str(plugin_dir), "path": str(plugin_dir.parent / "outside.py")},
        )


def test_isolation_run_isolated_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import isolation

    def slow(*a, **k):
        raise isolation.subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(isolation.subprocess, "run", slow)
    result = isolation.run_isolated("command", {"args": ["sleep"], "workspace": "."}, timeout=1)
    assert result["ok"] is False
    assert "timed out" in result["output"]


def test_isolation_run_isolated_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import isolation

    class FakeProc:
        returncode = 7
        stdout = ""

    monkeypatch.setattr(isolation.subprocess, "run", lambda *a, **k: FakeProc())
    result = isolation.run_isolated("command", {"args": ["x"], "workspace": "."})
    assert result["ok"] is False
    assert "without a result" in result["output"]


def test_isolation_run_isolated_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import isolation

    class FakeProc:
        returncode = 0
        stdout = "not json at all"

    monkeypatch.setattr(isolation.subprocess, "run", lambda *a, **k: FakeProc())
    result = isolation.run_isolated("command", {"args": ["x"], "workspace": "."})
    assert result["ok"] is False
    assert "invalid result" in result["output"]


def test_isolation_run_isolated_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import isolation

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"ok": True, "output": "fine"})

    monkeypatch.setattr(isolation.subprocess, "run", lambda *a, **k: FakeProc())
    result = isolation.run_isolated("command", {"args": ["x"], "workspace": "."})
    assert result == {"ok": True, "output": "fine"}


def test_isolation_limit_child_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from james.core import isolation

    monkeypatch.setattr(isolation.os, "name", "nt")
    isolation._limit_child()  # returns immediately on Windows


def test_isolation_limit_child_posix_no_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from james.core import isolation

    monkeypatch.setattr(isolation.os, "name", "posix")
    real_import = builtins.__import__

    def no_resource_import(name, *a, **k):
        if name == "resource":
            raise ImportError("no resource on this platform")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_resource_import)
    isolation._limit_child()  # must swallow ImportError


def test_isolation_worker_bootstrap_runs() -> None:
    from james.core.isolation import _worker_bootstrap

    assert "def main() -> int:" in _worker_bootstrap()
    assert "james.core.isolation" in _worker_bootstrap()


# ---------------------------------------------------------------------------
# secret keys: secrets.py
# ---------------------------------------------------------------------------


def test_secret_env_var_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from james.core.secrets import load_or_create_secret

    monkeypatch.setenv("JAMES_TEST_KEY", "from-env")
    assert load_or_create_secret("JAMES_TEST_KEY", tmp_path / "key.bin") == b"from-env"


def test_secret_created_and_reused(tmp_path: Path) -> None:
    from james.core.secrets import load_or_create_secret

    path = tmp_path / "keys" / "secret.bin"
    first = load_or_create_secret("JAMES_TEST_KEY2", path, length=32)
    assert len(first) == 32
    assert load_or_create_secret("JAMES_TEST_KEY2", path, length=32) == first
    assert path.exists()


def test_secret_reused_from_existing_file(tmp_path: Path) -> None:
    import base64
    import os

    from james.core.secrets import load_or_create_secret

    path = tmp_path / "secret.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(base64.urlsafe_b64encode(b"x" * 32).decode("ascii") + "\n", encoding="ascii")
    if os.name != "nt":
        os.chmod(path, 0o600)
    secret = load_or_create_secret("JAMES_TEST_KEY3", path, length=32)
    assert secret == b"x" * 32


def test_secret_corrupt_file_raises(tmp_path: Path) -> None:
    from james.core.secrets import load_or_create_secret

    path = tmp_path / "secret.bin"
    path.write_text("not base64!!!", encoding="ascii")
    with pytest.raises(RuntimeError, match="Could not read secret key"):
        load_or_create_secret("JAMES_TEST_KEY4", path)


def test_secret_creation_race_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import builtins

    from james.core import secrets

    path = tmp_path / "secret.bin"

    real_open = builtins.open

    def failing_open(path_like, flags, *a, **k):
        if "JAMES_TEST_KEY5" in str(path_like) and getattr(failing_open, "first", True):
            failing_open.first = False
            raise FileExistsError
        return real_open(path_like, flags, *a, **k)

    # os.open signature: os.open(path, flags, mode)
    real_os_open = secrets.os.open

    def fake_os_open(p, flags, mode=0o777):
        if getattr(fake_os_open, "first", True):
            fake_os_open.first = False
            raise FileExistsError
        return real_os_open(p, flags, mode)

    monkeypatch.setattr(secrets.os, "open", fake_os_open)
    monkeypatch.setattr(
        secrets, "secrets", type("S", (), {"token_bytes": staticmethod(lambda n: b"k" * n)})()
    )
    secret = secrets.load_or_create_secret("JAMES_TEST_KEY5", path, length=32)
    assert secret == b"k" * 32


# ---------------------------------------------------------------------------
# tool registry permission logic: registry.py
# ---------------------------------------------------------------------------


def test_registry_dangerous_tool_classification() -> None:
    from james.tools.registry import is_dangerous_tool_call

    assert is_dangerous_tool_call("run_shell_command", {}) is True
    assert is_dangerous_tool_call("delete_file", {"path": "/x"}) is True
    assert is_dangerous_tool_call("web_search", {}) is False


def test_registry_help_command() -> None:
    from james.tools.registry import help_command

    result = help_command.run()
    assert result.ok is True
    assert "Available tools" in result.output or "tools" in result.output.lower()


def test_registry_hmac_key_stable_and_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import james.tools.registry as registry

    monkeypatch.setattr(registry.settings.assistant, "audit_log", tmp_path / "audit.log")
    monkeypatch.setattr(registry.settings.assistant, "workspace_dir", tmp_path)
    first = registry._audit_hmac_key()
    second = registry._audit_hmac_key()
    assert first == second
    assert len(first) >= 32
    assert (tmp_path / ".james_audit_hmac.key").exists()
