"""CLI entry point for headless insider trade scanning."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from insider_scanner.services.application import (
    ApplicationServices,
    open_application_services,
)
from insider_scanner.utils.config import EU_WATCHLIST_FILE, ensure_dirs
from insider_scanner.utils.logging import get_logger, setup_logging

log = get_logger("cli")


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

    # cik
    p_cik = sub.add_parser("cik", help="Resolve ticker to SEC CIK number")
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
