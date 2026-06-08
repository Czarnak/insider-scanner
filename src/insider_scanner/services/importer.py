"""Explicit import of legacy JSON trade exports."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.persistence.repositories import UpsertResult
from insider_scanner.services.context import PersistenceContext
from insider_scanner.utils.logging import get_logger

log = get_logger("services.importer")

_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,31}$")
_US_SOURCES = {"secform4", "openinsider", "edgar"}
_CONGRESS_ENUMS = {
    "house": ("house", "House"),
    "senate": ("senate", "Senate"),
}
_EU_ENUMS = {
    "rns": ("UK", "FCA"),
    "bafin": ("DE", "BaFin"),
    "amf": ("FR", "AMF"),
    "afm": ("NL", "AFM"),
}
_EU_SOURCE_ALIASES = {"amf_bdif": "amf"}
DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class LegacyFileReport:
    path: Path
    inserted: int
    updated: int
    skipped: int
    errors: int
    messages: tuple[str, ...] = ()

    @property
    def imported(self) -> int:
        return self.inserted + self.updated


@dataclass(frozen=True)
class LegacyImportReport:
    files: tuple[LegacyFileReport, ...]

    @property
    def imported(self) -> int:
        return sum(item.imported for item in self.files)

    @property
    def inserted(self) -> int:
        return sum(item.inserted for item in self.files)

    @property
    def updated(self) -> int:
        return sum(item.updated for item in self.files)

    @property
    def skipped(self) -> int:
        return sum(item.skipped for item in self.files)

    @property
    def errors(self) -> int:
        return sum(item.errors for item in self.files)


def _json_files(path: Path) -> tuple[Path, ...]:
    if not path.exists():
        raise ValueError(f"Legacy import path does not exist: {path}")
    if path.is_file():
        if path.suffix.casefold() != ".json":
            raise ValueError("Legacy import accepts JSON files only")
        return (path,)
    if not path.is_dir():
        raise ValueError(f"Legacy import path is not a file or directory: {path}")

    root = path.resolve()
    found: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        names[:] = [name for name in names if not (current / name).is_symlink()]
        for filename in filenames:
            candidate = current / filename
            if candidate.suffix.casefold() != ".json":
                continue
            try:
                candidate.resolve().relative_to(root)
            except ValueError:
                continue
            found.append(candidate)
    return tuple(sorted(found))


def _record_type(item: dict) -> str | None:
    keys = set(item)
    if "official_name" in keys or "chamber" in keys or "amount_range" in keys:
        return "congress"
    if "isin" in keys or "regulatory_body" in keys:
        return "european"
    if "ticker" in keys or "insider_name" in keys:
        return "us"
    return None


def _require_strings(item: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if field in item and not isinstance(item[field], str):
            raise ValueError(f"{field} must be a string")


def _require_nonblank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _require_finite(values: tuple[float | None, ...]) -> None:
    if any(value is not None and not math.isfinite(value) for value in values):
        raise ValueError("numeric values must be finite")


def _normalize_ticker(value: str, *, required: bool) -> str:
    ticker = value.strip().upper()
    if not ticker and not required:
        return ""
    if not _TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("ticker has invalid format")
    return ticker


def _normalize_source(value: str, allowed: set[str], field: str = "source") -> str:
    source = _require_nonblank(value, field).casefold()
    if source not in allowed:
        raise ValueError(f"invalid {field}")
    return source


def _parse_us(item: dict) -> InsiderTrade:
    _require_strings(
        item,
        (
            "ticker",
            "company",
            "insider_name",
            "insider_title",
            "trade_type",
            "trade_date",
            "filing_date",
            "source",
            "edgar_url",
            "congress_member",
        ),
    )
    trade = InsiderTrade.from_dict(item)
    ticker = _normalize_ticker(trade.ticker, required=True)
    _require_nonblank(trade.insider_name, "insider_name")
    source = _normalize_source(trade.source, _US_SOURCES)
    if trade.trade_date is None and trade.filing_date is None:
        raise ValueError("trade_date or filing_date is required")
    if trade.trade_type not in {"Buy", "Sell", "Exercise", "Other"}:
        raise ValueError("invalid trade_type")
    if "is_congress" in item and not isinstance(item["is_congress"], bool):
        raise ValueError("is_congress must be a boolean")
    _require_finite((trade.shares, trade.price, trade.value, trade.shares_owned_after))
    return replace(trade, ticker=ticker, source=source)


def _parse_congress(item: dict) -> CongressTrade:
    _require_strings(
        item,
        (
            "official_name",
            "chamber",
            "party",
            "filing_date",
            "doc_id",
            "source_url",
            "trade_date",
            "asset_description",
            "ticker",
            "trade_type",
            "owner",
            "amount_range",
            "comment",
            "source",
        ),
    )
    trade = CongressTrade.from_dict(item)
    _require_nonblank(trade.official_name, "official_name")
    source = _normalize_source(trade.source, set(_CONGRESS_ENUMS))
    chamber_key = _require_nonblank(trade.chamber, "chamber").casefold()
    if chamber_key != source:
        raise ValueError("chamber does not match source")
    chamber = _CONGRESS_ENUMS[source][1]
    ticker = _normalize_ticker(trade.ticker, required=False)
    if not trade.doc_id.strip() and not trade.source_url.strip():
        raise ValueError("doc_id or source_url is required")
    if trade.trade_date is None and trade.filing_date is None:
        raise ValueError("trade_date or filing_date is required")
    if trade.trade_type not in {"Purchase", "Sale", "Exchange", "Other"}:
        raise ValueError("invalid trade_type")
    _require_finite((trade.amount_low, trade.amount_high))
    return replace(trade, ticker=ticker, source=source, chamber=chamber)


def _parse_european(item: dict) -> EuropeanInsiderTrade:
    _require_strings(
        item,
        (
            "isin",
            "issuer_name",
            "country",
            "regulatory_body",
            "insider_name",
            "position",
            "trade_date",
            "filing_date",
            "trade_type",
            "instrument_type",
            "currency",
            "source",
            "source_url",
        ),
    )
    trade = EuropeanInsiderTrade.from_dict(item)
    isin = _require_nonblank(trade.isin, "isin").upper()
    if not _ISIN_PATTERN.fullmatch(isin):
        raise ValueError("isin must be a valid 12-character identifier")
    source_value = _require_nonblank(trade.source, "source").casefold()
    source = _EU_SOURCE_ALIASES.get(source_value, source_value)
    if source not in _EU_ENUMS:
        raise ValueError("invalid source")
    expected_country, expected_body = _EU_ENUMS[source]
    country = _require_nonblank(trade.country, "country").upper()
    regulatory_body = _require_nonblank(trade.regulatory_body, "regulatory_body")
    if country != expected_country:
        raise ValueError("country does not match source")
    if regulatory_body.casefold() != expected_body.casefold():
        raise ValueError("regulatory_body does not match source")
    trade = replace(
        trade,
        isin=isin,
        country=expected_country,
        regulatory_body=expected_body,
        source=source,
    )
    _require_nonblank(trade.insider_name, "insider_name")
    _require_nonblank(trade.source, "source")
    if trade.trade_date is None and not trade.source_url.strip():
        raise ValueError("trade_date or source_url is required")
    if trade.trade_type not in {"Buy", "Sell", "Other"}:
        raise ValueError("invalid trade_type")
    _require_finite((trade.volume, trade.price, trade.total_value))
    return trade


def _parse_record(record_type: str, item: dict):
    if record_type == "us":
        return _parse_us(item)
    if record_type == "congress":
        return _parse_congress(item)
    return _parse_european(item)


def _log_import_error(message: str, error: Exception, **context: object) -> None:
    details = " ".join(f"{key}={value}" for key, value in context.items())
    log.error("%s: %s exception=%s", message, details, type(error).__name__)


def _import_file(
    path: Path,
    persistence: PersistenceContext,
    max_file_size_bytes: int,
) -> LegacyFileReport:
    try:
        file_size = path.stat().st_size
    except OSError as error:
        _log_import_error("Could not inspect legacy JSON file", error, path=path)
        return LegacyFileReport(path, 0, 0, 0, 1, ("file: file_read_error",))
    if file_size > max_file_size_bytes:
        log.warning(
            "Legacy JSON file exceeds size limit: path=%s size=%d limit=%d",
            path,
            file_size,
            max_file_size_bytes,
        )
        return LegacyFileReport(path, 0, 0, 0, 1, ("file: file_too_large",))

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _log_import_error("Malformed legacy JSON file", error, path=path)
        return LegacyFileReport(path, 0, 0, 0, 1, ("file: malformed_json",))
    except (OSError, UnicodeError) as error:
        _log_import_error("Could not read legacy JSON file", error, path=path)
        return LegacyFileReport(path, 0, 0, 0, 1, ("file: file_read_error",))
    if not isinstance(payload, list):
        log.warning("Invalid legacy JSON document shape: %s", path)
        return LegacyFileReport(path, 0, 0, 0, 1, ("file: invalid_document",))

    outcome = UpsertResult()
    errors = 0
    messages: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            errors += 1
            messages.append(f"record {index}: invalid_record")
            continue
        record_type = _record_type(item)
        if record_type is None:
            errors += 1
            messages.append(f"record {index}: invalid_record")
            continue
        try:
            trade = _parse_record(record_type, item)
            if record_type == "us":
                result = persistence.us_trades.upsert((trade,))
            elif record_type == "congress":
                result = persistence.congress_trades.upsert((trade,))
            else:
                result = persistence.european_trades.upsert((trade,))
        except (TypeError, ValueError, OverflowError) as error:
            _log_import_error(
                "Legacy record validation failed",
                error,
                path=path,
                index=index,
                type=record_type,
            )
            errors += 1
            messages.append(f"record {index}: invalid_record")
            continue
        except Exception as error:
            _log_import_error(
                "Legacy record persistence failed",
                error,
                path=path,
                index=index,
                type=record_type,
            )
            errors += 1
            messages.append(f"record {index}: persistence_error")
            continue
        outcome += result
    return LegacyFileReport(
        path,
        outcome.inserted,
        outcome.updated,
        outcome.skipped,
        errors,
        tuple(messages),
    )


def import_legacy_path(
    path: Path,
    persistence: PersistenceContext,
    *,
    max_file_size_bytes: int = DEFAULT_MAX_LEGACY_FILE_SIZE_BYTES,
) -> LegacyImportReport:
    """Import supported legacy JSON records without changing scan state."""
    if max_file_size_bytes <= 0:
        raise ValueError("max_file_size_bytes must be positive")
    reports = tuple(
        _import_file(item, persistence, max_file_size_bytes)
        for item in _json_files(path)
    )
    return LegacyImportReport(reports)
