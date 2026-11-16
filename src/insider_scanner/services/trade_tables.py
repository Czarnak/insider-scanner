"""Data preparation and exports shared by the Scan and Congress tables."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from insider_scanner.core.congress_identity import name_matches
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.logging import get_logger

log = get_logger("trade_tables")


@dataclass(frozen=True)
class CongressMembers:
    """Names and sectors from the same member-file snapshot."""

    names: list[str]
    sectors: dict[str, list[str]]


def load_congress_members(path: Path | None = None) -> CongressMembers:
    """Read member names and sectors once, retaining valid entries on errors."""
    from insider_scanner.utils.config import CONGRESS_FILE

    source = path if path is not None else CONGRESS_FILE
    empty = CongressMembers(["All"], {})
    try:
        entries = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except (OSError, UnicodeError, json.JSONDecodeError):
        log.warning("Congress member file could not be read: %s", source, exc_info=True)
        return empty
    if not isinstance(entries, list):
        log.warning("Congress member file must contain a list: %s", source)
        return empty

    sectors: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            log.warning("Skipping malformed Congress member entry in %s", source)
            continue
        name = entry.get("official_name") or entry.get("name")
        if not isinstance(name, str) or not name.strip():
            log.warning("Skipping Congress member entry without a name in %s", source)
            continue
        values = entry.get("sector", ["Other"])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list) or any(
            not isinstance(value, str) for value in values
        ):
            log.warning("Invalid Congress member sectors in %s", source)
            values = ["Other"]
        normalized = list(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )
        sectors[name.strip()] = normalized or ["Other"]
    return CongressMembers(["All", *sorted(sectors)], sectors)


def table_trades_to_dataframe(
    trades: Sequence[InsiderTrade | CongressTrade],
) -> pd.DataFrame:
    """Retain raw values and metadata independently of the visible columns."""
    return pd.DataFrame([trade.to_dict() for trade in trades])


congress_trades_to_dataframe = table_trades_to_dataframe


def safe_export_label(label: str) -> str:
    """Keep editable ticker/member labels within a single output filename."""
    return re.sub(r"[^\w.-]", "_", label).strip(".") or "scan"


def _sectors_for(official: str, mapping: dict[str, list[str]]) -> list[str]:
    if official in mapping:
        return mapping[official]
    matches = [
        sectors for name, sectors in mapping.items() if name_matches(name, official)
    ]
    return matches[0] if len(matches) == 1 else ["Other"]


def filter_congress_trades(
    trades: list[CongressTrade],
    *,
    trade_type: str | None = None,
    min_value: float | None = None,
    since: date | None = None,
    until: date | None = None,
    sector: str | None = None,
    member_sectors: dict[str, list[str]] | None = None,
) -> list[CongressTrade]:
    """Apply type, reported lower bound, filing dates and member sector filters."""
    result = trades
    if trade_type:
        result = [trade for trade in result if trade.trade_type == trade_type]
    if min_value is not None and min_value > 0:
        result = [trade for trade in result if trade.amount_low >= min_value]
    if since:
        result = [
            trade
            for trade in result
            if trade.filing_date and trade.filing_date >= since
        ]
    if until:
        result = [
            trade
            for trade in result
            if trade.filing_date and trade.filing_date <= until
        ]
    if sector and sector != "All" and member_sectors is not None:
        result = [
            trade
            for trade in result
            if sector in _sectors_for(trade.official_name, member_sectors)
        ]
    return result


def save_table_results(
    trades: Sequence[InsiderTrade | CongressTrade],
    label: str = "scan",
    output_dir: Path | None = None,
) -> Path:
    """Export raw records as UTF-8 CSV/JSON through atomic file publication."""
    from insider_scanner.utils.config import SCAN_OUTPUTS_DIR, ensure_dirs

    if output_dir is None:
        ensure_dirs()
    out = output_dir if output_dir is not None else SCAN_OUTPUTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    label = safe_export_label(label)
    rows = [trade.to_dict() for trade in trades]
    csv_content = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    json_content = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
    atomic_write_bytes(out / f"{label}.csv", csv_content)
    atomic_write_bytes(out / f"{label}.json", json_content)
    log.info("Saved %d trades to %s", len(trades), out)
    return out


def save_congress_results(
    trades: Sequence[CongressTrade],
    label: str = "congress_scan",
    output_dir: Path | None = None,
) -> Path:
    """Save Congress records using the common raw-record exporter."""
    return save_table_results(trades, label, output_dir)
