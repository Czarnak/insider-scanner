"""Resumable SEC daily-filing download + parse orchestration (no DB writes).

Walks a calendar date range, fetches each day's master index, downloads and
parses every ownership filing, and reports ``discovered / downloaded / parsed /
skipped / failed`` counts. Persistence to the database is intentionally out of
scope here (that is the next session); this service validates and caches filings
and is safe to resume from a JSON checkpoint after interruption.

It reuses the hardened SEC client/downloader and the Session 2 ownership parser,
so every security invariant (host allowlist, bounds, path safety, XML/footnote
hardening) continues to apply unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from insider_scanner.core import edgar, sec_index
from insider_scanner.core._sec_xml import SecXmlSecurityError
from insider_scanner.core.sec_client import (
    SecClient,
    SecClientError,
    SecClientSecurityError,
    SecFilingNotFoundError,
)
from insider_scanner.core.sec_downloader import (
    PendingSecFiling,
    SecDownloadError,
    fetch_filing,
    promote_validated_filing,
    purge_stale_cache,
    quarantine_failed_download,
)
from insider_scanner.core.sec_ownership_document import (
    SecOwnershipDocumentError,
    extract_ownership_document,
)
from insider_scanner.core.sec_ownership_parser import (
    OwnershipFiling,
    SecOwnershipParseError,
    parse_ownership_document,
)
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceProfile,
    SecSecurityPolicy,
)
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.persistence.json_state import atomic_write_json, read_json_dict
from insider_scanner.services.common import not_cancelled, validate_range
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_downloads")

_CHECKPOINT_VERSION = 1
_COUNTER_KEYS = ("discovered", "downloaded", "parsed", "skipped", "failed")

# A successfully parsed filing paired with its index row, handed to the sinks.
ParsedFiling = tuple["sec_index.SecMasterIndexRow", "OwnershipFiling"]

# Bounded concurrency for per-filing fetch+parse within a single date. SEC's
# rate limiter (shared, thread-safe) still caps the global request rate; threads
# only let network round-trips overlap so throughput approaches that ceiling.
DEFAULT_DOWNLOAD_WORKERS = 8

# Errors that are recoverable per-filing: log, count as failed, keep going.
_RECOVERABLE = (
    SecClientError,
    SecDownloadError,
    SecOwnershipDocumentError,
    SecOwnershipParseError,
    SecXmlSecurityError,
)


@dataclass(frozen=True, slots=True)
class SecDownloadProgress:
    """Cumulative snapshot emitted after each completed date."""

    current_date: date
    dates_completed: int
    dates_total: int
    discovered: int
    downloaded: int
    parsed: int
    skipped: int
    failed: int


@dataclass(frozen=True, slots=True)
class SecDownloadReport:
    """Final aggregate result of a download run.

    The five counters are independent tallies, not a partition: ``downloaded``
    and ``skipped`` record how bytes were obtained (network vs. fresh cache),
    while ``parsed`` and ``failed`` record the parse outcome. A filing that is
    fetched (or cache-served) and then fails to parse is therefore counted in
    both its source bucket and ``failed`` — do not sum the buckets as a total.
    """

    interval: DateInterval
    dates_total: int
    dates_completed: int
    discovered: int
    downloaded: int
    parsed: int
    skipped: int
    failed: int


def download_filings_in_range(
    *,
    start_date: date,
    end_date: date,
    client: SecClient,
    cache_root: Path,
    checkpoint_path: Path | None = None,
    on_progress: Callable[[SecDownloadProgress], None] | None = None,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
    on_date_parsed: Callable[[date, Sequence[ParsedFiling]], None] | None = None,
    cancelled: Callable[[], bool] = not_cancelled,
    continue_on_error: bool = True,
    cleanup: bool = True,
    retain_failed_downloads: bool = False,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> SecDownloadReport:
    """Download + parse every ownership filing across an inclusive date range.

    Resumes from *checkpoint_path* when its recorded range matches the request.
    Returns a :class:`SecDownloadReport`; never writes to the database.
    """
    if type(client) is not SecClient:
        raise TypeError("client must be exactly SecClient")
    if not isinstance(cache_root, Path):
        raise TypeError("cache_root must be a pathlib.Path")
    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")
    interval = validate_range(start_date, end_date)
    all_dates = _dates_in(interval)

    completed, counters = _load_checkpoint(checkpoint_path, interval)
    interrupted = False

    for day in all_dates:
        if day in completed:
            continue
        if cancelled():
            interrupted = True
            break
        outcome = _process_date(
            day,
            client=client,
            cache_root=cache_root,
            counters=counters,
            cancelled=cancelled,
            retain_failed_downloads=retain_failed_downloads,
            policy=policy,
            on_filing_parsed=on_filing_parsed,
            on_date_parsed=on_date_parsed,
            max_workers=max_workers,
        )
        if outcome is None:  # cancelled mid-date — leave the date uncovered
            interrupted = True
            break
        if outcome:
            completed.add(day)
            _save_checkpoint(checkpoint_path, interval, completed, counters)
            _emit_progress(on_progress, day, len(completed), len(all_dates), counters)
        elif not continue_on_error:
            interrupted = True
            break

    fully_done = len(completed) >= len(all_dates) and not interrupted
    if fully_done:
        if checkpoint_path is not None:
            _delete_checkpoint(checkpoint_path)
        if cleanup:
            purge_stale_cache(cache_root, policy=policy)

    return SecDownloadReport(
        interval=interval,
        dates_total=len(all_dates),
        dates_completed=len(completed),
        **{key: counters[key] for key in _COUNTER_KEYS},
    )


@dataclass(frozen=True, slots=True)
class _FilingOutcome:
    """Result of fetching + parsing one filing — no DB writes, no cache writes.

    This is the unit run concurrently across a date's filings. It performs only
    the network fetch and the (read-only) XML extraction/parse, so it touches no
    shared mutable state: ``downloaded``/``skipped`` record how the bytes were
    obtained, ``failed`` marks a fetch/parse failure, ``pending``/``filing`` are
    set on a successful parse (awaiting the serial cache promote), and
    ``to_quarantine`` carries failed-parse bytes for the serial diagnostic
    write. Cache promotion and quarantine — the only filesystem mutations — are
    applied later on the main thread to keep them race-free.
    """

    downloaded: bool
    skipped: bool
    failed: bool
    pending: PendingSecFiling | None = None
    filing: OwnershipFiling | None = None
    to_quarantine: PendingSecFiling | None = None


def _fetch_and_parse(
    row: sec_index.SecMasterIndexRow,
    *,
    client: SecClient,
    cache_root: Path,
    policy: SecSecurityPolicy,
) -> _FilingOutcome:
    """Fetch + extract + parse one filing (concurrency-safe; no FS/DB writes).

    The expensive, latency-bound work (network round-trip) and the CPU-bound XML
    parse run here so they can overlap across threads. The only filesystem
    mutations — promoting validated bytes into the cache and the diagnostic
    quarantine — are deferred to :func:`_promote_and_count` on the main thread.
    """
    try:
        pending = fetch_filing(row, client=client, cache_root=cache_root)
    except _RECOVERABLE as exc:
        _log_recoverable_level(
            "SEC filing failed stage=fetch cik=%s reason=%s", row.cik, exc
        )
        return _FilingOutcome(downloaded=False, skipped=False, failed=True)

    downloaded = not pending.from_cache
    skipped = pending.from_cache

    accession = _accession_from_path(row.archive_path)
    try:
        document = extract_ownership_document(
            pending.content, accession_number=accession, policy=policy
        )
        filing = parse_ownership_document(document, policy=policy)
    except _RECOVERABLE as exc:
        _log_recoverable_level(
            "SEC filing failed stage=parse cik=%s reason=%s", row.cik, exc
        )
        return _FilingOutcome(
            downloaded=downloaded, skipped=skipped, failed=True, to_quarantine=pending
        )

    return _FilingOutcome(
        downloaded=downloaded,
        skipped=skipped,
        failed=False,
        pending=pending,
        filing=filing,
    )


def _promote_and_count(
    outcome: _FilingOutcome,
    *,
    counters: dict[str, int],
    cache_root: Path,
    retain_failed_downloads: bool,
) -> ParsedFiling | None:
    """Apply an outcome's counters and serial filesystem effects on the main thread.

    Promotion of validated bytes into the cache happens here (serially) so the
    concurrent workers never perform colliding cache writes. Returns the
    ``(row, filing)`` pair when the filing was parsed and promoted, else None.
    """
    if outcome.downloaded:
        counters["downloaded"] += 1
    if outcome.skipped:
        counters["skipped"] += 1
    if outcome.failed:
        counters["failed"] += 1
        if outcome.to_quarantine is not None:
            _maybe_quarantine(
                outcome.to_quarantine,
                cache_root=cache_root,
                enabled=retain_failed_downloads,
            )
        return None

    pending = outcome.pending
    filing = outcome.filing
    if pending is None or filing is None:
        # Unreachable: a non-failed outcome always carries both. Guarded
        # explicitly (not asserted) so behavior is defined under `python -O`.
        return None
    try:
        promote_validated_filing(pending, cache_root=cache_root)
    except SecDownloadError as exc:
        counters["failed"] += 1
        _log_recoverable_level(
            "SEC filing failed stage=promote cik=%s reason=%s", pending.row.cik, exc
        )
        return None
    counters["parsed"] += 1
    return (pending.row, filing)


def _collect_outcomes(
    rows: Sequence[sec_index.SecMasterIndexRow],
    *,
    client: SecClient,
    cache_root: Path,
    policy: SecSecurityPolicy,
    max_workers: int,
) -> list[_FilingOutcome]:
    """Fetch + parse every filing for a date, concurrently when worthwhile.

    Results are returned in the original index order. The shared, thread-safe
    rate limiter keeps total request rate under SEC's ceiling; concurrency only
    overlaps network round-trips so throughput approaches that ceiling instead
    of being serialized by per-request latency.
    """
    workers = min(max_workers, len(rows))
    if workers <= 1:
        return [
            _fetch_and_parse(row, client=client, cache_root=cache_root, policy=policy)
            for row in rows
        ]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(
            executor.map(
                lambda row: _fetch_and_parse(
                    row, client=client, cache_root=cache_root, policy=policy
                ),
                rows,
            )
        )


def _process_date(
    day: date,
    *,
    client: SecClient,
    cache_root: Path,
    counters: dict[str, int],
    cancelled: Callable[[], bool],
    retain_failed_downloads: bool,
    policy: SecSecurityPolicy,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
    on_date_parsed: Callable[[date, Sequence[ParsedFiling]], None] | None = None,
    max_workers: int = DEFAULT_DOWNLOAD_WORKERS,
) -> bool | None:
    """Process one calendar day.

    Returns True when the date is fully handled (including an empty filing day),
    False on a date-level failure (so a resume retries it), or None if cancelled
    before the day's filings were processed.
    """
    url = edgar.build_daily_master_index_url(day)
    try:
        index_text = client.fetch_text(url, profile=SecResourceProfile.DAILY_INDEX)
    except SecFilingNotFoundError:
        _log.info("SEC daily index absent stage=index date=%s", day.isoformat())
        return True
    except _RECOVERABLE as exc:
        _log_recoverable_level(
            "SEC daily index failed stage=index date=%s reason=%s",
            day.isoformat(),
            exc,
        )
        return False

    rows = sec_index.parse_master_index(index_text)
    counters["discovered"] += len(rows)
    if not rows:
        return True
    if cancelled():
        return None

    outcomes = _collect_outcomes(
        rows,
        client=client,
        cache_root=cache_root,
        policy=policy,
        max_workers=max_workers,
    )
    # A cancel that arrived during the concurrent fetch leaves the date uncovered
    # (nothing is promoted or persisted) so a resume retries it. In-flight fetches
    # for this date still complete, but no further date is started.
    if cancelled():
        return None

    # Counters + cache promotion + quarantine run serially here (not in workers).
    parsed: list[ParsedFiling] = []
    for outcome in outcomes:
        result = _promote_and_count(
            outcome,
            counters=counters,
            cache_root=cache_root,
            retain_failed_downloads=retain_failed_downloads,
        )
        if result is not None:
            parsed.append(result)

    date_failed = False
    # Per-filing sink (serial, on this thread — preserves the streaming contract).
    if on_filing_parsed is not None:
        for filing_row, filing in parsed:
            try:
                on_filing_parsed(filing_row, filing)
            except (*_RECOVERABLE, PersistenceError) as exc:
                counters["failed"] += 1
                _log_recoverable_level(
                    "SEC filing failed stage=persist cik=%s reason=%s",
                    filing_row.cik,
                    exc,
                )
                date_failed = True

    # Per-date batch sink: one persistence transaction for the whole date.
    if on_date_parsed is not None and parsed:
        try:
            on_date_parsed(day, parsed)
        except (*_RECOVERABLE, PersistenceError) as exc:
            counters["failed"] += len(parsed)
            _log_recoverable_level(
                "SEC date persist failed date=%s reason=%s", day.isoformat(), exc
            )
            date_failed = True

    return False if date_failed else True


def process_filing_row(
    row: sec_index.SecMasterIndexRow,
    *,
    client: SecClient,
    cache_root: Path,
    counters: dict[str, int],
    retain_failed_downloads: bool = False,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
) -> bool:
    """Process a single filing (serial, per-filing streaming entrypoint).

    Returns True in all normal code paths (fetch/parse/promote failures keep the
    existing behavior — the date still completes). Returns False only when the
    on_filing_parsed sink raises, signalling a date-level retry.
    """
    outcome = _fetch_and_parse(row, client=client, cache_root=cache_root, policy=policy)
    result = _promote_and_count(
        outcome,
        counters=counters,
        cache_root=cache_root,
        retain_failed_downloads=retain_failed_downloads,
    )
    if result is None:
        return True

    filing_row, filing = result
    if on_filing_parsed is not None:
        try:
            on_filing_parsed(filing_row, filing)
        except (*_RECOVERABLE, PersistenceError) as exc:
            counters["failed"] += 1
            _log_recoverable_level(
                "SEC filing failed stage=persist cik=%s reason=%s", filing_row.cik, exc
            )
            return False

    return True


def _log_recoverable_level(message: str, identifier: str, exc: Exception) -> None:
    """Log a recoverable failure, escalating security-policy violations to ERROR.

    A :class:`SecClientSecurityError` (blocked host, disallowed redirect,
    content-type or size violation) is not normal for a legitimate SEC archive
    path, so it is surfaced at ERROR even though the batch continues.
    """
    reason = type(exc).__name__
    if isinstance(exc, SecClientSecurityError):
        _log.error(message, identifier, reason)
    else:
        _log.warning(message, identifier, reason)


def _maybe_quarantine(
    pending: PendingSecFiling, *, cache_root: Path, enabled: bool
) -> None:
    # Only freshly downloaded (non-cache) bytes are worth retaining for debug.
    if not enabled or pending.from_cache:
        return
    try:
        quarantine_failed_download(pending, cache_root=cache_root)
    except SecDownloadError:
        _log.warning("SEC diagnostic quarantine failed cik=%s", pending.row.cik)


def _dates_in(interval: DateInterval) -> tuple[date, ...]:
    span = (interval.end - interval.start).days
    return tuple(interval.start + timedelta(days=offset) for offset in range(span + 1))


def _accession_from_path(archive_path: str) -> str | None:
    stem = archive_path.rsplit("/", 1)[-1]
    for suffix in (".txt", ".xml"):
        if stem.casefold().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem or None


def new_counters() -> dict[str, int]:
    return {key: 0 for key in _COUNTER_KEYS}


def _load_checkpoint(
    path: Path | None, interval: DateInterval
) -> tuple[set[date], dict[str, int]]:
    counters = new_counters()
    completed: set[date] = set()
    if path is None:
        return completed, counters
    data = read_json_dict(path)
    if not data or data.get("version") != _CHECKPOINT_VERSION:
        return completed, counters
    saved_range = data.get("range")
    if (
        not isinstance(saved_range, dict)
        or saved_range.get("start") != interval.start.isoformat()
        or saved_range.get("end") != interval.end.isoformat()
    ):
        return completed, counters  # different range → start fresh

    raw_dates = data.get("completed_dates")
    if isinstance(raw_dates, list):
        for value in raw_dates:
            parsed = _parse_iso(value)
            if parsed is not None and interval.start <= parsed <= interval.end:
                completed.add(parsed)

    saved_counters = data.get("counters")
    if isinstance(saved_counters, dict):
        for key in _COUNTER_KEYS:
            value = saved_counters.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                counters[key] = value
    return completed, counters


def _save_checkpoint(
    path: Path | None,
    interval: DateInterval,
    completed: set[date],
    counters: dict[str, int],
) -> None:
    if path is None:
        return
    # A checkpoint write failure must not abort a resumable run: log and proceed
    # (the day is already complete in memory; only persistence is lost).
    try:
        atomic_write_json(
            path,
            {
                "version": _CHECKPOINT_VERSION,
                "range": {
                    "start": interval.start.isoformat(),
                    "end": interval.end.isoformat(),
                },
                "completed_dates": sorted(day.isoformat() for day in completed),
                "counters": {key: counters[key] for key in _COUNTER_KEYS},
            },
        )
    except OSError:
        _log.warning("SEC checkpoint save failed path=%s", path)


def _delete_checkpoint(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _log.warning("SEC checkpoint cleanup failed path=%s", path)


def _parse_iso(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _emit_progress(
    on_progress: Callable[[SecDownloadProgress], None] | None,
    current_date: date,
    dates_completed: int,
    dates_total: int,
    counters: dict[str, int],
) -> None:
    if on_progress is None:
        return
    on_progress(
        SecDownloadProgress(
            current_date=current_date,
            dates_completed=dates_completed,
            dates_total=dates_total,
            **{key: counters[key] for key in _COUNTER_KEYS},
        )
    )
