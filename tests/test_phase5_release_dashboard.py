"""Phase-5 tests for the legacy ``--web-dashboard`` HTTP surface.

Covers the real HTTP server: status/tools/history/mcp/memory/export GETs,
the per-tool Toggle endpoint that used to 404 (``POST /api/tools/<name>``),
permission updates, and the MCP enable/disable/toggle actions.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from james.config import settings
from james.ui import dashboard as dash


class _Server:
    """Run the dashboard handler on an ephemeral port; returns a client."""

    def __init__(self) -> None:
        self.httpd = None
        self.thread = None
        self.port = 0
        self._start()

    def _start(self) -> None:
        self.httpd = dash.HTTPServer(("127.0.0.1", 0), dash._DashboardHandler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = json.dumps(body) if body is not None else None
        conn.request(
            method,
            path,
            body=payload,
            headers={"Content-Type": "application/json"} if payload else {},
        )
        response = conn.getresponse()
        data = response.read().decode("utf-8")
        conn.close()
        return response.status, data

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def server(isolated_workspace: Path) -> _Server:
    srv = _Server()
    yield srv
    srv.close()


def test_dashboard_serves_index(server: _Server) -> None:
    status, body = server.request("GET", "/")
    assert status == 200
    assert "<html" in body.lower()


def test_dashboard_status(server: _Server) -> None:
    status, body = server.request("GET", "/api/status")
    assert status == 200
    data = json.loads(body)
    assert "allowed_tools" in data
    assert "offline_mode" in data


def test_dashboard_tools_listing(server: _Server) -> None:
    status, body = server.request("GET", "/api/tools")
    assert status == 200
    data = json.loads(body)
    assert data["tools"]
    assert all("name" in t and "dangerous" in t for t in data["tools"])


def test_dashboard_history_and_exports(server: _Server) -> None:
    status, body = server.request("GET", "/api/history")
    assert status == 200
    assert "messages" in json.loads(body)
    status, _ = server.request("GET", "/api/export/json")
    assert status == 200
    status, _ = server.request("GET", "/api/export/markdown")
    assert status == 200
    status, _ = server.request("GET", "/api/export/weird")
    assert status == 400


def test_dashboard_mcp_and_memory(server: _Server) -> None:
    status, body = server.request("GET", "/api/mcp")
    assert status == 200
    assert "servers" in json.loads(body)
    status, body = server.request("GET", "/api/memory")
    assert status == 200
    assert "memories" in json.loads(body)


def test_dashboard_unknown_route_404(server: _Server) -> None:
    status, _ = server.request("GET", "/api/nope")
    assert status == 404
    status, _ = server.request("POST", "/api/nope", {})
    assert status == 404


def test_dashboard_tool_toggle_enables_then_disables(server: _Server) -> None:
    """Regression: POST /api/tools/<name> used to 404 (the page's Toggle button)."""
    settings.assistant.allowed_tools = []
    settings.assistant.denied_tools = []
    status, _ = server.request("POST", "/api/tools/web_search", {})
    assert status == 200
    assert "web_search" in settings.assistant.allowed_tools
    status, _ = server.request("POST", "/api/tools/web_search", {})
    assert status == 200
    assert "web_search" not in settings.assistant.allowed_tools


def test_dashboard_tool_toggle_unknown_tool_400(server: _Server) -> None:
    status, body = server.request("POST", "/api/tools/not_a_real_tool", {})
    assert status == 400
    assert "Unknown tool" in body


def test_dashboard_permissions_allow_deny_reset(server: _Server) -> None:
    settings.assistant.allowed_tools = []
    settings.assistant.denied_tools = []
    status, _ = server.request(
        "POST", "/api/permissions", {"action": "allow", "tool": "run_shell_command"}
    )
    assert status == 200
    assert "run_shell_command" in settings.assistant.allowed_tools
    status, _ = server.request(
        "POST", "/api/permissions", {"action": "deny", "tool": "run_shell_command"}
    )
    assert status == 200
    assert "run_shell_command" not in settings.assistant.allowed_tools
    assert "run_shell_command" in settings.assistant.denied_tools
    status, _ = server.request("POST", "/api/permissions", {"action": "reset"})
    assert status == 200
    assert settings.assistant.allowed_tools == []
    assert settings.assistant.denied_tools == []


def test_dashboard_mcp_toggle_enable_disable(
    server: _Server, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = isolated_workspace / "mcp.json"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(mcp_path))
    from james.integrations.manager import IntegrationManager

    manager = IntegrationManager(path=mcp_path)
    manager.enable("filesystem")
    assert manager._enabled_names() == {"filesystem"}

    status, _body = server.request(
        "POST", "/api/mcp/toggle", {"action": "disable", "name": "filesystem"}
    )
    assert status == 200
    assert manager._enabled_names() == set()

    status, _body = server.request("POST", "/api/mcp/toggle", {"action": "enable", "name": "fetch"})
    assert status == 200
    assert manager._enabled_names() == {"fetch"}


def test_dashboard_mcp_toggle_flips_state(
    server: _Server, isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = isolated_workspace / "mcp.json"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(mcp_path))
    from james.integrations.manager import IntegrationManager

    manager = IntegrationManager(path=mcp_path)

    status, _body = server.request(
        "POST", "/api/mcp/toggle", {"action": "toggle", "name": "filesystem"}
    )
    assert status == 200
    assert manager._enabled_names() == {"filesystem"}

    status, _body = server.request(
        "POST", "/api/mcp/toggle", {"action": "toggle", "name": "filesystem"}
    )
    assert status == 200
    assert manager._enabled_names() == set()


def test_dashboard_mcp_toggle_user_defined_server_rejected(server: _Server) -> None:
    status, body = server.request(
        "POST", "/api/mcp/toggle", {"action": "toggle", "name": "my-custom"}
    )
    assert status == 200
    assert json.loads(body)["ok"] is False


def test_dashboard_mcp_toggle_missing_name(server: _Server) -> None:
    status, _body = server.request("POST", "/api/mcp/toggle", {"action": "toggle"})
    assert status == 400
