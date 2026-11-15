"""Alert acknowledgment and scan refresh regressions for PR 22."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from insider_scanner.core.models import InsiderTrade
from insider_scanner.gui.alerts_page import AlertsPage
from insider_scanner.gui.entity_pages import CompanyPage, InsiderPage
from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import FeedMarket, FeedRecord
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.alerts import AlertHit
from insider_scanner.services.context import open_persistence


def _record(key="us:1", *, created_at=datetime(2024, 6, 1, tzinfo=UTC)):
    return FeedRecord(
        key=key,
        market=FeedMarket.US,
        transaction_type="Buy",
        issuer="Example Corp",
        identifier="AAPL",
        person="Jane Director",
        role="CEO",
        transaction_date=None,
        filing_date=None,
        quantity=None,
        price=None,
        value_display="",
        value_sort=200_000.0,
        currency="USD",
        source="test",
        source_url="",
        created_at=created_at,
    )


class InlinePool:
    def start(self, worker):
        worker.run()


class QueuedPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


@pytest.fixture
def window(qtbot, tmp_path):
    from insider_scanner.gui.main_window import MainWindow

    persistence = open_persistence(tmp_path / "refresh.sqlite3")
    services = SimpleNamespace(
        persistence=persistence, us=None, congress=None, european=None
    )
    widget = MainWindow(services, state_path=tmp_path / "feed_state.json")
    qtbot.addWidget(widget)
    widget._thread_pool.waitForDone(5_000)
    yield widget
    widget._thread_pool.waitForDone(5_000)
    widget.close()
    persistence.close()


def test_acknowledge_uses_presented_timestamp_and_preserves_unshown_rule(qtbot):
    page = AlertsPage(AlertStore(rules=(AlertRule("Shown"), AlertRule("Unshown"))))
    qtbot.addWidget(page)
    presented_at = datetime(2024, 6, 1, tzinfo=UTC)
    page.display_hits((AlertHit("Shown", (_record(created_at=presented_at),), 1),))

    page._mark_all_seen()

    assert page.store().get("Shown").last_seen_at == presented_at
    assert page.store().get("Unshown").last_seen_at is None


def test_acknowledge_only_advances_through_presented_ingestion_sequence(qtbot):
    store = AlertStore(
        rules=(
            AlertRule("Shown", last_seen_sequence=3),
            AlertRule("Unshown", last_seen_sequence=4),
        )
    )
    page = AlertsPage(store)
    qtbot.addWidget(page)
    page.display_hits(
        (AlertHit("Shown", (_record(created_at=None),), 501, last_seen_sequence=7),)
    )

    page._mark_all_seen()

    assert page.store().get("Shown").last_seen_sequence == 7
    assert page.store().get("Shown").last_seen_at is None
    assert page.store().get("Unshown").last_seen_sequence == 4
    assert page.new_match_count() == 0


@pytest.mark.parametrize("records", [(), (_record(created_at=None),)])
def test_acknowledge_without_known_progress_never_uses_current_time(qtbot, records):
    page = AlertsPage(AlertStore(rules=(AlertRule("A"),)))
    qtbot.addWidget(page)
    page.display_hits((AlertHit("A", records, len(records)),))

    page._mark_all_seen()

    assert page.store().get("A").last_seen_at is None


def test_display_and_clear_publish_match_count_and_capped_summary(qtbot):
    page = AlertsPage()
    qtbot.addWidget(page)
    assert hasattr(page, "matchCountChanged")
    counts = []
    page.matchCountChanged.connect(counts.append)

    page.display_hits((AlertHit("A", (_record(),), 4),))

    assert counts == [4]
    assert "and 3 more" in page.summary_label.text()
    page.display_hits(())
    assert counts == [4, 0]


@pytest.mark.parametrize("action", ["toggle", "delete"])
def test_rule_mutation_clears_old_hits_before_publishing_store(
    qtbot, monkeypatch, action
):
    page = AlertsPage(AlertStore(rules=(AlertRule("A"),)))
    qtbot.addWidget(page)
    page.display_hits((AlertHit("A", (_record(),), 1),))
    counts_at_publish = []
    page.rulesChanged.connect(
        lambda _: counts_at_publish.append(page.new_match_count())
    )
    if action == "toggle":
        page.rules_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    else:
        page.rules_table.selectRow(0)
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes
        )
        page._delete_selected_rule()

    assert counts_at_publish == [0]
    assert page.results_table.rowCount() == 0


@pytest.mark.parametrize("page_type", [CompanyPage, InsiderPage])
def test_entity_reload_retains_current_selection_and_reads_new_rows(qtbot, page_type):
    records = []
    repository = SimpleNamespace(
        recent_by_issuer=lambda *args, **kwargs: tuple(records),
        recent_by_person=lambda *args, **kwargs: tuple(records),
    )
    page = page_type(repository, thread_pool=InlinePool())
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)
    assert callable(getattr(page, "reload", None))

    records.append(_record())
    page.reload()

    assert page.model.rowCount() == 1
    assert page.model.records()[0].key == "us:1"
    page.load("", FeedMarket.US)
    records.append(_record("us:2"))
    page.reload()
    assert page.model.rowCount() == 0


def test_blank_entity_selection_invalidates_pending_result(qtbot):
    pool = QueuedPool()
    page = CompanyPage(SimpleNamespace(), thread_pool=pool)
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)
    page.load("", FeedMarket.US)

    pool.workers[0].signals.result.emit((_record(),))

    assert page.model.rowCount() == 0
    assert "Search" in page.state_label.text()


def test_old_entity_worker_cannot_hide_current_request_progress(qtbot):
    pool = QueuedPool()
    page = CompanyPage(SimpleNamespace(), thread_pool=pool)
    qtbot.addWidget(page)
    page.load("AAPL", FeedMarket.US)
    page.load("MSFT", FeedMarket.US)

    pool.workers[0].signals.finished.emit()

    assert not page.progress.isHidden()
    pool.workers[1].signals.finished.emit()
    assert page.progress.isHidden()


@pytest.mark.parametrize("tab_name", ["scan_tab", "congress_tab", "european_tab"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_scan_completion_refreshes_feed_entities_and_alerts(
    window, monkeypatch, tab_name, cancelled
):
    calls = []
    for name in ("feed_page", "company_page", "insider_page"):
        page = getattr(window, name)
        assert callable(getattr(page, "reload", None))
        monkeypatch.setattr(page, "reload", lambda n=name: calls.append(n))
    monkeypatch.setattr(window, "_check_alerts", lambda: calls.append("alerts"))
    tab = getattr(window, tab_name)
    assert hasattr(tab, "dataChanged")
    if cancelled:
        tab._cancel_event.set()
    callback = tab._on_scan_result if tab_name == "european_tab" else tab._on_scan_done

    callback([])

    assert calls == ["feed_page", "company_page", "insider_page", "alerts"]


def test_disabling_all_rules_invalidates_inflight_evaluation(window):
    rule = AlertRule("A")
    window.alerts_page.set_store(AlertStore(rules=(rule,)))
    old_request = window._alert_request_id
    window.alerts_page.set_store(AlertStore(rules=(replace(rule, enabled=False),)))

    window._check_alerts()
    window._on_alerts_evaluated(old_request, (AlertHit("A", (_record(),), 1),))

    assert window.alerts_page.new_match_count() == 0
    assert window.alerts_button.text() == "Alerts"


def test_rule_changes_clear_badge_and_schedule_new_evaluation(window, monkeypatch):
    window.alerts_page.set_store(AlertStore(rules=(AlertRule("A"),)))
    window.alerts_page.display_hits((AlertHit("A", (_record(),), 1),))
    window._update_alert_badge(1)
    calls = []
    monkeypatch.setattr(window, "_check_alerts", lambda: calls.append("check"))
    old_request = window._alert_request_id

    window.alerts_page.rules_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)

    assert window.alerts_button.text() == "Alerts"
    assert calls == ["check"]
    window._on_alerts_evaluated(old_request, (AlertHit("A", (_record(),), 1),))
    assert window.alerts_page.new_match_count() == 0


def test_watchlist_changes_recheck_dependent_alerts(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "_check_alerts", lambda: calls.append("check"))

    window._on_watchlists_changed(WatchlistStore())

    assert calls == ["check"]


def test_persisted_scan_rows_refresh_selected_pages_and_raise_alert(window, qtbot):
    window.company_page.load("AAPL", FeedMarket.US)
    window.insider_page.load("Jane Director", FeedMarket.US)
    window.alerts_page.set_store(AlertStore(rules=(AlertRule("All"),)))
    window._thread_pool.waitForDone(5_000)
    qtbot.waitUntil(lambda: window.company_page.progress.isHidden())
    assert window.feed_page.model.rowCount() == 0
    assert window.company_page.model.rowCount() == 0
    assert window.insider_page.model.rowCount() == 0
    trade = InsiderTrade(
        ticker="AAPL",
        company="Example Corp",
        insider_name="Jane Director",
        trade_type="Buy",
        trade_date=date(2024, 6, 1),
        source="sec_edgar",
        accession_number="0000000001-24-000001",
        sec_row_id="1",
    )
    window._services.persistence.us_trades.upsert([trade])

    window.scan_tab._on_scan_done([trade])

    qtbot.waitUntil(lambda: window.feed_page.model.rowCount() == 1)
    qtbot.waitUntil(lambda: window.company_page.model.rowCount() == 1)
    qtbot.waitUntil(lambda: window.insider_page.model.rowCount() == 1)
    qtbot.waitUntil(lambda: window.alerts_button.text() == "Alerts (1)")
    window.alerts_page._mark_all_seen()
    qtbot.waitUntil(lambda: window.alerts_button.text() == "Alerts")
    assert window.alerts_page.store().get("All").last_seen_sequence is not None


@pytest.mark.parametrize("tab_name", ["scan_tab", "congress_tab", "european_tab"])
def test_failed_scan_refreshes_already_committed_rows(window, monkeypatch, tab_name):
    monkeypatch.setattr(window._thread_pool, "start", lambda worker: worker.run())
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    window.company_page.load("AAPL", FeedMarket.US)
    window.insider_page.load("Jane Director", FeedMarket.US)
    window.alerts_page.set_store(AlertStore(rules=(AlertRule("All"),)))
    window._services.persistence.us_trades.upsert(
        [
            InsiderTrade(
                ticker="AAPL",
                company="Example Corp",
                insider_name="Jane Director",
                trade_type="Buy",
                source="sec_edgar",
                accession_number="0000000001-24-000001",
                sec_row_id="1",
            )
        ]
    )

    getattr(window, tab_name)._on_scan_error(
        (RuntimeError, RuntimeError("A later provider failed"), None)
    )

    assert window.feed_page.model.rowCount() == 1
    assert window.company_page.model.rowCount() == 1
    assert window.insider_page.model.rowCount() == 1
    assert window.alerts_button.text() == "Alerts (1)"
