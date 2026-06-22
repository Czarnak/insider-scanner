"""CLI entry point for headless insider trade scanning."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from insider_scanner.services.application import (
    ApplicationServices,
    open_application_services,
)
from insider_scanner.core import edgar
from insider_scanner.core.prices import get_price_history
from insider_scanner.utils.config import EU_WATCHLIST_FILE, ensure_dirs
from insider_scanner.utils.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from insider_scanner.core.sec_client import SecClient
    from insider_scanner.services.sec_backfill import SecBackfillSummary
    from insider_scanner.services.sec_daily import SecDailyIngestionSummary
    from insider_scanner.services.sec_downloads import SecDownloadProgress

log = get_logger("cli")

# SEC submissions bulk archive — the only input to full backfill.
# VERIFY against https://www.sec.gov/search-filings/edgar-application-programming-interfaces
# (host www.sec.gov is already in the SEC client host allowlist).
SEC_BULK_SUBMISSIONS_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
)
SEC_BULK_INSTRUCTIONS = (
    "Full backfill needs the SEC 'submissions' bulk archive (multi-GB, refreshed nightly).\n"
    "1. Download it once:\n"
    f"   {SEC_BULK_SUBMISSIONS_URL}\n"
    "2. Run backfill against the local file:\n"
    "   insider-scanner-cli sec-backfill --zip PATH\\TO\\submissions.zip --confirm-full-backfill\n"
    "Backfill is resumable: re-run the same command after an interruption to continue."
)


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

    trades = services.us.scan(
        ticker,
        sources=("secform4", "openinsider"),
        start_date=since,
        end_date=until,
        use_cache=not args.no_cache,
    )

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
        print(
            f"  {t.trade_date or '?':>10}  {t.trade_type:<8}  "
            f"{t.insider_name:<25}  {t.shares:>10,.0f} shares  "
            f"${t.value:>12,.0f}{congress_tag}"
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
    trades = services.us.latest(
        count=args.count,
        sources=("openinsider",),
        use_cache=not args.no_cache,
        start_date=since,
        end_date=until,
    )

    print(f"\nLatest {len(trades)} insider trades:")
    for t in trades[:30]:
        congress_tag = " [CONGRESS]" if t.is_congress else ""
        print(
            f"  {t.trade_date or '?':>10}  {t.ticker:<6}  {t.trade_type:<8}  "
            f"{t.insider_name:<25}  ${t.value:>12,.0f}{congress_tag}"
        )

    if args.save:
        from insider_scanner.core.merger import save_scan_results

        save_scan_results(trades, label="latest_scan")


def cmd_resolve_cik(
    args: argparse.Namespace,
    _services: ApplicationServices,
) -> None:
    """Resolve a ticker to SEC CIK."""
    from insider_scanner.core.edgar import resolve_cik, get_filing_url

    ticker = args.ticker.upper()
    cik = resolve_cik(ticker, use_cache=not args.no_cache)

    if cik:
        print(f"{ticker} → CIK {cik}")
        print(f"EDGAR filings: {get_filing_url(cik)}")
    else:
        print(f"Could not resolve CIK for {ticker}")


def cmd_init_congress(
    args: argparse.Namespace,
    _services: ApplicationServices,
) -> None:
    """Initialize the default Congress member list."""
    from insider_scanner.core.senate import init_default_congress_file
    from insider_scanner.utils.config import CONGRESS_FILE

    init_default_congress_file()
    print(f"Congress member list created at: {CONGRESS_FILE}")


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

    all_trades = []
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
    from insider_scanner.utils.config import SEC_USER_AGENT

    try:
        # Only ``user_agent`` is caller-supplied here; the security and retry
        # policies use validated defaults, so a SecConfigurationError raised by
        # this call can only be the user-agent rule.
        return SecClient(user_agent=SEC_USER_AGENT)
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


def _sec_checkpoint_path() -> Path:
    """Stable checkpoint file under the cache dir for resumable catch-up runs."""
    from insider_scanner.utils.config import EDGAR_CACHE_DIR

    return EDGAR_CACHE_DIR / "sec_catchup_checkpoint.json"


def _sec_backfill_checkpoint_path() -> Path:
    """Stable checkpoint file under the cache dir for resumable full-backfill runs."""
    from insider_scanner.utils.config import EDGAR_CACHE_DIR

    return EDGAR_CACHE_DIR / "sec_backfill_checkpoint.json"


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


def _print_sec_summary(summary: SecDailyIngestionSummary) -> None:
    """Print a human-readable summary of one ingestion run to stdout."""
    interval = summary.interval
    print(
        f"SEC ingestion {interval.start.isoformat()}..{interval.end.isoformat()}: "
        f"{summary.dates_completed}/{summary.dates_total} dates completed"
    )
    print(
        f"  filings:      discovered={summary.filings_discovered} "
        f"parsed={summary.filings_parsed}"
    )
    print(
        f"  transactions: inserted={summary.transactions_inserted} "
        f"updated={summary.transactions_updated} "
        f"skipped={summary.transactions_skipped}"
    )
    print(f"  failures:     {summary.failures}")


def cmd_sec_daily(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Ingest one day of SEC ownership filings into the local database."""
    client = _build_sec_client()
    if client is None:
        return 1
    service = services.make_sec_daily(
        client=client,
        cache_root=_sec_cache_root(),
        cleanup=not args.no_cleanup,
        checkpoint_path=None,
    )
    day = args.date or date.today()
    summary = service.ingest_date(day, on_progress=_make_cli_progress(args.quiet))
    _print_sec_summary(summary)
    return 0


def cmd_sec_catchup(args: argparse.Namespace, services: ApplicationServices) -> int:
    """Ingest an inclusive date range of SEC ownership filings (resumable)."""
    from insider_scanner.services.common import validate_range

    try:
        validate_range(args.since, args.until)
    except ValueError as error:
        print(f"Invalid date range: {error}", file=sys.stderr)
        return 2
    client = _build_sec_client()
    if client is None:
        return 1
    service = services.make_sec_daily(
        client=client,
        cache_root=_sec_cache_root(),
        cleanup=not args.no_cleanup,
        checkpoint_path=_sec_checkpoint_path(),
    )
    summary = service.ingest_range(
        args.since, args.until, on_progress=_make_cli_progress(args.quiet)
    )
    _print_sec_summary(summary)
    return 0


def _print_backfill_summary(summary: SecBackfillSummary) -> None:
    print(
        f"SEC backfill: discovered={summary.filings_discovered} "
        f"parsed={summary.filings_parsed}"
    )
    print(
        f"  transactions: inserted={summary.transactions_inserted} "
        f"updated={summary.transactions_updated} skipped={summary.transactions_skipped}"
    )
    print(
        f"  skipped:      resume={summary.skipped_resume} "
        f"metadata={summary.skipped_metadata}"
    )
    print(f"  failures:     {summary.failures}")


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
        ciks = (
            frozenset(edgar.normalize_cik(c) for c in args.cik) if args.cik else None
        )
    except ValueError as error:
        print(f"Invalid --cik value: {error}", file=sys.stderr)
        return 2

    client = _build_sec_client()
    if client is None:
        return 1
    service = services.make_sec_backfill(
        client=client,
        cache_root=_sec_cache_root(),
        cleanup=not args.no_cleanup,
        checkpoint_path=_sec_backfill_checkpoint_path(),
    )
    print(f"Starting full bulk backfill from {zip_path} (resumable).")
    summary = service.run(zip_path, confirm=True, ciks=ciks)
    _print_backfill_summary(summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insider-scanner-cli",
        description="Scan insider trades from secform4.com, openinsider.com, SEC EDGAR, and European regulators.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan insider trades for a ticker")
    p_scan.add_argument("ticker", help="Stock ticker symbol")
    p_scan.add_argument(
        "--type", choices=["Buy", "Sell", "Exercise", "Other"], default=None
    )
    p_scan.add_argument("--min-value", type=float, default=None)
    p_scan.add_argument("--congress-only", action="store_true")
    p_scan.add_argument("--since", type=_parse_date_arg, default=None)
    p_scan.add_argument("--until", type=_parse_date_arg, default=None)
    p_scan.add_argument("--save", action="store_true")
    p_scan.add_argument("--no-cache", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    # latest
    p_latest = sub.add_parser("latest", help="Fetch latest insider trades")
    p_latest.add_argument("--count", type=int, default=100)
    p_latest.add_argument("--since", type=_parse_date_arg, default=None)
    p_latest.add_argument("--until", type=_parse_date_arg, default=None)
    p_latest.add_argument("--save", action="store_true")
    p_latest.add_argument("--no-cache", action="store_true")
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
            "Day to ingest (YYYY-MM-DD); defaults to today. The SEC daily index "
            "lags the filing date by about one business day."
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
        "--no-cleanup",
        action="store_true",
        help="Keep the download cache after a clean run",
    )
    p_sec_backfill.set_defaults(func=cmd_sec_backfill)

    return parser


def run(
    argv: list[str] | None = None,
    *,
    service_factory: Callable[[], ApplicationServices] = open_application_services,
) -> int:
    """Run one CLI command and return a process exit code."""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    services: ApplicationServices | None = None
    try:
        ensure_dirs()
        services = service_factory()
    except Exception:
        log.exception("Persistence startup failed")
        print("Could not initialize local database.", file=sys.stderr)
        return 1
    result = 0
    try:
        command_result = args.func(args, services)
        if isinstance(command_result, int):
            result = command_result
    except Exception:
        log.exception("CLI command failed")
        print("Command failed. See logs for details.", file=sys.stderr)
        result = 1
    if services is not None:
        try:
            services.close()
        except Exception as error:
            log.error(
                "CLI service shutdown failed: exception=%s",
                type(error).__name__,
            )
            print("Could not close local database cleanly.", file=sys.stderr)
            if result == 0:
                result = 1
    return result


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
