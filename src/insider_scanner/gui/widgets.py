"""Reusable GUI widgets: pandas table model, dashboard cards."""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd
from PySide6 import QtWidgets
from PySide6.QtCore import (
    Qt,
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
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
    """Qt table model backed by a pandas DataFrame, themed via the theme manager."""

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        parent=None,
        headers: list[str] | None = None,
    ):
        super().__init__(parent)
        self._df = df if df is not None else pd.DataFrame()
        self._headers = headers or []
        self._palette: ThemePalette = get_theme_manager().palette()
        self._mono_font: QFont = _build_mono_font()
        get_theme_manager().paletteChanged.connect(self._on_palette_changed)

    def _on_palette_changed(self, palette: ThemePalette) -> None:
        """Refresh cached palette and repaint all cells so colors recolor live."""
        self._palette = palette
        rows = len(self._df)
        cols = len(self._df.columns)
        if rows == 0 or cols == 0:
            return
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(rows - 1, cols - 1),
        )

    def set_dataframe(self, df: pd.DataFrame) -> None:
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self.endResetModel()

    def update_data(self, df: pd.DataFrame) -> None:
        """Backward-compatible alias used by older tabs."""
        self.set_dataframe(df)

    def rowCount(self, parent=QModelIndex()):
        return len(self._df)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        col_name = str(self._df.columns[index.column()])

        if role == Qt.ItemDataRole.DisplayRole:
            val = self._df.iloc[index.row(), index.column()]
            # pandas stores None and np.nan as NaN in numeric columns.
            # pd.isna() catches both; return empty string so cells appear
            # blank rather than showing "nan" or "None".
            if pd.isna(val):
                return ""
            if isinstance(val, float):
                return f"{val:,.2f}"
            return str(val)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            return column_alignment(col_name, self._df.dtypes.iloc[index.column()])

        if role == Qt.ItemDataRole.FontRole:
            if column_is_monospace(col_name):
                return self._mono_font
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            row = self._df.iloc[index.row()]
            val = self._df.iloc[index.row(), index.column()]
            fg = cell_foreground(col_name, val, row, self._palette)
            if fg is None:
                return None
            # Congress is a row-level categorical state: color the whole row
            # (amber) to preserve the prior whole-row emphasis.
            if fg == self._palette.warning:
                return QColor(fg)
            # Buy/sell: scope the green/red to a couple of columns so the
            # textual columns (names) stay dominant.
            if col_name.lower() in _TRADE_FG_COLUMNS:
                return QColor(fg)
            return None

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if section < len(self._headers):
                return self._headers[section]
            return str(self._df.columns[section])
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
        self.setDynamicSortFilter(True)

    def set_dataframe(self, df: pd.DataFrame):
        self._source.set_dataframe(df)

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
        price_usd: Optional[float],
        pct_change: Optional[float],
        bg_rgba: Tuple[int, int, int, int] | None = None,  # noqa: ARG002 — legacy arg, ignored
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
        bg_rgba: Tuple[int, int, int, int] | None = None,  # noqa: ARG002 — legacy arg, ignored
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
    bands: Tuple[Tuple[float, float, str], ...],
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
