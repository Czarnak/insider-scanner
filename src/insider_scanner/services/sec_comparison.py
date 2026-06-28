"""Local comparison reports for SEC EDGAR versus legacy US sources."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Literal, cast

from insider_scanner.core.models import InsiderTrade
from insider_scanner.services.context import PersistenceContext

SEC_EDGAR_SOURCE = "sec_edgar"
DEFAULT_LEGACY_SOURCES = ("secform4", "openinsider")
ReportFormat = Literal["text", "markdown", "json"]


@dataclass(frozen=True, slots=True)
class SecComparisonTarget:
    """One ticker and filing-date window to compare."""

    ticker: str
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        object.__setattr__(self, "ticker", ticker)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """Stable, report-safe projection of one US trade row."""

    source: str
    ticker: str
    filing_date: date | None
    trade_date: date | None
    insider_name: str
    trade_type: str
    shares: float
    value: float
    accession_number: str
    sec_row_id: str
    edgar_url: str

    @classmethod
    def from_trade(cls, trade: InsiderTrade) -> "ComparisonRow":
        return cls(
            source=trade.source,
            ticker=trade.ticker,
            filing_date=trade.filing_date,
            trade_date=trade.trade_date,
            insider_name=trade.insider_name,
            trade_type=trade.trade_type,
            shares=trade.shares,
            value=trade.value,
            accession_number=trade.accession_number,
            sec_row_id=trade.sec_row_id,
            edgar_url=trade.edgar_url,
        )


@dataclass(frozen=True, slots=True)
class SourceComparisonSummary:
    """Comparison result for SEC rows against one legacy source."""

    legacy_source: str
    sec_count: int
    legacy_count: int
    matched_count: int
    sec_only: tuple[ComparisonRow, ...]
    legacy_only: tuple[ComparisonRow, ...]


@dataclass(frozen=True, slots=True)
class TargetComparisonResult:
    """Comparison result for one target across all requested legacy sources."""

    target: SecComparisonTarget
    sec_count: int
    summaries: tuple[SourceComparisonSummary, ...]

    def summary_for(self, legacy_source: str) -> SourceComparisonSummary:
        normalized = _normalize_source(legacy_source)
        for summary in self.summaries:
            if summary.legacy_source == normalized:
                return summary
        raise KeyError(f"legacy source not compared: {legacy_source}")


@dataclass(frozen=True, slots=True)
class SecComparisonReport:
    """Complete local SEC validation report."""

    legacy_sources: tuple[str, ...]
    results: tuple[TargetComparisonResult, ...]


class SecComparisonService:
    """Build comparison reports from already-persisted US trade rows."""

    def __init__(self, persistence: PersistenceContext) -> None:
        self._persistence = persistence

    def compare(
        self,
        *,
        targets: Iterable[SecComparisonTarget],
        legacy_sources: Iterable[str] = DEFAULT_LEGACY_SOURCES,
    ) -> SecComparisonReport:
        target_values = tuple(targets)
        if not target_values:
            raise ValueError("at least one target is required")
        legacy_source_values = _normalize_legacy_sources(legacy_sources)
        results = tuple(
            self._compare_target(target, legacy_source_values)
            for target in target_values
        )
        return SecComparisonReport(
            legacy_sources=legacy_source_values,
            results=results,
        )

    def _compare_target(
        self,
        target: SecComparisonTarget,
        legacy_sources: tuple[str, ...],
    ) -> TargetComparisonResult:
        sec_rows = self._query(target, SEC_EDGAR_SOURCE)
        summaries = tuple(
            _compare_sources(sec_rows, self._query(target, source), source)
            for source in legacy_sources
        )
        return TargetComparisonResult(
            target=target,
            sec_count=len(sec_rows),
            summaries=summaries,
        )

    def _query(
        self,
        target: SecComparisonTarget,
        source: str,
    ) -> tuple[ComparisonRow, ...]:
        trades = self._persistence.us_trades.query(
            target.ticker,
            sources=(source,),
            start_date=target.start_date,
            end_date=target.end_date,
        )
        return tuple(
            sorted(
                (ComparisonRow.from_trade(trade) for trade in trades),
                key=_row_sort_key,
            )
        )


def render_report(report: SecComparisonReport, report_format: str) -> str:
    """Render a comparison report as text, Markdown, or JSON."""

    normalized = report_format.strip().lower()
    if normalized == "text":
        return _render_text(report, markdown=False)
    if normalized == "markdown":
        return _render_text(report, markdown=True)
    if normalized == "json":
        return json.dumps(_report_to_jsonable(report), indent=2, sort_keys=True)
    raise ValueError(f"unknown comparison report format: {report_format}")


def _compare_sources(
    sec_rows: tuple[ComparisonRow, ...],
    legacy_rows: tuple[ComparisonRow, ...],
    legacy_source: str,
) -> SourceComparisonSummary:
    sec_buckets = _bucket_rows(sec_rows)
    legacy_buckets = _bucket_rows(legacy_rows)
    matched_count = 0
    sec_only: list[ComparisonRow] = []
    legacy_only: list[ComparisonRow] = []
    for key in sorted(set(sec_buckets) | set(legacy_buckets)):
        sec_bucket = sec_buckets.get(key, ())
        legacy_bucket = legacy_buckets.get(key, ())
        matched = min(len(sec_bucket), len(legacy_bucket))
        matched_count += matched
        sec_only.extend(sec_bucket[matched:])
        legacy_only.extend(legacy_bucket[matched:])
    return SourceComparisonSummary(
        legacy_source=legacy_source,
        sec_count=len(sec_rows),
        legacy_count=len(legacy_rows),
        matched_count=matched_count,
        sec_only=tuple(sec_only),
        legacy_only=tuple(legacy_only),
    )


def _bucket_rows(
    rows: tuple[ComparisonRow, ...],
) -> dict[tuple[str, str, str, str, str, str, str], tuple[ComparisonRow, ...]]:
    buckets: dict[tuple[str, str, str, str, str, str, str], list[ComparisonRow]] = (
        defaultdict(list)
    )
    for row in rows:
        buckets[_fingerprint(row)].append(row)
    return {key: tuple(value) for key, value in buckets.items()}


def _fingerprint(row: ComparisonRow) -> tuple[str, str, str, str, str, str, str]:
    return (
        row.ticker.strip().upper(),
        " ".join(row.insider_name.casefold().split()),
        row.filing_date.isoformat() if row.filing_date else "",
        row.trade_date.isoformat() if row.trade_date else "",
        row.trade_type.strip().casefold(),
        f"{row.shares:.6f}",
        f"{row.value:.2f}",
    )


def _row_sort_key(row: ComparisonRow) -> tuple[str, str, str, str, str, str]:
    filing_date = row.filing_date.isoformat() if row.filing_date else ""
    trade_date = row.trade_date.isoformat() if row.trade_date else ""
    return (
        row.ticker.strip().upper(),
        filing_date,
        trade_date,
        row.insider_name.casefold(),
        row.trade_type.casefold(),
        f"{row.shares:.6f}",
    )


def _render_text(report: SecComparisonReport, *, markdown: bool) -> str:
    lines = [
        "# SEC EDGAR Comparison Report" if markdown else "SEC EDGAR Comparison Report",
        "",
        f"Legacy sources: {', '.join(report.legacy_sources)}",
        "",
    ]
    for result in report.results:
        prefix = "## " if markdown else ""
        target = result.target
        lines.append(
            f"{prefix}{target.ticker} {target.start_date.isoformat()}.."
            f"{target.end_date.isoformat()}"
        )
        for summary in result.summaries:
            lines.append(
                f"- {summary.legacy_source}: SEC={summary.sec_count} "
                f"legacy={summary.legacy_count} matched={summary.matched_count} "
                f"SEC-only={len(summary.sec_only)} "
                f"legacy-only={len(summary.legacy_only)}"
            )
            lines.extend(_render_unmatched_rows("SEC-only", summary.sec_only))
            lines.extend(_render_unmatched_rows("legacy-only", summary.legacy_only))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_unmatched_rows(label: str, rows: tuple[ComparisonRow, ...]) -> list[str]:
    return [
        (
            f"  - {label}: {row.filing_date or '?'} {row.trade_date or '?'} "
            f"{row.insider_name} {row.trade_type} shares={row.shares:g} "
            f"value={row.value:g}"
        )
        for row in rows
    ]


def _report_to_jsonable(report: SecComparisonReport) -> dict[str, object]:
    return cast(dict[str, object], _json_dates(asdict(report)))


def _json_dates(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [_json_dates(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_dates(item) for key, item in value.items()}
    return value


def _normalize_legacy_sources(sources: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(_normalize_source(source) for source in sources))
    if not normalized:
        raise ValueError("legacy_sources must not be empty")
    if SEC_EDGAR_SOURCE in normalized:
        raise ValueError("legacy_sources must not include sec_edgar")
    return normalized


def _normalize_source(source: str) -> str:
    normalized = source.strip().lower()
    if not normalized:
        raise ValueError("legacy_sources must not contain empty values")
    return normalized
