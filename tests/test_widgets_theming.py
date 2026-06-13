"""Theming tests for PandasTableModel: fonts, alignment, foreground colors."""

from __future__ import annotations

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

from insider_scanner.gui.theme import get_theme_manager
from insider_scanner.gui.theme.tokens import LIGHT, ThemeMode
from insider_scanner.gui.widgets import (
    PandasTableModel,
    PriceChangeCard,
    ValueCard,
    fg_color,
    indicator_color,
)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_type": ["Buy", "Sell"],
            "insider_name": ["Jane Doe", "John Roe"],
            "value": [10000.0, 5000.0],
            "shares": [1000, 500],
            "ticker": ["AAPL", "MSFT"],
            "is_congress": [False, False],
        }
    )


@pytest.fixture(autouse=True)
def _reset_theme_mode():
    """Ensure each test starts and ends on SYSTEM mode."""
    get_theme_manager().set_mode(ThemeMode.SYSTEM)
    yield
    get_theme_manager().set_mode(ThemeMode.SYSTEM)


def _col_index(df: pd.DataFrame, name: str) -> int:
    return list(df.columns).index(name)


def test_font_role_monospace_for_numeric_and_identifier(qtbot, sample_df):
    model = PandasTableModel(sample_df)

    for col in ("value", "ticker", "shares"):
        idx = model.index(0, _col_index(sample_df, col))
        font = model.data(idx, Qt.ItemDataRole.FontRole)
        assert isinstance(font, QFont)
        assert font.styleHint() == QFont.StyleHint.Monospace


def test_font_role_none_for_text_column(qtbot, sample_df):
    model = PandasTableModel(sample_df)
    idx = model.index(0, _col_index(sample_df, "insider_name"))
    assert model.data(idx, Qt.ItemDataRole.FontRole) is None


def test_alignment_right_for_numeric_and_identifier(qtbot, sample_df):
    model = PandasTableModel(sample_df)
    for col in ("value", "ticker"):
        idx = model.index(0, _col_index(sample_df, col))
        align = model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
        assert align & Qt.AlignmentFlag.AlignRight


def test_alignment_left_for_text_column(qtbot, sample_df):
    model = PandasTableModel(sample_df)
    idx = model.index(0, _col_index(sample_df, "insider_name"))
    align = model.data(idx, Qt.ItemDataRole.TextAlignmentRole)
    assert align & Qt.AlignmentFlag.AlignLeft


def test_foreground_colors_buy_row_scoped_columns(qtbot, sample_df):
    model = PandasTableModel(sample_df)
    palette = get_theme_manager().palette()

    # Buy row: trade_type cell colored purchase.
    idx_type = model.index(0, _col_index(sample_df, "trade_type"))
    fg_type = model.data(idx_type, Qt.ItemDataRole.ForegroundRole)
    assert isinstance(fg_type, QColor)
    assert fg_type.name() == QColor(palette.purchase).name()

    # Buy row: value cell colored purchase.
    idx_value = model.index(0, _col_index(sample_df, "value"))
    fg_value = model.data(idx_value, Qt.ItemDataRole.ForegroundRole)
    assert isinstance(fg_value, QColor)
    assert fg_value.name() == QColor(palette.purchase).name()

    # Buy row: insider_name cell NOT colored (None) for non-congress row.
    idx_name = model.index(0, _col_index(sample_df, "insider_name"))
    assert model.data(idx_name, Qt.ItemDataRole.ForegroundRole) is None


def test_foreground_warning_for_congress_row_all_columns(qtbot):
    df = pd.DataFrame(
        {
            "trade_type": ["Buy"],
            "insider_name": ["Politician"],
            "value": [99999.0],
            "ticker": ["TSLA"],
            "is_congress": [True],
        }
    )
    model = PandasTableModel(df)
    palette = get_theme_manager().palette()
    warning = QColor(palette.warning).name()

    for col in ("trade_type", "insider_name", "value", "ticker"):
        idx = model.index(0, list(df.columns).index(col))
        fg = model.data(idx, Qt.ItemDataRole.ForegroundRole)
        assert isinstance(fg, QColor)
        assert fg.name() == warning


def test_palette_changed_emits_data_changed(qtbot, sample_df):
    model = PandasTableModel(sample_df)

    with qtbot.waitSignal(model.dataChanged, timeout=1000):
        # Toggle to a non-SYSTEM mode to force a palette change emission.
        get_theme_manager().set_mode(ThemeMode.DARK)


def test_price_change_card_positive_uses_purchase(qtbot):
    card = PriceChangeCard("AAPL")
    qtbot.addWidget(card)
    palette = get_theme_manager().palette()

    card.set_value(150.0, 2.5)
    assert "+2.50%" in card.chg_lbl.text()
    assert palette.purchase.lower() in card.chg_lbl.styleSheet().lower()


def test_price_change_card_negative_uses_sale(qtbot):
    card = PriceChangeCard("AAPL")
    qtbot.addWidget(card)
    palette = get_theme_manager().palette()

    card.set_value(150.0, -3.0)
    assert "-3.00%" in card.chg_lbl.text()
    assert palette.sale.lower() in card.chg_lbl.styleSheet().lower()


def test_price_change_card_handles_none_and_legacy_arg(qtbot):
    card = PriceChangeCard("AAPL")
    qtbot.addWidget(card)
    # Legacy bg_rgba arg accepted and ignored; None values handled.
    card.set_value(None, None, bg_rgba=(40, 40, 40, 120))
    assert card.price_lbl.text() == "n/a"
    assert "n/a" in card.chg_lbl.text()


def test_value_card_set_value_and_legacy_arg(qtbot):
    card = ValueCard("Total")
    qtbot.addWidget(card)
    card.set_value("42", "meta info", bg_rgba=(40, 40, 40, 120))
    assert card.value_lbl.text() == "42"
    assert card.meta_lbl.text() == "meta info"


def test_cards_reapply_style_on_palette_change(qtbot):
    card = ValueCard("Total")
    qtbot.addWidget(card)
    get_theme_manager().set_mode(ThemeMode.DARK)
    # DARK surface token should appear in the rebuilt stylesheet.
    from insider_scanner.gui.theme.tokens import DARK

    assert DARK.surface.lower() in card.styleSheet().lower()


def test_fg_color_returns_hex_tokens():
    assert fg_color(10, LIGHT) == LIGHT.error
    assert fg_color(40, LIGHT) == LIGHT.warning
    assert fg_color(60, LIGHT) == LIGHT.warning
    assert fg_color(90, LIGHT) == LIGHT.success
    # palette=None path resolves the manager's palette.
    assert isinstance(fg_color(90), str)


def test_indicator_color_bands_and_fallback():
    bands = ((0.0, 1.0, "red"), (1.0, 2.0, "green"))
    assert indicator_color(0.5, bands, LIGHT) == LIGHT.error
    assert indicator_color(1.5, bands, LIGHT) == LIGHT.success
    # Out of range -> gray fallback (text_muted).
    assert indicator_color(9.0, bands, LIGHT) == LIGHT.text_muted
    assert isinstance(indicator_color(0.5, bands), str)
