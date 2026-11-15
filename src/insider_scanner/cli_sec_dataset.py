"""CLI orchestration for quarterly SEC archives and explicit coverage gaps."""

from __future__ import annotations

import argparse
import sys
from calendar import monthrange
from collections.abc import Callable
from contextlib import AbstractContextManager, closing
from datetime import date, timedelta
from pathlib import Path

from insider_scanner.core.edgar import normalize_cik
from insider_scanner.core.sec_bulk import SecBulkError
from insider_scanner.core.sec_client import (
    SecClient,
    SecClientError,
    SecDownloadCancelled,
)
from insider_scanner.core.sec_dataset_downloads import (
    DatasetQuarter,
    discover_dataset_quarters,
    download_dataset_quarter,
)
from insider_scanner.services.application import ApplicationServices
from insider_scanner.services.sec_dataset import SecDatasetSummary
from insider_scanner.services.sec_downloads import sec_today
from insider_scanner.utils.logging import get_logger

_log = get_logger("cli_sec_dataset")
_DATASET_START = date(2006, 1, 1)


def _quarter(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def _requested_quarters(since: date, until: date) -> set[tuple[int, int]]:
    """Include the next posting for the final week, allowing SEC holidays."""
    if until < _DATASET_START:
        return set()
    year, quarter = _quarter(max(since, _DATASET_START))
    last = _quarter(until)
    required = set()
    while (year, quarter) <= last:
        required.add((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    month = last[1] * 3
    boundary = date(last[0], month, monthrange(last[0], month)[1])
    # A weekday alone cannot identify the last SEC business day: for example,
    # EDGAR was closed Friday 2021-12-31. Conservatively overlap the final week.
    if until >= boundary - timedelta(days=7):
        required.add((year, quarter))
    return required


def _gap_guidance(since: date, until: date, missing: set[tuple[int, int]]) -> bool:
    incomplete = since < _DATASET_START or bool(missing)
    if since < _DATASET_START:
        print(
            "Datasets start at 2006 Q1. The 2003–2005 interval requires XML: "
            "sec-backfill --zip submissions.zip --since 2003-01-01 "
            "--until 2005-12-31 --confirm-full-backfill. Earlier non-XML filings "
            "are unsupported.",
            file=sys.stderr,
        )
    if missing:
        first = min(missing)
        last = max(missing)
        boundary = date(last[0], (last[1] - 1) * 3 + 1, 1) - timedelta(days=7)
        # Include a full week across the posting boundary, including weekends.
        start = max(since, boundary)
        print(
            f"{len(missing)} required quarter posting(s) unavailable; first is "
            f"{first[0]} Q{first[1]}. Publication gaps remain incomplete. "
            f"Use sec-catchup --since {start} --until {until} for the unpublished "
            "quarter and the quarter-end overlap. Use bounded sec-backfill XML "
            "repairs for older missing quarters.",
            file=sys.stderr,
        )
    return incomplete


def _progress(quiet: bool) -> Callable[[SecDatasetSummary], None] | None:
    if quiet:
        return None

    def report(snapshot: SecDatasetSummary) -> None:
        print(
            f"staged={snapshot.rows_staged} discovered={snapshot.filings_discovered} "
            f"parsed={snapshot.filings_parsed} committed={snapshot.filings_committed} "
            f"failed={snapshot.failures} skipped_resume={snapshot.skipped_resume} "
            f"elapsed={snapshot.elapsed_seconds:.1f}s",
            file=sys.stderr,
            flush=True,
        )

    return report


def _print_summary(path: Path, summary: SecDatasetSummary) -> None:
    print(
        f"SEC dataset {path.name}: discovered={summary.filings_discovered} "
        f"parsed={summary.filings_parsed} committed={summary.filings_committed} "
        f"inserted={summary.transactions_inserted} updated={summary.transactions_updated} "
        f"skipped={summary.transactions_skipped} skipped_resume={summary.skipped_resume} "
        f"failures={summary.failures}"
    )


def _published_plan(
    client: SecClient, since: date, until: date
) -> tuple[list[DatasetQuarter], set[tuple[int, int]]]:
    required = _requested_quarters(since, until)
    published = {
        (item.year, item.quarter): item for item in discover_dataset_quarters(client)
    }
    return [
        published[key] for key in sorted(required & published.keys())
    ], required - published.keys()


def run_dataset_command(
    args: argparse.Namespace,
    services: ApplicationServices,
    *,
    build_client: Callable[[], SecClient | None],
    cache_root: Callable[[], Path],
    interrupt: Callable[[], AbstractContextManager[Callable[[], bool]]],
) -> int:
    """Import local archives or download only SEC-advertised selected quarters."""
    since = args.since or _DATASET_START
    until = args.until or sec_today()
    paths = [Path(path) for path in args.zip or ()]
    try:
        if since > until or until > sec_today():
            raise ValueError("dates must be ordered and must not be in the future")
        ciks = frozenset(normalize_cik(cik) for cik in args.cik) if args.cik else None
        if any(not path.is_file() or path.is_symlink() for path in paths):
            raise ValueError("every --zip must name an existing regular archive")
    except ValueError as error:
        print(f"Invalid dataset input: {error}", file=sys.stderr)
        return 2
    incomplete = False
    missing: set[tuple[int, int]] = set()
    service = services.make_sec_dataset()
    with interrupt() as cancelled:

        def ingest(path: Path) -> None:
            nonlocal incomplete
            summary = service.run(
                path,
                since=since,
                until=until,
                ciks=ciks,
                reimport=args.reimport,
                cancelled=cancelled,
                on_progress=_progress(args.quiet),
            )
            _print_summary(path, summary)
            incomplete |= bool(summary.failures)

        try:
            if paths:
                for path in paths:
                    if cancelled():
                        break
                    ingest(path)
            elif until >= _DATASET_START:
                client = build_client()
                if client is None:
                    return 1
                with closing(client):
                    quarters, missing = _published_plan(client, since, until)
                    directory = args.download_dir or cache_root() / "datasets"
                    directory.mkdir(parents=True, exist_ok=True)
                    for quarter in quarters:
                        if cancelled():
                            break
                        destination = directory / quarter.filename
                        if destination.is_symlink():
                            raise ValueError(
                                "Dataset destination must not be a symlink"
                            )
                        if args.redownload or not destination.is_file():
                            if not args.quiet:
                                print(
                                    f"Downloading {quarter.year} Q{quarter.quarter}",
                                    file=sys.stderr,
                                )
                            download_dataset_quarter(
                                client,
                                quarter,
                                destination,
                                cancelled=cancelled,
                            )
                        ingest(destination)
        except SecDownloadCancelled:
            print(
                "Interrupted. Committed progress is preserved; rerun to resume.",
                file=sys.stderr,
            )
            return 130
        except (SecClientError, SecBulkError, OSError) as error:
            reason = " ".join(str(error).split())[:1000] or type(error).__name__
            _log.warning("SEC dataset command failed reason=%s", reason)
            print(
                f"SEC dataset import failed: {reason}. Rerun to retry.",
                file=sys.stderr,
            )
            return 1
        if cancelled():
            print(
                "Interrupted. Committed progress is preserved; rerun to resume.",
                file=sys.stderr,
            )
            return 130
    incomplete |= _gap_guidance(since, until, missing)
    return 3 if incomplete else 0
