"""Optional PyQt5 orb GUI — upgraded: live task canvas, streaming output, tray.

Run with:  python -m james --ui   (requires: pip install pyqt5)
The assistant runs in a worker thread; its activity streams into the UI:
  • status orb  — idle / listening / thinking / replying / speaking
  • task canvas — a live list of every tool the agent calls
  • reply label — the spoken answer, revealed word-by-word (streaming)
  • log         — full console output
  • history     — conversation history view
A system tray icon lets you hide/show and quit.
"""
from __future__ import annotations

import threading

from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


def _make_icon() -> QIcon:
    from PyQt5.QtGui import QBrush, QFont, QPixmap

    pix = QPixmap(64, 64)
    pix.fill(QColor("#0b3d5c"))
    p = QPainter(pix)
    p.setPen(QColor("#39c"))
    p.setBrush(QBrush(QColor("#39c")))
    f = QFont("Arial", 40, QFont.Bold)
    p.setFont(f)
    p.drawText(pix.rect(), 4, 48, "J")
    p.end()
    return QIcon(pix)


class _Worker(QThread):
    log = pyqtSignal(str)
    status = pyqtSignal(str)
    canvas = pyqtSignal(str)
    stream = pyqtSignal(str)
    tool_output = pyqtSignal(str, str)  # call_id, chunk
    canvas_start = pyqtSignal(str, str)   # call_id, name
    canvas_done = pyqtSignal(str, str, str, bool)  # call_id, name, snippet, ok

    def __init__(self):
        super().__init__()
        self._assistant = None
        self._lock = threading.Lock()

    def apply_model(self, provider: str, model: str) -> bool:
        """Immediately switch the running assistant to a new provider+model.

        Called from the GUI thread. The provider/agent rebuild is safe under the
        GIL; an in-flight agent call keeps its own provider reference, so the
        switch takes effect from the next turn onward.
        """
        with self._lock:
            assistant = self._assistant
        if assistant is None:
            return False
        with self._lock:
            try:
                ok = assistant.switch_model(provider, model)
            except Exception:
                ok = False
        self.status.emit("Model updated" if ok else "Model switch failed")
        return ok

    def run(self):
        from ..core.assistant import Assistant

        self._assistant = Assistant()
        self._assistant.on_event = self._on_event
        # Route every tool call (parent + delegated sub-agents) to the canvas.
        self._assistant.set_tool_hooks(self._on_tool, self._on_tool_start)

        import sys

        class _Sink:
            def write(self, text):
                if text.strip():
                    self.log.emit(text.rstrip("\n"))

            def flush(self):
                pass

        old = sys.stdout
        sys.stdout = _Sink()
        try:
            self._assistant.run()
        finally:
            sys.stdout = old

    # ---- hooks called from the assistant (worker thread) ----
    def _on_event(self, ev: dict):
        t = ev.get("type")
        if t == "user":
            self.status.emit("🎧 Listening")
            self.canvas.emit(f"🗣 You: {ev.get('text', '')[:70]}")
        elif t == "thinking":
            self.status.emit("🧠 Thinking…")
        elif t == "reply":
            self.status.emit("💬 Replying")
            self.stream.emit(ev.get("text", ""))
        elif t == "speak":
            self.status.emit("🔊 Speaking")

    def _on_tool_start(self, call_id: str, name: str, args: dict):
        self.canvas_start.emit(call_id, name)

    def _on_tool(self, call_id: str, name: str, args: dict, result: str):
        ok = not (result.startswith("Error") or "failed" in result.lower())
        self.canvas_done.emit(call_id, name, str(result)[:140].replace("\n", " "), ok)


class OrbWindow(QMainWindow):
    def __init__(self):
        from ..config import settings

        super().__init__()
        self.setWindowTitle(f"{settings.assistant.name} — JARVIS")
        self.resize(520, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.orb = QLabel("◉ ONLINE")
        self.orb.setStyleSheet("font-size:40px; color:#39c; qproperty-alignment:AlignCenter;")
        layout.addWidget(self.orb)

        # Model switcher (curated from the shared catalog)
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        self.model_combo.blockSignals(True)
        self.model_combo.addItems(self._model_items())
        self.model_combo.setCurrentIndex(self._current_model_index())
        self.model_combo.blockSignals(False)
        self.model_combo.currentTextChanged.connect(self._on_model_change)
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)

        self.reply = QLabel("")
        self.reply.setWordWrap(True)
        self.reply.setStyleSheet("font-size:14px; color:#cde; padding:6px;")
        layout.addWidget(self.reply)

        # History view
        layout.addWidget(QLabel("Conversation"))
        self.history_view = QListWidget()
        self.history_view.setStyleSheet("QListWidget::item { padding: 2px; }")
        layout.addWidget(self.history_view)

        layout.addWidget(QLabel("Live task canvas"))
        self.canvas = QListWidget()
        self.canvas.setStyleSheet("QListWidget::item { padding: 2px; }")
        layout.addWidget(self.canvas)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        # MCP server management
        layout.addWidget(QLabel("MCP Servers"))
        self.mcp_list = QListWidget()
        self.mcp_list.setStyleSheet("QListWidget::item { padding: 2px; }")
        layout.addWidget(self.mcp_list)

        mcp_btn_layout = QHBoxLayout()
        self.mcp_refresh_btn = QPushButton("Refresh")
        self.mcp_refresh_btn.clicked.connect(self._refresh_mcp)
        mcp_btn_layout.addWidget(self.mcp_refresh_btn)
        self.mcp_toggle_btn = QPushButton("Toggle Server")
        self.mcp_toggle_btn.clicked.connect(self._toggle_mcp_server)
        mcp_btn_layout.addWidget(self.mcp_toggle_btn)
        layout.addLayout(mcp_btn_layout)

        # Control buttons
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start JAMES")
        self.start_btn.clicked.connect(self.start)
        btn_layout.addWidget(self.start_btn)

        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._on_pause)
        self.pause_btn.setEnabled(False)
        btn_layout.addWidget(self.pause_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setEnabled(False)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

        self.worker = _Worker()
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.status.connect(self._on_status)
        self.worker.canvas.connect(self._on_canvas)
        self.worker.canvas_start.connect(self._on_canvas_start)
        self.worker.canvas_done.connect(self._on_canvas_done)
        self.worker.stream.connect(self._on_stream)
        self.worker.tool_output.connect(self._on_tool_output)
        self._stream_text = ""
        self._stream_i = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._canvas_items = {}
        self._paused = False
        self._cancelled = False
        self._tool_output_buffer = {}

        self._setup_tray()
        self.start()

    def _refresh_mcp(self) -> None:
        try:
            from ..tools.mcp_tools import load_mcp_configs

            configs = load_mcp_configs()
            self.mcp_list.clear()
            for cfg in configs:
                self.mcp_list.addItem(f"{cfg.name} ({cfg.transport}) — {cfg.command or cfg.url or 'N/A'}")
        except Exception as exc:
            self.mcp_list.addItem(f"Error: {exc}")

    def _toggle_mcp_server(self) -> None:
        selected = self.mcp_list.currentItem()
        if not selected:
            return
        name = selected.text().split(" ")[0]
        try:
            from ..tools.mcp_tools import load_mcp_configs

            configs = load_mcp_configs()
            for cfg in configs:
                if cfg.name == name:
                    self.log.appendPlainText(
                        f"MCP server '{name}' is configured and available. "
                        "Enable/disable it in your MCP config file, then restart JAMES."
                    )
                    break
            else:
                self.log.appendPlainText(f"Unknown MCP server: {name}")
        except Exception as exc:
            self.log.appendPlainText(f"MCP toggle error: {exc}")

    def _model_items(self) -> list[str]:
        """Flatten the shared catalog into ``provider:model`` combo entries."""
        from ..llm.catalog import DEFAULT_MODELS, PROVIDERS, model_choices

        items: list[str] = []
        for prov in PROVIDERS:
            choices = model_choices(prov) or [DEFAULT_MODELS.get(prov, "gpt-4o-mini")]
            for m in choices:
                items.append(f"{prov}:{m}")
        return items

    def _current_model_index(self) -> int:
        """Find the combo index matching the active settings, or 0."""
        from ..config import settings

        current = f"{settings.llm.provider}:{settings.llm.model}"
        items = self._model_items()
        for i, item in enumerate(items):
            if item == current:
                return i
        return 0

    def _on_model_change(self, text: str) -> None:
        provider, _, model = text.partition(":")
        if not provider or not model:
            return
        import os

        from ..config import settings
        from ..llm.catalog import save_llm_config

        # Persist + update live settings so a not-yet-started worker picks it up.
        settings.llm.provider = provider
        settings.llm.model = model
        if provider == "custom":
            settings.llm.custom_base_url = (
                settings.llm.custom_base_url or "http://localhost:11434/v1"
            )
        os.environ["LLM_PROVIDER"] = provider
        os.environ["LLM_MODEL"] = model
        save_llm_config(provider, model)

        worker = vars(self).get("worker")
        if worker is not None and worker.apply_model(provider, model):
            self.log.appendPlainText(f"Model switched to {provider}:{model}.")
        else:
            self.log.appendPlainText(
                f"Model set to {provider}:{model}. It applies when JAMES starts "
                f"(assistant not ready yet)."
            )

    def _on_pause(self) -> None:
        self._paused = not self._paused
        self.pause_btn.setText("Resume" if self._paused else "Pause")

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.cancel_btn.setEnabled(False)

    def start(self):
        if self.worker.isRunning():
            return
        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self._cancelled = False
        self.worker.start()

    # ---- streaming reveal ----
    def _on_stream(self, text: str):
        self._stream_text = text
        self._stream_i = 0
        self.reply.setText("")
        if text:
            self._timer.start(50)

    def _tick(self):
        if self._stream_i >= len(self._stream_text):
            self._timer.stop()
            return
        self._stream_i += 1
        self.reply.setText(self._stream_text[: self._stream_i])

    def _on_tool_output(self, call_id: str, chunk: str):
        if call_id not in self._tool_output_buffer:
            self._tool_output_buffer[call_id] = ""
        self._tool_output_buffer[call_id] += chunk
        item = self._canvas_items.get(call_id)
        if item is not None:
            item.setText(f"▶ {call_id[:8]}... {self._tool_output_buffer[call_id][-60:]}")

    def _on_status(self, text: str):
        self.orb.setText(f"◉ {text}")

    def _on_canvas(self, line: str):
        self.canvas.addItem(line)
        self.canvas.scrollToBottom()

    def _on_canvas_start(self, call_id: str, name: str):
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"▶ [{ts}] {name}…")
        item.setForeground(QColor("#e0b000"))
        self.canvas.addItem(item)
        self._canvas_items[call_id] = item
        self.canvas.scrollToBottom()

    def _on_canvas_done(self, call_id: str, name: str, snippet: str, ok: bool):
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S")
        item = self._canvas_items.pop(call_id, None)
        if item is None:
            item = QListWidgetItem()
            self.canvas.addItem(item)
        mark = "✓" if ok else "✗"
        item.setText(f"{mark} [{ts}] {name}: {snippet}")
        item.setForeground(QColor("#3ad17a" if ok else "#ff6b6b"))
        self.canvas.scrollToBottom()

    def _setup_tray(self):
        try:
            self.tray = QSystemTrayIcon(_make_icon(), self)
            menu = QMenu()
            show = menu.addAction("Show / Hide")
            show.triggered.connect(self._toggle)
            quit_a = menu.addAction("Quit")
            quit_a.triggered.connect(QApplication.quit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda *a: self._toggle())
            self.tray.show()
        except Exception:
            self.tray = None

    def _toggle(self):
        self.hide() if self.isVisible() else self.show()


def run_ui() -> int:
    import sys

    app = QApplication(sys.argv)
    window = OrbWindow()
    window.show()
    return app.exec_()
