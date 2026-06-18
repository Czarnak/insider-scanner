"""AlertsPage widget tests (pytest-qt)."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt

from insider_scanner.gui.alerts_page import AlertsPage
from insider_scanner.persistence.alerts import AlertRule, AlertStore
from insider_scanner.persistence.feed import FeedCriteria, FeedMarket, FeedRecord
from insider_scanner.services.alerts import AlertHit


def _record(key: str = "us:1", transaction_type: str = "Buy") -> FeedRecord:
    return FeedRecord(
        key=key,
        market=FeedMarket.US,
        transaction_type=transaction_type,
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
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


class TestCreateRule:
    def test_create_from_filters(self, qtbot, monkeypatch):
        page = AlertsPage(criteria_provider=lambda: FeedCriteria(value_min=100_000))
        qtbot.addWidget(page)
        emitted: list = []
        page.rulesChanged.connect(emitted.append)

        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("Big buys", True),
        )
        page._create_from_filters()

        rule = page.store().get("Big buys")
        assert rule is not None
        assert rule.criteria.value_min == 100_000
        assert emitted and emitted[-1].get("Big buys") is not None

    def test_create_cancelled_is_noop(self, qtbot, monkeypatch):
        page = AlertsPage()
        qtbot.addWidget(page)
        monkeypatch.setattr(
            "PySide6.QtWidgets.QInputDialog.getText",
            lambda *a, **kw: ("", False),
        )
        page._create_from_filters()
        assert page.store().rules == ()


class TestToggleAndDelete:
    def test_toggle_disables_rule(self, qtbot):
        store = AlertStore(rules=(AlertRule(name="A", enabled=True),))
        page = AlertsPage(store)
        qtbot.addWidget(page)

        page.rules_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        assert page.store().get("A").enabled is False

    def test_delete_selected_rule(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        store = AlertStore(rules=(AlertRule(name="A"), AlertRule(name="B")))
        page = AlertsPage(store)
        qtbot.addWidget(page)
        page.rules_table.selectRow(0)
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.question",
            lambda *a, **kw: QMessageBox.StandardButton.Yes,
        )
        page._delete_selected_rule()
        assert [r.name for r in page.store().rules] == ["B"]


class TestCheckAndDisplay:
    def test_check_now_emits(self, qtbot):
        page = AlertsPage()
        qtbot.addWidget(page)
        emitted: list = []
        page.checkRequested.connect(lambda: emitted.append(True))
        qtbot.mouseClick(page.check_button, Qt.MouseButton.LeftButton)
        assert emitted == [True]

    def test_display_hits_populates_results_and_count(self, qtbot):
        page = AlertsPage()
        qtbot.addWidget(page)
        hits = (
            AlertHit(
                rule_name="Big",
                records=(_record("us:1"), _record("us:2")),
                match_count=2,
            ),
        )
        page.display_hits(hits)
        assert page.results_table.rowCount() == 2
        assert page.new_match_count() == 2

    def test_mark_all_seen_sets_watermark_and_clears(self, qtbot):
        store = AlertStore(rules=(AlertRule(name="A"),))
        page = AlertsPage(store)
        qtbot.addWidget(page)
        page.display_hits(
            (AlertHit(rule_name="A", records=(_record(),), match_count=1),)
        )
        emitted: list = []
        page.rulesChanged.connect(emitted.append)

        page._mark_all_seen()

        assert all(r.last_seen_at is not None for r in page.store().rules)
        assert page.results_table.rowCount() == 0
        assert page.new_match_count() == 0
        assert emitted
