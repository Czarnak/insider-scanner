"""Reusable GUI widgets: pandas table model, dashboard cards."""

from __future__ import annotations

from collections.abc import Sequence
from numbers import Real

import pandas as pd
from PySide6 import QtWidgets
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor, QFont

from insider_scanner.gui.theme import (
    cell_foreground,
    column_alignment,
    column_is_monospace,
    get_theme_manager,
)
from insider_scanner.gui.theme.tokens import FONT_SIZE_XL, RADIUS_PANEL, ThemePalette
from insider_scanner.utils.logging import get_logger

_log = get_logger("gui.widgets")

# Separate the displayed text, sortable values and the domain object attached to
# a row. The proxy forwards these roles through sorting without losing identity.
SORT_ROLE = Qt.ItemDataRole.UserRole
ROW_OBJECT_ROLE = Qt.ItemDataRole.UserRole + 1

#: Columns that receive buy/sell foreground emphasis (kept narrow so names stay
#: dominant and the whole row is not flooded with green/red).
_TRADE_FG_COLUMNS: frozenset[str] = frozenset({"trade_type", "value"})

#: Monospace font-family fallback chain used by numeric/identifier cells.
_MONO_FAMILIES: list[str] = [
    "JetBrains Mono",
    "Cascadia Mono",
    "Consolas",
    "Courier New",
]


def _build_mono_font() -> QFont:
    """Build the cached monospace QFont used for numeric/identifier columns."""
    font = QFont()
    font.setFamilies(_MONO_FAMILIES)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class PandasTableModel(QAbstractTableModel):
    """DataFrame-backed table with cached cell presentation and row metadata."""

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        parent=None,
        headers: list[str] | None = None,
    ):
        super().__init__(parent)
        self._headers = headers or []
        self._palette: ThemePalette = get_theme_manager().palette()
        self._mono_font: QFont = _build_mono_font()
        self.set_dataframe(df if df is not None else pd.DataFrame())
        get_theme_manager().paletteChanged.connect(self._on_palette_changed)

    def _on_palette_changed(self, palette: ThemePalette) -> None:
        """Refresh cached palette and repaint all cells so colors recolor live."""
        self._palette = palette
        self._cache_foregrounds()
        rows = self.rowCount()
        cols = self.columnCount()
        if rows == 0 or cols == 0:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(rows - 1, cols - 1),
        )

    def set_dataframe(
        self,
        df: pd.DataFrame,
        *,
        visible_columns: list[str] | None = None,
        row_objects: Sequence[object] | None = None,
    ) -> None:
        """Cache presentation once while keeping full, undisplayed row metadata.

        ``row_objects`` associates each source row with its domain object. Reading
        ``ROW_OBJECT_ROLE`` on a proxy index then returns the displayed object,
        including after a sort. Congress amount ranges sort by ``amount_low``.
        Explicit ``<column>_missing`` flags display as N/A and sort as missing,
        while the original numeric value remains available in ``dataframe``.
        """
        columns = list(df.columns) if visible_columns is None else list(visible_columns)
        missing = [column for column in columns if column not in df.columns]
        if missing:
            raise ValueError(f"Unknown table columns: {missing}")
        if row_objects is not None and len(row_objects) != len(df):
            raise ValueError("Table row objects must match the dataframe row count")

        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self._columns = [str(column) for column in columns]
        self._rows = self._df.to_dict(orient="records")
        self._row_objects = (
            list(row_objects) if row_objects is not None else [None] * len(df)
        )
        self._alignments = [
            column_alignment(str(column), df[column].dtype) for column in columns
        ]
        self._monospace = [column_is_monospace(str(column)) for column in columns]
        self._emphasized = [
            str(column).lower() in _TRADE_FG_COLUMNS for column in columns
        ]
        self._display: list[list[str]] = []
        self._sort: list[list[object]] = []
        for row in self._rows:
            missing_flags = [
                self._raw_value(row.get(f"{column}_missing", False)) is True
                for column in columns
            ]
            values = [
                None if missing else self._raw_value(row[column])
                for column, missing in zip(columns, missing_flags, strict=True)
            ]
            self._display.append(
                [
                    "N/A" if missing else self._display_value(value)
                    for missing, value in zip(missing_flags, values, strict=True)
                ]
            )
            self._sort.append(
                [
                    self._raw_value(row.get("amount_low"))
                    if not missing
                    and str(column).lower() == "amount_range"
                    and "amount_low" in row
                    else value
                    for column, missing, value in zip(
                        columns, missing_flags, values, strict=True
                    )
                ]
            )
        self._cache_foregrounds()
        self.endResetModel()

    @staticmethod
    def _raw_value(value):
        """Normalize pandas missing scalars and numpy scalars once per reset."""
        if pd.isna(value):
            return None
        return value.item() if hasattr(value, "item") else value

    @staticmethod
    def _display_value(value) -> str:
        if value is None:
            return ""
        return f"{value:,.2f}" if isinstance(value, float) else str(value)

    def _cache_foregrounds(self) -> None:
        self._row_foreground: list[QColor | None] = []
        self._congress_rows: list[bool] = []
        for row in self._rows:
            color = cell_foreground("", None, row, self._palette)
            self._row_foreground.append(QColor(color) if color is not None else None)
            self._congress_rows.append(color == self._palette.warning)

    def update_data(self, df: pd.DataFrame) -> None:
        """Backward-compatible alias used by older tabs."""
        self.set_dataframe(df)

    def rowCount(self, parent=QModelIndex()):  # noqa: B008 - Qt uses an immutable invalid index
        return 0 if parent.isValid() else len(self._display)

    def columnCount(self, parent=QModelIndex()):  # noqa: B008 - Qt uses an immutable invalid index
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (
            0 <= index.row() < self.rowCount()
            and 0 <= index.column() < self.columnCount()
        ):
            return None

        row, column = index.row(), index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display[row][column]
        if role == SORT_ROLE:
            return self._sort[row][column]
        if role == ROW_OBJECT_ROLE:
            return self._row_objects[row]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self._alignments[column]
        if role == Qt.ItemDataRole.FontRole:
            return self._mono_font if self._monospace[column] else None
        if role == Qt.ItemDataRole.ForegroundRole:
            if self._congress_rows[row] or self._emphasized[column]:
                return self._row_foreground[row]
            return None

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
            return self._columns[section] if 0 <= section < self.columnCount() else None
        return str(section + 1)

    @property
    def dataframe(self):
        return self._df


class SortableTableModel(QSortFilterProxyModel):
    """Proxy adding sort/filter on top of PandasTableModel.

    The source ``PandasTableModel`` carries the palette subscription; this
    proxy simply forwards its roles unchanged.
    """

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        headers: list[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._source = PandasTableModel(df=df, parent=self, headers=headers)
        self.setSourceModel(self._source)
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_dataframe(
        self,
        df: pd.DataFrame,
        *,
        visible_columns: list[str] | None = None,
        row_objects: Sequence[object] | None = None,
    ):
        self._source.set_dataframe(
            df, visible_columns=visible_columns, row_objects=row_objects
        )

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Sort missing, numeric and textual scalars consistently, even if mixed."""
        return self._sort_key(left.data(SORT_ROLE)) < self._sort_key(
            right.data(SORT_ROLE)
        )

    @staticmethod
    def _sort_key(value):
        if value is None:
            return (0, "")
        if isinstance(value, Real):
            return (1, value)
        return (2, str(value))

    def update_data(self, df: pd.DataFrame):
        """Backward-compatible alias used by older tabs."""
        self.set_dataframe(df)

    @property
    def dataframe(self):
        return self._source.dataframe


# -------------------------------------------------------------------
# Dashboard card widgets
# -------------------------------------------------------------------


class PriceChangeCard(QtWidgets.QFrame):
    """Card showing a price and 1-day % change, themed via palette tokens."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("PriceChangeCard")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        self.title_lbl = QtWidgets.QLabel(title)
        self.title_lbl.setObjectName("CardTitle")
        self.price_lbl = QtWidgets.QLabel("—")
        self.price_lbl.setObjectName("CardValue")
        self.chg_lbl = QtWidgets.QLabel("")
        self.chg_lbl.setObjectName("CardMeta")

        lay.addWidget(self.title_lbl)
        lay.addWidget(self.price_lbl)
        lay.addWidget(self.chg_lbl)
        lay.addStretch(1)

        self._apply_style(get_theme_manager().palette())
        get_theme_manager().paletteChanged.connect(self._apply_style)

    def _apply_style(self, palette: ThemePalette) -> None:
        """Rebuild the card QSS from palette tokens."""
        self.setStyleSheet(
            f"""
            QFrame#PriceChangeCard {{
                border: 1px solid {palette.border};
                border-radius: {RADIUS_PANEL}px;
                background-color: {palette.surface};
                padding: 10px;
            }}
            QLabel#CardTitle {{
                color: {palette.text_secondary};
                font-weight: 600;
            }}
            QLabel#CardValue {{
                color: {palette.text_primary};
                font-size: {FONT_SIZE_XL}px;
                font-weight: 700;
            }}
            QLabel#CardMeta {{
                color: {palette.text_muted};
                font-weight: 600;
            }}
            """
        )

    def set_value(
        self,
        price_usd: float | None,
        pct_change: float | None,
        bg_rgba: tuple[int, int, int, int] | None = None,
    ):
        if price_usd is None:
            self.price_lbl.setText("n/a")
        else:
            self.price_lbl.setText(f"{price_usd:,.2f} USD")

        if pct_change is None:
            self.chg_lbl.setText("Δ1D: n/a")
            self.chg_lbl.setStyleSheet(
                f"color: {get_theme_manager().palette().text_muted}; font-weight: 600;"
            )
        else:
            sign = "+" if pct_change >= 0 else "-"
            magnitude = abs(pct_change)
            self.chg_lbl.setText(f"Δ1D: {sign}{magnitude:.2f}%")
            palette = get_theme_manager().palette()
            color = palette.purchase if pct_change >= 0 else palette.sale
            self.chg_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")


class ValueCard(QtWidgets.QFrame):
    """Generic card showing a title, large value, and optional meta text."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("ValueCard")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        self.title_lbl = QtWidgets.QLabel(title)
        self.title_lbl.setObjectName("CardTitle")
        self.value_lbl = QtWidgets.QLabel("—")
        self.value_lbl.setObjectName("CardValue")
        self.meta_lbl = QtWidgets.QLabel("")
        self.meta_lbl.setObjectName("CardMeta")

        lay.addWidget(self.title_lbl)
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.meta_lbl)
        lay.addStretch(1)

        self._apply_style(get_theme_manager().palette())
        get_theme_manager().paletteChanged.connect(self._apply_style)

    def _apply_style(self, palette: ThemePalette) -> None:
        """Rebuild the card QSS from palette tokens."""
        self.setStyleSheet(
            f"""
            QFrame#ValueCard {{
                border: 1px solid {palette.border};
                border-radius: {RADIUS_PANEL}px;
                background-color: {palette.surface};
                padding: 10px;
            }}
            QLabel#CardTitle {{
                color: {palette.text_secondary};
                font-weight: 600;
            }}
            QLabel#CardValue {{
                color: {palette.text_primary};
                font-size: {FONT_SIZE_XL}px;
                font-weight: 700;
            }}
            QLabel#CardMeta {{
                color: {palette.text_muted};
            }}
            """
        )

    def set_value(
        self,
        value_text: str,
        meta_text: str = "",
        bg_rgba: tuple[int, int, int, int] | None = None,
    ):
        self.value_lbl.setText(value_text)
        self.meta_lbl.setText(meta_text)


# -------------------------------------------------------------------
# Color helpers for dashboard
# -------------------------------------------------------------------


def fg_color(value: int, palette: ThemePalette | None = None) -> str:
    """Map a 0–100 Fear & Greed score to a semantic palette color (hex str)."""
    if palette is None:
        palette = get_theme_manager().palette()
    if value < 25:
        return palette.error  # Extreme Fear — red
    if value < 50:
        return palette.warning  # Fear — amber
    if value < 75:
        return palette.warning  # Greed — amber
    return palette.success  # Extreme Greed — green


def indicator_color(
    value: float,
    bands: tuple[tuple[float, float, str], ...],
    palette: ThemePalette | None = None,
) -> str:
    """Map a numeric value to a semantic palette color (hex str) via bands."""
    if palette is None:
        palette = get_theme_manager().palette()
    color_map = {
        "red": palette.error,
        "orange": palette.warning,
        "yellow": palette.warning,
        "green": palette.success,
        "gray": palette.text_muted,
    }
    for lo, hi, name in bands:
        if lo <= value < hi:
            return color_map.get(name, color_map["gray"])
    return color_map["gray"]
