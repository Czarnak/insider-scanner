"""Inclusive date interval math and successful scan coverage storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.persistence.engine import immediate_transaction
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.schema import scan_coverage

_DAY = timedelta(days=1)


@dataclass(frozen=True, order=True)
class DateInterval:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("start must not be after end")


def normalize_intervals(intervals: list[DateInterval]) -> tuple[DateInterval, ...]:
    ordered = sorted(intervals)
    if not ordered:
        return ()
    compacted = [ordered[0]]
    for interval in ordered[1:]:
        previous = compacted[-1]
        if interval.start <= previous.end + _DAY:
            compacted[-1] = DateInterval(
                previous.start, max(previous.end, interval.end)
            )
        else:
            compacted.append(interval)
    return tuple(compacted)


def uncovered_gaps(
    requested: DateInterval,
    covered: list[DateInterval] | tuple[DateInterval, ...],
) -> tuple[DateInterval, ...]:
    gaps: list[DateInterval] = []
    cursor = requested.start
    for interval in normalize_intervals(list(covered)):
        if interval.end < requested.start:
            continue
        if interval.start > requested.end:
            break
        clipped_start = max(interval.start, requested.start)
        clipped_end = min(interval.end, requested.end)
        if cursor < clipped_start:
            gaps.append(DateInterval(cursor, clipped_start - _DAY))
        cursor = max(cursor, clipped_end + _DAY)
        if cursor > requested.end:
            break
    if cursor <= requested.end:
        gaps.append(DateInterval(cursor, requested.end))
    return tuple(gaps)


class CoverageRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _conditions(domain: str, identifier: str, source: str):
        return and_(
            scan_coverage.c.domain == domain,
            scan_coverage.c.identifier == identifier,
            scan_coverage.c.source == source,
        )

    def get(
        self, domain: str, identifier: str, source: str
    ) -> tuple[DateInterval, ...]:
        try:
            with self._engine.begin() as connection:
                rows = connection.execute(
                    select(scan_coverage.c.start_date, scan_coverage.c.end_date)
                    .where(self._conditions(domain, identifier, source))
                    .order_by(scan_coverage.c.start_date)
                )
                return tuple(DateInterval(row.start_date, row.end_date) for row in rows)
        except SQLAlchemyError as error:
            raise PersistenceError("coverage query failed") from error

    def add(
        self,
        domain: str,
        identifier: str,
        source: str,
        interval: DateInterval,
        *,
        now: datetime | None = None,
    ) -> None:
        recorded_at = datetime.now(UTC) if now is None else now
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if domain in {"us", "congress"}:
            # An incomplete calendar day can receive more filings after this scan.
            completed_end = min(interval.end, recorded_at.date() - _DAY)
            if completed_end < interval.start:
                return
            interval = DateInterval(interval.start, completed_end)
        recorded_at = recorded_at.astimezone(UTC)
        try:
            with immediate_transaction(self._engine) as connection:
                rows = connection.execute(
                    select(
                        scan_coverage.c.start_date,
                        scan_coverage.c.end_date,
                        scan_coverage.c.covered_at,
                    ).where(self._conditions(domain, identifier, source))
                )
                entries = sorted(
                    [
                        (DateInterval(row.start_date, row.end_date), row.covered_at)
                        for row in rows
                    ]
                    + [(interval, recorded_at)]
                )
                compacted: list[tuple[DateInterval, datetime]] = []
                for item, covered_at in entries:
                    if compacted and item.start <= compacted[-1][0].end + _DAY:
                        previous, previous_at = compacted[-1]
                        compacted[-1] = (
                            DateInterval(previous.start, max(previous.end, item.end)),
                            min(previous_at, covered_at),
                        )
                    else:
                        compacted.append((item, covered_at))
                connection.execute(
                    delete(scan_coverage).where(
                        self._conditions(domain, identifier, source)
                    )
                )
                if compacted:
                    connection.execute(
                        insert(scan_coverage),
                        [
                            {
                                "domain": domain,
                                "identifier": identifier,
                                "source": source,
                                "start_date": item.start,
                                "end_date": item.end,
                                "covered_at": covered_at,
                            }
                            for item, covered_at in compacted
                        ],
                    )
        except SQLAlchemyError as error:
            raise PersistenceError("coverage update failed") from error

    def gaps(
        self,
        domain: str,
        identifier: str,
        source: str,
        requested: DateInterval,
    ) -> tuple[DateInterval, ...]:
        return uncovered_gaps(requested, self.get(domain, identifier, source))
