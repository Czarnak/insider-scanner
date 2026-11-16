"""Table sorting, identity and cached rendering regressions for PR 24."""

from __future__ import annotations

import pandas as pd
import pytest
from PySide6.QtCore import Qt

from insider_scanner.gui import widgets
from insider_scanner.gui.theme import get_theme_manager


@pytest.mark.parametrize("column", ["shares", "price", "value"])
def test_numeric_sort_uses_raw_values_and_retains_missing_rows(qtbot, column):
    model = widgets.SortableTableModel()
    model.set_dataframe(pd.DataFrame({column: [95.0, 1_000_000.0, None, 200.0]}))

    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data() for row in range(4)] == [
        "",
        "95.00",
        "200.00",
        "1,000,000.00",
    ]
    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert [model.index(row, 0).data() for row in range(4)] == [
        "1,000,000.00",
        "200.00",
        "95.00",
        "",
    ]


@pytest.mark.parametrize("column", ["shares", "price", "value"])
def test_explicit_missing_flag_changes_display_and_sort_without_replacing_raw_value(
    qtbot, column
):
    model = widgets.SortableTableModel()
    frame = pd.DataFrame(
        {column: [95.0, 0.0, 0.0], f"{column}_missing": [False, True, False]}
    )
    model.set_dataframe(frame, visible_columns=[column])

    assert model.index(1, 0).data() == "N/A"
    assert model.index(1, 0).data(widgets.SORT_ROLE) is None
    assert model.dataframe[column].tolist() == [95.0, 0.0, 0.0]
    assert frame[column].tolist() == [95.0, 0.0, 0.0]
    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data() for row in range(3)] == ["N/A", "0.00", "95.00"]
    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert [model.index(row, 0).data() for row in range(3)] == ["95.00", "0.00", "N/A"]


def test_range_sort_and_domain_identity_use_hidden_metadata(qtbot):
    model = widgets.SortableTableModel()
    objects = [object(), object(), object()]
    model.set_dataframe(
        pd.DataFrame(
            {
                "amount_range": [
                    "$100,001 - $250,000",
                    "$15,001 - $50,000",
                    "$1,001 - $15,000",
                ],
                "amount_low": [100_001, 15_001, 1_001],
                "is_congress": [True, False, False],
                "trade_type": ["Purchase", "Sale", "Purchase"],
            }
        ),
        visible_columns=["amount_range"],
        row_objects=objects,
    )

    assert model.columnCount() == 1
    assert "is_congress" in model.dataframe.columns
    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data() for row in range(3)] == [
        "$1,001 - $15,000",
        "$15,001 - $50,000",
        "$100,001 - $250,000",
    ]
    index = model.index(2, 0)
    assert index.data(widgets.ROW_OBJECT_ROLE) is objects[0]
    assert index.data(widgets.SORT_ROLE) == 100_001
    assert (
        index.data(Qt.ItemDataRole.ForegroundRole).name()
        == get_theme_manager().palette().warning.lower()
    )


def test_cell_reads_use_cached_values_and_styles(qtbot, monkeypatch):
    model = widgets.PandasTableModel()
    model.set_dataframe(
        pd.DataFrame(
            {"ticker": ["AAPL"], "value": [1234.5], "trade_type": ["Purchase"]}
        )
    )

    def uncached(*args, **kwargs):
        raise AssertionError(
            "cell reads must not recompute pandas access or column/row styles"
        )

    monkeypatch.setattr(pd.DataFrame, "iloc", property(uncached))
    monkeypatch.setattr(widgets, "column_alignment", uncached)
    monkeypatch.setattr(widgets, "column_is_monospace", uncached)
    monkeypatch.setattr(widgets, "cell_foreground", uncached)
    for _ in range(3):
        assert model.index(0, 1).data() == "1,234.50"
        assert (
            model.index(0, 1).data(Qt.ItemDataRole.TextAlignmentRole)
            & Qt.AlignmentFlag.AlignRight
        )
        assert model.index(0, 1).data(Qt.ItemDataRole.FontRole) is not None
        assert model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole) is None
        assert (
            model.index(0, 1).data(Qt.ItemDataRole.ForegroundRole).name()
            == get_theme_manager().palette().purchase.lower()
        )


def test_replacing_data_resets_identity_and_palette_recolors_cached_rows(qtbot):
    from dataclasses import replace

    model = widgets.PandasTableModel()
    model.set_dataframe(
        pd.DataFrame({"ticker": ["OLD"], "is_congress": [True]}),
        visible_columns=["ticker"],
        row_objects=[object()],
    )
    palette = replace(get_theme_manager().palette(), warning="#123456")
    model._on_palette_changed(palette)
    assert model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole).name() == "#123456"
    model.set_dataframe(pd.DataFrame({"ticker": ["NEW"]}))
    assert model.index(0, 0).data() == "NEW"
    assert model.index(0, 0).data(widgets.ROW_OBJECT_ROLE) is None
    assert model.index(0, 0).data(Qt.ItemDataRole.ForegroundRole) is None


def test_mixed_values_have_deterministic_numeric_then_text_order(qtbot):
    model = widgets.SortableTableModel(
        pd.DataFrame({"value": ["unknown", 95, 1000, None]})
    )
    model.sort(0, Qt.SortOrder.AscendingOrder)
    assert [model.index(row, 0).data() for row in range(4)] == [
        "",
        "95",
        "1000",
        "unknown",
    ]
