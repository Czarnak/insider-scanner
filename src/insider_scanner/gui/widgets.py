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


class PandasTableModel(QAbstractTableModel):
    """Qt table model backed by a pandas DataFrame."""

    def __init__(
        self,
        df: pd.DataFrame | None = None,
        parent=None,
        headers: list[str] | None = None,
    ):
        super().__init__(parent)
        self._df = df if df is not None else pd.DataFrame()
        self._headers = headers or []

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
        if role == Qt.ItemDataRole.DisplayRole:
            val = self._df.iloc[index.row(), index.column()]
            if isinstance(val, float):
                return f"{val:,.2f}"
            return str(val)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        # Highlight congress trades
        if role == Qt.ItemDataRole.ForegroundRole:
            if "is_congress" in self._df.columns:
                if self._df.iloc[index.row()]["is_congress"]:
                    from PySide6.QtGui import QColor

                    return QColor(200, 50, 50)
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
    """Proxy adding sort/filter on top of PandasTableModel."""

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
    """Card showing a price and 1-day % change with colored background."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("PriceChangeCard")
        self.setStyleSheet("""
            QFrame#PriceChangeCard {
                border: 1px solid rgba(128,128,128,80);
                border-radius: 10px;
                padding: 10px;
            }
        """)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        self.title_lbl = QtWidgets.QLabel(title)
        self.title_lbl.setStyleSheet("font-weight: 600;")

        self.price_lbl = QtWidgets.QLabel("—")
        self.price_lbl.setStyleSheet("font-size: 22px; font-weight: 700;")

        self.chg_lbl = QtWidgets.QLabel("")
        self.chg_lbl.setStyleSheet("font-weight: 600; color: #cccccc;")

        lay.addWidget(self.title_lbl)
        lay.addWidget(self.price_lbl)
        lay.addWidget(self.chg_lbl)
        lay.addStretch(1)

    def set_value(
        self,
        price_usd: Optional[float],
        pct_change: Optional[float],
        bg_rgba: Tuple[int, int, int, int] = (40, 40, 40, 120),
    ):
        if price_usd is None:
            self.price_lbl.setText("n/a")
        else:
            self.price_lbl.setText(f"{price_usd:,.2f} USD")

        if pct_change is None:
            self.chg_lbl.setText("Δ1D: n/a")
        else:
            sign = "+" if pct_change >= 0 else ""
            self.chg_lbl.setText(f"Δ1D: {sign}{pct_change:.2f}%")

        r, g, b, a = bg_rgba
        self.setStyleSheet(
            """
            QFrame#PriceChangeCard {
                border: 1px solid rgba(128,128,128,80);
                border-radius: 10px;
                padding: 10px;
                background-color: rgba(%d,%d,%d,%d);
            }
        """
            % (r, g, b, a)
        )


class ValueCard(QtWidgets.QFrame):
    """Generic card showing a title, large value, and optional meta text."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setObjectName("ValueCard")
        self.setStyleSheet("""
            QFrame#ValueCard {
                border: 1px solid rgba(128,128,128,80);
                border-radius: 10px;
                padding: 10px;
            }
        """)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        self.title_lbl = QtWidgets.QLabel(title)
        self.title_lbl.setStyleSheet("font-weight: 600;")
        self.value_lbl = QtWidgets.QLabel("—")
        self.value_lbl.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.meta_lbl = QtWidgets.QLabel("")
        self.meta_lbl.setStyleSheet("color: black;")

        lay.addWidget(self.title_lbl)
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.meta_lbl)
        lay.addStretch(1)

    def set_value(
        self,
        value_text: str,
        meta_text: str = "",
        bg_rgba: Tuple[int, int, int, int] = (40, 40, 40, 120),
    ):
        self.value_lbl.setText(value_text)
        self.meta_lbl.setText(meta_text)
        r, g, b, a = bg_rgba
        self.setStyleSheet(
            """
            QFrame#ValueCard {
                border: 1px solid rgba(128,128,128,80);
                border-radius: 10px;
                padding: 10px;
                background-color: rgba(%d,%d,%d,%d);
            }
        """
            % (r, g, b, a)
        )


# -------------------------------------------------------------------
# Color helpers for dashboard
# -------------------------------------------------------------------


def fg_color(value: int) -> Tuple[int, int, int, int]:
    """Map a 0–100 Fear & Greed score to an RGBA background color."""
    if value < 25:
        return (180, 40, 40, 160)  # Extreme Fear — red
    if value < 50:
        return (200, 120, 40, 160)  # Fear — orange
    if value < 75:
        return (200, 180, 40, 160)  # Greed — yellow
    return (60, 160, 80, 160)  # Extreme Greed — green


def indicator_color(
    value: float,
    bands: Tuple[Tuple[float, float, str], ...],
) -> Tuple[int, int, int, int]:
    """Map a numeric value to an RGBA color using band definitions."""
    palette = {
        "red": (180, 40, 40, 160),
        "orange": (200, 120, 40, 160),
        "yellow": (200, 180, 40, 160),
        "green": (60, 160, 80, 160),
        "gray": (80, 80, 80, 120),
    }
    for lo, hi, name in bands:
        if lo <= value < hi:
            return palette.get(name, palette["gray"])
    return palette["gray"]
