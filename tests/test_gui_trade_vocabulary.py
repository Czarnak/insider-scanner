"""The same transaction retains its direction and value across GUI views."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from insider_scanner.gui.alerts_page import AlertsPage, _format_value
from insider_scanner.gui.entity_pages import _build_summary
from insider_scanner.gui.feed_page import FeedTableModel
from insider_scanner.gui.investigation_drawer import InvestigationDrawer
from insider_scanner.gui.price_chart import PriceChartWidget
from insider_scanner.gui.theme import cell_foreground, get_theme_manager
from insider_scanner.gui.theme.tokens import DARK, LIGHT
from insider_scanner.gui.trade_display import format_value
from insider_scanner.persistence.feed import FeedMarket, FeedRecord
from tests.factories import congress_trade, us_trade


def _record(**changes) -> FeedRecord:
    record = FeedRecord(
        key="us:example",
        market=FeedMarket.US,
        transaction_type="Exercise",
        issuer="Example Corp",
        identifier="AAPL",
        person="Jane Director",
        role="Director",
        transaction_date=date(2026, 1, 5),
        filing_date=date(2026, 1, 6),
        quantity=100,
        price=25,
        value_display="",
        value_sort=2500,
        currency="USD",
        source="test",
        source_url="",
    )
    return replace(record, **changes)


@pytest.mark.parametrize(
    ("trade_type", "color_name"),
    [
        (" Buy ", "purchase"),
        ("pUrChAsE", "purchase"),
        (" eXeRcIsE ", "purchase"),
        ("sElL", "sale"),
        (" SALE ", "sale"),
        ("Exchange", None),
        ("Gift", None),
        ("Other", None),
    ],
)
def test_direction_colors_agree_across_scan_feed_drawer_and_alerts(
    qtbot, trade_type, color_name
):
    palette = get_theme_manager().palette()
    expected = QColor(getattr(palette, color_name)) if color_name else None
    model = FeedTableModel()
    model.replace_records((_record(transaction_type=trade_type),))
    drawer = InvestigationDrawer(None)
    alerts = AlertsPage()
    qtbot.addWidget(drawer)
    qtbot.addWidget(alerts)

    feed_brush = model.data(model.index(0, 1), Qt.ItemDataRole.ForegroundRole)
    alert_brush = alerts._type_brush(trade_type)
    table_color = cell_foreground(
        "trade_type", trade_type, {"trade_type": trade_type}, palette
    )

    if expected is not None:
        assert feed_brush.color() == expected
        assert alert_brush.color() == expected
        assert QColor(table_color) == expected
        assert drawer._transaction_color(trade_type) == expected
    else:
        assert feed_brush is None
        assert alert_brush is None
        assert table_color is None
        assert drawer._transaction_color(trade_type) == QColor(palette.text_primary)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"value_display": "$1,001 - $15,000", "value_sort": 1001}, "$1,001 - $15,000"),
        ({"value_sort": 1234567.5}, "1,234,567.50 USD"),
        ({"value_sort": 0, "currency": ""}, "0.00"),
        ({"value_sort": None}, "—"),
    ],
)
def test_reported_value_is_consistent_across_feed_drawer_and_alerts(changes, expected):
    record = _record(**changes)

    assert format_value(record) == expected
    assert FeedTableModel._display_value(record, 10) == expected
    assert InvestigationDrawer._format_value(record) == expected
    assert _format_value(record) == expected


def test_entity_summary_counts_exercise_and_leaves_neutral_transactions_unclassified():
    records = tuple(
        _record(transaction_type=trade_type)
        for trade_type in (" eXeRcIsE ", "Purchase", "Sell", "Exchange", "Gift")
    )

    assert _build_summary(records) == (
        "Showing latest 5 transactions · 2 buys · 1 sells · latest 2026-01-05"
    )


def test_chart_keeps_other_transactions_out_of_sell_markers_and_clears_all_groups(
    qtbot,
):
    chart = PriceChartWidget()
    qtbot.addWidget(chart)
    trades = [
        us_trade(trade_type=" Buy ", trade_date=date(2026, 1, 1)),
        us_trade(trade_type="eXeRcIsE", trade_date=date(2026, 1, 2)),
        congress_trade(trade_type="Purchase", trade_date=date(2026, 1, 3)),
        congress_trade(trade_type=" SALE ", trade_date=date(2026, 1, 4)),
        congress_trade(trade_type="Exchange", trade_date=date(2026, 1, 5)),
        us_trade(trade_type="Other", trade_date=date(2026, 1, 6)),
        us_trade(trade_type="Gift", trade_date=date(2026, 1, 7)),
        us_trade(trade_type="Sell", trade_date=None),
    ]

    chart.set_trade_markers(trades)

    def dates(scatter):
        xs, _ys = scatter.getData()
        return [datetime.fromtimestamp(x, tz=UTC).date() for x in xs]

    assert dates(chart.buy_scatter) == [date(2026, 1, day) for day in (1, 2, 3)]
    assert dates(chart.sell_scatter) == [date(2026, 1, 4)]
    assert dates(chart.other_scatter) == [date(2026, 1, day) for day in (5, 6, 7)]

    chart.set_trade_markers([])

    assert len(chart.buy_scatter.data) == 0
    assert len(chart.sell_scatter.data) == 0
    assert len(chart.other_scatter.data) == 0


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_chart_other_markers_remain_neutral_when_theme_changes(qtbot, palette):
    chart = PriceChartWidget()
    qtbot.addWidget(chart)
    chart.set_trade_markers([us_trade(trade_type="Other")])

    chart._apply_palette(palette)

    other_color = chart.other_scatter.opts["brush"].color()
    assert other_color == QColor(palette.text_muted)
    assert other_color != QColor(palette.purchase)
    assert other_color != QColor(palette.sale)
    assert chart.other_scatter.opts["symbol"] == "o"
