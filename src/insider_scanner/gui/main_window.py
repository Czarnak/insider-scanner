"""Main window with default OS style and tabbed interface."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
)


class MainWindow(QMainWindow):
    """Insider Scanner main window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Insider Scanner")
        self.setMinimumSize(900, 550)
        self.resize(1100, 650)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._init_scan_tab()
        self._init_congress_tab()
        self._init_european_tab()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _init_scan_tab(self):
        try:
            from insider_scanner.gui.scan_tab import ScanTab

            self.scan_tab = ScanTab()
            self.tabs.addTab(self.scan_tab, "Insider Scan")
        except Exception as exc:
            self.tabs.addTab(
                QLabel(f"Scan tab failed to load: {exc}"),
                "Insider Scan",
            )

    def _init_congress_tab(self):
        try:
            from insider_scanner.gui.congress_tab import CongressTab

            self.congress_tab = CongressTab()
            self.tabs.addTab(self.congress_tab, "Congress Scan")
        except Exception as exc:
            self.tabs.addTab(
                QLabel(f"Congress tab failed to load: {exc}"),
                "Congress Scan",
            )

    def _init_european_tab(self):
        try:
            from insider_scanner.gui.european_tab import EuropeanTab

            self.european_tab = EuropeanTab()
            self.tabs.addTab(self.european_tab, "European Insiders")
        except Exception as exc:
            self.tabs.addTab(
                QLabel(f"European tab failed to load: {exc}"),
                "European Insiders",
            )

    def log_status(self, message: str):
        self.status_bar.showMessage(message)
