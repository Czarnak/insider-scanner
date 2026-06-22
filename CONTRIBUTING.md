# Contributing to Insider Scanner

First off, thank you for considering contributing to Insider Scanner! It's people like you that make this tool great.

## Setup for Development

```bash
git clone <repo-url>
cd insider-scanner
pip install -e ".[dev]"
```

## Adding Sources

To add a new scraping source:

1. Create `src/insider_scanner/core/newsource.py` with a `scrape_ticker(ticker) -> list[InsiderTrade]` function
2. Have the parser return `InsiderTrade` records with `source="newsource"`
3. Add it to the merger pipeline in `scan_tab.py` and `cli.py`
4. Write mocked tests in `tests/test_newsource.py`

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

## CI/CD

GitHub Actions runs on push/PR:

- **Test matrix**: Python 3.11 + 3.12 + 3.13 + 3.14 on Ubuntu + Windows
- **Offline tests only**: Live tests excluded via `-m "not live"`
- **GUI tests**: Run under `xvfb-run` on Linux for headless display; skipped on Windows
- **Lint**: `ruff check .`
- **Format**: `ruff format --check .`
- **Coverage**: Uploaded as artifact for Python 3.12 Ubuntu

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## SEC EDGAR integration notes

All EDGAR HTTP calls go through `src/insider_scanner/utils/http.py`, which enforces
the ≤ 10 requests/second rate limit required by SEC policy. The `SEC_USER_AGENT`
environment variable must carry a real name and contact email
(e.g. `MyApp/1.0 (you@example.com)`); the placeholder default is intentionally
rejected at startup.

### Bulk backfill command

The `sec-backfill` CLI command ingests SEC ownership filings from a locally
downloaded bulk archive. Key implementation details for contributors:

- Entry point: `src/insider_scanner/cli.py` — the `sec-backfill` subcommand.
- The `--confirm-full-backfill` flag is a required acknowledgement; exit code `2`
  if it is absent.
- The `--zip PATH` argument is required; `--cik CIK` (repeatable) limits
  processing to specific CIKs; `--no-cleanup` keeps the extraction cache.
- Resumability is checkpoint-based — already-processed CIKs are skipped on
  re-runs; do not break this invariant when modifying the backfill logic.
- The backfill reuses the same downloader and parser as `sec-daily`; fixes to
  the shared HTTP/parser layer apply to both.
- The GUI shows the archive URL and the exact CLI command in an info panel but
  does **not** invoke the backfill; keep backfill execution strictly CLI-only.
- When writing tests for backfill logic, mock the ZIP extraction and the EDGAR
  HTTP layer (use the `responses` library) so tests remain offline.

The bulk archive URL for manual testing reference:
`https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip`
(multi-GB; do not fetch it in automated tests).
