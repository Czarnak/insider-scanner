"""Pure table/cell styling helpers for the theme system.

These functions are used by the widget layer (PandasTableModel, etc.) but
live here so they can be unit-tested without Qt and are exported from the
``insider_scanner.gui.theme`` package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtCore import Qt

    from insider_scanner.gui.theme.tokens import ThemePalette

# ---------------------------------------------------------------------------
# Column classification frozensets (lowercased stems)
# ---------------------------------------------------------------------------

NUMERIC_COLS: frozenset[str] = frozenset(
    {
        "value",
        "price",
        "shares",
        "amount",
        "quantity",
        "volume",
        "change",
        "pct",
        "percent",
        "ratio",
        "total",
        "avg",
        "average",
        "cost",
        "gain",
        "loss",
        "market_cap",
        "pe",
        "eps",
    }
)

IDENTIFIER_COLS: frozenset[str] = frozenset(
    {
        "ticker",
        "symbol",
        "cik",
        "accession",
        "id",
        "code",
        "isin",
        "cusip",
        "lei",
        "filing_date",
        "trade_date",
        "date",
        "report_date",
        "transaction_date",
    }
)

TEXT_COLS: frozenset[str] = frozenset(
    {
        "insider_name",
        "company",
        "issuer",
        "insider_title",
        "congress_member",
        "source",
        "name",
        "description",
        "notes",
        "comment",
        "title",
        "role",
        "position",
        "relationship",
        "country",
        "exchange",
        "sector",
        "industry",
    }
)


def _match_stem(col_name: str, stems: frozenset[str]) -> bool:
    """Return True if *col_name* (lowercased) equals a stem or starts/ends with one.

    Rules (in priority order):
    1. Exact match: ``col_name == stem``
    2. Prefix match: ``col_name`` starts with ``stem + "_"``
    3. Suffix match: ``col_name`` ends with ``"_" + stem``

    Plain substring is intentionally avoided to prevent false matches such as
    ``"insider_name"`` matching the stem ``"name"`` in IDENTIFIER_COLS.
    """
    col = col_name.lower()
    if col in stems:
        return True
    for stem in stems:
        if col.startswith(stem + "_") or col.endswith("_" + stem):
            return True
    return False


def column_alignment(col_name: str, dtype: Any) -> "Qt.AlignmentFlag":
    """Return the Qt alignment flag for *col_name* with *dtype*.

    Rules:
    - Numeric stems  → AlignRight | AlignVCenter
    - Identifier stems → AlignRight | AlignVCenter
    - Text stems     → AlignLeft  | AlignVCenter
    - Unknown:       → dtype decides (numeric → right, else left)

    ``Qt`` is imported lazily so callers in non-Qt environments still work
    for the non-alignment-returning path.
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415 — lazy import

    col = col_name.lower()

    if _match_stem(col, NUMERIC_COLS) or _match_stem(col, IDENTIFIER_COLS):
        return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

    if _match_stem(col, TEXT_COLS):
        return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    # Fall back to dtype inspection
    import pandas as pd  # noqa: PLC0415

    try:
        is_numeric = pd.api.types.is_numeric_dtype(dtype)
    except Exception:  # noqa: BLE001
        is_numeric = False

    if is_numeric:
        return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def column_is_monospace(col_name: str) -> bool:
    """Return True when the column should be rendered in a monospace font.

    Numeric and identifier columns use monospace for readability.
    """
    col = col_name.lower()
    return _match_stem(col, NUMERIC_COLS) or _match_stem(col, IDENTIFIER_COLS)


_BUY_TYPES: frozenset[str] = frozenset({"Buy", "Purchase", "Exercise"})
_SELL_TYPES: frozenset[str] = frozenset({"Sell", "Sale"})


def cell_foreground(
    col_name: str,  # noqa: ARG001 — reserved for future column-specific logic
    value: Any,
    row: Any,
    palette: "ThemePalette",
) -> str | None:
    """Return a hex foreground color for a table cell, or ``None`` for default.

    Priority order:
    1. ``is_congress`` truthy → ``palette.warning``
    2. trade type in buy set  → ``palette.purchase``
    3. trade type in sell set → ``palette.sale``
    4. Otherwise              → ``None``

    *row* may be a ``pandas.Series`` or any object with a ``.get(key, default)``
    method, or a plain ``dict``.
    """
    # Resolve is_congress
    if hasattr(row, "get"):
        is_congress = row.get("is_congress", False)
        trade_type = row.get("trade_type", "")
    else:
        # Plain subscript — shouldn't happen but guard anyway
        is_congress = False
        trade_type = ""

    if is_congress:
        return palette.warning

    trade_type_str = str(trade_type) if trade_type is not None else ""

    if trade_type_str in _BUY_TYPES:
        return palette.purchase
    if trade_type_str in _SELL_TYPES:
        return palette.sale

    return None
