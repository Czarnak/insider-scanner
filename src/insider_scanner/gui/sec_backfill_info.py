"""Static, CLI-only informational panel for SEC full bulk backfill."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from insider_scanner.cli import SEC_BULK_SUBMISSIONS_URL


def build_sec_backfill_info_group() -> QGroupBox:
    group = QGroupBox("SEC Full Backfill (command line)")
    layout = QVBoxLayout(group)
    label = QLabel(
        "Historical backfill runs from the command line. For 2006 Q1 onward, use:<br>"
        "<code>insider-scanner-cli sec-backfill-dataset</code><br><br>"
        "For 2003–2005 or targeted XML repairs, download the "
        "SEC submissions bulk archive (multi-GB, refreshed nightly):<br>"
        f'<a href="{SEC_BULK_SUBMISSIONS_URL}">{SEC_BULK_SUBMISSIONS_URL}</a><br><br>'
        "Then run with filing-date bounds and, for repairs, a CIK:<br>"
        "<code>insider-scanner-cli sec-backfill --zip PATH\\TO\\submissions.zip "
        "--since 2003-01-01 --until 2005-12-31 --confirm-full-backfill</code><br><br>"
        "The run is resumable — re-run the same command to continue after an "
        "interruption."
    )
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)
    layout.addWidget(label)
    return group
