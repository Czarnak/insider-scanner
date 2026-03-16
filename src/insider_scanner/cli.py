"""CLI entry point for headless insider trade scanning."""

from __future__ import annotations

import argparse
from datetime import date

from insider_scanner.utils.config import ensure_dirs
from insider_scanner.utils.logging import setup_logging, get_logger

log = get_logger("cli")


def _parse_date_arg(value: str) -> date:
    """Parse a YYYY-MM-DD date string from CLI arguments."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: {value!r} (expected YYYY-MM-DD)"
        )


def cmd_scan(args: argparse.Namespace) -> None:
    """Scan for insider trades on a ticker."""
    from insider_scanner.core.secform4 import scrape_ticker as sf4_scrape
    from insider_scanner.core.openinsider import scrape_ticker as oi_scrape
    from insider_scanner.core.merger import (
        merge_trades,
        filter_trades,
        save_scan_results,
    )

    ticker = args.ticker.upper()
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    log.info("Scanning insider trades for %s...", ticker)

    sf4_trades = sf4_scrape(
        ticker, use_cache=not args.no_cache, start_date=since, end_date=until
    )
    oi_trades = oi_scrape(
        ticker, use_cache=not args.no_cache, start_date=since, end_date=until
    )

    merged = merge_trades(sf4_trades, oi_trades)
    filtered = filter_trades(
        merged,
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


def cmd_latest(args: argparse.Namespace) -> None:
    """Fetch latest insider trades across all tickers."""
    from insider_scanner.core.openinsider import scrape_latest

    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    trades = scrape_latest(
        count=args.count,
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


def cmd_resolve_cik(args: argparse.Namespace) -> None:
    """Resolve a ticker to SEC CIK."""
    from insider_scanner.core.edgar import resolve_cik, get_filing_url

    ticker = args.ticker.upper()
    cik = resolve_cik(ticker, use_cache=not args.no_cache)

    if cik:
        print(f"{ticker} → CIK {cik}")
        print(f"EDGAR filings: {get_filing_url(cik)}")
    else:
        print(f"Could not resolve CIK for {ticker}")


def cmd_init_congress(args: argparse.Namespace) -> None:
    """Initialize the default Congress member list."""
    from insider_scanner.core.senate import init_default_congress_file
    from insider_scanner.utils.config import CONGRESS_FILE

    init_default_congress_file()
    print(f"Congress member list created at: {CONGRESS_FILE}")


def cmd_eu_scan(args: argparse.Namespace) -> None:
    """Scan European insider transactions for one or more ISINs."""
    from insider_scanner.core.eu_merger import (
        filter_eu_trades,
        merge_eu_trades,
        save_eu_results,
    )
    from insider_scanner.core.eu_scan import scrape_eu_trades_for_isin
    from insider_scanner.utils.config import load_eu_watchlist

    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    country = (args.country or "All").upper()

    # Resolve ISINs: explicit arg or watchlist
    if args.watchlist:
        isins = load_eu_watchlist()
        if not isins:
            print("EU watchlist is empty. Add ISINs to data/eu_watchlist.txt.")
            return
    elif args.isin:
        isins = [args.isin.upper()]
    else:
        print("Provide an ISIN or use --watchlist.")
        return

    all_trades = []
    for isin in isins:
        print(f"Scanning {isin}…")
        all_trades.extend(scrape_eu_trades_for_isin(isin, country, since, until))

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
        help="Scan all ISINs in data/eu_watchlist.txt",
    )
    p_eu.add_argument("--save", action="store_true")
    p_eu.set_defaults(func=cmd_eu_scan)

    return parser


def main() -> None:
    setup_logging()
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
