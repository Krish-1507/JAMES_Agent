"""Professional PyQt5 desktop shell with explicit safety approvals."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import ClassVar

from PyQt5.QtCore import QEvent, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


def _brand_asset() -> Path:
    return Path(__file__).resolve().parents[2] / "James.png"


def _make_icon() -> QIcon:
    asset = _brand_asset()
    if asset.exists():
        return QIcon(str(asset))
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#39c6d8"))
    return QIcon(pixmap)


def _redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            result[key] = (
                "***REDACTED***"
                if any(
                    marker in lowered
                    for marker in ("token", "secret", "password", "api_key", "authorization")
                )
                else _redact(item)
            )
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class _ApprovalRequest:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments
        self._event = threading.Event()
        self.allowed = False

    def wait(self, timeout: float = 300.0) -> bool:
        self._event.wait(timeout)
        return self.allowed

    def respond(self, allowed: bool) -> None:
        if self._event.is_set():
            return
        self.allowed = bool(allowed)
        self._event.set()


class _ApprovalDialog(QDialog):
    _RISKS: ClassVar[dict[str, str]] = {
        "delete_file": "Moves a workspace item to recoverable trash.",
        "run_shell_command": "Runs an approved read-only command in an isolated process.",
        "schedule_task": "Schedules a command that may execute after this session.",
        "install_plugin": "Installs third-party code after signature verification.",
        "computer_use": "Allows JAMES to control the visible desktop.",
    }

    def __init__(self, request: _ApprovalRequest, parent=None):
        super().__init__(parent)
        self.request = request
        self.setObjectName("approvalDialog")
        self.setWindowTitle("Approve action")
        self.setModal(True)
        self.resize(540, 400)
        layout = QVBoxLayout(self)
        eyebrow = QLabel("SAFETY CHECK")
        eyebrow.setObjectName("eyebrow")
        title = QLabel(f"Allow {request.name}?")
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        risk = QLabel(
            self._RISKS.get(request.name, "This action can change local or external state.")
        )
        risk.setWordWrap(True)
        boundary = QLabel("Scope: the configured JAMES workspace. Approval applies once.")
        boundary.setObjectName("muted")
        details = QPlainTextEdit(
            json.dumps(_redact(request.arguments), indent=2, ensure_ascii=False)
        )
        details.setReadOnly(True)
        details.setObjectName("approvalDetails")
        buttons = QDialogButtonBox()
        deny = buttons.addButton("Deny", QDialogButtonBox.RejectRole)
        allow = buttons.addButton("Allow once", QDialogButtonBox.AcceptRole)
        deny.setObjectName("approvalDenyButton")
        allow.setObjectName("approvalAllowButton")
        deny.setDefault(True)
        deny.setAutoDefault(True)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(risk)
        layout.addWidget(boundary)
        layout.addWidget(details, 1)
        layout.addWidget(buttons)

    def accept(self) -> None:
        self.request.respond(True)
        super().accept()

    def reject(self) -> None:
        self.request.respond(False)
        super().reject()


class _Worker(QThread):
    event = pyqtSignal(object)
    log = pyqtSignal(str)
    ready = pyqtSignal()
    busy = pyqtSignal(bool)
    approval_requested = pyqtSignal(object)
    model_changed = pyqtSignal(bool, str, str)
    recovery_done = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._assistant = None

    def submit_text(self, text: str) -> None:
        self._commands.put(("turn", text))

    def request_model(self, provider: str, model: str) -> None:
        self._commands.put(("model", provider, model))

    def restore_last_deleted(self) -> None:
        self._commands.put(("restore",))

    def stop(self) -> None:
        self._commands.put(("stop",))

    def _confirm(self, name: str, arguments: dict) -> bool:
        request = _ApprovalRequest(name, arguments)
        self.approval_requested.emit(request)
        return request.wait()

    def _wire(self) -> None:
        self._assistant.on_event = self.event.emit
        self._assistant.set_confirmation_handler(self._confirm)
        self._assistant.set_tool_hooks(self._on_tool, self._on_tool_start, self._on_tool_pending)

    def _on_tool_pending(self, call_id: str, name: str, arguments: dict) -> None:
        self.event.emit(
            {"type": "tool_pending", "call_id": call_id, "name": name, "args": arguments}
        )

    def _on_tool_start(self, call_id: str, name: str, arguments: dict) -> None:
        self.event.emit({"type": "tool_start", "call_id": call_id, "name": name, "args": arguments})

    def _on_tool(self, call_id: str, name: str, arguments: dict, result: str) -> None:
        lowered = result.lower()
        self.event.emit(
            {
                "type": "tool",
                "call_id": call_id,
                "name": name,
                "args": arguments,
                "result": result,
                "ok": not (result.startswith("Error") or "failed" in lowered),
            }
        )

    def run(self) -> None:
        from ..core.assistant import Assistant

        try:
            self._assistant = Assistant(confirm=self._confirm)
            self._wire()
            self.ready.emit()
        except Exception as exc:
            self.log.emit(f"Startup failed: {exc}")
            return
        while True:
            command = self._commands.get()
            if command[0] == "stop":
                break
            self.busy.emit(True)
            try:
                if command[0] == "turn":
                    self._assistant.handle_turn(command[1])
                elif command[0] == "model":
                    ok = self._assistant.switch_model(command[1], command[2])
                    if ok:
                        self._wire()
                    self.model_changed.emit(ok, command[1], command[2])
                elif command[0] == "restore":
                    from ..tools.file_tools import restore_last_deleted

                    result = restore_last_deleted.run()
                    self.recovery_done.emit(result.ok, result.output)
            except Exception as exc:
                self.log.emit(str(exc))
            finally:
                self.busy.emit(False)


class _Composer(QPlainTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class DesktopWindow(QMainWindow):
    def __init__(self):
        from ..config import settings

        super().__init__()
        self.setWindowTitle(f"{settings.assistant.name} Desktop")
        self.setWindowIcon(_make_icon())
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self._activity_items: dict[str, QListWidgetItem] = {}
        self._build_ui()
        self._apply_style()
        self.worker = _Worker()
        self.worker.event.connect(self._on_event)
        self.worker.log.connect(self._log)
        self.worker.ready.connect(lambda: self._set_status("Ready", "ready"))
        self.worker.busy.connect(self._on_busy)
        self.worker.approval_requested.connect(self._show_approval)
        self.worker.model_changed.connect(self._on_model_result)
        self.worker.recovery_done.connect(self._on_recovery)
        self.worker.start()
        self._setup_tray()

    def _build_ui(self) -> None:
        from ..config import settings
        from ..llm.catalog import PROVIDERS

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 16, 20, 20)
        outer.setSpacing(14)

        top = QHBoxLayout()
        brand = QLabel("JAMES")
        brand.setObjectName("brand")
        edition = QLabel("DESKTOP")
        edition.setObjectName("edition")
        top.addWidget(brand)
        top.addWidget(edition)
        top.addStretch(1)
        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        top.addWidget(self.status)
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.addItems(PROVIDERS)
        self.provider_combo.setCurrentText(settings.llm.provider)
        self.provider_combo.currentTextChanged.connect(self._populate_models)
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setMinimumWidth(230)
        top.addWidget(self.provider_combo)
        top.addWidget(self.model_combo)
        self.apply_model_btn = QPushButton("Apply")
        self.apply_model_btn.clicked.connect(self._apply_model)
        top.addWidget(self.apply_model_btn)
        outer.addLayout(top)
        self._populate_models(settings.llm.provider, settings.llm.model)

        body = QHBoxLayout()
        body.setSpacing(14)
        nav = QVBoxLayout()
        nav.setSpacing(6)
        self.nav_buttons = []
        for index, label in enumerate(("Chat", "Activity", "Integrations", "Recovery")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("navButton")
            button.clicked.connect(lambda _checked, i=index: self._navigate(i))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        nav.addStretch(1)
        workspace = QLabel(f"Workspace\n{settings.assistant.workspace_dir.resolve()}")
        workspace.setObjectName("workspaceLabel")
        workspace.setWordWrap(True)
        nav.addWidget(workspace)
        nav_host = QFrame()
        nav_host.setObjectName("sidebar")
        nav_host.setFixedWidth(180)
        nav_host.setLayout(nav)
        body.addWidget(nav_host)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._chat_page())
        self.pages.addWidget(self._activity_page())
        self.pages.addWidget(self._integrations_page())
        self.pages.addWidget(self._recovery_page())
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

    def _page_header(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("muted")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        return page, layout

    def _chat_page(self) -> QWidget:
        page, layout = self._page_header("Chat", "Ask JAMES to work inside your bounded workspace.")
        from ..config import settings

        self.chat_transcript = QListWidget()
        self.chat_transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_transcript.setWordWrap(True)
        self.chat_transcript.setObjectName("chatTranscript")
        self.chat_transcript.addItem(
            "JAMES  ·  Ready when you are. Dangerous actions always ask first."
        )
        provider = settings.llm.provider
        api_key = getattr(settings.llm, f"{provider}_api_key", "")
        if provider != "custom" and not api_key:
            self.chat_transcript.addItem(
                "SETUP  |  Choose a provider and model above, then add its API key to .env "
                "or run `james --setup`. For local models choose Custom."
            )
        layout.addWidget(self.chat_transcript, 1)
        self.response = QLabel("")
        self.response.setWordWrap(True)
        self.response.setObjectName("assistantResponse")
        layout.addWidget(self.response)
        composer_row = QHBoxLayout()
        self.composer = _Composer()
        self.composer.setObjectName("chatComposer")
        self.composer.setPlaceholderText("Message JAMES…  (Ctrl+Enter to send)")
        self.composer.setFixedHeight(78)
        self.composer.send_requested.connect(self._send)
        composer_row.addWidget(self.composer, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primaryButton")
        self.send_button.clicked.connect(self._send)
        composer_row.addWidget(self.send_button)
        layout.addLayout(composer_row)
        return page

    def _activity_page(self) -> QWidget:
        page, layout = self._page_header(
            "Activity", "A transparent ledger of every tool JAMES uses."
        )
        self.activity = QListWidget()
        self.activity.setObjectName("activityList")
        self.activity.itemDoubleClicked.connect(self._show_activity_details)
        layout.addWidget(self.activity, 1)
        activity_actions = QHBoxLayout()
        activity_hint = QLabel("Select a row to inspect redacted arguments and results.")
        activity_hint.setObjectName("mutedLabel")
        activity_actions.addWidget(activity_hint, 1)
        self.activity_details_button = QPushButton("View details")
        self.activity_details_button.setEnabled(False)
        self.activity.currentItemChanged.connect(
            lambda current, _previous: self.activity_details_button.setEnabled(current is not None)
        )
        self.activity_details_button.clicked.connect(
            lambda: self._show_activity_details(self.activity.currentItem())
        )
        activity_actions.addWidget(self.activity_details_button)
        layout.addLayout(activity_actions)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setObjectName("diagnosticsLog")
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(110)
        self.diagnostics.setVisible(False)
        layout.addWidget(self.diagnostics)
        return page

    def _integrations_page(self) -> QWidget:
        page, layout = self._page_header(
            "Integrations", "Configured MCP servers and model connections."
        )
        self.mcp_list = QListWidget()
        self.mcp_list.setObjectName("mcpServerList")
        layout.addWidget(self.mcp_list, 1)
        refresh = QPushButton("Refresh MCP servers")
        refresh.clicked.connect(self._refresh_mcp)
        layout.addWidget(refresh)
        self._refresh_mcp()
        return page

    def _recovery_page(self) -> QWidget:
        page, layout = self._page_header(
            "Recovery",
            "Deletes are moved to private workspace trash and can be restored. Shell and plugin work runs in isolated processes.",
        )
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        title = QLabel("Undo the most recent delete")
        title.setObjectName("cardTitle")
        self.recovery_status = QLabel("No recovery action has been requested.")
        self.recovery_status.setObjectName("muted")
        self.restore_button = QPushButton("Restore last deleted item")
        self.restore_button.setObjectName("restoreLastDeletedButton")
        self.restore_button.clicked.connect(lambda: self.worker.restore_last_deleted())
        card_layout.addWidget(title)
        card_layout.addWidget(self.recovery_status)
        card_layout.addWidget(self.restore_button)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _populate_models(self, provider: str, selected: str = "") -> None:
        from ..llm.catalog import DEFAULT_MODELS, model_choices

        choices = model_choices(provider) or [DEFAULT_MODELS.get(provider, "")]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.setEditable(provider == "custom")
        self.model_combo.addItems([choice for choice in choices if choice])
        if selected:
            if self.model_combo.findText(selected) < 0:
                self.model_combo.addItem(selected)
            self.model_combo.setCurrentText(selected)
        self.model_combo.blockSignals(False)

    def _apply_model(self) -> None:
        provider = self.provider_combo.currentText().strip()
        model = self.model_combo.currentText().strip()
        if not provider or not model:
            self._set_status("Choose a model", "error")
            return
        self.apply_model_btn.setEnabled(False)
        self._set_status("Switching…", "busy")
        self.worker.request_model(provider, model)

    def _on_model_result(self, ok: bool, provider: str, model: str) -> None:
        from ..config import settings

        self.apply_model_btn.setEnabled(True)
        if ok:
            self._set_status(f"{provider} · {model}", "ready")
            self._log(f"Model switched to {provider}:{model}")
        else:
            self.provider_combo.setCurrentText(settings.llm.provider)
            self._populate_models(settings.llm.provider, settings.llm.model)
            self._set_status("Model switch failed", "error")

    def _send(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text:
            return
        self.chat_transcript.addItem(f"You  ·  {text}")
        self.composer.clear()
        self.worker.submit_text(text)

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "thinking":
            self._set_status("Thinking…", "busy")
        elif kind == "reply":
            text = str(event.get("text", ""))
            self.response.setText(text)
            self.chat_transcript.addItem(f"JAMES  ·  {text}")
            self.chat_transcript.scrollToBottom()
        elif kind == "tool_pending":
            item = QListWidgetItem(f"Pending approval  |  {event.get('name')}")
            item.setForeground(QColor("#e7ad46"))
            item.setData(Qt.UserRole, event)
            self.activity.addItem(item)
            self._activity_items[str(event.get("call_id"))] = item
        elif kind == "tool_start" and str(event.get("call_id")) in self._activity_items:
            item = self._activity_items[str(event.get("call_id"))]
            item.setText(f"Running  |  {event.get('name')}")
            item.setForeground(QColor("#39c6d8"))
            item.setData(Qt.UserRole, event)
            self.activity.scrollToBottom()
        elif kind == "tool_start":
            item = QListWidgetItem(f"Pending  ·  {event.get('name')}")
            item.setText(f"Running  |  {event.get('name')}")
            item.setForeground(QColor("#39c6d8"))
            item.setData(Qt.UserRole, event)
            self.activity.addItem(item)
            self._activity_items[str(event.get("call_id"))] = item
        elif kind == "tool":
            item = self._activity_items.pop(str(event.get("call_id")), None)
            if item is None:
                item = QListWidgetItem()
                self.activity.addItem(item)
            ok = bool(event.get("ok"))
            item.setText(
                f"{'Done' if ok else 'Failed'}  ·  {event.get('name')}  ·  {str(event.get('result', ''))[:160]}"
            )
            item.setText(
                f"{'Done' if ok else 'Failed'}  |  {event.get('name')}  |  {str(event.get('result', ''))[:160]}"
            )
            item.setData(Qt.UserRole, event)
            item.setForeground(QColor("#58c69a" if ok else "#e36464"))

    def _show_approval(self, request: _ApprovalRequest) -> None:
        self._set_status("Approval required", "warning")
        _ApprovalDialog(request, self).exec_()

    def _on_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        if not busy:
            self._set_status("Ready", "ready")

    def _on_recovery(self, ok: bool, message: str) -> None:
        self.recovery_status.setText(message)
        self._set_status("Restored" if ok else "Nothing restored", "ready" if ok else "warning")

    def _set_status(self, text: str, state: str) -> None:
        self.status.setText(f"●  {text}")
        colors = {"ready": "#58c69a", "warning": "#e7ad46", "error": "#e36464", "busy": "#39c6d8"}
        self.status.setStyleSheet(f"color:{colors.get(state, '#94a4b3')};font-weight:600")

    def _navigate(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    def _refresh_mcp(self) -> None:
        self.mcp_list.clear()
        try:
            from ..tools.mcp_tools import load_mcp_configs

            configs = load_mcp_configs()
            if not configs:
                self.mcp_list.addItem("No MCP servers configured.")
            for config in configs:
                self.mcp_list.addItem(
                    f"{config.name}  ·  {config.transport}  ·  {config.command or config.url or 'ready'}"
                )
        except Exception as exc:
            self.mcp_list.addItem(f"Could not load MCP configuration: {exc}")

    def _log(self, text: str) -> None:
        self.diagnostics.setVisible(True)
        self.diagnostics.appendPlainText(text)

    def _show_activity_details(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        event = item.data(Qt.UserRole) or {}
        dialog = QDialog(self)
        dialog.setWindowTitle("Activity details")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        title = QLabel(str(event.get("name", "Tool activity")))
        title.setObjectName("dialogTitle")
        details = QPlainTextEdit(json.dumps(_redact(event), indent=2, ensure_ascii=False))
        details.setReadOnly(True)
        layout.addWidget(title)
        layout.addWidget(details, 1)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_icon(), self)
        menu = QMenu()
        toggle = menu.addAction("Show / Hide")
        toggle.triggered.connect(lambda: self.hide() if self.isVisible() else self.show())
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background:#111820; color:#eef4f7; font-family:'Segoe UI'; font-size:13px; }
            QFrame#sidebar, QFrame#card { background:#18212b; border:1px solid #34414e; border-radius:12px; }
            QLabel#brand { font-size:22px; font-weight:800; letter-spacing:2px; color:#ffffff; }
            QLabel#edition, QLabel#eyebrow { color:#39c6d8; font-size:10px; font-weight:700; letter-spacing:1px; }
            QLabel#pageTitle, QLabel#dialogTitle { font-size:24px; font-weight:700; }
            QLabel#cardTitle { font-size:16px; font-weight:650; }
            QLabel#muted, QLabel#workspaceLabel { color:#94a4b3; }
            QLabel#workspaceLabel { font-size:11px; padding:8px; }
            QPushButton { background:#202b36; border:1px solid #34414e; border-radius:9px; padding:9px 13px; }
            QPushButton:hover { border-color:#39c6d8; }
            QPushButton#navButton { text-align:left; border-color:transparent; padding:11px 14px; }
            QPushButton#navButton:checked, QPushButton#primaryButton { background:#167889; color:white; border-color:#39c6d8; }
            QPushButton#approvalDenyButton { border-color:#e36464; }
            QComboBox, QPlainTextEdit, QListWidget { background:#18212b; border:1px solid #34414e; border-radius:9px; padding:8px; selection-background-color:#167889; }
            QListWidget::item { padding:9px; border-bottom:1px solid #26323d; }
            QScrollBar:vertical { background:#111820; width:10px; }
            QScrollBar::handle:vertical { background:#34414e; border-radius:5px; min-height:28px; }
            """
        )

    def closeEvent(self, event: QEvent) -> None:
        if hasattr(self, "worker"):
            self.worker.stop()
            self.worker.wait(2500)
        event.accept()


def run_ui() -> int:
    import sys

    app = QApplication.instance() or QApplication(sys.argv)
    window = DesktopWindow()
    window.show()
    return app.exec_()


OrbWindow = DesktopWindow

__all__ = ["DesktopWindow", "OrbWindow", "_ApprovalDialog", "_ApprovalRequest", "run_ui"]
