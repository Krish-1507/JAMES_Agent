"""Qt shell for the Phase-3 desktop app: hosts the `james serve` web UI.

The FastAPI sidecar (``james.ui.server``) runs in a background thread and is
displayed inside a QWebEngineView window with a system tray icon (minimize to
tray, tray menu, tray balloon on hide).

Falls back to plain browser mode when PyQt5/PyQtWebEngine are not installed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("james")

TRAY_BALLOON_MS = 2000


def run_ui(port: int = 8124) -> int:
    """Start the sidecar + Qt shell. Returns the process exit code."""
    try:
        from PyQt5.QtCore import QUrl
        from PyQt5.QtWebEngineWidgets import QWebEngineView
        from PyQt5.QtWidgets import (
            QApplication,
            QMenu,
            QMessageBox,
            QSystemTrayIcon,
        )
    except ImportError:
        log.info("PyQt5/PyQtWebEngine not installed — falling back to browser mode.")
        from .server import serve_cli

        return serve_cli(port=port)

    from .server import run_server

    holder: dict[str, Any] = {}

    def _start() -> None:
        try:
            holder["runtime"] = run_server(port=port, open_browser=False)
        except Exception:
            log.exception("Failed to start the JAMES server")
            holder["error"] = True

    threading.Thread(target=_start, daemon=True).start()

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)  # tray keeps the process alive

    url = f"http://127.0.0.1:{port}/"
    view = QWebEngineView()
    view.setWindowTitle("JAMES")
    view.resize(1180, 780)
    view.load(QUrl(url))

    tray = QSystemTrayIcon(parent=app)
    tray.setIcon(view.windowIcon())
    tray.setToolTip("JAMES")

    menu = QMenu()
    show_action = menu.addAction("Show JAMES")
    show_action.triggered.connect(lambda: (view.show(), view.raise_(), view.activateWindow()))
    menu.addSeparator()
    quit_action = menu.addAction("Quit")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            (view.show(), view.raise_(), view.activateWindow())
            if reason == QSystemTrayIcon.Trigger
            else None
        )
    )
    tray.show()

    def _error_watch() -> None:
        deadline = time.time() + 15
        while time.time() < deadline and "runtime" not in holder and "error" not in holder:
            time.sleep(0.1)
        if holder.get("error"):
            QMessageBox.critical(view, "JAMES", "Failed to start the JAMES server — see the logs.")
            app.quit()

    threading.Thread(target=_error_watch, daemon=True).start()

    def _close_to_tray(event) -> None:
        if tray.isVisible():
            event.ignore()
            view.hide()
            tray.showMessage(
                "JAMES",
                "Still running in the system tray.",
                QSystemTrayIcon.Information,
                TRAY_BALLOON_MS,
            )
        else:
            event.accept()

    view.closeEvent = _close_to_tray  # type: ignore[method-assign]
    view.show()

    code = app.exec_()

    runtime = holder.get("runtime")
    if runtime is not None:
        try:
            runtime.stop(timeout=2.0)
        except Exception:
            log.exception("Error stopping the JAMES server")
    return code
