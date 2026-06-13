"""Theming tests for PriceChartWidget and the chart_colors helper."""

from __future__ import annotations

from datetime import date

import pytest

from insider_scanner.core.prices.model import PriceBar
from insider_scanner.gui.price_chart import ChartColors, PriceChartWidget, chart_colors
from insider_scanner.gui.theme.tokens import DARK, LIGHT


def test_chart_colors_distinct_buy_vs_sell_light():
    colors = chart_colors(LIGHT)
    assert isinstance(colors, ChartColors)
    assert colors.buy_brush != colors.sell_brush
    assert colors.buy_pen != colors.sell_pen


def test_chart_colors_distinct_buy_vs_sell_dark():
    colors = chart_colors(DARK)
    assert colors.buy_brush != colors.sell_brush
    assert colors.buy_pen != colors.sell_pen


def test_chart_colors_light_and_dark_differ():
    light = chart_colors(LIGHT)
    dark = chart_colors(DARK)
    assert light.background != dark.background
    assert light.price != dark.price


@pytest.fixture
def chart(qtbot):
    w = PriceChartWidget()
    qtbot.addWidget(w)
    return w


def test_widget_constructs_without_error(chart):
    assert chart.plot_widget is not None
    assert chart.buy_scatter is not None
    assert chart.sell_scatter is not None
    assert chart._vline is not None
    assert chart._hline is not None


def test_apply_palette_repens_after_set_price_data(chart):
    bars = [
        PriceBar("AAPL", date(2026, 1, 5), 1, 2, 0.5, 1.5, 10),
        PriceBar("AAPL", date(2026, 1, 6), 1, 2, 0.5, 1.8, 10),
    ]
    chart.set_price_data(bars)
    assert chart.price_curve is not None

    # Re-pen with both palettes without raising.
    chart._apply_palette(DARK)
    chart._apply_palette(LIGHT)
