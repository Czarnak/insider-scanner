"""Serial GUI test — see auto-memory 'gui-tests-serial-only': never run GUI tests
in parallel (PySide6 QApplication COM deadlock on Windows)."""

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QLabel
from insider_scanner.gui.sec_backfill_info import build_sec_backfill_info_group
from insider_scanner.cli import SEC_BULK_SUBMISSIONS_URL


def test_info_group_shows_link_and_command(qapp):  # qapp: pytest-qt shared fixture
    group = build_sec_backfill_info_group()
    text = group.findChild(QLabel).text()
    assert SEC_BULK_SUBMISSIONS_URL in text
    assert "sec-backfill" in text
