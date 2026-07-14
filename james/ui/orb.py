"""Optional PyQt5 orb GUI.

Run with:  python -m james --ui
Requires:  pip install pyqt5

This is a thin, optional visual shell around the Assistant. The assistant runs
in a worker thread; the orb shows status and a live activity log.
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _Worker(QThread):
    log = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._assistant = None

    def run(self):
        from ..core.assistant import Assistant

        self._assistant = Assistant()
        # Re-route the Rich console so logs appear in the GUI too.
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


class OrbWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from ..config import settings

        self.setWindowTitle(f"{settings.assistant.name} — JARVIS")
        self.resize(420, 520)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.orb = QLabel(f"◉ {settings.assistant.name}")
        self.orb.setStyleSheet("font-size:48px; color:#39c; qproperty-alignment:AlignCenter;")
        layout.addWidget(self.orb)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

        self.start_btn = QPushButton("Start JAMES")
        self.start_btn.clicked.connect(self.start)
        layout.addWidget(self.start_btn)

    def start(self):
        self.start_btn.setEnabled(False)
        self.worker = _Worker()
        self.worker.log.connect(self.log.appendPlainText)
        self.worker.start()
        self.orb.setText("◉ ONLINE")


def run_ui() -> int:
    import sys

    app = QApplication(sys.argv)
    window = OrbWindow()
    window.show()
    return app.exec_()
