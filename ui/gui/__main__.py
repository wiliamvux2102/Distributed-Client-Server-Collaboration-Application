"""Entry point for running the PySide6 desktop GUI (`python -m ui.gui`)."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CollabChat")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
