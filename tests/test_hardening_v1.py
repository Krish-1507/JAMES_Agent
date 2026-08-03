"""Release-hardening coverage for workspace, signing, isolation, and desktop approvals."""

from __future__ import annotations

import os
from pathlib import Path

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
    os.getenv("CI") == "true" and os.name != "nt", reason="Qt runtime optional in CI"
)
def test_desktop_approval_defaults_to_deny_and_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QPlainTextEdit

    from james.ui.desktop import _ApprovalDialog, _ApprovalRequest

    app = QApplication.instance() or QApplication([])
    request = _ApprovalRequest("run_shell_command", {"command": "echo ok", "api_key": "secret"})
    dialog = _ApprovalDialog(request)
    details = dialog.findChild(QPlainTextEdit, "approvalDetails")
    assert details is not None
    assert "secret" not in details.toPlainText()
    assert "***REDACTED***" in details.toPlainText()
    QTimer.singleShot(0, dialog.reject)
    dialog.exec_()
    assert request.allowed is False
    app.processEvents()


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


def test_desktop_structure_and_custom_model_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication

    from james.ui import desktop

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(desktop._Worker, "start", lambda self: None)
    monkeypatch.setattr(desktop._Worker, "stop", lambda self: None)
    monkeypatch.setattr(desktop._Worker, "wait", lambda self, _timeout=0: True)
    window = desktop.DesktopWindow()
    assert window.provider_combo.objectName() == "providerCombo"
    assert window.model_combo.objectName() == "modelCombo"
    assert window.composer.objectName() == "chatComposer"
    assert window.activity.objectName() == "activityList"
    assert window.restore_button.objectName() == "restoreLastDeletedButton"
    window._populate_models("custom", "local-model")
    assert window.model_combo.isEditable()
    window.close()
    app.processEvents()


def test_desktop_approval_allow_once() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt5")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QPushButton

    from james.ui.desktop import _ApprovalDialog, _ApprovalRequest

    app = QApplication.instance() or QApplication([])
    request = _ApprovalRequest("delete_file", {"path": "notes.txt"})
    dialog = _ApprovalDialog(request)
    allow = dialog.findChild(QPushButton, "approvalAllowButton")
    assert allow is not None
    QTimer.singleShot(0, allow.click)
    dialog.exec_()
    assert request.allowed is True
    app.processEvents()
