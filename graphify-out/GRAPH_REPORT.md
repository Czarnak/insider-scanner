# Graph Report - .  (2026-05-19)

## Corpus Check

- Large corpus: 2990 files · ~6,133,589 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary

- 1094 nodes · 1777 edges · 96 communities (65 shown, 31 thin omitted)
- Extraction: 71% EXTRACTED · 29% INFERRED · 0% AMBIGUOUS · INFERRED: 524 edges (avg confidence: 0.72)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)

- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Insider Components|Insider Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Scripts Components|Scripts Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Utils Components|Utils Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Utils Components|Utils Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Gui Components|Gui Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Insider Components|Insider Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Src Components|Src Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Tests Components|Tests Components]]
- [[_COMMUNITY_Core Components|Core Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Test Components|Test Components]]
- [[_COMMUNITY_Rationale Components|Rationale Components]]

## God Nodes (most connected - your core abstractions)

1. `CongressTrade` - 52 edges
2. `ScanTab` - 41 edges
3. `EuropeanInsiderTrade` - 39 edges
4. `EuropeanTab` - 38 edges
5. `CongressTab` - 36 edges
6. `filter_congress_trades()` - 26 edges
7. `EFDSession` - 25 edges
8. `InsiderTrade` - 21 edges
9. `MainWindow` - 21 edges
10. `PandasTableModel` - 20 edges

## Surprising Connections (you probably didn't know these)

- `TestResolveCikFromJson` --uses--> `InsiderTrade`  [INFERRED]
  tests/test_edgar.py → src/insider_scanner/core/models.py
- `TestRefreshFunctions` --uses--> `CongressTrade`  [INFERRED]
  tests/test_congress_house.py → src/insider_scanner/core/models.py
- `Legislator Data Updater` --semantically_similar_to--> `Congress Member List Manager`  [INFERRED] [semantically similar]
  scripts/update_congress.py → src/insider_scanner/core/senate.py
- `test_404_raises()` --calls--> `ensure_house_index()`  [INFERRED]
  tests/test_congress_house.py → src/insider_scanner/core/congress_house.py
- `test_download_and_cache()` --calls--> `fetch_ptr_pdf()`  [INFERRED]
  tests/test_congress_house.py → src/insider_scanner/core/congress_house.py

## Hyperedges (group relationships)

- **European Scan Workflow** — core_eu_scan_orchestrator, core_afm_scraper, core_amf_scraper, core_bafin_scraper, core_rns_scraper, core_eu_merger [EXTRACTED 1.00]
- **Congress Data Pipeline** — script_update_congress, core_congress_manager, core_house_scraper, core_senate_scraper [INFERRED 0.85]
- **GUI Tabs Group** — gui_scan_tab, gui_congress_tab, gui_european_tab [INFERRED 0.80]
- **Core Utilities** — utils_caching, utils_config, utils_http, utils_threading [INFERRED 0.80]
- **European Insider Trading Data Flow** — test_european_european_workflow_tests, test_eu_merger_eu_merger_logic_tests, test_eu_models_eu_trade_model_tests, test_eu_sources_eu_scraper_tests [EXTRACTED 0.90]
- **US Congress Trading Data Flow** — test_integration_congress_integration_tests, test_senate_congress_flagging_tests, test_update_congress_script_update_tests [EXTRACTED 0.85]
- **US Corporate Insider Flow** — test_edgar_sec_edgar_tests, test_secform4_secform4_scraper_tests, test_openinsider_openinsider_scraper_tests, test_merger_trade_merger_tests [EXTRACTED 0.90]

## Communities (96 total, 31 thin omitted)

### Community 0 - "Gui Components"

Cohesion: 0.06
Nodes (24): EuropeanTab, _on_scan_finished(),_on_scan_result(), European Insider Scan tab — GUI for UK, DE, FR, NL disclosures.  Provides a fu, Fetch the N most recent trades from each EU source globally (no ISIN filter)., Backward-compatible wrapper around the core EU dispatcher., European insider scan tab., _scrape_isin() (+16 more)

### Community 1 - "Tests Components"

Cohesion: 0.07
Nodes (25): _dedup_key(), filter_trades(), merge_trades(), Deduplicate and merge insider trades from multiple sources.  Merging strategy: t, Filter trades by various criteria.      Parameters     ----------     trades : l, Convert a list of InsiderTrade to a pandas DataFrame., Save scan results as CSV and JSON.      Returns the output directory., Generate a deduplication key for a trade. (+17 more)

### Community 2 - "Tests Components"

Cohesion: 0.04
Nodes (12): Tests for the Congress member update script., Judiciary (Other) + Armed Services (Defense) → only Defense., Sectors returned in priority order regardless of input order., Subcommittee IDs should resolve to parent committee name., TestCommitteeSectorMapping, TestDetermineSectors, TestEnrichWithCommittees, TestFetchCommitteeMembership (+4 more)

### Community 3 - "Tests Components"

Cohesion: 0.07
Nodes (21): _br_split(), _classify_trade(), _parse_date(), _parse_number(), parse_secform4_html(), Scrape insider trades from secform4.com., Parse insider trades from secform4.com HTML.      secform4.com uses compound tab, Parse date from various formats. (+13 more)

### Community 4 - "Tests Components"

Cohesion: 0.07
Nodes (20): _classify_trade(), _parse_date(), _parse_number(), parse_openinsider_html(), Scrape insider trades from openinsider.com., Scrape the latest insider trades across all tickers.      Parameters     -------, Parse insider trades from openinsider.com HTML.      Parameters     ----------, Scrape insider trades for a specific ticker from openinsider.com.      Parameter (+12 more)

### Community 5 - "Gui Components"

Cohesion: 0.10
Nodes (10): build_edgar_url_for_trade(), Generate an EDGAR search URL for a given trade (for verification)., _on_scan_done(),_on_scan_error(), Scan tab: ticker search, source selection, filters, results table, EDGAR links., Enable or disable all scan-triggering buttons., Signal the running watchlist scan to stop., Full scan workflow: enter ticker → select sources → scan → view → EDGAR. (+2 more)

### Community 6 - "Insider Components"

Cohesion: 0.07
Nodes (21): build_parser(), cmd_init_congress(), cmd_latest(), main(), _parse_date_arg(), CLI entry point for headless insider trade scanning., Initialize the default Congress member list., Parse a YYYY-MM-DD date string from CLI arguments. (+13 more)

### Community 7 - "Tests Components"

Cohesion: 0.11
Nodes (15): flag_congress_trades(), init_default_congress_file(), load_congress_members(),_normalize_name(), Congress member list management and trade flagging.  Maintains a local JSON file, Create the default congress members file if it doesn't exist., Load the Congress member list from disk.      Returns a list of dicts, each with, Save the Congress member list to disk. (+7 more)

### Community 8 - "Tests Components"

Cohesion: 0.11
Nodes (8): filter_congress_trades(), Filter CongressTrade records.      Parameters     ----------     trades : list o, Pelosi has both Finance and Technology sectors., Sector filter with no member_sectors dict has no effect., TestFilterCongressTrades, Test filtering across mixed House + Senate trades., Purchase + Technology sector + min $500K., TestFilterPipeline

### Community 9 - "Core Components"

Cohesion: 0.11
Nodes (20): _fetch_csv_export(), fetch_de_latest(), fetch_de_trades(),_map_csv_rows(), _map_instrument(), _map_trade_type(),_parse_filing_date(), _parse_result_table() (+12 more)

### Community 10 - "Core Components"

Cohesion: 0.12
Nodes (19): _download_pdf(), fetch_fr_latest(), fetch_fr_trades(), _item_to_trade(),_map_trade_type(), _parse_french_date(),_parse_iso_date(), _parse_pdf_text() (+11 more)

### Community 11 - "Core Components"

Cohesion: 0.13
Nodes (17): _determine_trade_type(), fetch_uk_latest(), fetch_uk_trades(),_parse_announcement(),_parse_price_volume(), _parse_trade_date(), UK insider trade scraper — Investegate (RNS/GNW announcements).  Data source:, Parse a single MAR Article 19 announcement page into a_RawRns object.      Th (+9 more)

### Community 12 - "Tests Components"

Cohesion: 0.15
Nodes (15): EFDSession, Search for Senate PTR filings and return parsed results.      Handles pagination, Manages an authenticated session with efdsearch.senate.gov.      Handles CSRF to, search_senate_filings(), Tests for Senate EFD financial disclosure scraper (congress_senate.py)., test_authenticate(), test_authenticate_no_csrf(), test_empty_results() (+7 more)

### Community 13 - "Core Components"

Cohesion: 0.14
Nodes (14): _find_header_row(),_index_txt_path(), _is_scanned_pdf(),_map_columns(), parse_ptr_pdf(),_parse_table_row(), House of Representatives financial disclosure index and PDF parsing.  Data comes, Detect if a PDF is scanned (image-based) rather than electronic.      Scanned PD (+6 more)

### Community 14 - "Tests Components"

Cohesion: 0.17
Nodes (17): ensure_house_index(), Ensure the House financial disclosure index for *year* is available.      Downlo, Re-download indexes for all specified years (force=True).      Parameters     --, Re-download only the current year's index., refresh_all_indexes(), refresh_current_year(), _make_sample_zip(), Tests for House financial disclosure scraper (congress_house.py). (+9 more)

### Community 15 - "Core Components"

Cohesion: 0.13
Nodes (14): _find_transaction_table(),_map_senate_columns(), _normalize_tx_type(),_parse_date(), parse_ptr_page(),_parse_senate_row(), Senate financial disclosure scraper via efdsearch.senate.gov.  The Senate's Elec, Extract transactions from a Senate PTR HTML page.      The page contains a table (+6 more)

### Community 16 - "Core Components"

Cohesion: 0.16
Nodes (15): _coalesce(),_dedup_key(), eu_trades_to_dataframe(), filter_eu_trades(), merge_eu_trades(), European insider trade deduplication, filtering, and export.  Mirrors the role, Merge trades from multiple country scrapers, deduplicating by key.      Strate, Filter a list of European insider trades by multiple criteria.      Parameters (+7 more)

### Community 17 - "Scripts Components"

Cohesion: 0.16
Nodes (17): determine_sectors(), enrich_with_committees(), fetch_committee_membership(), fetch_committees(), fetch_federal_legislators(), fetch_state_legislators(), main(), map_committee_to_sector() (+9 more)

### Community 18 - "Tests Components"

Cohesion: 0.20
Nodes (10): _house_trades(), Integration tests for the Congress scan pipeline.  These tests verify that all c, Test House + Senate trades merged into one list., Test DataFrame conversion with mixed data., Filter → DataFrame pipeline., Simulated House scraper output., Simulated Senate scraper output., _senate_trades() (+2 more)

### Community 19 - "Tests Components"

Cohesion: 0.15
Nodes (7): EuropeanInsiderTrade, Unified insider trade record from a European regulatory disclosure., Tests for CLI parsing and EU scan command wiring., TestBuildParser, TestCmdEuScan, TestParseDateArg, TestEuropeanInsiderTrade

### Community 20 - "Tests Components"

Cohesion: 0.18
Nodes (4): CongressTab, Congress trade scanner: select official → scan sources → filter → view., Signal the background scan to stop., TestCongressTab

### Community 21 - "Gui Components"

Cohesion: 0.13
Nodes (10): dataframe(), fg_color(), indicator_color(), PriceChangeCard, Reusable GUI widgets: pandas table model, dashboard cards., Card showing a price and 1-day % change with colored background., Generic card showing a title, large value, and optional meta text., Map a 0–100 Fear & Greed score to an RGBA background color. (+2 more)

### Community 22 - "Core Components"

Cohesion: 0.15
Nodes (17): CLI Scan Command, AFM (Netherlands) Scraper, AMF (France) Scraper, BaFin (Germany) Scraper, Congress Member List Manager, EU Trade Merger, EuropeanInsiderTrade Data Model, EU Scan Orchestrator (+9 more)

### Community 23 - "Gui Components"

Cohesion: 0.17
Nodes (7): _load_member_sectors(),_on_scan_done(), _on_scan_error(), Congress Scan tab: scan trades by Congress member with sector filtering., Toggle scan-related buttons., Load official_name → sector list mapping from congress_members.json.      Return, TestLoadMemberSectors

### Community 24 - "Tests Components"

Cohesion: 0.15
Nodes (9): congress_trades_to_dataframe(), Convert a list of CongressTrade to a pandas DataFrame.,_make_trade(), Tests for congress_tab helper functions (non-GUI)., Create a CongressTrade with sensible defaults, accepting overrides., All display columns should exist in CongressTrade.to_dict()., TestCongressTradesToDataframe, TestDisplayColumns (+1 more)

### Community 25 - "Tests Components"

Cohesion: 0.21
Nodes (5): CongressTrade, Financial disclosure trade record for a Congress member.      Unlike InsiderTrad, Tests for CongressTrade dataclass., TestCongressTradeBasic, TestCongressTradeSerialisation

### Community 26 - "Gui Components"

Cohesion: 0.21
Nodes (5): MainWindow, Main window with default OS style and tabbed interface., Insider Scanner main window., QMainWindow, TestMainWindow

### Community 27 - "Core Components"

Cohesion: 0.17
Nodes (9): create_efd_session(), _extract_ticker(), _rate_limit(), Search the EFD report data API.          Parameters         ----------         f, Fetch a page within the EFD session.          Returns the HTML text of the page., Create and authenticate an EFD session.      Returns     -------     EFDSession, Extract ticker from asset description., Enforce rate limiting between requests. (+1 more)

### Community 28 - "Tests Components"

Cohesion: 0.20
Nodes (5): normalize_position(), European insider trade data model.  Covers disclosures from UK (FCA/RNS), Germ, Normalise a raw position/role string to a standard English category.      Retu, Tests for European insider trade models and helpers., TestNormalizePosition

### Community 29 - "Gui Components"

Cohesion: 0.20
Nodes (7): Backward-compatible alias used by older tabs., Backward-compatible alias used by older tabs., Proxy adding sort/filter on top of PandasTableModel., SortableTableModel, QSortFilterProxyModel, GUI tests using pytest-qt for widget creation and basic interactions., TestSortableTableModel

### Community 30 - "Gui Components"

Cohesion: 0.21
Nodes (4): PandasTableModel, Qt table model backed by a pandas DataFrame., QAbstractTableModel, TestPandasTableModel

### Community 31 - "Gui Components"

Cohesion: 0.18
Nodes (5): _load_congress_names(), Reload congress_members.json and repopulate the dropdown., Load Congress member names from the JSON data file.      Returns a sorted list o, QWidget, TestLoadCongressNames

### Community 32 - "Tests Components"

Cohesion: 0.21
Nodes (6): Save Congress scan results as CSV and JSON.      Returns the output directory., save_congress_results(), TestSaveCongressResults, Test save → reload round-trip preserves data., Filter → save only filtered results., TestSaveReloadPipeline

### Community 33 - "Tests Components"

Cohesion: 0.23
Nodes (11): Resolve a ticker to CIK using SEC's company_tickers.json.      This is the prefe, resolve_cik_from_json(), Tests for SEC EDGAR CIK resolver (mocked HTTP)., resolve_cik() should use JSON primary, HTML fallback, and zero-pad., test_resolve_aapl(), test_resolve_case_insensitive(), test_resolve_network_error(), test_resolve_not_found() (+3 more)

### Community 34 - "Tests Components"

Cohesion: 0.29
Nodes (6): TestGetSetCached, get_cached(), Simple file-based cache with TTL expiry., Return cached content if it exists and hasn't expired, else None., Write content to cache with current timestamp., set_cached()

### Community 35 - "Tests Components"

Cohesion: 0.25
Nodes (6): Scrape Senate PTR filings and return CongressTrade records.      Parameters, scrape_senate_trades(), Test scrape_senate_trades with fully mocked session., Verify official_name gets split correctly for the search., PTR page fetch failures should be skipped gracefully., TestScrapeSentateTrades

### Community 36 - "Tests Components"

Cohesion: 0.25
Nodes (6): Tests for European trade merge/filter/export helpers., TestEuTradesToDataFrame, TestFilterEuTrades, TestMergeEuTrades, TestSaveEuResults, _trade()

### Community 37 - "Tests Components"

Cohesion: 0.20
Nodes (4): DummyApp, DummyWindow, Tests for the GUI application entrypoint., test_main_initialises_app()

### Community 38 - "Core Components"

Cohesion: 0.22
Nodes (8): _extract_ticker(), _normalize_tx_type(), Try to extract a ticker symbol from an asset description.      Looks for pattern, Normalize transaction type string to Purchase/Sale/Exchange/Other., Scrape House PTR filings and return parsed CongressTrade records.      Parameter, scrape_house_trades(), test_full_pipeline(), TestScrapeHouseTrades

### Community 39 - "Tests Components"

Cohesion: 0.31
Nodes (4): Search the House index for matching filings.      Parameters     ----------, search_filings(), name=None should return all PTR filings., TestSearchFilings

### Community 41 - "Core Components"

Cohesion: 0.27
Nodes (9): _normalise_trade_type(),_parse_nl_date(), _parse_record(), Dutch insider trade scraper — AFM Directors' Dealings register.  Fetches insid, Fetch Directors' Dealings for an ISIN from the AFM register.      Parameters, Parse date strings from AFM API responses., Map AFM transaction type strings to Buy / Sell / Other., Convert a single AFM API result record to an EuropeanInsiderTrade. (+1 more)

### Community 42 - "Core Components"

Cohesion: 0.22
Nodes (8): Shared European scan orchestration used by GUI and CLI., Fetch the N most recent insider trades from each selected EU source.      Unli, Filter out false-match records and assign the query ISIN to blank ones.      S, Dispatch scraping for one ISIN across the selected European sources., scrape_eu_latest(), scrape_eu_trades_for_isin(), _verify_isin(), TestEuropeanScan

### Community 43 - "Tests Components"

Cohesion: 0.20
Nodes (6): Test that the congress_tab scan flow calls scrapers correctly., Simulate a House-only scan through the pipeline., Simulate a Senate-only scan through the pipeline., Simulate the full House + Senate scan → filter → save flow., Simulate 'All' officials scan (official_name=None)., TestScraperIntegration

### Community 44 - "Tests Components"

Cohesion: 0.22
Nodes (8): Resolve a ticker symbol to a SEC CIK number.      Uses the SEC company_tickers.j, resolve_cik(), cmd_resolve_cik(), Resolve a ticker to SEC CIK., test_both_fail(), test_json_miss_html_fallback(), test_json_primary_success(), TestEdgarLive

### Community 45 - "Tests Components"

Cohesion: 0.28
Nodes (6): _index_xml_path(), parse_house_index(), Parse the House financial disclosure XML index for a given year.      Returns a, Path where the extracted XML index lives., BOM-prefixed XML (as found in real data) should parse correctly., TestParseHouseIndex

### Community 46 - "Tests Components"

Cohesion: 0.36
Nodes (3): _parse_date_flexible(), Parse a date from various formats found in PDFs., TestParseDateFlexible

### Community 47 - "Core Components"

Cohesion: 0.25
Nodes (6): fetch_filings_page(), get_filing_url(), SEC EDGAR CIK resolver and Form 4 filing lookup.  Compliance: Uses proper User-A, Return the EDGAR filing listing URL for a given CIK., Fetch the EDGAR Form 4 filings listing page for a CIK.      Parameters     -----, TestFilingUrl

### Community 48 - "Core Components"

Cohesion: 0.25
Nodes (7): fetch_ptr_pdf(), _pdf_cache_path(), Download a PTR PDF and return its raw bytes.      Caches locally under data/hous, Path where a cached PTR PDF lives., Second call should use cache, not HTTP., test_download_and_cache(), TestFetchPtrPdf

### Community 49 - "Tests Components"

Cohesion: 0.39
Nodes (3): _determine_years(), Determine which year indexes need to be fetched for the date range., TestDetermineYears

### Community 50 - "Tests Components"

Cohesion: 0.39
Nodes (3): parse_search_results(), Parse the JSON response from the EFD search API.      Each result row is: [first, TestParseSearchResults

### Community 51 - "Tests Components"

Cohesion: 0.39
Nodes (3): Split an official name into (first_name, last_name) for search.      Handles for, _split_name(), TestSplitName

### Community 52 - "Tests Components"

Cohesion: 0.29
Nodes (4): Tests for European source-specific parsing helpers., _sample_trade(), TestAfmParsing, TestEuScanDispatch

### Community 53 - "Tests Components"

Cohesion: 0.32
Nodes (4): Tests for European models, orchestration, and GUI behavior., TestEuropeanMerger, TestEuropeanModels, _trade()

### Community 54 - "Core Components"

Cohesion: 0.32
Nodes (5): parse_cik_from_html(), Extract CIK from EDGAR company search result page., Resolve CIK by scraping the EDGAR company browse page (fallback).,_resolve_cik_html(), TestParseCik

### Community 55 - "Utils Components"

Cohesion: 0.29
Nodes (7): fetch_company_info(), Fetch company submission info from EDGAR.      Returns a dict with keys like 'na, fetch_url(),_rate_limit(), Rate-limited HTTP client with SEC EDGAR compliance and optional caching., Block until enough time has passed since the last request., Fetch a URL with optional caching and rate limiting.      Parameters     -------

### Community 56 - "Gui Components"

Cohesion: 0.62
Nodes (7): GUI Background Task Pattern, CongressTab, EuropeanTab, MainWindow, ScanTab, Reusable GUI Widgets, Background Worker Threading

### Community 57 - "Tests Components"

Cohesion: 0.43
Nodes (3): _normalize_owner(), Normalize owner code to Self/Spouse/Dependent Child/Joint., TestNormalizeOwner

### Community 58 - "Tests Components"

Cohesion: 0.33
Nodes (4): Tests for the file-based caching system., TestClearCache, clear_cache(), Remove all cached files. Returns number of files removed.

### Community 60 - "Tests Components"

Cohesion: 0.47
Nodes (3): TestCacheKey, cache_key(), Create a filesystem-safe cache key from a URL.

### Community 64 - "Tests Components"

Cohesion: 0.40
Nodes (3): Mock pdfplumber to simulate an electronic PTR PDF., Scanned PDFs (very little text) should return empty., TestParsePtrPdf

### Community 65 - "Utils Components"

Cohesion: 0.50
Nodes (4): SEC EDGAR Compliance, File-based Caching, Configuration, Rate-limited HTTP Client

### Community 66 - "Core Components"

Cohesion: 0.67
Nodes (3): House of Representatives Scraper, CongressTrade Data Model, Senate Disclosures Scraper

### Community 67 - "Test Components"

Cohesion: 0.67
Nodes (3): Congress Data Pipeline Rationale, Congress Pipeline Integration Tests, Congress Member Flagging Tests

## Knowledge Gaps

- **29 isolated node(s):** `TestScrapeOpeninsider`, `TestFetchFederalLegislators`, `TestFetchStateLegislators`, `TestFetchCommittees`, `TestFetchCommitteeMembership` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **31 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions

_Questions this graph is uniquely positioned to answer:*

- **Why does `CongressTrade` connect `Tests Components` to `Tests Components`, `Tests Components`, `Tests Components`, `Core Components`, `Tests Components`, `Core Components`, `Tests Components`, `Gui Components`, `Tests Components`, `Gui Components`, `Tests Components`, `Tests Components`, `Core Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Core Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`?**
  *High betweenness centrality (0.266) - this node is a cross-community bridge.*
- **Why does `EuropeanInsiderTrade` connect `Tests Components` to `Gui Components`, `Tests Components`, `Gui Components`, `Core Components`, `Core Components`, `Core Components`, `Core Components`, `Core Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Gui Components`, `Tests Components`, `Gui Components`, `Gui Components`?**
  *High betweenness centrality (0.176) - this node is a cross-community bridge.*
- **Why does `InsiderTrade` connect `Tests Components` to `Tests Components`, `Tests Components`, `Tests Components`, `Tests Components`, `Core Components`, `Core Components`?**
  *High betweenness centrality (0.175) - this node is a cross-community bridge.*
- **Are the 49 inferred relationships involving `CongressTrade` (e.g. with `EFDSession` and `TestExtractTicker`) actually correct?**
  *`CongressTrade` has 49 INFERRED edges - model-reasoned connections that need verification.*
- **Are the 22 inferred relationships involving `ScanTab` (e.g. with `MainWindow` and `SortableTableModel`) actually correct?**
  *`ScanTab` has 22 INFERRED edges - model-reasoned connections that need verification.*
- **Are the 36 inferred relationships involving `EuropeanInsiderTrade` (e.g. with `_RawRns` and `EuropeanTab`) actually correct?**
  *`EuropeanInsiderTrade` has 36 INFERRED edges - model-reasoned connections that need verification.*
- **Are the 20 inferred relationships involving `EuropeanTab` (e.g. with `EuropeanInsiderTrade` and `SortableTableModel`) actually correct?**
  *`EuropeanTab` has 20 INFERRED edges - model-reasoned connections that need verification.*
