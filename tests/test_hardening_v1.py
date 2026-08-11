"""Release-hardening coverage for workspace, signing, isolation, and UI approvals."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from james.config import settings
from james.core.workspace import WorkspaceViolation, resolve_workspace_path
from james.sdk import create_plugin, sign_plugin_source, verify_plugin_signature
from james.tools.file_tools import delete_file, restore_last_deleted


def test_workspace_paths_reject_absolute_and_parent_escapes(isolated_workspace: Path) -> None:
    assert resolve_workspace_path("notes/today.md") == isolated_workspace / "notes" / "today.md"
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path(isolated_workspace.parent / "outside.txt")
    with pytest.raises(WorkspaceViolation):
        resolve_workspace_path("../outside.txt")


def test_delete_is_recoverable_and_isolated(isolated_workspace: Path) -> None:
    target = isolated_workspace / "recover-me.txt"
    target.write_text("important", encoding="utf-8")
    removed = delete_file.run(path="recover-me.txt")
    assert removed.ok, removed.output
    assert not target.exists()
    restored = restore_last_deleted.run()
    assert restored.ok, restored.output
    assert target.read_text(encoding="utf-8") == "important"


def test_plugin_signature_detects_tampering(plugin_dir: Path) -> None:
    unsigned_path = create_plugin("signed_demo", directory=plugin_dir)
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signed = sign_plugin_source(unsigned_path.read_text(encoding="utf-8"), private_pem, "release")
    ok, reason = verify_plugin_signature(signed, {"release": public_pem})
    assert ok, reason
    ok, _ = verify_plugin_signature(
        signed.replace("Processed input", "Tampered"), {"release": public_pem}
    )
    assert not ok


@pytest.mark.skipif(
    os.getenv("CI") == "true" and os.name != "nt", reason="approval loop timing in CI"
)
def test_ui_approval_defaults_to_deny_and_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from james.ui import server as server_module
    from james.ui.server import _redact_args

    redacted = _redact_args("run_shell_command", {"command": "echo ok", "api_key": "secret"})
    assert "secret" not in json.dumps(redacted)
    assert redacted["api_key"] == "***"

    runtime = server_module.ServerRuntime()
    monkeypatch.setattr(server_module, "_NO_CLIENT_GRACE", 0.05)
    monkeypatch.setattr(server_module, "_APPROVAL_TIMEOUT", 5.0)
    allowed = runtime._confirm("run_shell_command", {"command": "rm -rf /", "api_key": "secret"})
    assert allowed is False


def test_assistant_preserves_confirmation_and_hooks_on_model_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from james.core import assistant as assistant_module

    class FakeProvider:
        pass

    fake = FakeProvider()
    monkeypatch.setattr(assistant_module, "build_provider", lambda _settings: fake)
    monkeypatch.setattr(assistant_module, "build_stt", lambda _settings: object())
    monkeypatch.setattr(assistant_module, "build_tts", lambda _settings: object())
    monkeypatch.setattr(assistant_module.scheduler, "start", lambda: None)

    def confirm(_name, _args):
        return True

    instance = assistant_module.Assistant(confirm=confirm)

    def hook(*_args):
        return None

    def start_hook(*_args):
        return None

    instance.set_tool_hooks(hook, start_hook)
    assert instance.switch_model(settings.llm.provider, settings.llm.model)
    assert instance.agent.confirm is confirm
    assert instance.agent.on_tool is hook
    assert instance.agent.on_tool_start is start_hook


def test_ui_web_assets_expose_required_controls() -> None:
    """The served single-page UI must keep the control surface wired up."""
    from importlib.resources import files

    html = (files("james.ui.web") / "index.html").read_text(encoding="utf-8")
    for control in (
        "provider-select",
        "model-select",
        "composer-input",
        "session-list",
        "tool-list",
        "voice-pill",
        "modal-root",
        "settings-form",
    ):
        assert control in html, f"missing control: {control}"
    for script in ("/static/app.js", "/static/style.css"):
        assert script in html


def test_ui_approval_allow_once_via_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from james.ui.server import ServerRuntime, create_app

    class FakeAssistant:
        history: ClassVar[list] = []
        registry = type("R", (), {"schemas": staticmethod(lambda: [])})()
        sessions: ClassVar[list[str]] = ["default"]
        session = "default"

        def current_session(self) -> str:  # pragma: no cover - trivial
            return self.session

        def list_sessions(self) -> list[str]:  # pragma: no cover - trivial
            return self.sessions

        def new_session(self) -> str:  # pragma: no cover - trivial
            self.session = f"s{len(self.sessions) + 1}"
            self.sessions.append(self.session)
            return self.session

        def switch_session(self, name: str) -> None:  # pragma: no cover - trivial
            if name in self.sessions:
                self.session = name

        def clear_history(self) -> None:  # pragma: no cover - trivial
            self.history = []

        def switch_model(self, provider: str, model: str) -> bool:  # pragma: no cover - trivial
            return True

        def send_voice_text(self, text: str) -> None:  # pragma: no cover - trivial
            pass

    runtime = ServerRuntime(assistant_factory=FakeAssistant)
    runtime._assistant = runtime._assistant_factory()
    runtime._assistant.on_event = runtime._on_event
    runtime.bus.connect()  # a client is watching

    results: list = []

    def run_confirm() -> None:
        results.append(runtime._confirm("delete_file", {"path": "notes.txt"}))

    import threading

    thread = threading.Thread(target=run_confirm)
    thread.start()
    try:
        client = TestClient(create_app(runtime))
        req_id = None
        for _ in range(100):
            pending = [
                e for e in runtime.bus.drain(0)[0] if e["payload"]["type"] == "approval_requested"
            ]
            if pending:
                req_id = pending[-1]["payload"]["id"]
                break
            import time

            time.sleep(0.05)
        assert req_id is not None, "approval request never surfaced"
        assert client.post(f"/api/approvals/{req_id}", json={"allowed": True}).status_code == 200
    finally:
        thread.join(timeout=10)

    assert results == [True]
