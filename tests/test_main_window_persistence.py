"""MainWindow persistence-failure surfacing tests.

Saving feed state, watchlists, and alerts must not fail silently: an access
(permission) failure surfaces a clear, non-technical status-bar message, and a
generic failure still surfaces a message rather than being swallowed.
"""

from __future__ import annotations

import pytest

from insider_scanner.gui import main_window as mw


@pytest.fixture
def window(qtbot, tmp_path):
    win = mw.MainWindow(services=None, state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(win)
    return win


def test_persist_feed_state_permission_error_surfaces_writable_message(
    window, monkeypatch
):
    def boom(path, state):
        raise PermissionError("denied")

    monkeypatch.setattr(mw, "save_feed_state", boom)

    window._persist_feed_state()  # must not raise

    message = window.status_bar.currentMessage().lower()
    assert "couldn't save filters" in message
    assert "writable" in message


def test_persist_feed_state_generic_error_surfaces_message_without_writable(
    window, monkeypatch
):
    def boom(path, state):
        raise RuntimeError("disk full")

    monkeypatch.setattr(mw, "save_feed_state", boom)

    window._persist_feed_state()

    message = window.status_bar.currentMessage().lower()
    assert "couldn't save filters" in message
    assert "writable" not in message


def test_persist_watchlists_permission_error_surfaces_message(window, monkeypatch):
    def boom(path, store):
        raise PermissionError("denied")

    monkeypatch.setattr(mw, "save_watchlists", boom)

    window._persist_watchlists()

    message = window.status_bar.currentMessage().lower()
    assert "couldn't save watchlists" in message
    assert "writable" in message


def test_persist_alerts_permission_error_surfaces_message(window, monkeypatch):
    def boom(path, store):
        raise PermissionError("denied")

    monkeypatch.setattr(mw, "save_alerts", boom)

    window._persist_alerts()

    message = window.status_bar.currentMessage().lower()
    assert "couldn't save alerts" in message
    assert "writable" in message
