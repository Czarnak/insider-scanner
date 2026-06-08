# Insider Scanner

Scan insider trades from **secform4.com**, **openinsider.com**, **SEC EDGAR**, and European regulators (FCA, BaFin, AMF, AFM). Includes congressional financial disclosure scanning (House and Senate), multi-source deduplication, committee-based sector filtering, and a desktop GUI with EDGAR filing links plus a European scan workspace.

---

## Setup

```bash
git clone <repo-url>
cd insider-scanner
pip install -e ".[dev]"
```

### Requirements

Python 3.11+. Dependencies include `requests`, `beautifulsoup4`, `lxml`,
`pandas`, `PySide6`, `pyyaml`, `pdfplumber`, `numpy`, `platformdirs`, and
SQLAlchemy.

---

## Usage

### GUI

```bash
insider-scanner
# or
python -m insider_scanner.main
```

The GUI tabs cover the core use cases:

#### Insider Scan tab
- Search a ticker and run both secform4.com and openinsider.com scrapers in one click.
- Fetch the latest trades (configurable count) and run watchlist scans backed by the user data directory's `tickers_watchlist.txt`.
- Toggle sources, specify a date range, trade type, minimum value, or Congress-only filter.
- View sortable tables that display filing/trade dates, highlight congressional filings, show EDGAR links, and let you export CSV/JSON.
- Cancel long-running scans and resolve any ticker to its SEC CIK + filing page.

#### Congress Scan tab
- Pick a legislator (House/Senate dropdown) or the whole committee list, select sources (House/Senate), and preview results in a threaded worker with progress + cancel.
- Use filters such as trade type, sector, and minimum value, then double-click any row to open the original PDF/PTR.
- Save filtered results to CSV/JSON, with exports reflecting the current filters.

#### European Insiders tab
- Choose All/UK/DE/FR/NL, type an ISIN, or scan the user data directory's `eu_watchlist.txt`.
- Enable optional date bounds, filter by trade type and minimum value, and watch the progress bar while each ISIN is processed.
- Results are sortable, show normalized positions/currency, provide detail text on double-click, and allow opening the regulator source URL.
- Save filtered results to CSV/JSON (filename reflects the ISIN + country) or clear filters to adjust the view.

### CLI

```bash
# Scan a specific ticker
insider-scanner-cli scan AAPL
insider-scanner-cli scan AAPL --type Buy --min-value 1000000 --save

# Scan with date range
insider-scanner-cli scan AAPL --since 2025-01-01 --until 2025-06-30

# Fetch latest insider trades
insider-scanner-cli latest --count 50 --save
insider-scanner-cli latest --since 2025-06-01

# Resolve SEC CIK
insider-scanner-cli cik AAPL

# Initialize default Congress member list
insider-scanner-cli init-congress

# Congress-only filter
insider-scanner-cli scan AAPL --congress-only

# Import legacy JSON exports into SQLite
insider-scanner-cli import-legacy ./old-exports
insider-scanner-cli import-legacy ./large.json --max-file-size-mib 100
```

### European scan CLI

```bash
# Scan a single ISIN or run the built-in watchlist
insider-scanner-cli eu-scan GB0002875804
insider-scanner-cli eu-scan --watchlist --country UK --min-value 50000 --save
```

Pass `--country` to restrict to `UK`/`DE`/`FR`/`NL` (default: `All`), `--type` to filter `Buy`/`Sell` trades, `--min-value` for the total reported value, and `--since`/`--until` for date bounds. Use `--watchlist` to scan every configured ISIN, and `--save` to persist the filtered CSV/JSON bundle.

### Local persistence and paths

Parsed US, Congress, and European trades are stored in a local SQLite database.
Writable data, cache, watchlist, House disclosure, and export paths are resolved
with `platformdirs`; the application does not write runtime data into the source
checkout. Print the paths selected for the current OS and user with:

```bash
python -c "from insider_scanner.utils.config import DEFAULT_PATHS; print(DEFAULT_PATHS)"
```

The SQLite database stores normalized trade records, successful source/date
coverage, and latest-refresh state. HTTP caches remain transient files under the
platform cache directory and can be removed without deleting parsed trades.

For bounded scans, the service queries SQLite first, calculates uncovered
intervals independently for each source, fetches only those gaps, and then
returns the merged local result. Failed or cancelled intervals are not marked as
covered. AMF search bounds are publication dates rather than transaction dates,
so bounded French scans conservatively persist and filter returned trades but do
not mark the requested trade-date interval as covered. Repeating an AMF bounded
scan therefore rechecks the regulator. RNS, BaFin, and AFM bounded scans retain
normal coverage caching. Latest scans reuse a successful refresh for one hour by default;
different requested counts have independent refresh state. European latest mode
uses only sources with a latest endpoint. AFM remains available for bounded
Netherlands scans but is excluded from latest refreshes.

`import-legacy PATH` accepts a JSON file or recursively scans a directory for
JSON exports. Imports are validated, idempotent, do not modify source files, and
do not alter scan coverage or refresh state. Each file is limited to 50 MiB by
default; use `--max-file-size-mib` to set a different positive limit. Any
malformed, oversized, or invalid record produces a nonzero exit code.

Database initialization and migration failures stop GUI or CLI startup rather
than continuing with partial state. User-facing errors remain concise; detailed
diagnostics are sent through application logging.

#### Database migration recovery

The database is `insider_scanner.sqlite3` under the platform user data directory
reported by `DEFAULT_PATHS.database_file`. Typical locations are
`%LOCALAPPDATA%\Insider Scanner\Insider Scanner\` on Windows,
`~/Library/Application Support/Insider Scanner/` on macOS, and
`~/.local/share/Insider Scanner/` on Linux.

If startup reports a database or migration failure:

1. Close every Insider Scanner GUI and CLI process.
2. Back up `insider_scanner.sqlite3` before making any recovery change.
3. Do not edit `schema_version`, tables, indexes, or other schema objects manually.
4. Restore a known-good database backup, or rename the corrupt database and
   restart the application to build a fresh database.
5. Optionally repopulate the fresh database with
   `insider-scanner-cli import-legacy PATH` using validated legacy JSON exports.

---

## Architecture

```
src/insider_scanner/
├── core/
│   ├── models.py        # InsiderTrade + CongressTrade dataclasses
│   ├── secform4.py      # secform4.com scraper (compound-column parser, direct filing links)
│   ├── openinsider.py   # openinsider.com HTML parser + scraper
│   ├── edgar.py         # SEC EDGAR CIK resolver (JSON primary + HTML fallback) + filing URLs
│   ├── senate.py        # Congress member list + trade flagging
│   ├── congress_house.py # House financial disclosures (ZIP index + PTR PDF parsing)
│   ├── congress_senate.py # Senate EFD scraper (session + search + PTR page parsing)
│   ├── merger.py        # Multi-source dedup, filtering, export
│   ├── afm.py           # Dutch AFM API client
│   ├── amf.py           # French AMF BDIF API client
│   ├── bafin.py         # German BaFin download+CSV parser
│   ├── eu_models.py     # European trade dataclass + helpers
│   ├── eu_merger.py     # European dedup/filter/export helpers
│   ├── eu_scan.py       # Dispatcher that runs the selected European scrapers
│   └── rns_investegate.py # UK RNS announcements via Investegate
├── gui/
│   ├── main_window.py   # Main window (default OS style + tab management)
│   ├── scan_tab.py      # Insider scan workflow: search, filters, table, EDGAR links
│   ├── congress_tab.py  # Congress tab with sector filtering and save/export helpers
│   ├── european_tab.py  # European tab with ISIN/watchlist scans and detail panel
│   └── widgets.py       # Pandas table model, sortable proxy, table helpers
├── persistence/
│   ├── schema.py        # SQLAlchemy Core table definitions
│   ├── bootstrap.py     # Versioned SQLite initialization and migrations
│   ├── repositories.py  # Immutable trade upserts and queries
│   ├── coverage.py      # Source-aware covered intervals and gap calculation
│   └── refresh.py       # Latest-scan freshness state
├── resources/
│   └── seeds/           # Packaged default watchlists and Congress members
├── services/
│   ├── application.py   # Shared GUI/CLI service composition
│   ├── us.py            # DB-first US scan orchestration
│   ├── congress.py      # DB-first Congress scan orchestration
│   ├── european.py      # DB-first European scan orchestration
│   └── importer.py      # Validated legacy JSON import
├── utils/
│   ├── config.py        # Paths, SEC/User-Agent constants, watchlists
│   ├── logging.py       # Logging setup
│   ├── caching.py       # File cache with TTL expiry
│   ├── http.py          # Rate-limited HTTP helper
│   └── threading.py     # Worker/Signal helpers for GUI
├── main.py              # GUI entry point
└── cli.py               # CLI entry point (scan, latest, EU, import)

scripts/
└── update_congress.py   # Fetch current federal + state legislators
```

### Data Flow — Insider Trades

1. **Resolve**: `edgar.py` resolves ticker → CIK via SEC `company_tickers.json` (cached 24h, HTML fallback)
2. **Scrape**: `secform4.py` fetches CIK-based pages with compound-column parsing (date+type, name+title split by `<br>`); `openinsider.py` fetches ticker-based pages; both produce `InsiderTrade` records
3. **Persist**: parsed records are upserted into SQLite; successful source/date intervals are recorded separately
4. **Cache**: raw HTTP responses are cached independently with configurable TTL (default 1h)
5. **Merge**: `merger.py` deduplicates trades across sources (matching by ticker + name + date + share count)
6. **Flag**: `senate.py` checks insider names against the Congress member list (fuzzy matching)
7. **Verify**: secform4 trades include direct SEC filing links; others get generated EDGAR search URLs
8. **Export**: results are saved as CSV + JSON under the platform user data directory

### Data Flow — Congress (House)

1. **Index**: `congress_house.py` downloads yearly ZIP archives from `disclosures-clerk.house.gov` containing XML indexes of all financial disclosure filings. Past years are cached permanently; current year can be refreshed on demand.
2. **Search**: XML index is parsed to find PTR (Periodic Transaction Report) filings matching the selected official and date range. Multi-year ranges download multiple indexes as needed.
3. **Fetch**: Individual PTR PDFs are downloaded and cached under `house_disclosures/{year}/pdfs/` in the platform user data directory.
4. **Parse**: `pdfplumber` extracts transaction tables from electronically-filed PDFs. Scanned/handwritten PDFs are detected and skipped.
5. **Convert**: Raw table rows are converted to `CongressTrade` records with parsed tickers (from asset descriptions), normalized amount ranges, owner codes, and transaction types.

### Data Flow — Congress (Senate)

1. **Session**: `congress_senate.py` establishes an authenticated session with `efdsearch.senate.gov` by accepting the prohibition agreement and obtaining a CSRF token.
2. **Search**: POST to the EFD JSON API with senator name, report type (PTR), and date range. Results include links to individual PTR pages. Paper filings (scanned PDFs) are automatically filtered out.
3. **Parse**: Each electronic PTR page contains an HTML table with columns for transaction date, owner, ticker, asset name, type, amount range, and comment. These are parsed via BeautifulSoup.
4. **Convert**: Transactions are converted to `CongressTrade` records. Tickers are read directly from the "Ticker" column when available; when the ticker is "--", it's extracted from the asset description (e.g. "Vanguard ETF (BND)" → BND).

### Data Flow — European Insider Trades (UK / DE / FR / NL)

1. **Search**: `rns_investegate` queries Investegate for UK Director/PDMR announcements; `bafin`, `amf`, and `afm` POST/GET the regulators' portals for Germany, France, and the Netherlands respectively, honoring optional date bounds and ISIN filters.
2. **Parse**: Each scraper normalizes the (possibly localized) position text, currency formatting, and trade/filing dates before emitting `EuropeanInsiderTrade` records.
3. **Merge**: `eu_scan.scrape_eu_trades_for_isin` runs the requested sources, `eu_merger.merge_eu_trades` deduplicates across regulators, and `eu_merger.filter_eu_trades` applies the GUI/CLI filters (country, trade type, min value, date range).
4. **Save**: The European tab (and `eu-scan`) use `eu_merger.save_eu_results` to output labeled CSV/JSON bundles after the filters are applied.

### Congress Tab — GUI Integration

The **Congress Scan** tab provides a full GUI workflow for scanning congressional financial disclosures:

- **Official selection**: searchable dropdown populated from `congress_members.json`, with an "All" option
- **Source checkboxes**: independently toggle House and Senate scrapers
- **Date range**: optional filing date filter
- **Filters**: trade type (Purchase/Sale/Exchange), minimum dollar amount, and committee-based sector filtering
- **Background scanning**: threaded execution with progress bar and cancellable stop button
- **Results table**: sortable columns for filing date, trade date, official, chamber, ticker, asset, type, owner, amount range, and source
- **Detail panel**: double-click a row to see full details including official's committee sectors
- **Open Filing**: launches the original disclosure page (House PDF or Senate PTR) in browser
- **Save**: exports filtered results to CSV + JSON

### SEC EDGAR Compliance

All EDGAR requests use a proper `User-Agent` header and are rate-limited to 10 requests/second as required by SEC policy. The User-Agent is configurable via the `SEC_USER_AGENT` environment variable.

---

## Runtime Files

| Location within runtime paths | Description |
|------|-------------|
| `insider_scanner.sqlite3` | Parsed trades, coverage intervals, refresh state, and schema version |
| `congress_members.json` | Editable Congress member list with committee assignments and sector mappings |
| `tickers_watchlist.txt` | Editable ticker watchlist |
| `eu_watchlist.txt` | Editable European ISIN watchlist |
| `house_disclosures/` | Cached House disclosure indexes and PDFs |
| `exports/scans/` | Generated CSV/JSON scan exports |
| platform cache directory | Transient EDGAR and scraper HTTP caches |

Packaged defaults are copied into the user data directory only when a file is
missing; existing user edits are never overwritten. `eu_watchlist.txt` stores
one 12-character ISIN per line (comments start with `#`).

The Congress member list is populated by `scripts/update_congress.py` and includes committee assignments and sector mappings derived from the [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) project.

### Congress Data Model

Congress financial disclosures differ from standard insider trades. Instead of exact transaction values, they report dollar ranges (e.g. "$1,001 – $15,000"). The `CongressTrade` dataclass in `models.py` handles this with `amount_range` (original string), `amount_low` and `amount_high` (parsed floats), plus fields for `owner` (Self/Spouse/Dependent Child/Joint), `asset_description`, and `comment`.

### Committee → Sector Mapping

Each federal legislator is assigned one or more sectors based on their committee assignments. Committees are mapped to sectors via keyword matching (e.g. "Armed Services" → Defense, "Financial Services" → Finance). The available sectors are: Defense, Energy, Finance, Technology, Healthcare, Industrials, and Other. The `sector` field is a list — for example, a member serving on both Armed Services and Financial Services is tagged as `["Defense", "Finance"]`. "Other" is only included when no higher-priority sector applies.

**Limitation**: Family member financial disclosures (spouses, children) are not publicly machine-readable and would require paid data services. This is a known limitation documented here.

---

## Scripts

Standalone utility scripts live in `scripts/`.

### `update_congress.py`

Fetches the current list of federal and (optionally) state legislators, enriches them with committee assignments and sector mappings, and writes them to the configured `congress_members.json` in the platform user data directory.

```bash
# Federal only with committee enrichment (no API key needed)
python scripts/update_congress.py

# Federal + state legislators (requires free Open States API key)
OPENSTATES_API_KEY=your_key python scripts/update_congress.py --include-state

# Skip committee enrichment
python scripts/update_congress.py --no-committees

# Preview without saving
python scripts/update_congress.py --dry-run

# Custom output path
python scripts/update_congress.py --output /path/to/members.json
```

Federal data and committee assignments come from the [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) project (public domain, community-maintained YAML). State data uses the [Open States API](https://v3.openstates.org) (free key required).

---

## Tests

```bash
# Run all offline tests (default)
python -m pytest -m "not live" -v

# Run only live integration tests (requires internet)
python -m pytest -m live -v

# Run everything
python -m pytest -v

# With coverage
python -m pytest -m "not live" --cov=insider_scanner --cov-report=term-missing -v

# Static checks
python -m ruff check src tests
python -m ruff format --check src tests

# Verify the lock file
uv lock --check
```

Tests are split into two categories:

- **Offline (mocked)**: Use the `responses` library to mock HTTP calls. No internet needed. Run by default in CI.
- **Live integration**: Hit real websites. Marked with `@pytest.mark.live`. Excluded from CI. Run manually with `-m live`.

### Test modules

| Module | Tests | Description |
|--------|------:|-------------|
| `test_models.py` | 16 | InsiderTrade + CongressTrade dataclasses, amount range parsing |
| `test_secform4.py` | 19 | secform4.com compound-column HTML parser |
| `test_openinsider.py` | 13 | openinsider.com scraper |
| `test_edgar.py` | 14 | CIK resolution (JSON + HTML fallback), EDGAR URL builder |
| `test_senate.py` | 14 | Congress member flagging |
| `test_merger.py` | 19 | Deduplication, filtering, export |
| `test_caching.py` | 10 | File cache with TTL |
| `test_config.py` | 9 | Config paths, watchlist loading |
| `test_update_congress.py` | 34 | Committee enrichment, sector mapping |
| `test_congress_house.py` | 52 | House ZIP index, XML parsing, PDF extraction pipeline |
| `test_congress_senate.py` | 36 | Senate EFD session, search, PTR page parsing |
| `test_congress_tab.py` | 23 | Congress tab functions: filter, sector, save, dataframe |
| `test_integration.py` | 22 | End-to-end pipeline: scrapers → filter → save → reload |
| `test_eu_models.py` | 5 | EuropeanInsiderTrade dataclass, normalize_position |
| `test_eu_merger.py` | 4 | EU deduplication, filtering, dataframe export |
| `test_eu_sources.py` | 24 | AFM/AMF/BaFin/RNS parsing helpers and dispatcher |
| `test_european.py` | 8 | European tab GUI, scan dispatch, filter/save workflow |
| `test_gui.py` | 30 | Widget creation, controls, interactions (requires display) |
| `test_cli.py` | 6 | CLI entry point commands |
| `test_threading.py` | 2 | Worker/Signal threading helpers |
| `test_main_entrypoint.py` | 1 | GUI entry point smoke test |
| `test_live.py` | 6 | Live website tests (deselected in CI) |

---

## CI/CD

GitHub Actions runs on push/PR:

- **Test matrix**: Python 3.11 + 3.12 + 3.13 + 3.14 on Ubuntu + Windows
- **Offline tests only**: Live tests excluded via `-m "not live"`
- **GUI tests**: Run under `xvfb-run` on Linux for headless display; skipped on Windows
- **Lint**: `ruff check .`
- **Format**: `ruff format --check .`
- **Coverage**: Uploaded as artifact for Python 3.12 Ubuntu

---

## Adding Sources

To add a new scraping source:

1. Create `src/insider_scanner/core/newsource.py` with a `scrape_ticker(ticker) -> list[InsiderTrade]` function
2. Have the parser return `InsiderTrade` records with `source="newsource"`
3. Add it to the merger pipeline in `scan_tab.py` and `cli.py`
4. Write mocked tests in `tests/test_newsource.py`

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## License

MIT

---

*Created with Claude AI*
