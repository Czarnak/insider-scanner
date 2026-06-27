"""Static, CLI-only informational panel for SEC full bulk backfill."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from insider_scanner.cli import SEC_BULK_SUBMISSIONS_URL


def build_sec_backfill_info_group() -> QGroupBox:
    group = QGroupBox("SEC Full Backfill (command line)")
    layout = QVBoxLayout(group)
    label = QLabel(
        "Full historical backfill runs from the command line. First download the "
        "SEC submissions bulk archive (multi-GB, refreshed nightly):<br>"
        f'<a href="{SEC_BULK_SUBMISSIONS_URL}">{SEC_BULK_SUBMISSIONS_URL}</a><br><br>'
        "Then run:<br>"
        "<code>insider-scanner-cli sec-backfill --zip PATH\\TO\\submissions.zip "
        "--confirm-full-backfill</code><br><br>"
        "The run is resumable — re-run the same command to continue after an "
        "interruption."
    )
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)
    layout.addWidget(label)
    return group
