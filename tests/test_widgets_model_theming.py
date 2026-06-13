"""Tests for table_style helpers in gui/theme/.

Pure helper tests — no qtbot needed for these.
TDD: Written BEFORE implementation.
"""

from __future__ import annotations

import pytest

from insider_scanner.gui.theme.tokens import DARK, LIGHT

# We import helpers from the package __init__ (as other units will)
from insider_scanner.gui.theme import (
    NUMERIC_COLS,
    IDENTIFIER_COLS,
    TEXT_COLS,
    cell_foreground,
    column_alignment,
    column_is_monospace,
)


# ---------------------------------------------------------------------------
# Frozenset membership
# ---------------------------------------------------------------------------


def test_numeric_cols_is_frozenset() -> None:
    assert isinstance(NUMERIC_COLS, frozenset)


def test_identifier_cols_is_frozenset() -> None:
    assert isinstance(IDENTIFIER_COLS, frozenset)


def test_text_cols_is_frozenset() -> None:
    assert isinstance(TEXT_COLS, frozenset)


def test_numeric_cols_contains_expected() -> None:
    # "value", "price", "shares" are numeric stems
    for stem in ("value", "price", "shares"):
        assert stem in NUMERIC_COLS, f"{stem!r} not in NUMERIC_COLS"


def test_identifier_cols_contains_expected() -> None:
    for stem in ("ticker", "cik", "accession"):
        assert stem in IDENTIFIER_COLS, f"{stem!r} not in IDENTIFIER_COLS"


def test_text_cols_contains_expected() -> None:
    for stem in (
        "insider_name",
        "company",
        "issuer",
        "insider_title",
        "congress_member",
        "source",
    ):
        assert stem in TEXT_COLS, f"{stem!r} not in TEXT_COLS"


# ---------------------------------------------------------------------------
# column_alignment
# ---------------------------------------------------------------------------


def _numeric_dtype():
    import pandas as pd

    return pd.Series([1.0]).dtype


def _object_dtype():
    import pandas as pd

    return pd.Series(["a"]).dtype


def test_column_alignment_value_is_right(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("value", _numeric_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignRight)


def test_column_alignment_shares_is_right(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("shares", _numeric_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignRight)


def test_column_alignment_ticker_is_right(qapp) -> None:
    from PySide6.QtCore import Qt

    # ticker is an identifier → AlignRight
    result = column_alignment("ticker", _object_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignRight)


def test_column_alignment_insider_name_is_left(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("insider_name", _object_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignLeft)


def test_column_alignment_company_is_left(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("company", _object_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignLeft)


def test_column_alignment_unknown_numeric_dtype_is_right(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("some_unknown_col", _numeric_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignRight)


def test_column_alignment_unknown_object_dtype_is_left(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("some_unknown_col", _object_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignLeft)


def test_column_alignment_returns_valign_vcenter(qapp) -> None:
    from PySide6.QtCore import Qt

    result = column_alignment("value", _numeric_dtype())
    assert int(result) & int(Qt.AlignmentFlag.AlignVCenter)


# ---------------------------------------------------------------------------
# column_is_monospace
# ---------------------------------------------------------------------------


def test_ticker_is_monospace() -> None:
    assert column_is_monospace("ticker") is True


def test_cik_is_monospace() -> None:
    assert column_is_monospace("cik") is True


def test_value_is_monospace() -> None:
    assert column_is_monospace("value") is True


def test_price_is_monospace() -> None:
    assert column_is_monospace("price") is True


def test_insider_name_is_not_monospace() -> None:
    assert column_is_monospace("insider_name") is False


def test_company_is_not_monospace() -> None:
    assert column_is_monospace("company") is False


def test_source_is_not_monospace() -> None:
    assert column_is_monospace("source") is False


# ---------------------------------------------------------------------------
# cell_foreground — pure dict-like rows
# ---------------------------------------------------------------------------


# Helper: build a plain dict acting as a "row"
def _buy_row() -> dict:
    return {"trade_type": "Buy", "is_congress": False}


def _sell_row() -> dict:
    return {"trade_type": "Sell", "is_congress": False}


def _purchase_row() -> dict:
    return {"trade_type": "Purchase", "is_congress": False}


def _exercise_row() -> dict:
    return {"trade_type": "Exercise", "is_congress": False}


def _sale_row() -> dict:
    return {"trade_type": "Sale", "is_congress": False}


def _congress_row() -> dict:
    return {"trade_type": "Buy", "is_congress": True}


def _neutral_row() -> dict:
    return {"trade_type": "Hold", "is_congress": False}


class DictRow:
    """Thin wrapper so .get() works like pandas Series.get()."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key: str):
        return self._data[key]


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_buy_returns_purchase_color(palette) -> None:
    row = DictRow(_buy_row())
    result = cell_foreground("trade_type", "Buy", row, palette)
    assert result == palette.purchase


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_purchase_returns_purchase_color(palette) -> None:
    row = DictRow(_purchase_row())
    result = cell_foreground("trade_type", "Purchase", row, palette)
    assert result == palette.purchase


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_exercise_returns_purchase_color(palette) -> None:
    row = DictRow(_exercise_row())
    result = cell_foreground("trade_type", "Exercise", row, palette)
    assert result == palette.purchase


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_sell_returns_sale_color(palette) -> None:
    row = DictRow(_sell_row())
    result = cell_foreground("trade_type", "Sell", row, palette)
    assert result == palette.sale


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_sale_returns_sale_color(palette) -> None:
    row = DictRow(_sale_row())
    result = cell_foreground("trade_type", "Sale", row, palette)
    assert result == palette.sale


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_congress_row_returns_warning(palette) -> None:
    row = DictRow(_congress_row())
    result = cell_foreground("trade_type", "Buy", row, palette)
    assert result == palette.warning


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_neutral_returns_none(palette) -> None:
    row = DictRow(_neutral_row())
    result = cell_foreground("trade_type", "Hold", row, palette)
    assert result is None


@pytest.mark.parametrize("palette", [LIGHT, DARK])
def test_cell_foreground_congress_takes_priority_over_buy(palette) -> None:
    # congress flag should win over trade type
    row = DictRow({"trade_type": "Buy", "is_congress": True})
    result = cell_foreground("trade_type", "Buy", row, palette)
    assert result == palette.warning


# Works with plain dict too (no .get method override)
def test_cell_foreground_with_plain_dict_buy() -> None:
    row = _buy_row()
    result = cell_foreground("trade_type", "Buy", row, LIGHT)
    assert result == LIGHT.purchase


def test_cell_foreground_with_plain_dict_neutral() -> None:
    row = _neutral_row()
    result = cell_foreground("trade_type", "Hold", row, LIGHT)
    assert result is None


# ---------------------------------------------------------------------------
# table_style: stem prefix/suffix branch (line 104)
# ---------------------------------------------------------------------------


def test_match_stem_prefix_match() -> None:
    """e.g. 'date_filed' — 'date' is a stem, 'date_filed' starts with 'date_'"""
    # "date" is in IDENTIFIER_COLS; "date_filed" starts with "date_"
    assert column_is_monospace("date_filed") is True


def test_match_stem_suffix_match() -> None:
    """e.g. 'transaction_date' ends with '_date' which matches 'date' stem."""
    assert column_is_monospace("transaction_date") is True


# ---------------------------------------------------------------------------
# column_alignment: is_numeric_dtype exception branch (lines 135-136)
# ---------------------------------------------------------------------------


def test_column_alignment_handles_non_dtype_object(qapp) -> None:
    """Pass an object that raises in is_numeric_dtype — must fall back to left."""
    from PySide6.QtCore import Qt

    class BadDtype:
        """Raises on any attribute access that pd.api.types.is_numeric_dtype tries."""

        def __repr__(self):
            return "BadDtype"

    result = column_alignment("completely_unknown_col_xyz", BadDtype())
    # Should not raise; falls back to left-aligned
    assert int(result) & int(Qt.AlignmentFlag.AlignLeft)


# ---------------------------------------------------------------------------
# cell_foreground: row without .get() (lines 179-180 fallback branch)
# ---------------------------------------------------------------------------


class NoGetRow:
    """Row-like object with no .get() — exercises the else branch."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]

    # deliberately no .get()


def test_cell_foreground_row_without_get_returns_none() -> None:
    row = NoGetRow({"trade_type": "Buy", "is_congress": False})
    # Without .get(), both is_congress and trade_type default to falsy/empty
    result = cell_foreground("trade_type", "Buy", row, LIGHT)
    # Falls into the no-.get() branch: is_congress=False, trade_type="" → None
    assert result is None
