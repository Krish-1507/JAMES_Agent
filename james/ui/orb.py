"""Optional PyQt5 orb GUI — upgraded: live task canvas, streaming output, tray.

Run with:  python -m james --ui   (requires: pip install pyqt5)
The assistant runs in a worker thread; its activity streams into the UI:
  • status orb  — idle / listening / thinking / replying / speaking
  • task canvas — a live list of every tool the agent calls
  • reply label — the spoken answer, revealed word-by-word (streaming)
  • log         — full console output
A system tray icon lets you hide/show and quit.
"""
from __future__ import annotations

from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QMenu,
)


def _make_icon() -> QIcon:
    from PyQt5.QtCore import QSize
    from PyQt5.QtGui import QFont, QBrush, QPixmap

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

    def __init__(self):
        super().__init__()
        self._assistant = None

    def run(self):
        from ..core.assistant import Assistant

        self._assistant = Assistant()
        self._assistant.on_event = self._on_event
        if getattr(self._assistant, "agent", None) is not None:
            self._assistant.agent.on_tool = self._on_tool

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

    def _on_tool(self, name: str, args: dict, result: str):
        snippet = str(result)[:90].replace("\n", " ")
        self.canvas.emit(f"🔧 {name}: {snippet}")


class OrbWindow(QMainWindow):
    def __init__(self):
        from ..config import settings

        super().__init__()
        self.setWindowTitle(f"{settings.assistant.name} — JARVIS")
        self.resize(460, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.orb = QLabel("◉ ONLINE")
        self.orb.setStyleSheet("font-size:40px; color:#39c; qproperty-alignment:AlignCenter;")
        layout.addWidget(self.orb)

        self.reply = QLabel("")
        self.reply.setWordWrap(True)
        self.reply.setStyleSheet("font-size:14px; color:#cde; padding:6px;")
        layout.addWidget(self.reply)

        layout.addWidget(QLabel("Live task canvas"))
        self.canvas = QListWidget()
        layout.addWidget(self.canvas)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        self.start_btn = QPushButton("Start JAMES")
        self.start_btn.clicked.connect(self.start)
        layout.addWidget(self.start_btn)

        self.worker = _Worker()
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.status.connect(self._on_status)
        self.worker.canvas.connect(self._on_canvas)
        self.worker.stream.connect(self._on_stream)
        self._stream_text = ""
        self._stream_i = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

        self._setup_tray()
        self.start()

    # ---- streaming reveal ----
    def _on_stream(self, text: str):
        self._stream_text = text
        self._stream_i = 0
        self.reply.setText("")
        if text:
            self._timer.start(18)

    def _tick(self):
        if self._stream_i >= len(self._stream_text):
            self._timer.stop()
            return
        self._stream_i += 2
        self.reply.setText(self._stream_text[: self._stream_i])

    def _on_status(self, text: str):
        self.orb.setText(f"◉ {text}")

    def _on_canvas(self, line: str):
        self.canvas.addItem(line)
        self.canvas.scrollToBottom()

    def start(self):
        if self.worker.isRunning():
            return
        self.start_btn.setEnabled(False)
        self.worker.start()

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
