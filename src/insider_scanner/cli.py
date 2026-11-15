"""CLI entry point for headless insider trade scanning."""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from threading import Event, current_thread, main_thread
from time import monotonic
from typing import TYPE_CHECKING, TypedDict

from insider_scanner.core import edgar
from insider_scanner.core.fetch_outcome import describe_incomplete
from insider_scanner.core.prices import get_price_history
from insider_scanner.core.sec_bulk import SecBulkError
from insider_scanner.core.sec_client import SecBlockedError
from insider_scanner.core.sec_security import DEFAULT_SEC_SECURITY_POLICY
from insider_scanner.services.application import (
    ApplicationServices,
    open_application_services,
)
from insider_scanner.services.sec_downloads import sec_today
from insider_scanner.utils.config import EU_WATCHLIST_FILE, ensure_dirs
from insider_scanner.utils.logging import diagnostic_hint, get_logger, setup_logging
from insider_scanner.utils.trade_display import format_reported_number

if TYPE_CHECKING:
    from insider_scanner.core.eu_models import EuropeanInsiderTrade
    from insider_scanner.core.sec_client import SecClient
    from insider_scanner.services.sec_backfill import SecBackfillSummary
    from insider_scanner.services.sec_comparison import SecComparisonTarget
    from insider_scanner.services.sec_daily import SecDailyIngestionSummary
    from insider_scanner.services.sec_downloads import SecDownloadProgress

log = get_logger("cli")

# SEC submissions metadata archive for bounded legacy ingestion and XML repairs.
# Verified reachable: ranged GET with a compliant SEC User-Agent returned HTTP 206,
# Content-Type application/zip, ~1.55 GB. Host www.sec.gov is in the SEC client allowlist.
SEC_BULK_SUBMISSIONS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
)
SEC_BULK_INSTRUCTIONS = (
    "For history from 2006 Q1, use quarterly SEC datasets:\n"
    "   insider-scanner-cli sec-backfill-dataset --since 2006-01-01\n"
    "For 2003–2005 or targeted XML repairs, use the SEC 'submissions' archive "
    "(multi-GB, refreshed nightly).\n"
    "1. Download it once:\n"
    f"   {SEC_BULK_SUBMISSIONS_URL}\n"
    "2. Run backfill against the local file:\n"
    "   insider-scanner-cli sec-backfill --zip PATH\\TO\\submissions.zip --confirm-full-backfill\n"
    "Use --since/--until to bound filing dates, and --cik for targeted repairs.\n"
    "Add --force to a scoped repair to replace an already-ingested dataset filing.\n"
    "Backfill is resumable: re-run the same command after an interruption to continue."
)
DEFAULT_US_SCAN_SOURCES = ("secform4", "openinsider")
DEFAULT_US_LATEST_SOURCES = ("openinsider",)


class _BackfillOptions(TypedDict, total=False):
    since: date
    until: date
    force: bool


def _parse_date_arg(value: str) -> date:
    """Parse a YYYY-MM-DD date string from CLI arguments."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: {value!r} (expected YYYY-MM-DD)"
        )


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Expected a positive integer: {value!r}")
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"Expected a positive integer: {value!r}")
    return parsed


def cmd_scan(args: argparse.Namespace, services: ApplicationServices) -> None:
    """Scan for insider trades on a ticker."""
    from insider_scanner.core.merger import (
        filter_trades,
        save_scan_results,
    )

    ticker = args.ticker.upper()
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    log.info("Scanning insider trades for %s...", ticker)

    selected_sources = tuple(getattr(args, "sources", None) or DEFAULT_US_SCAN_SOURCES)
    trades = services.us.scan(
        ticker,
        sources=selected_sources,
        start_date=since,
        end_date=until,
        use_cache=not args.no_cache,
    )
    if warning := describe_incomplete(trades):
        print(f"Warning: {warning}", file=sys.stderr)

    filtered = filter_trades(
        trades,
        trade_type=args.type,
        min_value=args.min_value,
        congress_only=args.congress_only,
        since=since,
        until=until,
    )

    print(f"\nFound {len(filtered)} trades for {ticker}")
    for t in filtered[:20]:
        congress_tag = " [CONGRESS]" if t.is_congress else ""
        shares = format_reported_number(t.shares, missing=t.shares_missing)
        value = format_reported_number(t.value, missing=t.value_missing, currency=True)
        print(
            f"  {t.trade_date or '?':>10}  {t.trade_type:<8}  "
            f"{t.insider_name:<25}  {shares:>10} shares  "
            f"{value:>13}{congress_tag}"
        )
    if len(filtered) > 20:
        print(f"  ... and {len(filtered) - 20} more")

    if args.save:
        out = save_scan_results(filtered, label=f"{ticker}_scan")
        print(f"\nResults saved to: {out}")


def cmd_latest(args: argparse.Namespace, services: ApplicationServices) -> None:
    """Fetch latest insider trades across all tickers."""
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    selected_sources = tuple(
        getattr(args, "sources", None) or DEFAULT_US_LATEST_SOURCES
    )
    trades = services.us.latest(
        count=args.count,
        sources=selected_sources,
        use_cache=not args.no_cache,
        start_date=since,
        end_date=until,
    )
    if warning := describe_incomplete(trades):
        print(f"Warning: {warning}", file=sys.stderr)

    print(f"\nLatest {len(trades)} insider trades:")
    for t in trades[:30]:
        congress_tag = " [CONGRESS]" if t.is_congress else ""
        value = format_reported_number(t.value, missing=t.value_missing, currency=True)
        print(
            f"  {t.trade_date or '?':>10}  {t.ticker:<6}  {t.trade_type:<8}  "
            f"{t.insider_name:<25}  {value:>13}{congress_tag}"
        )

    if args.save:
        from insider_scanner.core.merger import save_scan_results

        out = save_scan_results(trades, label="latest_scan")
        print(f"\nResults saved to: {out}")


def cmd_resolve_cik(
    args: argparse.Namespace,
    _services: ApplicationServices,
) -> int:
    """Resolve a ticker to SEC CIK."""
    from insider_scanner.core.edgar import get_filing_url, resolve_cik

    ticker = args.ticker.upper()
    cik = resolve_cik(ticker, use_cache=not args.no_cache)

    if cik:
        print(f"{ticker} → CIK {cik}")
        print(f"EDGAR filings: {get_filing_url(cik)}")
        return 0
    else:
        print(f"Could not resolve CIK for {ticker}", file=sys.stderr)
        return 1


def cmd_init_congress(
    args: argparse.Namespace,
    _services: ApplicationServices | None,
) -> None:
    """Initialize the default Congress member list."""
    from insider_scanner.utils.config import CONGRESS_FILE, initialize_congress_seed

    status, backup = initialize_congress_seed(force=args.force)
    if status == "exists":
        print(f"Congress member list already exists at: {CONGRESS_FILE}")
    else:
        print(f"Congress member list {status} at: {CONGRESS_FILE}")
    if backup is not None:
        print(f"Previous member list backed up at: {backup}")


def cmd_eu_scan(
    args: argparse.Namespace,
    services: ApplicationServices | None = None,
) -> None:
    """Scan European insider transactions for one or more ISINs."""
    from insider_scanner.core.eu_merger import (
        filter_eu_trades,
        merge_eu_trades,
        save_eu_results,
    )
    from insider_scanner.utils.config import load_eu_watchlist

    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    country = (args.country or "All").upper()

    # Resolve ISINs: explicit arg or watchlist
    if args.watchlist:
        isins = load_eu_watchlist()
        if not isins:
            print(f"EU watchlist is empty. Add ISINs to {EU_WATCHLIST_FILE}.")
            return
    elif args.isin:
        isins = [args.isin.upper()]
    else:
        print("Provide an ISIN or use --watchlist.")
        return

    if services is None:
        raise RuntimeError("European scan service is required")

    all_trades: list[EuropeanInsiderTrade] = []
    for isin in isins:
        print(f"Scanning {isin}…")
        all_trades.extend(
            services.european.scan(
                isin,
                country=country,
                start_date=since,
                end_date=until,
                use_cache=True,
            )
        )

    merged = merge_eu_trades(all_trades)
    filtered = filter_eu_trades(
        merged,
        country=country if country != "ALL" else None,
        trade_type=args.type if args.type else None,
        min_value=args.min_value,
        since=since,
        until=until,
    )

    print(f"\nFound {len(filtered)} European insider trade(s)")
    for t in filtered[:30]:
        print(
            f"  {t.trade_date or '?':>10}  {t.country:<3}  {t.isin:<14}  "
            f"{t.issuer_name:<30}  {t.insider_name:<25}  "
            f"{t.trade_type:<5}  {t.total_value or '?'}"
        )
    if len(filtered) > 30:
        print(f"  ... and {len(filtered) - 30} more")

    if args.save:
        label = (args.isin or "watchlist").upper() + "_eu_scan"
        out = save_eu_results(filtered, label=label)
        print(f"\nResults saved to: {out}")


def cmd_import_legacy(
    args: argparse.Namespace,
    services: ApplicationServices,
) -> int:
    """Import legacy JSON exports into local persistence."""
    from insider_scanner.services.importer import import_legacy_path

    report = import_legacy_path(
        args.path,
        services.persistence,
        max_file_size_bytes=args.max_file_size_mib * 1024 * 1024,
    )
    for item in report.files:
        print(
            f"{item.path}: inserted={item.inserted} updated={item.updated} "
            f"skipped={item.skipped} errors={item.errors}"
        )
        for message in item.messages:
            print(f"  {message}")
    print(
        f"Total: inserted={report.inserted} updated={report.updated} "
        f"skipped={report.skipped} errors={report.errors}"
    )
    return 1 if report.errors else 0


def cmd_price(
    args: argparse.Namespace,
    services: ApplicationServices | None = None,
) -> None:
    """Fetch + cache daily bars for a US ticker and print them."""
    from datetime import date, timedelta

    end = args.until or date.today()
    start = args.since or (end - timedelta(days=365))
    if start > end:
        raise ValueError("--since must not be after --until")
    engine = services.persistence.engine if services else None
    bars = get_price_history(args.ticker.upper(), start, end, engine=engine)
    if not bars:
        print(f"No price data for {args.ticker.upper()} in {start}..{end}")
        return
    print(f"{args.ticker.upper()}  {start}..{end}  ({len(bars)} bars)")
    print(
        f"{'date':<12} {'open':>10} {'high':>10} {'low':>10} {'close':>10} {'volume':>14}"
    )
    for b in bars:
        print(
            f"{b.date.isoformat():<12} {b.open:>10.2f} {b.high:>10.2f} "
            f"{b.low:>10.2f} {b.close:>10.2f} {b.volume:>14.0f}"
        )


# ---------------------------------------------------------------------------
# SEC EDGAR daily ingestion commands
# ---------------------------------------------------------------------------


def _build_sec_client() -> SecClient | None:
    """Construct a hardened SEC client, or print guidance and return ``None``.

    The SEC fair-access client rejects the default placeholder contact email,
    so a misconfigured ``SEC_USER_AGENT`` is surfaced as a friendly stderr
    message and a ``None`` return (the caller maps this to exit code 1) before
    any network work begins.
    """
    from insider_scanner.core.sec_client import SecClient, SecConfigurationError
    from insider_scanner.utils.config import get_sec_user_agent

    try:
        # Only ``user_agent`` is caller-supplied here; the security and retry
        # policies use validated defaults, so a SecConfigurationError raised by
        # this call can only be the user-agent rule.
        return SecClient(user_agent=get_sec_user_agent())
    except SecConfigurationError:
        print(
            "SEC_USER_AGENT is not set for SEC fair access. Set it to your app or "
            "company name plus a real contact email, e.g.\n"
            '  SEC_USER_AGENT="MyApp/1.0 (you@example.com)"\n'
            "The default placeholder email is rejected — see the README SEC "
            "fair-access section.",
            file=sys.stderr,
        )
        return None


def _sec_cache_root() -> Path:
    """Directory holding the validated SEC filing cache."""
    from insider_scanner.utils.config import EDGAR_CACHE_DIR

    return EDGAR_CACHE_DIR


def _make_cli_progress(
    quiet: bool,
) -> Callable[[SecDownloadProgress], None] | None:
    """Build a per-date progress callback that writes to stderr, or ``None``."""
    if quiet:
        return None

    def _on_progress(snapshot: SecDownloadProgress) -> None:
        print(
            f"[{snapshot.dates_completed}/{snapshot.dates_total}] "
            f"{snapshot.current_date.isoformat()} "
            f"discovered={snapshot.discovered} parsed={snapshot.parsed} "
            f"failed={snapshot.failed}",
            file=sys.stderr,
        )

    return _on_progress


@contextmanager
def _cooperative_interrupt() -> Iterator[Callable[[], bool]]:
    """Let SEC services flush their batch on the first Ctrl+C."""
    stopped = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        if stopped.is_set():
            raise KeyboardInterrupt
        stopped.set()
        print(
            "Stopping after the current filing; preserving committed progress...",
            file=sys.stderr,
        )

    previous = signal.getsignal(signal.SIGINT)
    install = current_thread() is main_thread()
    if install:
        signal.signal(signal.SIGINT, request_stop)
    try:
        yield stopped.is_set
    finally:
        if install:
            signal.signal(signal.SIGINT, previous)


def _make_backfill_progress(
    quiet: bool,
) -> Callable[[SecBackfillSummary], None] | None:
    if quiet:
        return None
    last_report: float | None = None

    def report(snapshot: SecBackfillSummary) -> None:
        nonlocal last_report
        now = monotonic()
        if last_report is not None and now - last_report < 2 and not snapshot.cancelled:
            return
        last_report = now
        eta = (
            "unknown"
            if snapshot.eta_seconds is None
            else f"{snapshot.eta_seconds:.0f}s"
        )
        print(
            f"members={snapshot.members_completed}/{snapshot.members_total} "
            f"discovered={snapshot.filings_discovered} parsed={snapshot.filings_parsed} "
            f"committed={snapshot.filings_committed} "
            f"failed={snapshot.failures} skipped_resume={snapshot.skipped_resume} "
            f"rate={snapshot.filings_per_second:.2f} filings/s ETA={eta}",
            file=sys.stderr,
            flush=True,
        )

    return report


def _sec_exit_status(summary: SecDailyIngestionSummary, interrupted: bool) -> int:
    if summary.cancelled or interrupted:
        print(
            "Interrupted. Committed progress is preserved; rerun to resume.",
            file=sys.stderr,
        )
        return 130
    if summary.stopped_on_error or summary.dates_completed < summary.dates_total:
        return 3
    return 0


def _comparison_targets_from_args(
    args: argparse.Namespace,
) -> tuple[SecComparisonTarget, ...]:
    from insider_scanner.services.sec_comparison import SecComparisonTarget

    tickers = tuple(args.tickers or ())
    dates = tuple(args.dates or ())
    if dates:
        if args.since is not None or args.until is not None:
            raise ValueError("Use --date or --since/--until, not both")
        return tuple(
            SecComparisonTarget(ticker, day, day) for ticker in tickers for day in dates
        )
    if args.since is None or args.until is None:
        raise ValueError("Provide --date or both --since and --until")
    return tuple(
        SecComparisonTarget(ticker, args.since, args.until) for ticker in tickers
    )


def cmd_sec_compare(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Render a local DB comparison report for SEC validation."""
    from insider_scanner.services.sec_comparison import (
        DEFAULT_LEGACY_SOURCES,
        render_report,
    )

    try:
        targets = _comparison_targets_from_args(args)
        legacy_sources = tuple(args.legacy_sources or DEFAULT_LEGACY_SOURCES)
        report = services.make_sec_comparison().compare(
            targets=targets,
            legacy_sources=legacy_sources,
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    rendered = render_report(report, args.format)
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Comparison report written to: {args.output}")
    else:
        print(rendered, end="")
    return 0


def _print_sec_summary(summary: SecDailyIngestionSummary) -> None:
    """Print a human-readable summary of one ingestion run to stdout."""
    interval = summary.interval
    print(
        f"SEC ingestion {interval.start.isoformat()}..{interval.end.isoformat()}: "
        f"{summary.dates_completed}/{summary.dates_total} dates completed"
    )
    print(
        f"  filings:      discovered={summary.filings_discovered} "
        f"parsed={summary.filings_parsed} unsupported={summary.unsupported}"
    )
    print(
        f"  transactions: inserted={summary.transactions_inserted} "
        f"updated={summary.transactions_updated} "
        f"skipped={summary.transactions_skipped}"
    )
    print(f"  failures:     {summary.failures}")
    if summary.dates_completed < summary.dates_total:
        print(
            "Warning: some SEC dates are incomplete and remain available for retry. "
            "The current day's index is provisional until that day ends.",
            file=sys.stderr,
        )


def cmd_sec_daily(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Ingest one day of SEC ownership filings into the local database."""
    today = sec_today()
    day = args.date
    if day is None:
        day = today - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    if day > today:
        print("Invalid date: SEC date must not be in the future", file=sys.stderr)
        return 2
    client = _build_sec_client()
    if client is None:
        return 1
    with closing(client), _cooperative_interrupt() as cancelled:
        service = services.make_sec_daily(
            client=client,
            cache_root=_sec_cache_root(),
            cleanup=not args.no_cleanup,
        )
        summary = service.ingest_date(
            day, cancelled=cancelled, on_progress=_make_cli_progress(args.quiet)
        )
        _print_sec_summary(summary)
        return _sec_exit_status(summary, cancelled())


def cmd_sec_catchup(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Ingest an inclusive date range of SEC ownership filings (resumable)."""
    from insider_scanner.services.common import validate_range

    try:
        validate_range(args.since, args.until)
        if args.until > sec_today():
            raise ValueError("end_date must not be in the future")
    except ValueError as error:
        print(f"Invalid date range: {error}", file=sys.stderr)
        return 2
    client = _build_sec_client()
    if client is None:
        return 1
    with closing(client), _cooperative_interrupt() as cancelled:
        service = services.make_sec_daily(
            client=client,
            cache_root=_sec_cache_root(),
            cleanup=not args.no_cleanup,
        )
        summary = service.ingest_range(
            args.since,
            args.until,
            cancelled=cancelled,
            on_progress=_make_cli_progress(args.quiet),
        )
        _print_sec_summary(summary)
        return _sec_exit_status(summary, cancelled())


def _print_backfill_summary(summary: SecBackfillSummary) -> None:
    print(
        f"SEC backfill: discovered={summary.filings_discovered} "
        f"parsed={summary.filings_parsed} unsupported={summary.unsupported} "
        f"rows={summary.discovered_rows} duplicates={summary.duplicates}"
    )
    print(
        f"  transactions: inserted={summary.transactions_inserted} "
        f"updated={summary.transactions_updated} skipped={summary.transactions_skipped}"
    )
    print(
        f"  skipped:      resume={summary.skipped_resume} "
        f"metadata={summary.skipped_metadata} members={summary.skipped_members}"
    )
    print(f"  failures:     {summary.failures}")
    print(
        f"  archive:      members={summary.members_completed}/{summary.members_total} "
        f"(durable position); this run elapsed={summary.elapsed_seconds:.1f}s "
        f"rate={summary.filings_per_second:.2f} filings/s"
    )


def cmd_sec_backfill(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Run an explicit, resumable full bulk backfill from a local submissions ZIP."""
    if not args.confirm_full_backfill:
        print(SEC_BULK_INSTRUCTIONS)
        print(
            "\nRefusing to start a full bulk backfill without --confirm-full-backfill.",
            file=sys.stderr,
        )
        return 2

    zip_path = Path(args.zip)
    if not zip_path.is_file():
        print(SEC_BULK_INSTRUCTIONS, file=sys.stderr)
        print(f"\nSubmissions archive not found: {zip_path}", file=sys.stderr)
        return 2

    try:
        ciks = frozenset(edgar.normalize_cik(c) for c in args.cik) if args.cik else None
        since, until = getattr(args, "since", None), getattr(args, "until", None)
        force = getattr(args, "force", False)
        if since is not None and until is not None and since > until:
            raise ValueError("since must not be after until")
        if force and not ciks and (since is None or until is None):
            raise ValueError("--force requires --cik or both --since and --until")
    except ValueError as error:
        print(f"Invalid backfill input: {error}", file=sys.stderr)
        return 2

    limits = {
        name: value
        for name in ("zip_max_entries", "zip_max_total_bytes", "zip_max_member_bytes")
        if (value := getattr(args, name, None)) is not None
    }
    policy = replace(DEFAULT_SEC_SECURITY_POLICY, **limits)
    client = _build_sec_client()
    if client is None:
        return 1
    with closing(client), _cooperative_interrupt() as cancelled:
        service = services.make_sec_backfill(
            client=client,
            cache_root=_sec_cache_root(),
            policy=policy,
            cleanup=not args.no_cleanup,
        )
        print(f"Starting full bulk backfill from {zip_path} (resumable).")
        options: _BackfillOptions = {}
        if since is not None:
            options["since"] = since
        if until is not None:
            options["until"] = until
        if force:
            options["force"] = True
        try:
            summary = service.run(
                zip_path,
                confirm=True,
                ciks=ciks,
                cancelled=cancelled,
                on_progress=_make_backfill_progress(getattr(args, "quiet", False)),
                **options,
            )
        except SecBulkError as error:
            print(f"SEC backfill archive error: {error}", file=sys.stderr)
            return 1
        _print_backfill_summary(summary)
        if summary.cancelled or cancelled():
            print(
                "Interrupted. Committed progress is preserved; rerun to resume.",
                file=sys.stderr,
            )
            return 130
        if summary.skipped_members:
            print(
                "Warning: some archive members could not be read and remain available for retry.",
                file=sys.stderr,
            )
            return 3
        return 0


def cmd_sec_backfill_dataset(
    args: argparse.Namespace, services: ApplicationServices
) -> int:
    """Import quarterly history with explicit publication-gap guidance."""
    from insider_scanner.cli_sec_dataset import run_dataset_command

    return run_dataset_command(
        args,
        services,
        build_client=_build_sec_client,
        cache_root=_sec_cache_root,
        interrupt=_cooperative_interrupt,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insider-scanner-cli",
        description="Scan insider trades from secform4.com, openinsider.com, SEC EDGAR, and European regulators.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        default=None,
        help="Diagnostic level (default: INSIDER_SCANNER_LOG_LEVEL or INFO)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser(
        "scan",
        help="Scan insider trades for a ticker",
        description=(
            "Scan the requested ticker from the selected sources. "
            "Without --since/--until, scan the last six months through today."
        ),
    )
    p_scan.add_argument("ticker", help="Stock ticker symbol")
    p_scan.add_argument(
        "--type", choices=["Buy", "Sell", "Exercise", "Other"], default=None
    )
    p_scan.add_argument("--min-value", type=float, default=None)
    p_scan.add_argument("--congress-only", action="store_true")
    p_scan.add_argument("--since", type=_parse_date_arg, default=None)
    p_scan.add_argument("--until", type=_parse_date_arg, default=None)
    p_scan.add_argument("--save", action="store_true")
    p_scan.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch again, bypassing stored coverage, refresh freshness and HTTP caches",
    )
    p_scan.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=("secform4", "openinsider", "sec_edgar"),
        default=None,
        help=(
            "US source to scan (repeatable). Default before validation approval: "
            "secform4 + openinsider. Use sec_edgar for local SEC-native rows."
        ),
    )
    p_scan.set_defaults(func=cmd_scan)

    # latest
    p_latest = sub.add_parser("latest", help="Fetch latest insider trades")
    p_latest.add_argument("--count", type=_parse_positive_int, default=100)
    p_latest.add_argument("--since", type=_parse_date_arg, default=None)
    p_latest.add_argument("--until", type=_parse_date_arg, default=None)
    p_latest.add_argument("--save", action="store_true")
    p_latest.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch again, bypassing refresh freshness and HTTP caches",
    )
    p_latest.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=("secform4", "openinsider", "sec_edgar"),
        default=None,
        help=(
            "US latest source (repeatable). Default before validation approval: "
            "openinsider. Use sec_edgar for local SEC-native rows."
        ),
    )
    p_latest.set_defaults(func=cmd_latest)

    # resolve-cik
    p_cik = sub.add_parser(
        "resolve-cik",
        aliases=["cik"],
        help="Resolve ticker to SEC CIK number",
    )
    p_cik.add_argument("ticker")
    p_cik.add_argument("--no-cache", action="store_true")
    p_cik.set_defaults(func=cmd_resolve_cik)

    # init-congress
    p_init = sub.add_parser("init-congress", help="Create default congress member list")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Reset from the packaged seed after a backup",
    )
    p_init.set_defaults(func=cmd_init_congress)

    # eu-scan
    p_eu = sub.add_parser(
        "eu-scan",
        help="Scan European insider transactions (UK, DE, FR, NL)",
    )
    p_eu.add_argument(
        "isin",
        nargs="?",
        default=None,
        help="12-character ISIN (e.g. GB0002875804)",
    )
    p_eu.add_argument(
        "--country",
        choices=["UK", "DE", "FR", "NL", "All"],
        default="All",
        help="Restrict to a single country (default: All)",
    )
    p_eu.add_argument(
        "--type",
        choices=["Buy", "Sell", "Other"],
        default=None,
    )
    p_eu.add_argument("--min-value", type=float, default=None)
    p_eu.add_argument("--since", type=_parse_date_arg, default=None)
    p_eu.add_argument("--until", type=_parse_date_arg, default=None)
    p_eu.add_argument(
        "--watchlist",
        action="store_true",
        help=f"Scan all ISINs in {EU_WATCHLIST_FILE}",
    )
    p_eu.add_argument("--save", action="store_true")
    p_eu.set_defaults(func=cmd_eu_scan)

    p_import = sub.add_parser(
        "import-legacy",
        help="Import legacy JSON trade exports",
    )
    p_import.add_argument("path", type=Path)
    p_import.add_argument(
        "--max-file-size-mib",
        type=_parse_positive_int,
        default=50,
        help="Maximum size of each JSON file in MiB (default: 50)",
    )
    p_import.set_defaults(func=cmd_import_legacy)

    p_price = sub.add_parser("price", help="Fetch + cache daily prices for a US ticker")
    p_price.add_argument("ticker", help="US stock ticker symbol")
    p_price.add_argument("--since", type=_parse_date_arg, default=None)
    p_price.add_argument("--until", type=_parse_date_arg, default=None)
    p_price.set_defaults(func=cmd_price)

    # sec-daily
    p_sec_daily = sub.add_parser(
        "sec-daily",
        help="Ingest one day of SEC ownership filings into the local DB",
    )
    p_sec_daily.add_argument(
        "--date",
        type=_parse_date_arg,
        default=None,
        help=(
            "Day to ingest (YYYY-MM-DD); defaults to the previous weekday in US "
            "Eastern time. Today's index remains provisional and is retried later."
        ),
    )
    p_sec_daily.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep the download cache after a clean run",
    )
    p_sec_daily.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-date progress output on stderr",
    )
    p_sec_daily.set_defaults(func=cmd_sec_daily)

    # sec-catchup
    p_sec_catchup = sub.add_parser(
        "sec-catchup",
        help="Ingest a date range of SEC ownership filings (resumable)",
    )
    p_sec_catchup.add_argument(
        "--since",
        type=_parse_date_arg,
        required=True,
        help="First day to ingest (YYYY-MM-DD)",
    )
    p_sec_catchup.add_argument(
        "--until",
        type=_parse_date_arg,
        required=True,
        help="Last day to ingest (YYYY-MM-DD)",
    )
    p_sec_catchup.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Keep the download cache after a clean run",
    )
    p_sec_catchup.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-date progress output on stderr",
    )
    p_sec_catchup.set_defaults(func=cmd_sec_catchup)

    # sec-backfill (full bulk backfill from a local submissions ZIP)
    p_sec_backfill = sub.add_parser(
        "sec-backfill",
        help="Full bulk backfill from a local SEC submissions ZIP (resumable)",
        epilog=SEC_BULK_INSTRUCTIONS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_sec_backfill.add_argument(
        "--zip",
        required=True,
        help="Path to a locally-downloaded SEC submissions.zip (see epilog for the link)",
    )
    p_sec_backfill.add_argument(
        "--cik",
        action="append",
        default=None,
        help="Limit backfill to one or more CIKs (repeatable). Default: all filings.",
    )
    p_sec_backfill.add_argument(
        "--confirm-full-backfill",
        action="store_true",
        help="Required: acknowledge the heavy, network-intensive full backfill",
    )
    p_sec_backfill.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live progress on stderr; keep the final summary and errors",
    )
    p_sec_backfill.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Compatibility option; bulk ingestion no longer uses the download cache",
    )
    for limit, description in (
        ("entries", "archive entry count"),
        ("total-bytes", "total uncompressed archive bytes"),
        ("member-bytes", "uncompressed bytes per member"),
    ):
        p_sec_backfill.add_argument(
            f"--zip-max-{limit}",
            type=_parse_positive_int,
            default=None,
            help=f"Override the {description} limit for a trusted local archive",
        )
    p_sec_backfill.set_defaults(func=cmd_sec_backfill)

    p_sec_backfill.add_argument(
        "--since", type=_parse_date_arg, help="First filing date to fetch (inclusive)"
    )
    p_sec_backfill.add_argument(
        "--until", type=_parse_date_arg, help="Last filing date to fetch (inclusive)"
    )
    p_sec_backfill.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch terminal filings; requires CIKs or both date bounds",
    )

    p_dataset = sub.add_parser(
        "sec-backfill-dataset",
        help="Import quarterly SEC datasets (2006 Q1 onward)",
        description="Import local quarterly Form 3/4/5 ZIPs or download published quarters from SEC. Unpublished quarters require sec-catchup; 2003–2005 requires XML backfill.",
    )
    p_dataset.add_argument(
        "--zip",
        action="append",
        type=Path,
        help="Local quarterly ZIP; repeat for several quarters. Omit to download from SEC.",
    )
    p_dataset.add_argument(
        "--since",
        type=_parse_date_arg,
        help="First filing date, inclusive (default: 2006-01-01)",
    )
    p_dataset.add_argument(
        "--until",
        type=_parse_date_arg,
        help="Last filing date, inclusive (default: today in US Eastern time)",
    )
    p_dataset.add_argument(
        "--cik", action="append", help="Select issuer/reporting-owner CIK; repeatable"
    )
    p_dataset.add_argument(
        "--download-dir",
        type=Path,
        help="Keep downloaded quarters here (default: SEC cache/datasets)",
    )
    p_dataset.add_argument(
        "--redownload",
        action="store_true",
        help="Atomically refresh cached ZIPs from the published links",
    )
    p_dataset.add_argument(
        "--reimport",
        action="store_true",
        help="Reimport successful dataset filings; existing XML retains priority",
    )
    p_dataset.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress; keep final summaries and errors",
    )
    p_dataset.set_defaults(func=cmd_sec_backfill_dataset)

    # sec-compare (local validation report, no live network fetches)
    p_sec_compare = sub.add_parser(
        "sec-compare",
        help="Compare persisted SEC EDGAR rows against persisted legacy US sources",
    )
    p_sec_compare.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        required=True,
        help="Ticker to compare (repeatable)",
    )
    p_sec_compare.add_argument(
        "--date",
        action="append",
        dest="dates",
        type=_parse_date_arg,
        default=None,
        help="Single filing date to compare (repeatable)",
    )
    p_sec_compare.add_argument(
        "--since",
        type=_parse_date_arg,
        default=None,
        help="First filing date in a comparison range (YYYY-MM-DD)",
    )
    p_sec_compare.add_argument(
        "--until",
        type=_parse_date_arg,
        default=None,
        help="Last filing date in a comparison range (YYYY-MM-DD)",
    )
    p_sec_compare.add_argument(
        "--legacy-source",
        action="append",
        dest="legacy_sources",
        default=None,
        help="Legacy source to compare against (repeatable; default: secform4 + openinsider)",
    )
    p_sec_compare.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Report format (default: text)",
    )
    p_sec_compare.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to this file instead of stdout",
    )
    p_sec_compare.set_defaults(func=cmd_sec_compare)
    return parser


def _validate_cli_range(args: argparse.Namespace) -> None:
    """Reject incomplete or reversed scan ranges before opening persistence."""
    if args.func not in (cmd_scan, cmd_latest, cmd_price):
        return
    start = args.since
    end = args.until
    if args.func is cmd_price:
        end = end or date.today()
        start = start or (end - timedelta(days=365))
    elif (start is None) != (end is None):
        raise ValueError("provide both --since and --until")
    if start is not None and end is not None and start > end:
        raise ValueError("--since must not be after --until")


def run(
    argv: list[str] | None = None,
    *,
    service_factory: Callable[[], ApplicationServices] = open_application_services,
) -> int:
    """Run one CLI command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(getattr(args, "log_level", None))
    try:
        _validate_cli_range(args)
    except ValueError as error:
        print(f"Invalid input: {error}", file=sys.stderr)
        return 2
    services: ApplicationServices | None = None
    if args.func is cmd_init_congress:
        try:
            cmd_init_congress(args, None)
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            return 130
        except OSError:
            log.exception("Congress member list initialization failed")
            print(
                f"Could not initialize Congress member list. {diagnostic_hint()}",
                file=sys.stderr,
            )
            return 1
        return 0
    try:
        ensure_dirs()
        services = service_factory()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception:
        log.exception("Persistence startup failed")
        print(
            f"Could not initialize local database. {diagnostic_hint()}", file=sys.stderr
        )
        return 1
    result = 0
    try:
        command_result = args.func(args, services)
        if isinstance(command_result, int):
            result = command_result
    except KeyboardInterrupt:
        print("Interrupted. Committed progress is preserved.", file=sys.stderr)
        result = 130
    except SecBlockedError as error:
        print(str(error), file=sys.stderr)
        result = 1
    except ValueError as error:
        print(f"Invalid input: {error}", file=sys.stderr)
        result = 2
    except Exception:
        log.exception("CLI command failed")
        print(f"Command failed. {diagnostic_hint()}", file=sys.stderr)
        result = 1
    finally:
        if services is not None:
            try:
                services.close()
            except Exception as error:  # noqa: BLE001 - report shutdown failure without losing command status
                log.error(
                    "CLI service shutdown failed: exception=%s",
                    type(error).__name__,
                )
                print(
                    f"Could not close local database cleanly. {diagnostic_hint()}",
                    file=sys.stderr,
                )
                if result == 0:
                    result = 1
    return result


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
