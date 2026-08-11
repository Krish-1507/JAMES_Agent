"""Phase-4 tests: one-click MCP integrations + live registry reload."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from james.integrations.catalog import MCP_CATALOG
from james.integrations.manager import IntegrationManager, mcp_config_path
from james.tools.registry import ToolRegistry
from james.ui.server import ServerRuntime, create_app


@pytest.fixture
def mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "mcp.json"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(config))
    return config


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


def test_catalog_has_the_phase4_defaults() -> None:
    names = {entry["name"] for entry in MCP_CATALOG}
    assert {
        "filesystem",
        "fetch",
        "browser_use",
        "github",
        "slack",
        "notion",
        "gmail",
        "sequential_thinking",
    } <= names


def test_catalog_env_vars_are_declared() -> None:
    for entry in MCP_CATALOG:
        assert entry["name"] and entry["title"] and entry["description"]
        assert entry["transport"] in ("stdio", "http")
        if entry["transport"] == "stdio":
            assert entry["command"] and entry["args"]
        else:
            assert entry["url"]


# ---------------------------------------------------------------------------
# manager
# ---------------------------------------------------------------------------


def test_enable_writes_config_and_status(mcp_config: Path) -> None:
    manager = IntegrationManager()
    ok, _ = manager.enable("fetch")
    assert ok
    assert mcp_config.exists()
    servers = manager.load_config()
    assert [s["name"] for s in servers] == ["fetch"]
    status = {row["name"]: row for row in manager.status()}
    assert status["fetch"]["enabled"] is True
    assert status["filesystem"]["enabled"] is False


def test_enable_twice_and_unknown(mcp_config: Path) -> None:
    manager = IntegrationManager()
    ok, _ = manager.enable("fetch")
    assert ok
    ok2, msg = manager.enable("fetch")
    assert not ok2 and "already enabled" in msg
    ok3, msg3 = manager.enable("does-not-exist")
    assert not ok3 and "Unknown" in msg3


def test_disable_removes_entry_keeps_user_servers(mcp_config: Path) -> None:
    manager = IntegrationManager()
    manager.enable("github")
    manager.save_config(
        [
            *manager.load_config(),
            {"name": "my-custom", "command": "python", "args": ["server.py"], "env": {}},
        ]
    )
    ok, _ = manager.disable("github")
    assert ok
    remaining = [s["name"] for s in manager.load_config()]
    assert remaining == ["my-custom"]


def test_filesystem_workspace_substitution(
    mcp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from james.config import settings

    monkeypatch.setattr(settings.assistant, "workspace_dir", Path("C:/fake-workspace"))
    manager = IntegrationManager()
    manager.enable("filesystem")
    entry = manager.load_config()[0]
    assert str(settings.assistant.workspace_dir) in entry["args"]


def test_mcp_config_path_override(mcp_config: Path) -> None:
    assert mcp_config_path() == mcp_config.resolve()


def test_enabled_count(mcp_config: Path) -> None:
    manager = IntegrationManager()
    assert manager.enabled_count() == 0
    manager.enable("sequential_thinking")
    assert manager.enabled_count() == 1


# ---------------------------------------------------------------------------
# live registry reload
# ---------------------------------------------------------------------------


def test_reload_removes_stale_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ToolRegistry(discover_plugins=False)
    registry._tools["mcp_old_server_some_tool"] = registry._tools["notify"]  # fake stale tool
    monkeypatch.setattr(
        "james.tools.registry.discover_mcp_tools",
        lambda: [],  # no network in tests
    )
    counts = registry.reload_mcp_tools()
    assert counts["removed"] == 1
    assert "mcp_old_server_some_tool" not in registry._tools


def test_reload_without_mcp_package(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ToolRegistry(discover_plugins=False)

    def _raise() -> list:
        raise RuntimeError("mcp package not installed")

    monkeypatch.setattr("james.tools.registry.discover_mcp_tools", _raise)
    counts = registry.reload_mcp_tools()
    assert counts == {"removed": 0, "added": 0}


# ---------------------------------------------------------------------------
# server API
# ---------------------------------------------------------------------------


class FakeAssistant:
    def __init__(self) -> None:
        self.registry = ToolRegistry(discover_plugins=False)
        self.gateway = None
        self.recipe_engine = None
        self.history = []
        self.session = "default"
        self.on_event = None

    def current_session(self) -> str:
        return "default"

    def list_sessions(self) -> list[str]:
        return ["default"]

    def switch_model(self, provider: str, model: str) -> bool:
        return True


@pytest.fixture
def client(mcp_config: Path) -> TestClient:
    runtime = ServerRuntime(assistant_factory=FakeAssistant)
    runtime._assistant = FakeAssistant()
    return TestClient(create_app(runtime))


def test_integrations_endpoint(client: TestClient) -> None:
    response = client.get("/api/integrations")
    assert response.status_code == 200
    rows = response.json()["integrations"]
    assert len(rows) == len(MCP_CATALOG)
    assert all("enabled" in row and "name" in row for row in rows)


def test_integration_enable_disable_roundtrip(client: TestClient, mcp_config: Path) -> None:
    response = client.post("/api/integrations/fetch/enable")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] and payload["reloaded"] == {"removed": 0, "added": 0}
    assert mcp_config.exists()

    status = {row["name"]: row for row in client.get("/api/integrations").json()["integrations"]}
    assert status["fetch"]["enabled"] is True

    response = client.post("/api/integrations/fetch/disable")
    assert response.status_code == 200
    status = {row["name"]: row for row in client.get("/api/integrations").json()["integrations"]}
    assert status["fetch"]["enabled"] is False


def test_integration_enable_unknown(client: TestClient) -> None:
    response = client.post("/api/integrations/not-a-server/enable")
    assert response.status_code == 404


def test_integrations_reload_endpoint(client: TestClient) -> None:
    response = client.post("/api/integrations/reload")
    assert response.status_code == 200
    assert response.json()["reloaded"] == {"removed": 0, "added": 0}


# ---------------------------------------------------------------------------
# registry surface (Phase-4 tools present)
# ---------------------------------------------------------------------------


def test_phase4_tools_registered() -> None:
    registry = ToolRegistry(discover_plugins=False)
    names = set(registry.names())
    assert {
        "outlook_read_inbox",
        "outlook_send_email",
        "outlook_create_event",
        "excel_read_cells",
        "excel_write_cells",
        "word_read_document",
        "powerpoint_create",
        "notify",
        "create_recipe",
        "compose_recipe",
        "list_recipes",
        "delete_recipe",
        "run_recipe_now",
        "send_message",
        "update_marketplace",
    } <= names
