# insider-scan — Deep Codebase Review

*Date: 2026-11-14 · Commit: `ec37237` (master) · Scope: everything except European scraping (`afm`, `amf`, `bafin`, `rns_investegate`, `eu_*`, `services/european.py`, `gui/european_tab.py`).*

## How this review was done

- Ten reviewers ran in parallel, each reading one subsystem in full:
  - SEC bulk-backfill performance
  - SEC fetch/IO
  - SEC XML parsing and trade mapping
  - US services and merging
  - persistence
  - Congress and prices
  - GUI and threading
  - CLI, config and CI
  - cross-cutting architecture
  - the test suite
- An adversarial verifier then re-checked every finding against the code and tried to refute it. It also added issues the reviewer had missed.
- Results: **287 verified findings**, merged to **~240 distinct items** (6 critical, 59 high, 94 medium, 128 low before merging). Only 2 findings were refuted. Verdicts: ~70% *confirmed* verbatim, ~20% *partially* (real, but details or severity corrected), ~10% found by the verifiers.
- Performance numbers come from benchmarks run on this machine with synthetic data, against a copy of the real schema and code paths. The scripts are in the session scratchpad.
- The test suite was run: **2032 passed, 5 skipped, 8 live tests deselected, in 107 s** (Python 3.13, `QT_QPA_PLATFORM=offscreen`).

The rest of this page is curated. The **Appendix** lists every finding with evidence location, description, suggested fix and verifier notes.

---

## 1. The SEC full-history backfill: why it never gets halfway

The per-filing path is:

```
submissions.zip member → json.loads → filter Forms 3/4/5 → HTTP GET full .txt submission (one per filing, sequential)
  → write to 24 h disk cache + fsync → XML decode/guard/parse (twice) → map to trades
  → SQLite upsert (own transaction, new connection) → rewrite the whole checkpoint file
```

Several costs compound. In order of impact:

| # | Cause | Where | Measured cost |
| --- | --- | --- | --- |
| 1 | **Checkpoint is O(N²).** After every filing, the entire set of completed accessions is `sorted()`, pretty-printed with `indent=2`, written to a temp file, `fsync`ed and `os.replace`d. | `services/sec_backfill.py:158-160, 199-210`, `persistence/json_state.py:29-40` | 20 ms per save at 10k done, 169 ms at 100k, 1.2 s at 500k, 2.5 s at 1M. Total ≈ 1.25e-6·N² s: **~3.5 h of checkpointing alone to reach 100k filings, ~87 h to reach 500k.** This alone explains "hours and not even half". |
| 2 | **One HTTP request per filing, strictly sequential**, capped by the fair-access limiter (8 req/s default). | `sec_backfill.py:150` → `sec_downloads.process_filing_row` → `sec_downloader.fetch_filing` | Theoretical floor at 8 req/s ≈ 28.8k filings/h. The archive holds roughly 5–7M XML-era ownership accessions, so **about 9 days minimum, even with everything else fixed.** |
| 3 | **No HTTP keep-alive**: `requests.get()` builds a throwaway Session, so every request does a full TCP+TLS handshake. | `core/sec_client.py:84` (also `utils/http.py:85`, `congress_house.py`) | 30–100 ms extra per filing. |
| 4 | **One SQLite transaction per filing over a new connection** (`NullPool`), a SELECT before every row, a SQLAlchemy statement rebuilt per row, and the default rollback journal with `synchronous=FULL`. | `persistence/engine.py:46,61`, `repositories.py:75-110` | ~30–38 ms per filing. WAL + `synchronous=NORMAL` alone: 29.8 → 10.7 ms. Batched `executemany`: ~59 µs per row (~17× faster). |
| 5 | **Every fetched filing is written to a 24 h cache with fsync**, which backfill never reads back. The cache is only purged after a fully completed run, so it grows without limit. | `services/sec_downloads.py:282`, `sec_downloader.py:298` | ~12 ms fsync + ~3.5 ms of repeated `Path.resolve()` per filing, plus disk growth. |
| 6 | **The same accession is fetched once per related CIK** (issuer + each reporting owner). Dedup is by archive path, not accession. | `core/sec_bulk.py:98`, `core/sec_index.py:53` | ~2× the work for most Form 4s. |
| 7 | **Resume re-decompresses and `json.loads` every ZIP member** (~900k CIK files) before it reaches new work. It also skips completed accessions one at a time. | `core/sec_bulk.py:186-201`, `sec_backfill.py:129` | Minutes to tens of minutes on every restart. |
| 8 | XML is decoded, guarded and parsed twice per filing; the pure-Python `guard_xml_tree` walk accounts for ~40% of parse CPU. | `sec_ownership_document.py`, `_sec_xml.py` | ~1 ms per filing. Real but minor. |

Throughput now: **~10–14k filings/h at the start, ~7k/h at 100k done, ~2.5k/h at 500k done**, and still falling.

### Correctness problems on the same path

- **BULK-09 / SECIO-10:** When iteration finishes, the **checkpoint is deleted even if filings failed** (`sec_backfill.py:164-166`). A rerun meant to retry the failures starts from zero.
- **BULK-M1 / CLI-M2:** A targeted `sec-backfill --cik X` run **shares the same checkpoint file** as the full run (keyed only by the ZIP name) and **deletes it on completion**. That wipes progress on a multi-day full run.
- **BULK-M2 / SECIO-09:** **One malformed ZIP member raises an uncaught exception** and aborts the whole backfill. Because iteration order is fixed, every resume fails at the same place. The tests enshrine this behaviour (TESTS-14).
- **BULK-10:** Filings that always fail (pre-2003 non-XML Form 4s, permanently malformed XML) are refetched on every run.
- **BULK-M3:** Mapping failures are swallowed without logging, yet still marked completed, so they are never retried or diagnosed.
- **BULK-12 / SECIO-05 / ARCH-23:** Timeouts and connection resets are **never retried**; only 429/503 are. SEC's **403 "Request Rate Threshold Exceeded"** is not treated as throttling, and there is no circuit breaker (SECIO-06).
- **BULK-13 / CLI-04 / ARCH-22:** **No progress output** during a multi-day run (`on_progress` fires once, at the end), and Ctrl+C has no clean handling. The CLI also exits 0 when filings failed.
- **BULK-16 / SECPARSE-01:** The parser keeps **only the first `<reportingOwner>`**, so joint filings lose co-filers.

### The structural answer

The verifier partially confirmed this. SEC DERA publishes the **Insider Transactions Data Sets** (sec.gov/dera/data/form-345). They are quarterly ZIPs of TSVs extracted from the XML part of Forms 3/4/5, with `SUBMISSION`, `REPORTINGOWNER`, `NONDERIV_TRANS` and `DERIV_TRANS` tables keyed by `ACCESSION_NUMBER`. All of history becomes roughly 80 downloads plus bulk inserts: **minutes to about an hour instead of weeks**. Caveat: the datasets **start at 2006 Q1**. Filings from 2003–2005 would still need the per-filing path, or would remain a gap. The current quarter still needs the daily-index path. `submissions.zip` remains useful only for targeted per-CIK repairs.

Even if the per-filing design is kept, fixing #1, #3, #4, #5 and #6 brings it to the fair-access ceiling (~28k filings/h). That is still days for full history, which is why the dataset route matters.

---

## 2. Correctness bugs that silently lose or distort data

These matter more than their individual severities suggest, because they are **silent**: the app reports success and the missing data is never fetched again.

### 2a. "Covered" coverage is recorded for data that was never fetched (cross-cutting)

The services treat *the adapter returned without raising* as *the interval is fully covered*, and write that permanently to `scan_coverage`. Several things break that contract:

- **Scrapers swallow errors.** `openinsider` and `secform4` catch everything and return `[]` (ARCH-01, USSVC-02). The House and Senate scrapers skip failed PDFs, years and pages but return the rest (CONGRESS-04).
- **Silent caps.** The OpenInsider ticker scrape is hard-coded to `cnt=100` with no paging (USSVC-03, ARCH-02). The Senate search stops at 1000 filings (CONGRESS-05).
- **Today and future dates get marked covered.** The GUI end date defaults to today. `sec-daily` defaults to today, whose index is not yet published, and a 404 index counts as "no filings, done" (`sec_downloads.py:201`). `coverage.py` ignores `covered_at` (PERSIST-07, USSVC-04, SECIO-07, CLI-02, ARCH-03/04). **Anything filed later that day, for that ticker or date, is never fetched.**
- **SEC daily/catch-up marks a date complete when individual filings failed** to download, parse or cache (SECIO-04, ARCH-M1, SECIO-M1). A failed optional cache write even drops an already-parsed filing.
- **The House current-year index is downloaded once and never refreshed.** `refresh_current_year()` and `refresh_all_indexes()` have no callers (CONGRESS-03, ARCH-10). New House PTRs for the current year never appear.

### 2b. Common-path wrong results

- **`parse_date` returns `None` for `"YYYY-MM-DD HH:MM:SS"`** (`utils/parsing.py:24`; checked in the code). OpenInsider shows filing dates in that format (confirmed against the live site), so **every OpenInsider row loses its filing date** and drops out of `--since/--until` filters (CLI-01, ARCH-12, USSVC-05).
- **Unbounded ticker scan never fetches the ticker.** `scan TICKER` without dates, and the GUI with "use dates" unchecked, only refresh the market-wide "latest 200" feed and then read the DB (USSVC-01, CLI-03).
- **OpenInsider sales are negative** (`-12,345`), but `us_trades` has `CHECK (shares >= 0)` / `(value >= 0)`. **One sale row aborts the whole upsert batch** (USSVC-M1).
- **Named Congress scans return nothing for House members.** The scraper stores `"Hon. Nancy Pelosi"`, the GUI queries `"Nancy Pelosi"`, and the repository uses exact equality (CONGRESS-01, USSVC-07, ARCH-11). Senate `_split_name` mangles ~180 of 538 names with middle names, initials or ", Jr." (CONGRESS-02).
- **House `"S (partial)"` codes normalise to `"Other"`** and drop out of the Sale filters and statistics (CONGRESS-06).
- **Scan tab opens or shows the wrong trade after filtering.** Filtered model rows are indexed into the unfiltered `self._trades` list (`gui/scan_tab.py:530, 551`; checked in the code) (GUI-01, ARCH-19).
- **Numeric columns in the Scan and Congress tables sort as text** (`"1,000,000.00" < "200.00"`) (GUI-02).

### 2c. Identity and dedup

- **Form 4/A amendments are stored alongside the originals.** The computed "amends" key is never used, so restated transactions are double-counted in totals, cluster detection and alerts (SECPARSE-02, USSVC-15, PERSIST-M2). Amended Senate PTRs have the same problem (CONGRESS-M2).
- **The fuzzy US dedup key ignores trade type, price and sign.** A same-day option exercise and sale of the same quantity collapse into one row, while real duplicates split because of sign or date-format differences. The key is defined twice, in `core/merger.py:25` and `persistence/mappings.py:79` (USSVC-06, PERSIST-08).
- **SEC-native rows and scraped rows get different canonical keys**, so the same trade from EDGAR and from openinsider/secform4 is stored twice (PERSIST-04, ARCH-20).
- **One malformed optional value** (a comma in a number, a timezone-suffixed date) **discards every transaction in the filing** (SECPARSE-06, SECPARSE-11). Non-UTF-8 filings are rejected outright (SECPARSE-05, BULK-M4).

### 2d. Other notable bugs

- **Prices:**
  - One null or slightly inconsistent Yahoo bar discards the whole fetch (CONGRESS-17).
  - Cache coverage is min/max of stored dates, so interior holes are never filled (CONGRESS-15).
  - Split/dividend-adjusted values are never re-adjusted (CONGRESS-16).
  - Today's in-progress intraday bar is stored permanently (CONGRESS-M1).
- **`scripts/update_congress.py`:**
  - It writes to a path the app never reads (CONGRESS-12).
  - It omits the required Open States `jurisdiction` parameter (CONGRESS-14).
  - A failed fetch overwrites the members file with an empty list.
- **Alerts:**
  - Alerts can miss new records, because the top-N window is sorted by trade date and the feed has no `created_at` filter (USSVC-11, PERSIST-22).
  - Renaming a watchlist leaves alert rules pointing at the old name (PERSIST-19).
  - A corrupt state file is silently replaced with defaults on the next save, losing watchlists and alerts (PERSIST-17).
- **Window close:** `closeEvent` blocks the GUI for up to 5 s, then silently refuses to close if workers are still running (GUI-06).

---

## 3. Performance beyond the backfill

These scale badly once the backfill succeeds and the table holds millions of rows:

- **`query_latest` loads the whole `us_trades` table into Python** (no `LIMIT`, JSON provenance decoded per row) and then slices to `count`. Measured at 300k rows: **5.65 s**. It is used by "Latest" in the GUI and the CLI, and by every unbounded scan (`persistence/repositories.py:185`; checked in the code) (PERSIST-06, BULK-14).
- **Every feed page runs `COUNT(*)`, a sort and `MAX(created_at)`** over a 3-table `UNION ALL`, with no supporting indexes and `OFFSET` paging. Measured at 300k rows: **1.5–2.7 s per page**, growing linearly (PERSIST-05, PERSIST-13, ARCH-24).
- **Indexes don't match the queries.** Unused indexes slow every insert while the indexes queries need are missing (PERSIST-14). `lower(coalesce(...))` wrappers defeat the issuer and person indexes (PERSIST-12).
- **`NullPool`** opens a new SQLite connection, re-running the PRAGMAs, on every repository call (PERSIST-03).
- **`get_price_history` builds a new engine and runs bootstrap migrations** (taking an IMMEDIATE lock) on every call (CONGRESS-19, ARCH-16).
- **GUI:**
  - The pandas-backed `PandasTableModel.data()` does expensive per-cell indexing, combined with `ResizeToContents` on large tables (GUI-03, GUI-04).
  - "Resolve CIK" makes blocking SEC calls on the GUI thread (GUI-07).
- **Without WAL, GUI reads and backfill writes block each other** (PERSIST-02).
- **Legacy import commits once per record** (USSVC-13).
- **Congress flagging re-reads the members file from disk on every query** and does an O(N·M) substring scan with false positives, for example on empty names (USSVC-14, CONGRESS-10, ARCH-15).

---

## 4. Simplification and architecture

- **Three independent rate limiters**: `SecClient`'s `RateLimiter`, `utils/http.py`'s (not thread-safe, wall-clock based, runs at the 10 req/s ceiling), and the Senate module's. SEC traffic from `edgar.py`, `secform4` and `openinsider` bypasses the SEC fair-access limiter entirely (SECIO-15, CLI-10).
- **Resume and checkpoint logic is implemented three times**, in `sec_downloads`, `sec_daily` and `sec_backfill` (ARCH-21). A single DB-backed "ingested accessions" table would replace all three and fix the backfill checkpoint bugs above.
- **Two dedup identities for US trades** that disagree (`core/merger.py` vs `persistence/mappings.py`) (ARCH-20).
- **Overlapping modules**: `core/senate.py` vs `core/congress_senate.py`; `edgar.py` / `secform4.py` / `sec_client`. Several functions have no production callers (for example the House refresh helpers); the appendix lists the dead code the reviewers verified.
- **`MainWindow` is a god object** (127 graph edges). It owns navigation, three persistence stores, alert evaluation, screens and two duplicate theme controls (GUI-15). The Scan and Congress tabs are near-duplicates and contain business logic and file I/O (GUI-17). The `Worker` + request-id boilerplate is copied about 8 times even though a `dispatch()` helper exists.
- **Layering**: `core/prices` imports the services layer (ARCH-16).
- **Data duplication**: `data/` holds a stale copy of the seeds in `resources/seeds/`. `init-congress` is always a no-op, and seed updates never reach existing users (CLI-13).
- **Dead parameters and branches**, for example `clock` and the ticker branch in `latest` in the US services.

## 5. Good-practice gaps

- **Tests write into the developer's real data directory.** `DEFAULT_PATHS` is fixed at import time and no fixture redirects it, so `MainWindow()` in `test_gui.py` rewrites `feed_state.json`, `watchlists.json` and `alerts.json` under `%LOCALAPPDATA%\Insider Scanner\` (TESTS-01). The packaging tests also write `build/` and `*.egg-info` into the repo.
- **Backfill test coverage is thin.** There is only a 4-record fixture with loose `>= 1` assertions, and no tests for scale, progress, cancellation or checkpoint edge cases (TESTS-03, TESTS-15).
- **CI ignores `uv.lock`.** It installs unpinned dependencies with pip, so the stream of Dependabot `uv.lock` PRs is never actually tested (CLI-14, TESTS-05). There is no per-test or job timeout (one local run hung for more than 9 minutes) (TESTS-06). CI runs ruff (lint + format) but no type checker such as mypy or pyright, even though the code is heavily typed.
- **Live network tests run by default locally**; `-m "not live"` is only applied in CI (TESTS-04).
- **Slow tests:**
  - Senate tests sleep for real through the module-global rate limiter (~16 s, TESTS-08).
  - Two packaging tests build wheels (~12 s).
  - About 30% of the 107 s runtime is avoidable.
- **Some GUI tests assert nothing meaningful**, for example sort order is never checked (TESTS-11). That is how GUI-02 slipped through.
- **Logging is stderr-only**, with no file and no rotation, even though six user-facing messages say "see logs" (CLI-12).
- **Exceptions are swallowed silently.** Upsert failures lose their cause, and one bad row rolls back the batch (PERSIST-10). Mapping failures are not logged (BULK-M3).
- **`json_state` loaders documented as "never raises"** crash on a non-UTF-8 file (PERSIST-16).

## 6. Strengths

The SEC client and XML layer are defensively written:

- XXE/DTD bans and size, depth and text limits
- path-safe cache writes and bounded reads
- validated User-Agent

Elsewhere:

- **Persistence:** all SQL is parameterised (LIKE input is escaped; no injection found), migrations are ordered and transactional, and datetimes are typed UTC.
- **GUI:** the newer feed-first pages (Feed, drawer, entity pages, search, alerts, watchlists) do their DB work off the GUI thread with request-id guards.
- **Release workflow:** uses OIDC trusted publishing with SHA-pinned actions.
- **Tests:** the suite is large and genuinely offline (a run with sockets blocked showed zero network attempts).

## 7. Environment notes from the review

- **The repo's `.venv` is broken.** It points at a Python 3.13 interpreter that no longer exists. The reviewers used a separate scratch venv to run tests and benchmarks.
- **Side effects of running the tests during this review** (a consequence of TESTS-01):
  - They created `%LOCALAPPDATA%\Insider Scanner\Insider Scanner\` (seed copies, `Cache/`, `exports/`, `feed_state.json`, `watchlists.json`, `alerts.json`). That folder did not exist before today (created 13:07), so no prior user data was overwritten.
  - They (re)created `build/` and `src/insider_scanner.egg-info/` in the repo. Both are git-ignored.
  - All three can be deleted safely. No tracked files were changed.

---

## Appendix: all verified findings

How to read this: findings are grouped by the subsystem of the primary reviewer and sorted by severity. When several reviewers reported the same issue, the entries are merged and the other IDs are listed under *Also reported as*. Verdicts mean: **confirmed** = an adversarial verifier reproduced it in the code; **partially** = the core is real but details or severity were corrected (the verifier's note says what is true); **verifier-found** = the verifier spotted it and checked it while reviewing. Line numbers refer to commit ec37237.

### SEC bulk backfill performance deep dive (BULK) — 23 items

> I traced the full sec-backfill path end to end: ZIP member, then JSON, then metadata, then one sequential HTTP fetch per filing, then a cache write with fsync, then XML parsed twice, then trade mapping, then one SQLite transaction per filing, then a full checkpoint rewrite. The main reason the run never gets halfway is the checkpoint. After every filing it sorts the whole completed set, pretty-prints it as JSON and fsyncs it. Measured on this machine (benchmark at C:\Users\lukas\AppData\Local\Temp\claude\ca06d48fbcec0067\e24a750a-1bcd-4398-9d3f-359558b4187e\scratchpad\BULK\bench.py), one save takes 20 ms at 10k completed, 169 ms at 100k, 1.17 s at 500k and 2.5 s at 1M. That makes the run quadratic: about 1.25e-6·N² seconds, so about 3.5 h of checkpointing alone to reach 100k filings and about 87 h to reach 500k. Other measured fixed costs per filing: DB upsert 38 ms (one BEGIN IMMEDIATE per filing, NullPool reconnect, rollback journal with synchronous=FULL); cache write with fsync 11.8 ms plus 3.5 ms of repeated Path.resolve checks; parsing 0.8 ms. The network uses requests.get with no Session (TLS handshake to <www.sec.gov> measured at 30–100 ms per new connection) and no parallelism, which I estimate at about 0.15–0.3 s per filing. Current throughput is roughly 10–14k filings/h at the start, about 7k/h at 100k completed and about 2.5k/h at 500k. The archive has an estimated 5–7M unique XML-era Form 3/4/5 accessions, and each appears once per related CIK, so roughly 2x as many metadata rows. Even with every local fix in place, fetching each filing is capped by SEC fair access (8 req/s by default, about 28.8k filings/h), so about 6M filings would take about 9 days. The structural fix is the SEC DERA Insider Transactions Data Sets. I confirmed by web search (sec.gov/dera/data/form-345 and the readme PDF) that they are built from the XML part of Forms 3/4/5, are updated quarterly, and include SUBMISSION, REPORTINGOWNER and NONDERIV_TRANS/DERIV_TRANS tables keyed by ACCESSION_NUMBER. History would then take about 80 downloads plus bulk inserts, measured at about 150 µs per filing with executemany, so minutes to about an hour in total. The per-filing pipeline would only be needed for the current quarter. Secondary issues: errors that will always fail (pre-2003 non-XML Form 4s, xs:date values with a timezone) are fetched again on every run; the checkpoint is deleted after a 'complete' run even when there were failures; the parser keeps only the first reportingOwner; and after a backfill, query_latest and the feed scan or sort the whole multi-million-row table.

#### BULK-02 · CRITICAL · performance · effort L · partially

**Fetching every filing over the network cannot finish a full history; use the DERA Insider Transactions Data Sets**  
`src/insider_scanner/services/sec_backfill.py:150`

Every ownership accession in submissions.zip turns into one HTTP GET for the full .txt submission. XML-era Forms 3/4/5 (mid-2003 to now) number roughly 5–7M unique accessions (estimate). At the client's own cap (DEFAULT_REQUESTS_PER_SECOND = 8.0, sec_fair_access.py:23) the theoretical floor is 6M/8 = 750k s, about 8.7 days of nonstop fetching. The realistic sequential rate today is 3–5 req/s, which means 2–4 weeks even with BULK-01 fixed. I confirmed by web search that SEC publishes the 'Insider Transactions Data Sets' (sec.gov/dera/data/form-345, readme at sec.gov/files/insider_transactions_readme.pdf). They are extracted from the XML part of Forms 3/4/5, updated quarterly, and have SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS, DERIV_TRANS, footnote and related tables keyed by ACCESSION_NUMBER. Those tables map almost one-to-one onto InsiderTrade/sec_detail_json.

*Suggestion:* Add a 'sec-backfill-dataset' path. Download each quarterly ZIP (about 80 requests for the whole history), stream the TSVs with the csv module, join SUBMISSION, REPORTINGOWNER and the transaction tables on ACCESSION_NUMBER, and build InsiderTrade rows with the same sec_row_id scheme. Use NONDERIV_TRANS_SK/DERIV_TRANS_SK, or a per-accession ordinal that matches the parser's ':nonDerivative:{idx}' so keys line up with daily ingestion. Bulk-insert with executemany, which measured about 150 µs per filing. Keep the per-filing XML pipeline only for the current, not-yet-published quarter, through the daily index catch-up. Keep submissions.zip only for targeted --cik repairs.

*Verifier:* Confirmed: every metadata row becomes one rate-limited fetch of the full .txt submission (sec_backfill.py:150 -> fetch_filing), and 8 req/s caps throughput at about 28.8k filings/h, so a full XML-era history takes days to weeks. The DERA Insider Transactions Data Sets are the right structural fix, with one caveat: they start at 2006 Q1, not mid-2003, so the 2003-2005 filings would still need the per-filing path (or have to be accepted as a gap).

*Also reported as:* USSVC-M4

#### BULK-01 · CRITICAL · performance · effort M · confirmed

**Checkpoint rewrites, sorts and fsyncs the entire completed set after every filing (O(N^2))**  
`src/insider_scanner/services/sec_backfill.py:160`

After each successful filing, _save_completed calls sorted(completed), then json.dumps(indent=2) (json_state.py:29), writes a temp file, fsyncs it (json_state.py:39) and calls os.replace. The cost grows with the number of completed filings. Measured on this machine: 6 ms/save at 1k, 20 ms at 10k, 169 ms at 100k (2.9 MB file), 1,175 ms at 500k (14.5 MB) and 2,528 ms at 1M (29 MB). Total time is about 1.25e-6·N² s: about 3.5 h to reach 100k filings, about 87 h to reach 500k and about 14 days to reach 1M, before any network time. This alone explains 'takes hours and does not even get to half'. The module docstring (lines 8-10) admits the set 'can grow large' but misses that it is rewritten for every filing.

*Suggestion:* Replace the JSON snapshot with an append-only log: one accession per line, opened once, flushed every K filings, fsynced every M filings or T seconds, and loaded into a set on start. Better still, make the database the checkpoint: add a sec_ingested_filings(accession PRIMARY KEY, status, attempts, last_error, ingested_at) table and write its rows in the same transaction as the trades. Persisting trades and progress atomically also removes the crash window between the upsert and the checkpoint. If a snapshot is kept, throttle it to every few thousand filings or 30 s and drop indent=2 and sorted().

*Also reported as:* SECIO-01, TESTS-02, USSVC-M2, PERSIST-M1, ARCH-05, SECIO-12

#### BULK-03 · HIGH · performance · effort S · confirmed

**Each request opens a new TCP+TLS connection (requests.get with no Session or keep-alive)**  
`src/insider_scanner/core/sec_client.py:84`

requests.get builds and discards a Session and its connection pool on every call, so each filing pays a TCP and TLS handshake plus Session setup. Measured from this machine to <www.sec.gov:443>: TCP 6–31 ms, TCP+TLS 30–102 ms per new connection. Across millions of sequential requests that is about 8–28 h of handshakes per 1M filings. It also limits per-filing latency, so in practice the 8 req/s limiter is rarely reached.

*Suggestion:* Have RequestsSecTransport own a requests.Session, using one per worker thread if BULK-06 is adopted, with an HTTPAdapter(pool_connections, pool_maxsize) and urllib3 Retry for connect errors. Close it in a context manager. The frozen dataclass can hold the session through field(default_factory=requests.Session).

*Also reported as:* SECIO-02

#### BULK-05 · HIGH · performance · effort S · confirmed

**No SQLite journal_mode=WAL or synchronous pragmas: each commit pays full fsyncs, and readers block the backfill writer**  
`src/insider_scanner/persistence/engine.py:61`

The database runs with SQLite defaults: journal_mode=DELETE, synchronous=FULL and a small page cache. Each per-filing commit creates, fsyncs and deletes a -journal file. Switching only to WAL with synchronous=NORMAL cut per-filing upserts from 38.5 ms to 23.6 ms in my benchmark. Without WAL, a GUI that reads the same DB during a long backfill (feed queries scanning millions of rows) holds a SHARED lock. The writer then waits up to busy_timeout=5 s, after which SQLAlchemy raises OperationalError → PersistenceError → process_filing_row returns False. That filing is counted as failed and refetched on the next run.

*Suggestion:* In configure_sqlite_connection, set PRAGMA journal_mode=WAL once (it persists in the file), PRAGMA synchronous=NORMAL, a larger cache_size (e.g. -65536) and temp_store=MEMORY. For bulk loads, optionally run PRAGMA wal_autocheckpoint with a larger value and a final wal_checkpoint(TRUNCATE).

*Also reported as:* PERSIST-02, TESTS-M3

#### BULK-04 · HIGH · performance · effort M · confirmed

**One BEGIN IMMEDIATE transaction per filing over a new NullPool connection, with a SELECT before each row (38 ms/filing)**  
`src/insider_scanner/persistence/repositories.py:75`

The backfill sink calls self._persistence.us_trades.upsert(trades) once per filing (sec_backfill.py:148). Each call opens a fresh SQLite connection, because engine.py:46 uses poolclass=NullPool and every connect re-runs the PRAGMAs. It then issues BEGIN IMMEDIATE, runs a SELECT before each trade, then an INSERT, and commits with a rollback-journal fsync. Measured on a temp DB file: 38.5 ms per filing for 3 trades with current settings; 2.7 ms per filing with WAL and 500 filings per transaction; 0.15 ms per filing with raw executemany INSERT OR IGNORE. At 1M filings the current path spends about 10.7 h in SQLite commits alone.

*Suggestion:* Gather trades from N filings (e.g. 500–2,000, or about 5 s of work) and upsert each batch in one transaction. For source='sec_edgar' rows with a deterministic SEC canonical key, use INSERT ... ON CONFLICT(canonical_key) DO NOTHING/UPDATE through executemany instead of SELECT+merge for each row. Keep the merge path only when a non-SEC row already holds the key. Use a pooled engine (SingletonThreadPool/QueuePool) for long CLI runs, or keep one connection open for the whole backfill.

#### BULK-14 · HIGH · performance · effort M · confirmed

**query_latest loads the entire us_trades table into Python and slices it, which breaks down after a backfill**  
`src/insider_scanner/persistence/repositories.py:185`

UsScanService's latest scans (services/us.py:167 and :245) call query_latest(count, sources=selected). The statement has no LIMIT. Every row is fetched, turned into an InsiderTrade dataclass and filtered on provenance JSON in Python, then sliced to count. After even a partial backfill (millions to tens of millions of SEC rows), each 'latest' scan uses gigabytes of memory and takes minutes, on whatever thread runs the scan.

*Suggestion:* Push the filter and LIMIT into SQL. Filter by source = ANY(selected), or use EXISTS (SELECT 1 FROM json_each(source_provenance_json) WHERE value IN (...)), or normalize provenance into a us_trade_sources(trade_id, source) table with an index. Then apply .limit(count) on top of the existing ORDER BY filing_date DESC, which can use an index on (filing_date, trade_date).

*Also reported as:* PERSIST-06, ARCH-09, USSVC-M3

#### BULK-06 · HIGH · performance · effort L · confirmed

**Fetching is strictly sequential, so latency rather than the 8–10 req/s fair-access cap limits throughput**  
`src/insider_scanner/services/sec_backfill.py:124`

Each filing completes fetch, parse, cache fsync, DB commit and checkpoint before the next request starts. The RateLimiter (sec_fair_access.py:53-59) already supports threads through its lock, but only one thread calls it. With realistic per-filing wall time of 0.25–0.4 s before BULK-01's growth, the run reaches about 3–4 req/s, which is 40–50% of the allowed rate.

*Suggestion:* Use a bounded producer/consumer pipeline. The ZIP iterator feeds a queue, and 4–8 fetch workers (each with its own requests.Session) share the existing RateLimiter set at about 8–9 req/s. Workers parse and map, then send trades to one writer thread that batches DB upserts and checkpoint appends (BULK-01, BULK-04). Keep the queues bounded so memory stays flat. This approaches the fair-access ceiling of about 28.8k filings/h.

#### BULK-M1 · HIGH · bug · effort S · verifier-found

**A targeted --cik backfill deletes the full-archive checkpoint because both share one file keyed only by ZIP name**  
`src/insider_scanner/services/sec_backfill.py:165`

cmd_sec_backfill always uses the same checkpoint path, EDGAR_CACHE_DIR/sec_backfill_checkpoint.json (cli.py:340-344, 518), whether or not --cik is given. _load_completed matches only on zip_path.name, and the ciks filter is not stored in the checkpoint. Suppose a user is partway through a multi-day full run and runs a quick `sec-backfill --cik X` repair against the same submissions.zip. That run loads the full completed set, iterates only CIK X, finishes without interruption, and calls_delete_checkpoint(). All progress from the full run is lost, and the next full run refetches from zero.

*Suggestion:* Store the ciks filter (or a hash of it) in the checkpoint and either use a separate checkpoint per scope or never delete the checkpoint after a filtered run. Better, use the DB-backed ingest table proposed in BULK-01, so progress is not tied to one file that any run can delete.

#### BULK-09 · HIGH · bug · effort S · confirmed

**Checkpoint is deleted after a 'complete' run even when filings failed, so the next run restarts from zero**  
`src/insider_scanner/services/sec_backfill.py:166`

fully_done only means the ZIP iterator was exhausted. It ignores counters['failed'] and mapping failures. Failed accessions are never added to completed (the documented retry design), but deleting the checkpoint also discards the millions of successful accessions. Re-running the documented 'resume' command to retry failures then re-iterates and refetches the entire archive, because the cache expired after 24 h (BULK-07). Upserts would skip the rows, but every network request is repeated: days of wasted fetching.

*Suggestion:* Delete the checkpoint only when failures == 0. Otherwise keep it and record the failed accessions. Better, use the DB-backed ingest table from BULK-01, so a re-run retries only status='failed' rows (with an attempts cap) and successes never need fetching again, whatever happens to the checkpoint file.

*Also reported as:* SECIO-10

#### BULK-11 · MEDIUM · performance · effort M · confirmed

**The same accession is yielded once per related CIK (issuer and each reporting owner); dedup happens only after success and counters double-count**  
`src/insider_scanner/core/sec_bulk.py:98`

In submissions.zip a Form 4 appears in the issuer's CIK JSON and in every reporting owner's CIK JSON, so the same accession is yielded roughly twice or more. The service dedupes only through the completed set (sec_backfill.py:129), which holds successes only. Any failed or never-completing accession is therefore fetched again for each CIK. Because archive_path embeds meta.cik, each copy also gets a different cache key (sha256 of the path), so even a fresh cache entry is not reused. filings_discovered counts every copy, which makes the CLI summary and any progress percentage misleading.

*Suggestion:* Keep a set of attempted accessions for the run (not only successes) and skip repeats. Better, collect unique accessions first (one pass over the ZIP, a few minutes at the measured 50 MB/s JSON throughput), then process each once. Key the cache by accession, not by path. Report discovered_unique separately from discovered_rows.

#### BULK-M2 · MEDIUM · bug · effort S · verifier-found

**One malformed archive member aborts the whole backfill with an uncaught exception, deterministically on every resume**  
`src/insider_scanner/core/sec_bulk.py:309`

_read_member_json raises SecBulkError (or SecBulkSecurityError on an oversized read) from inside the generator, in the middle of iteration. In SecBackfillService.run only bulk_metadata_to_index_row is wrapped in try/except SecBulkError (sec_backfill.py:132-136). The `for meta in gen` loop is not, so the exception ends the run. cmd_sec_backfill (cli.py:521) does not catch it either, so the user gets a traceback and no summary. The same member fails on every resume, so a single truncated or corrupt member (common in a partial multi-GB download) blocks every later CIK for good.

*Suggestion:* Catch per-member read and parse errors in iter_ownership_filings, or yield an error marker. Log the member index without its content, count it as skipped_members, and continue. Keep only archive-level preflight violations fatal. In the CLI, catch SecBulkError and print a clear message plus the partial summary.

#### BULK-10 · MEDIUM · performance · effort M · partially

**Filings that will always fail (pre-XML Form 4s, permanently malformed XML) are refetched on every run and never converge**  
`src/insider_scanner/core/sec_bulk.py:358`

The metadata filter accepts any OWNERSHIP_FORMS row whatever its date. Ownership XML became mandatory only from mid-2003. Earlier electronic Form 3/4/5 submissions in the submissions JSON contain no <ownershipDocument>, so extract_ownership_document raises NonOwnershipDocumentError → counted failed → not completed (sec_backfill.py:12-18 documents this non-convergence). Each such accession costs a full rate-limited fetch on every run. The cost doubles because the same accession is also listed under each reporting owner's CIK (BULK-11).

*Suggestion:* Skip rows with filing_date before about 2003-06-30 cheaply in_filter_ownership_rows or the service, and count them as skipped_metadata. Record permanent failure classes (NonOwnershipDocumentError, SecOwnershipParseError, SecXmlSecurityError, 404) as status='failed_permanent' in the ingest table or checkpoint, so they are not refetched. Retry only transient classes (SecTransportError, 429/503, PersistenceError).

*Verifier:* Confirmed that _filter_ownership_rows filters only on form type (line 358), with no date cutoff, and that failed filings are retried on every run (the docstring at lines 12-18 says so). One detail is wrong: a pre-2003 text Form 4 has a <TYPE>4 block, so_extract_from_full_submission returns its plain text, and_validate_ownership_xml then raises SecOwnershipDocumentError ('not well-formed'), not NonOwnershipDocumentError. The outcome is the same: a permanent failure that is refetched on every run.

#### BULK-12 · MEDIUM · bug · effort S · confirmed

**Transport failures (timeouts, connection resets) are never retried, so they turn into per-filing failures**  
`src/insider_scanner/core/sec_client.py:246`

_request_with_retry retries only HTTP statuses in retry_policy.retry_statuses (429/503). Any requests exception (ReadTimeout with the 15 s timeouts, ConnectionError, ChunkedEncodingError while reading the body, which_read_bounded maps to SecTransportError) fails the filing at once. Over millions of requests, transient network errors run to thousands. Each one is left out of completed and costs another fetch on the next run, and with BULK-09 it is lost entirely once the checkpoint is deleted.

*Suggestion:* Treat SecTransportError (connect/read timeout, connection reset) as retryable inside _request_with_retry, using the same bounded backoff and attempt cap. Also retry 500/502/504. Keep security errors non-retryable.

*Also reported as:* ARCH-23, SECIO-05

#### BULK-M4 · MEDIUM · bug · effort M · verifier-found

**The whole .txt submission must be valid UTF-8; one non-UTF-8 byte in any attachment rejects the filing permanently**  
`src/insider_scanner/core/sec_ownership_document.py:142`

The backfill fetches the full submission (edgar/data/{cik}/{accession}.txt, sec_bulk.py:98), which bundles every document in the filing, including EX-24 power-of-attorney exhibits and HTML attachments. extract_ownership_document decodes the whole payload strictly before it looks for the ownership block. Legacy EDGAR text can contain Latin-1 or cp1252 bytes, so one such byte in an exhibit fails a filing whose ownership XML is valid. Under the backfill retry design it is then refetched on every run. I have not checked how often this happens on the real archive. The code path is certain.

*Suggestion:* Find the ownership <DOCUMENT> block at the byte level, or decode with latin-1 for envelope scanning, and strictly decode only the extracted XML (honoring its XML encoding declaration). Alternatively, fetch the primary XML document itself using the primary_document already captured in BulkFilingMetadata but never used. That also cuts bandwidth.

#### BULK-16 · MEDIUM · bug · effort M · confirmed

**Parser keeps only the first <reportingOwner>, so joint filings lose their other insiders**  
`src/insider_scanner/core/sec_ownership_parser.py:268`

Form 3/4/5 XML allows several reportingOwner elements, as in joint filings by fund entities, their general partners and individual managers, which are common among 10% owners. Only the first is parsed, so insider_name, cik_insider and insider_title in every mapped trade show only that owner. The others are silently dropped. Because the same accession is also listed under the other owners' CIKs in submissions.zip, those owners' entity pages never show the trade, and the deterministic canonical key prevents a second row.

*Suggestion:* Parse all reportingOwner elements into a tuple. Either store the primary owner in the existing columns plus the full list in sec_detail_json, or emit one row per (owner, transaction) with the owner CIK in sec_row_id. Document whichever you choose.

*Also reported as:* SECPARSE-01

#### BULK-M3 · MEDIUM · practice · effort S · verifier-found

**Mapping failures are swallowed without logging and marked completed, so they are never retried or diagnosed**  
`src/insider_scanner/services/sec_backfill.py:145`

When ownership_filing_to_trades raises (for example the period_of_report > filing_date check at sec_trade_mapping.py:158), the sink increments a counter and returns normally with no log line and no accession recorded. reached_sink[0] is already True and process_filing_row returns True, so line 159 adds the accession to completed. Its trades are dropped for good: after a mapper fix, resuming will never re-ingest those filings, and nothing records which accessions were affected. The fetch failures this is meant to mirror are logged with the CIK and reason.

*Suggestion:* Log at WARNING with the accession and the exception type. Record these accessions as status='mapping_failed' (in the ingest table or a failed set in the checkpoint) rather than 'completed', so a later run with a fixed mapper can retry just them.

#### BULK-13 · MEDIUM · practice · effort M · confirmed

**No progress reporting or graceful cancellation during a multi-day run; summary counters reset on resume**  
`src/insider_scanner/services/sec_backfill.py:180`

on_progress is called once, after the loop finishes. cmd_sec_backfill (cli.py:521) passes neither on_progress nor cancelled, so the CLI prints only 'Starting full bulk backfill ...' and then nothing for hours or days. The user cannot see the rate, how many are done, or the ETA, which is why the slowdown from BULK-01 went unnoticed. Ctrl+C raises KeyboardInterrupt through the loop, so no summary is printed. The counters are not stored in the checkpoint, so after a resume the final summary covers only the last session.

*Suggestion:* Emit progress every N filings or T seconds with discovered/completed/failed, filings per second and an ETA, and wire it to stderr in the CLI as cmd_sec_catchup does. Install a SIGINT handler that sets a cancelled flag so the loop exits cleanly, flushes the batch and checkpoint, and prints the summary. Store cumulative counters in the checkpoint or ingest table.

#### BULK-07 · MEDIUM · performance · effort S · confirmed

**Every fetched filing is written to a cache with fsync that the backfill never reuses (24 h freshness, deleted on read)**  
`src/insider_scanner/services/sec_downloads.py:282`

For each downloaded filing, promote_validated_filing runs mkstemp, write, flush, os.fsync and replace (sec_downloader.py:298-308). I measured 11.8 ms per 8 KB file on this machine, against 1.2 ms for a plain write. The cache has no value for the backfill. Completed accessions are skipped through the checkpoint before fetch_filing is called. cache_freshness_seconds is 24*60*60 (sec_security.py:213), and_read_fresh_cache unlinks anything older (sec_downloader.py:275-277), so a resume after more than a day refetches anyway. All entries go into one flat validated-v1 directory, so a multi-million-file directory builds up during the run (Defender scans each new file), and purge_stale_cache later walks all of it. Cost: about 3.3 h of I/O per 1M filings, plus tens of GB of disk.

*Suggestion:* Add a cache=False (or write_cache policy) option to process_filing_row and use it for the backfill: parse straight from the bytes in memory and skip promote. If caching stays for diagnostics, drop the per-file fsync (atomic replace is enough for a cache), shard the namespace into subdirectories (e.g. the first 2 hex characters of the digest), and use a longer freshness for immutable archive documents.

#### BULK-08 · LOW · performance · effort S · confirmed

**Redundant Path.resolve and symlink checks run twice per filing (3.5 ms/filing on Windows)**  
`src/insider_scanner/core/sec_downloader.py:86`

fetch_filing runs_validate_inputs (cache_root is_symlink/exists/is_dir) and _validate_content_path (two Path.resolve calls plus two is_symlink calls). promote_validated_filing (lines 118-121) repeats all of it, then _prepare_cache_directory runs two mkdir calls plus is_symlink/is_dir. bulk_metadata_to_index_row has already validated the archive path, and cache_root is constant for the whole run. Measured at 3.49 ms per filing, about 1 h per 1M filings.

*Suggestion:* Validate and resolve cache_root and the namespace directory once per run, for example in a small CacheLocation object created when the service starts. Per filing, check only the leaf: a lexical join of a hex digest, which cannot escape the root.

#### BULK-18 · LOW · simplification · effort M · confirmed

**Each ownership XML is parsed twice with a new hardened parser each time**  
`src/insider_scanner/core/sec_ownership_parser.py:206`

extract_ownership_document already encodes, guards, parses and tree-walks the XML (sec_ownership_document.py:231-242) and then throws the tree away. parse_ownership_document repeats all of it and also builds a second XMLParser. Measured extract+parse+map at about 0.8 ms per small filing, of which the duplicate parse is roughly half. That is minor next to the network but amounts to about 7 CPU-minutes per 1M filings, plus extra complexity.

*Suggestion:* Have extract_ownership_document return the validated root element (or carry it in OwnershipDocument as a private field) and let parse_ownership_document accept it, re-validating only when given raw text from an untrusted caller. Reuse one hardened parser per thread through threading.local.

#### BULK-17 · LOW · bug · effort S · confirmed

**Dates with an xs:date timezone suffix reject the whole filing, permanently**  
`src/insider_scanner/core/sec_ownership_parser.py:601`

The ownership schema types transactionDate, periodOfReport and related fields as xs:date, which allows a timezone suffix. I verified that on Python 3.13 date.fromisoformat('2004-05-12-05:00') and date.fromisoformat('2004-05-12Z') raise ValueError. The parser turns that into SecOwnershipParseError, which fails the whole filing. Under the backfill's retry design such a filing is refetched on every run and never ingested (BULK-10).

*Suggestion:* Normalize the value before parsing, e.g. take raw[:10] when it matches r'^\d{4}-\d{2}-\d{2}(Z|[+-]\d{2}:\d{2})?$'. Apply this in _value_date and _parse_date_elem, and add a fixture with a timezone-suffixed date.

#### BULK-20 · LOW · practice · effort S · partially

**ZIP preflight limits are close to the real submissions.zip size; when it grows, the run aborts with a generic error**  
`src/insider_scanner/core/sec_security.py:222`

_preflight_members rejects the whole archive if it has more than 2M entries or more than 16 GiB uncompressed in total, raising SecBulkSecurityError with only the reason 'zip'. submissions.zip holds about 900k+ CIK JSONs plus continuation files and grows every night. I did not verify current headroom, but once it crosses either limit the backfill stops immediately with a message that does not say which limit was hit. The limits are module constants and the CLI cannot override them.

*Suggestion:* Report which limit was exceeded and the observed value in the error, without member names. Read the current entry count and total size of the real archive (e.g. len(infolist()), sum(file_size)), set the limits with a documented margin, and allow a CLI override for trusted local archives.

*Verifier:* The limits are at sec_security.py:222-224, and_preflight_members raises a bare SecBulkSecurityError() with reason 'zip' (sec_bulk.py:232-239) that does not say which limit was hit, so the diagnostics gap is real. The claim that the real archive is close to the limits is unverified. The user's runs get past preflight today, so this is not a current failure.

#### BULK-19 · LOW · simplification · effort S · confirmed

**ZIP location check is a no-op because the trusted root is set to the ZIP's own parent**  
`src/insider_scanner/services/sec_backfill.py:120`

iter_ownership_filings documents cache_root as an 'injected trusted root the archive must live under'. The backfill passes zip_path.parent, so resolves_within(zip_path, cache_root) is always true and_validate_zip_location only checks for symlinks. The containment check adds code and test surface without protecting anything, and it can mislead readers into thinking the ZIP location is constrained.

*Suggestion:* Either pass a real configured root (for example a dedicated bulk-download directory) or drop the containment parameter from this call path and keep only the symlink check, with a comment explaining why.

### SEC fetch / download / IO correctness (SECIO) — 19 items

> The SEC fetch layer (SecClient, RateLimiter, bounded reads, path-safe cache) is defensively written and mostly correct, but the orchestration around it has serious throughput and data-integrity problems. The slow full backfill you already know about has a clear main cause: SecBackfillService rewrites a sorted, indented JSON file of every completed accession (with fsync) after each filing. That makes the run O(N^2). A local benchmark measured about 140 ms per save at 100k accessions and about 500 ms at 500k, which is already slower than the 125 ms network budget per filing. Even with that fixed, the design still sends one sequential HTTP request per filing for all of history, with no keep-alive Session and no concurrency. Filings listed under several CIKs are fetched more than once. So a full-history backfill takes days at best. On correctness: daily/catch-up ingestion marks a date as covered even when some of its filings failed to download. Network errors and SEC's 403 throttling response are not retried. A 404 index for today or a future date is recorded as permanent coverage. The backfill deletes its checkpoint after one pass even if some filings failed, and it gets stuck forever on one malformed archive member. Smaller issues: raw OSErrors escape the recoverable-error handling, an fd is closed twice in the atomic write, the cache grows without limit, the legacy http.py rate limiter is separate from the SecClient one and not thread-safe, and the per-range checkpoint in sec_downloads repeats what DB coverage already tracks.

#### SECIO-08 · HIGH · performance · effort S · confirmed

**The same filing is fetched once per associated CIK because dedup uses archive path, not accession**  
`src/insider_scanner/core/sec_index.py:53`

EDGAR indexes list an ownership filing once per associated entity (the issuer and each reporting owner), each under its own path edgar/data/{cik}/{accession}.txt. Dedup by archive_path keeps all of them, so daily ingestion downloads, parses and upserts the same Form 4 two or more times. The cache does not help because the cache key is sha256(archive_path) (sec_downloader.py:251). submissions.zip has the same duplication across CIK members. The backfill skips a duplicate only after the first copy succeeded, counts it as 'skipped_resume', and inflates 'discovered', so progress numbers are misleading. If the first copy failed, the duplicate is re-downloaded under a different URL.

*Suggestion:* Deduplicate on the accession number (the last path segment without .txt) in parse_master_index and in the backfill loop, keeping a run-scoped seen-accession set separate from 'completed'. Key the filing cache by accession instead of archive_path. Report duplicates in their own counter.

*Also reported as:* SECPARSE-M1

#### SECIO-03 · HIGH · performance · effort L · confirmed

**Full backfill makes one HTTP request per historical filing and has no date filter**  
`src/insider_scanner/services/sec_backfill.py:124`

The backfill turns every Form 3/4/5 accession in submissions.zip (all CIKs, all years) into a separate fetch of the full .txt submission. At the SEC fair-access ceiling (about 8 rps, roughly 29k filings per hour) the network alone puts a lower bound of days to weeks on a multi-million-filing archive, even after SECIO-01 and SECIO-02 are fixed. run() takes a CIK filter but no since/until filter, although BulkFilingMetadata.filing_date is available. A user cannot limit the run to, say, the last 5 years.

*Suggestion:* Add since/until filtering on meta.filing_date before any network work. For deep history, ingest SEC's quarterly 'Insider Transactions Data Sets' (Form 3/4/5 flattened TSV ZIPs from DERA). About 80 quarterly downloads replace millions of per-filing requests. Keep per-filing fetching for recent gaps only.

#### SECIO-07 · HIGH · bug · effort S · confirmed

**A 404 daily index marks the date complete, so today and future dates become permanently covered**  
`src/insider_scanner/services/sec_downloads.py:201`

Treating 404 as 'no filings that day' is right for weekends and holidays. It is wrong for dates whose index has not been published yet. cmd_sec_daily defaults to date.today() (cli.py:442), and validate_range (services/common.py:54) accepts future end dates. The master.YYYYMMDD.idx file only appears after SEC builds the nightly index. A daytime run of `sec-daily`, or `sec-catchup --until <future>`, gets 404 for those dates, returns True, and sec_daily records coverage. Later runs skip those dates and never ingest their filings.

*Suggestion:* Accept 404 as 'empty day' only for dates strictly before the most recent published index (for example date < today in US/Eastern, or after checking the quarter's daily-index listing). For today or later, leave the date uncovered (return False). Also reject or clamp end dates after today in validate_range or in the CLI.

*Also reported as:* ARCH-03

#### SECIO-04 · HIGH · bug · effort M · confirmed

**Per-filing fetch failures still complete the date, so coverage is recorded and the filings are permanently lost**  
`src/insider_scanner/services/sec_downloads.py:253`

process_filing_row returns True (meaning 'date can complete') for fetch failures, including transient SecTransportError, a 503 that exhausted its retries, and SecHttpError 403 from throttling. _process_date then returns True, download_filings_in_range adds the date to the checkpoint and calls on_progress. SecDailyIngestionService records that day as covered (sec_daily.py:258-264). Later ingest_range calls skip covered dates via coverage.gaps(), so any filing that hit a temporary network or throttling error is never retried. Nothing records which accessions failed.

*Suggestion:* Treat transient fetch failures (transport errors, 5xx, 429/403 after retries) as date-level failures: return False so the date stays uncovered and a resume retries it. Or record failed accessions in a persisted retry list (DB table) that later runs work through. Only deterministic failures (404 filing, security rejections, parse errors) should let the date complete.

#### SECIO-M1 · HIGH · bug · effort S · verifier-found

**An optional cache write blocks persistence: if promotion fails, a parsed filing is dropped and its date still gets marked covered**  
`src/insider_scanner/services/sec_downloads.py:281`

process_filing_row writes the validated filing to the disk cache before calling the persist sink. A failed cache write is wrapped as SecDownloadIoError, for example from disk full (made likely by unbounded cache growth, SECIO-12), a PermissionError on os.replace from Windows antivirus, or a path-policy rejection. The function then returns True without ever calling on_filing_parsed. In daily/catch-up ingestion the date completes and coverage is recorded, so a filing that downloaded and parsed correctly is permanently lost because of a cache that only serves as an optimisation. In backfill the accession is never completed and is refetched on every resume.

*Suggestion:* Call the persist sink right after a successful parse, and make cache promotion best-effort afterwards (log on failure, do not count it as a filing failure, do not skip persistence). Better still, skip cache promotion entirely in backfill, where the bytes are never reused.

#### SECIO-M3 · MEDIUM · performance · effort M · verifier-found

**Every backfill resume decompresses and JSON-parses the whole submissions archive again just to skip completed accessions**  
`src/insider_scanner/core/sec_bulk.py:201`

The resume state is only a set of accessions, and iter_ownership_filings has no start position. Each resume (and each run after a SECIO-09 crash) therefore reads up to 64 MiB per member, fully json.loads every CIK member (about 1M entities, most with no ownership filings), and builds BulkFilingMetadata for every ownership row before sec_backfill.py:129 discards it. Because the checkpoint is saved after every filing, the whole pre-resume prefix is parsed again on every restart.

*Suggestion:* Add a compact positional cursor (member index plus row index, valid because ZIP central-directory order is deterministic). Let iter_ownership_filings take start_member, so a resume can skip already-finished members without opening them. Keep the accession set (or a DB table) only as a safety net for the partially processed member.

#### SECIO-09 · MEDIUM · bug · effort S · partially

**One malformed bulk member, or any unexpected exception, aborts the backfill, and every resume fails at the same point**  
`src/insider_scanner/core/sec_bulk.py:201`

_read_member_json raises SecBulkError inside the generator. SecBackfillService.run only catches SecBulkError around bulk_metadata_to_index_row (sec_backfill.py:133-136), not around `for meta in gen` (line 124). So the exception ends the whole run, and since generators cannot continue after raising, the rest of the archive is never reached. On resume the generator starts again from the first member, re-reads every member up to the bad one, and fails again. The backfill never gets past that point. A dict-shaped 'form' array in _filter_ownership_rows (line 347-355, KeyError) or an OSError from the cache (SECIO-12) behaves the same way.

*Suggestion:* Catch per-member errors inside iter_ownership_filings: log the member index/name, count it as a skipped member, and continue. Optionally take an on_member_error callback. Validate that the parallel arrays are lists before indexing. In run(), also report member-level skips in the summary.

*Verifier:* The mechanism is real. SecBulkError raised by _read_member_json (raised at line 309, called at line 201) propagates out of the generator. run() only catches SecBulkError around bulk_metadata_to_index_row (lines 132-136), so the run aborts, and on resume it re-reads members up to the same point and fails again. A dict-valued 'form' would raise KeyError at line 355. However, a malformed member in a genuine SEC archive is an unusual edge case, so 'high' is overstated.

#### SECIO-13 · MEDIUM · bug · effort S · confirmed

**Raw OSError from cache read or unlink escapes the recoverable-error handling and aborts the run**  
`src/insider_scanner/core/sec_downloader.py:276`

fetch_filing wraps neither_read_fresh_cache's unlink() (lines 273 and 276) nor read_bytes() (line 278) in its OSError -> SecDownloadIoError translation; only promote_validated_filing does that. On Windows, unlinking or reading a file that antivirus or the indexer has open raises PermissionError. That is not in sec_downloads._RECOVERABLE, so one locked cache file ends the whole catch-up or backfill with a traceback, rather than counting one failure. purge_stale_cache (line 179) likewise catches only FileNotFoundError around stat().

*Suggestion:* Wrap the cache read path in try/except OSError. On unlink failure, ignore it and fall through to a network fetch. On read failure, either raise SecDownloadIoError (which is recoverable) or treat it as a cache miss. In purge_stale_cache, catch OSError around stat as well.

#### SECIO-06 · MEDIUM · bug · effort M · partially

**SEC's 403 'Request Rate Threshold Exceeded' is not treated as throttling, and there is no circuit breaker**  
`src/insider_scanner/core/sec_fair_access.py:75`

When a client goes over its fair-access allowance, SEC EDGAR usually answers 403 (with an 'Undeclared Automated Tool' or 'Request Rate Threshold Exceeded' page) and blocks the IP for about 10 minutes. It does not usually send 429. Here 403 raises SecHttpError at once. Each filing then fails quickly and is counted as failed. A daily catch-up keeps going through the block window and marks every date in it covered with all filings failed (SECIO-04). A backfill burns through thousands of accessions as failures. The 30 s cap on Retry-After and the 4-attempt limit (1+2+4 s) are also far shorter than a 10-minute block.

*Suggestion:* Detect sustained 403/429 from <www.sec.gov> as a throttle signal. Open a circuit breaker that pauses all requests for a long cooldown (for example 10 minutes, honoring Retry-After without the 30 s cap when it comes from SEC), then probe again. Abort the run with a clear message if blocking continues, rather than counting every filing as failed.

*Verifier:* This is true: retry_statuses is {429, 503} (line 75), so a 403 raises SecHttpError immediately, there is no circuit breaker, and backoff_delay caps Retry-After at 30 s. However, the single-threaded client runs at 8 rps, below SEC's 10 rps ceiling. Sustained 403 blocks are realistic mainly when combined with the separate legacy limiter (SECIO-15) or several processes. The exact 403 behaviour of SEC cannot be verified from this code. It is a hardening gap rather than a common-path failure.

#### SECIO-M2 · MEDIUM · performance · effort M · verifier-found

**Backfill and daily persistence commit one SQLite IMMEDIATE transaction per filing, with a SELECT for every trade**  
`src/insider_scanner/services/sec_backfill.py:148`

Each sinked filing calls UsTradeRepository.upsert. That opens BEGIN IMMEDIATE (persistence/engine.py:20-26), runs a SELECT by canonical_key for every trade, then an INSERT or UPDATE, and commits. Each commit is a journal/WAL sync, which costs milliseconds on Windows. Over millions of backfill filings this adds hours of fsync-bound commits on top of the checkpoint (SECIO-01) and cache (SECIO-12) fsyncs, so every filing costs three durable syncs. sec_daily.py:243 follows the same pattern.

*Suggestion:* Buffer trades from N filings (for example 200-1000) or T seconds and upsert them in one transaction. Mark accessions completed only after their batch commits. Inside upsert, prefetch existing canonical keys with a single IN query, or use INSERT ... ON CONFLICT, instead of one SELECT per trade.

#### SECIO-11 · MEDIUM · practice · effort S · confirmed

**Backfill reports progress only once, at the very end**  
`src/insider_scanner/services/sec_backfill.py:180`

run() accepts an on_progress callback but calls it once, after the whole (multi-day) iteration. cmd_sec_backfill (cli.py:521) passes neither on_progress nor cancelled. A user watching a run that lasts hours sees no output, rate, failure count or ETA, and cannot tell a stuck run (see SECIO-09 and SECIO-06) from a slow one.

*Suggestion:* Call on_progress every N filings or T seconds with the running summary (counts, filings per second, failures). In the CLI, print a throttled status line to stderr, as_make_cli_progress already does for catch-up. Wire a SIGINT-safe cancelled flag so Ctrl-C stops cleanly.

*Also reported as:* ARCH-22

#### SECIO-15 · MEDIUM · bug · effort M · confirmed

**Legacy http.py rate limiter is separate from SecClient's, runs at the 10 rps ceiling, uses the wall clock and is not thread-safe**  
`src/insider_scanner/utils/http.py:21`

edgar.py calls fetch_url(use_sec_agent=True), which uses a module-global limiter set to SEC_MAX_REQUESTS_PER_SECOND = 10 (config.py:93), the hard ceiling with no margin. It has no lock, so several GUI QThreads (scan tab, feed, entity pages) can read the same_last_request_time and fire together. It uses time.time(), which jumps with clock adjustments. It is also completely independent of SecClient's 8 rps RateLimiter, so legacy EDGAR lookups running alongside a catch-up or backfill push total traffic from one IP above 10 rps. That triggers the 403 block in SECIO-06. fetch_url also has no retry on 429/503.

*Suggestion:* Route SEC traffic in fetch_url through one shared process-wide RateLimiter (the sec_fair_access one, with monotonic clock and lock), or move these edgar.py calls onto SecClient. Remove the duplicate limiter.

*Also reported as:* CLI-10

#### SECIO-23 · LOW · simplification · effort S · confirmed

**OWNERSHIP_FORMS is defined twice**  
`src/insider_scanner/core/sec_bulk.py:36`

sec_bulk.py and sec_index.py:11 each define the same ownership form set. If one copy changes (for example to drop Form 3/5 for speed, or to add a variant), daily ingestion and bulk backfill quietly start accepting different filing types.

*Suggestion:* Define OWNERSHIP_FORMS once (for example in sec_index or edgar) and import it in sec_bulk.

#### SECIO-22 · LOW · performance · effort S · confirmed

**purge_stale_cache resolves the cache root again for every entry**  
`src/insider_scanner/core/sec_downloader.py:176`

resolves_within calls root.resolve() and path.resolve() on every call. For a flat directory holding millions of cached filings (SECIO-12), the purge does two filesystem resolutions plus a symlink check and a stat for each entry. Measured at about 0.2 ms per entry on this machine, that is several minutes per million files, for a containment check that is redundant once the namespace directory has been confirmed not to be a symlink.

*Suggestion:* Resolve cache_root once before the loop. Use os.scandir (DirEntry.is_symlink() and stat(follow_symlinks=False) are cached and cheap). Skip the per-entry resolve, because iterdir entries of a verified non-symlink directory are direct children by construction.

#### SECIO-14 · LOW · bug · effort S · confirmed

**_write_atomically can close an fd that has already been closed and reused by another thread**  
`src/insider_scanner/core/sec_downloader.py:311`

Once os.fdopen succeeds, the `with` block owns the descriptor and closes it. If temp_path.replace() then fails (common on Windows when the target is momentarily locked by antivirus or a reader), the except block calls os.close(descriptor) again on a closed fd number. In a process with other threads (the GUI's QThreads, or a future worker pool for SECIO-02), the OS may already have given that number to another file, which then gets silently closed. That causes data loss or EBADF in unrelated code.

*Suggestion:* Track ownership: set a flag (or descriptor = -1) once os.fdopen succeeds, and only call os.close when fdopen never took ownership. Also consider a short retry loop around replace() for PermissionError on Windows.

*Also reported as:* BULK-M5

#### SECIO-19 · LOW · bug · effort S · confirmed

**atomic_write_json leaves orphan temp files when interrupted with Ctrl-C**  
`src/insider_scanner/persistence/json_state.py:41`

The cleanup handler catches Exception, not BaseException, so KeyboardInterrupt and SystemExit skip deleting the temp file. Ctrl-C is the only way to stop the CLI backfill, and checkpoint writing currently takes most of the backfill's time (SECIO-01). So an interrupt usually lands inside this function and leaves a multi-MB '.sec_backfill_checkpoint.json.*.tmp' file behind each time. sec_downloader._write_atomically already uses BaseException correctly.

*Suggestion:* Catch BaseException (re-raising afterwards), as _write_atomically does. Optionally sweep stale '.<name>.*.tmp' siblings at startup.

*Also reported as:* ARCH-M3

#### SECIO-17 · LOW · simplification · effort S · confirmed

**sec_downloads' range checkpoint duplicates DB coverage and in practice never resumes**  
`src/insider_scanner/services/sec_downloads.py:135`

The only production caller is SecDailyIngestionService. It calls download_filings_in_range once per coverage gap, with the same checkpoint file, and records DB coverage in on_progress at the same moment the checkpoint records a completed date (lines 157-160). After an interruption, the next run computes smaller gaps from coverage, so the saved range never equals the new interval (line 359-364) and the checkpoint is thrown away. Its only real effect is an extra fsync'd JSON write per date, plus counters restored from an old run leaking into the new summary (see SECIO-18).

*Suggestion:* Drop checkpointing from download_filings_in_range and treat DB coverage as the only source of resume truth. If standalone (non-DB) use needs resume, keep the checkpoint as an optional feature that sec_daily does not enable.

#### SECIO-18 · LOW · bug · effort S · partially

**Counters double-count retried dates and carry over between runs**  
`src/insider_scanner/services/sec_downloads.py:213`

Counters change while a date is being processed. When a date ends with a sink failure (returns False) and continue_on_error is True, its partial discovered/downloaded/parsed/failed counts stay in the shared dict and are saved by the next successful date's checkpoint. A later resume processes that date again and counts it twice. Counters restored from a checkpoint also go into SecDownloadReport, so sec_daily's per-run summary (sec_daily.py:180-183) includes numbers from earlier runs.

*Suggestion:* Count each date in a local dict and merge it into the run totals only when the date completes. Do not restore counters from the checkpoint into a new run's report (or report them separately as 'previous').

*Verifier:* Counters are shared and are restored from the checkpoint (lines 373-378). However, restoring requires an exact range match, which the sec_daily path practically never produces (see SECIO-17). The carry-over into sec_daily summaries is therefore essentially unreachable today. Double counting is only real for standalone download_filings_in_range resumes.

#### SECIO-21 · LOW · practice · effort S · confirmed

**Failure logs drop the accession, HTTP status and URL, and failed filings are not recorded anywhere**  
`src/insider_scanner/services/sec_downloads.py:311`

Per-filing failures are logged as 'cik=<cik> reason=SecHttpError'. A CIK has many filings, and the status code (403 vs 500 vs 404) and the safe URL label, which SecHttpError already carries in a sanitized form, are dropped. After a long run you cannot tell whether failures came from throttling, outages or bad documents, and you cannot list the affected accessions to re-run them.

*Suggestion:* Log the accession number (from row.archive_path), status_code when present, and the already-sanitized exc.url. Also collect failed accessions with a reason code in the report, or persist them, so they can be retried (this also covers SECIO-04 and SECIO-10).

### SEC ownership XML parsing & trade mapping (SECPARSE) — 25 items

> The SEC ownership pipeline (_sec_xml -> sec_ownership_document -> sec_ownership_parser -> sec_trade_mapping) is cleanly layered. It is typed with frozen dataclasses, has solid XXE/DTD hardening (resolve_entities=False, no_network, a pre-parse DTD/ENTITY ban, and tree size/depth/text limits), and builds idempotency keys deterministically from accession plus table index. The main correctness gaps are in what the data model keeps. Only the first reportingOwner is parsed. 4/A amendments are stored alongside the filings they amend, and the computed "amends" key is never used. Holdings rows (all of Form 3) are dropped. A single unparseable optional value (a comma in a number, a timezone-suffixed date, exercise date after expiration) throws away the whole filing. Filings that are not UTF-8, or that declare a non-UTF-8 encoding, are rejected or come out garbled. Performance: I profiled the 3.4 KB fixture with the repo's .venv packages under system Python 3.13 (the .venv interpreter is broken). Extraction takes about 0.50 ms and parsing about 0.65 ms per filing. The pure-Python guard_xml_tree walk accounts for about 40% of that CPU time, and it runs twice per filing because the XML is decoded, encoded, guarded and parsed twice. So parsing costs about 1 ms of CPU per filing. That is real but not the main reason the full-history backfill is slow. The bigger cost sits in the in-scope extractor: every filing, including Form 3s that give no rows and pre-2003 non-XML filings that can never parse, is fetched over the network at SEC's rate limit before it fails or yields nothing. edgar.py has some dead code, repeated JSON parsing in CIK lookup, and URLs that are not encoded.

#### SECPARSE-02 · HIGH · bug · effort L · confirmed

**4/A amendments never supersede the original; the 'amends' key is unused and not unique**  
`src/insider_scanner/core/sec_trade_mapping.py:198`

An amendment has its own accession number. Its rows get new sec_row_ids ('<acc>:nonDerivative:<idx>'), so us_canonical_key treats them as new trades. A 4/A that restates a Form 4 therefore doubles every transaction in the database, which inflates totals, cluster-buy detection and alerts. The 'amends' string goes into sec_detail_json, but grep shows no code anywhere reads it. It also cannot identify a transaction: it is only issuer|insider|period|security_title, so all 'Common Stock' rows in one amended filing share the same key.

*Suggestion:* Store amendment linkage in real columns, not JSON. On ingest of an X/A, mark rows from earlier filings for the same (issuer CIK, owner CIK, period_of_report, base form type) as superseded, or exclude superseded rows in feed/aggregation queries, for example 'latest accession per (issuer, owner, period) wins'. If per-row matching is needed, build the key from (security_title, transaction_date, transaction_code, table index), not security_title alone.

#### SECPARSE-05 · MEDIUM · bug · effort M · confirmed

**Non-UTF-8 filings rejected outright; a declared non-UTF-8 encoding causes mojibake**  
`src/insider_scanner/core/sec_ownership_document.py:142`

EDGAR .txt submissions are not always UTF-8. Older filings and some filer software produce Latin-1/cp1252 bytes, for example names with accented letters. Any such byte makes the whole filing fail (scratch test 'latin1-bytes' -> SecOwnershipDocumentError). Separately, _validate_ownership_xml and parse_ownership_document re-encode the decoded str as UTF-8 and hand the bytes to lxml. lxml obeys the XML declaration, so a document declaring encoding="ISO-8859-1" is decoded a second time as Latin-1 and the names come out garbled (scratch test 'latin1-decl-str' produced 'MÃ\x9c...' in rptOwnerName).

*Suggestion:* Work on bytes end to end. Locate the <XML> block with byte regexes and pass the original bytes to lxml so it applies the declared encoding. Alternatively, decode with errors='replace' or a cp1252 fallback, then strip or rewrite the XML declaration's encoding before re-encoding. Add fixtures for Latin-1 and for a non-UTF-8 declaration.

#### SECPARSE-09 · MEDIUM · performance · effort S · confirmed

**Holdings tables ignored: Form 3 (and Form 4/5 holding rows) produce nothing yet are fetched and parsed**  
`src/insider_scanner/core/sec_ownership_parser.py:373`

Only nonDerivativeTransaction and derivativeTransaction are read. nonDerivativeHolding and derivativeHolding are skipped. A Form 3 consists only of holdings, so every 3 and 3/A always maps to zero trades. Even so, OWNERSHIP_FORM_TYPES (sec_ownership_document.py:28) and the discovery filters (sec_index.py:11, sec_bulk.py:36) include them. Each one costs a rate-limited SEC download plus a full parse during the multi-hour backfill, for no output.

*Suggestion:* Decide which you want. Either parse holdings into a separate record type (useful for initial positions), or drop 3 and 3/A from the discovery form set so they are never downloaded. If holdings are dropped, remove '3' and '3/A' from the three duplicated OWNERSHIP_FORMS sets together (see SECPARSE-19).

#### SECPARSE-06 · MEDIUM · bug · effort S · partially

**One malformed optional value discards every transaction in the filing**  
`src/insider_scanner/core/sec_ownership_parser.py:573`

_value_decimal, _value_date (line 600) and _parse_date_elem (line 619) raise SecOwnershipParseError for any unparseable lexeme, even in non-essential fields such as underlyingSecurityShares, exerciseDate or conversionOrExercisePrice. The whole filing is then counted as failed and quarantined. Scratch tests: '100,000' in transactionShares and an xs:date with a timezone suffix ('2026-06-12-05:00', '2026-06-12Z') both rejected the entire filing. Both are legal or common lexical variants. date.fromisoformat does not accept xs:date timezone suffixes.

*Suggestion:* Normalise lexemes before conversion: strip thousands separators and surrounding '$', and accept xs:date timezone suffixes by stripping the trailing Z or ±hh:mm. For non-essential fields, log the problem, record it in sec_detail_json and store None instead of raising. Keep hard failures for fields that define the row (transactionCode, transactionDate).

*Verifier:* Confirmed: date.fromisoformat rejects '2026-06-12-05:00' and '2026-06-12Z' (I tested this on 3.13), and any bad value raises for the whole filing. Timezone-suffixed dates are valid xs:date, so this is a real risk. The '100,000' example is unlikely in practice: EDGAR validates ownership XML against the XSD on submission, and a comma is not a valid xs:decimal.

#### SECPARSE-11 · MEDIUM · bug · effort S · confirmed

**Data-quality checks in mapping reject the whole filing and are re-run per transaction**  
`src/insider_scanner/core/sec_trade_mapping.py:139`

A typo in one derivative row's exercise or expiration date, or a periodOfReport later than the index filing date (line 156-164), raises SecTradeMappingError. ownership_filing_to_trades then returns nothing, so all valid non-derivative purchases and sales in the filing are lost too. In sec_backfill.py the sink has already set reached_sink, so the accession is checkpointed as completed and never retried._validate_common_dates is a filing-level check, yet it runs inside the per-transaction loop. It runs N times for N rows and never runs for filings with zero transactions.

*Suggestion:* Turn these into per-row warnings: keep the row, flag it in sec_detail_json (e.g. 'warnings': [...]) and log once. If you keep the period check, run it once before the loops.

#### SECPARSE-M2 · MEDIUM · bug · effort M · verifier-found

**Derivative rows are merged into Buy/Sell/Exercise with no consumer filtering is_derivative, so option exercises count twice**  
`src/insider_scanner/core/sec_trade_mapping.py:418`

An option exercise (code M) shows up as a derivative row disposing of the options and as a non-derivative row acquiring the stock. Both map to trade_type 'Exercise' and are emitted as separate InsiderTrades. Derivative P/S rows (buying or selling options or warrants) become 'Buy'/'Sell', with shares counting derivative units and price being the premium. grep shows nothing outside models, schema and merger reads is_derivative. price_chart.py:197 and table_style.py:152 treat 'Exercise' as a buy type, so each exercise is plotted or counted twice and option trades mix into stock Buy/Sell feeds and value totals.

*Suggestion:* Either do not give derivative rows the Buy/Sell/Exercise trade types (map them to 'Other' or a separate type), or make the feed, aggregation and chart queries filter on is_derivative by default. At minimum, keep only one side of an M/C/X exercise pair when counting.

#### SECPARSE-18 · LOW · bug · effort S · partially

**The DTD/ENTITY byte scan rejects harmless filings that only mention '<!DOCTYPE' in CDATA or comments**  
`src/insider_scanner/core/_sec_xml.py:83`

The regex matches anywhere in the payload, not just in the prolog. A footnote or remark containing literal '<!DOCTYPE' or '<!ENTITY' inside CDATA or a comment (for example pasted HTML) causes the whole filing to be rejected as a security violation (verified with scratch test 'doctype-in-cdata'). lxml is already set up with resolve_entities=False and load_dtd=False, so a real DTD can only appear before the root element.

*Suggestion:* Scan only the prolog, i.e. the bytes before the first '<' that is not '<?' or '<!--'. Alternatively, keep the full scan for the strict path but check root.getroottree().docinfo.internalDTD after parsing to confirm.

*Verifier:* Reproduced: '<![CDATA[<!DOCTYPE html>]]>' in remarks raises SecXmlSecurityError. In practice it is almost unreachable, because escaped text (&lt;!DOCTYPE) does not match the byte regex, and CDATA or comments in EDGAR ownership XML are very rare. Narrowing the scan to the prolog also weakens a deliberate defense-in-depth check, so the trade-off is not clearly worth it.

#### SECPARSE-04 · LOW · performance · effort S · confirmed

**guard_xml_tree is a slow pure-Python DFS that encodes every text node**  
`src/insider_scanner/core/_sec_xml.py:106`

For every element (whitespace-only tails included) the walk builds a tuple, runs a generator, and creates a new UTF-8 bytes object just to measure its length. In the profile this is the largest single CPU cost in the parse path (about 0.2 ms per call on a 3.4 KB document; extend plus genexpr alone is about 570k calls per 3000 filings). The depth check largely repeats libxml2's own depth cap, which applies because huge_tree=False.

*Suggestion:* Iterate with root.iter() at C speed for the element count. Count chars with len(text) and only compute exact UTF-8 bytes when chars*4 could exceed the limit. Compute depth cheaply, for example with a single XPath max-depth check or by tracking depth only when the element count is high. Together with SECPARSE-03 this removes most of the guard overhead.

#### SECPARSE-23 · LOW · simplification · effort S · confirmed

**Dead code in edgar.py: four unused URL constants and two unused fetch helpers**  
`src/insider_scanner/core/edgar.py:19`

No code under src/ references EDGAR_COMPANY_SEARCH, EDGAR_CIK_LOOKUP, EDGAR_FULL_TEXT_SEARCH or EDGAR_FILING_BASE. fetch_filings_page (204) and fetch_company_info (227, together with EDGAR_SUBMISSIONS) are also unused in src. fetch_company_info pads with cik.zfill(10) and skips the module's own normalize_cik validation.

*Suggestion:* Delete the unused constants and helpers, or if fetch_company_info is kept, route it through normalize_cik.

*Also reported as:* ARCH-27

#### SECPARSE-22 · LOW · performance · effort S · confirmed

**resolve_cik_from_json re-reads and re-parses the ~1 MB tickers file and scans it linearly on every call**  
`src/insider_scanner/core/edgar.py:112`

Each lookup (secform4.py:53 per scanned ticker, the GUI, the CLI) reads the cached file from disk, parses about 10k entries with json.loads, and walks them all. Multi-ticker scans repeat this for every ticker. The broad 'except Exception' also hides programming errors.

*Suggestion:* Build a ticker->CIK dict once, memoised with functools.lru_cache keyed on the file's mtime or a 24h TTL. Narrow the exception to network and JSON errors.

#### SECPARSE-24 · LOW · bug · effort S · confirmed

**build_edgar_url_for_trade does not URL-encode names or tickers**  
`src/insider_scanner/core/edgar.py:246`

Insider names such as 'SMITH & CO LLC', "O'NEIL", names containing '#', or non-ASCII names are inserted raw into the query string. '&' splits the q parameter, so the verification link the GUI opens (scan_tab.py:466, 557) searches for the wrong text. The fallback date range 2020-01-01..2030-12-31 is hard-coded.

*Suggestion:* Build the query with urllib.parse.urlencode({'q': f'"{name}" "{ticker}"', 'forms': '4', ...}) and derive the fallback range from today's date.

#### SECPARSE-M3 · LOW · bug · effort S · verifier-found

**The GUI verification link opens the EDGAR full-text-search JSON API, and the fallback window ends before the filing**  
`src/insider_scanner/core/edgar.py:256`

efts.sec.gov/LATEST/search-index is the backend JSON endpoint for EDGAR full-text search, not the human UI (<www.sec.gov/edgar/search/#/q=>...). The 'verify' link that scan_tab.py opens therefore shows raw JSON. Also, when filing_date is missing the window is [first of month, trade_date], but a Form 4 is filed on or after the trade date (up to 2 business days later, often more), so the window can exclude the filing.

*Suggestion:* Build the link to <https://www.sec.gov/edgar/search/#/q=...&forms=4&dateRange=custom&startdt=...&enddt=>..., with urlencoded parameters. When only trade_date is known, extend enddt to trade_date plus a few weeks.

#### SECPARSE-10 · LOW · performance · effort S · partially

**Pre-XML (pre-2003) and non-XML ownership filings pay a full lxml parse before failing, with an unclear reason**  
`src/insider_scanner/core/sec_ownership_document.py:197`

When a <TYPE>4 block has no <XML> wrapper, which is the case for every Form 4 filed before the June 2003 XML mandate, the raw text or HTML body is handed to the XML guards and lxml. It then fails as 'not well-formed', is counted as a parse failure and is quarantined. A full-history backfill contains many such filings. Each needs a network fetch plus a wasted parse, and they are reported as errors, not as 'not machine-readable'.

*Suggestion:* If there is no <XML> wrapper and the text does not start with '<?xml' or '<ownershipDocument', raise NonOwnershipDocumentError straight away, as a distinct, non-quarantined skip category. Better, filter by filing date or by primary-document extension at discovery so these filings are never downloaded.

*Verifier:* Line 197 does fall back to the raw TEXT body when there is no <XML> wrapper. The result is a generic 'not well-formed' SecOwnershipDocumentError, counted as failed. Per the sec_backfill module docstring, such filings are re-fetched on every resume because they never reach the sink. The volume is smaller than implied, because most pre-mid-2003 Forms 3/4/5 were paper filings. The parse itself is cheap; the cost is the repeated fetch.

#### SECPARSE-16 · LOW · bug · effort S · confirmed

**Transaction-code categories mislabel W and Z as gifts and leave out several codes**  
`src/insider_scanner/core/sec_ownership_parser.py:76`

Under the SEC Form 4 code table, W means acquisition or disposition by will or the laws of descent, and Z means deposit into or withdrawal from a voting trust. Neither is a gift. D (disposition to the issuer), E/H (expirations), I (discretionary), J (other), K (equity swap), L (small acquisition), U (tender) and V (voluntary report) all collapse to 'other'. Today category only feeds trade_type (everything else becomes 'Other'), but the field is public API and invites misuse.

*Suggestion:* Correct the mapping (W -> inheritance, Z -> trust, D -> disposition_to_issuer, E/H -> expiration, etc.) and document the code table next to _CATEGORY_MAP.

#### SECPARSE-03 · LOW · performance · effort M · partially

**Each filing is decoded, encoded, guarded and lxml-parsed twice**  
`src/insider_scanner/core/sec_ownership_parser.py:201`

extract_ownership_document already runs _validate_ownership_xml: encode, pre-parse guard, fromstring, guard_xml_tree, root check (sec_ownership_document.py:231-246). It then throws the tree away and keeps only a str. parse_ownership_document repeats every step. Profiling 3000 iterations on tests/fixtures/sec_form4_submission.txt gave guard_xml_tree 1.21 s of 3.0 s total (6000 calls, two per filing). The combined cost is about 1.15 ms of CPU per small filing, which adds up to hours of CPU over a multi-million-filing backfill.

*Suggestion:* Parse once. Have the extraction step return the validated bytes and the parsed root, for example a private field on OwnershipDocument or a combined extract_and_parse() used by process_filing_row. Keep the standalone parse_ownership_document re-validation for external callers, but skip it when it receives a pre-validated document from the same policy.

*Verifier:* The double encode, guard and parse is real (sec_ownership_document.py:231-242 and sec_ownership_parser.py:201-210). My timing on the 3.4 KB fixture with Python 3.13 was about 0.21 ms to extract and 0.29 ms to parse, roughly 0.5 ms per filing. A rate-limited SEC fetch costs 100 ms or more, so parsing is under 1% of wall time. This is not a medium contributor to the slow backfill.

#### SECPARSE-08 · LOW · bug · effort S · partially

**Missing issuer/reportingOwner (or a namespaced root) yields blank trades or an empty 'successful' parse**  
`src/insider_scanner/core/sec_ownership_parser.py:256`

The parser treats every structural element as optional. A document without <issuer> or <reportingOwner> still yields trades, with ticker '', cik_issuer '' and insider_name '' (mapping line 383-395), and these are persisted as real rows. In addition, _validate_ownership_xml (sec_ownership_document.py:244) accepts a namespaced root by checking only localname, while every find() here uses unqualified tags. A default-namespaced document therefore parses to a filing with no issuer, owner or transactions. Scratch test 'ns' gave Issuer(None, None, None) and []. That filing is counted as 'parsed' and checkpointed as completed, so it is never retried.

*Suggestion:* Require issuer/issuerCik and at least one reportingOwner/rptOwnerCik; raise SecOwnershipParseError otherwise. Either reject namespaced roots in _validate_ownership_xml (require root.tag == 'ownershipDocument' exactly) or strip namespaces before the find() calls, so validation and extraction agree.

*Verifier:* The inconsistency is real. Validation checks only the root localname (sec_ownership_document.py:244) while the finds use unqualified tags, and a namespaced document parsed to Issuer(None,None,None) with no transactions. However, the ownership schema requires issuer and reportingOwner, and real EDGAR ownership XML has no namespace, so this is very unlikely with SEC data. Requiring the issuer CIK is still cheap hardening.

#### SECPARSE-14 · LOW · bug · effort S · confirmed

**Placeholder trading symbols ('NONE', 'N/A', 'NA') are stored as real tickers**  
`src/insider_scanner/core/sec_ownership_parser.py:259`

Filers of non-listed issuers often write NONE, N/A or similar in issuerTradingSymbol. These pass through unchanged, are upper-cased by persistence, and become one shared ticker 'NONE'. That merges unrelated private issuers on ticker-keyed views and price lookups (repositories.py:122 filters by exact ticker).

*Suggestion:* Normalise the symbol in the parser: strip, upper-case, map known placeholders (NONE, N/A, NA, '-', 'NOT APPLICABLE') to None, and when a comma or space separated list appears, take the first token.

#### SECPARSE-17 · LOW · bug · effort S · confirmed

**Alternative value fields in the schema are ignored**  
`src/insider_scanner/core/sec_ownership_parser.py:454`

The schema offers a choice for several fields: transactionShares or transactionTotalValue, sharesOwnedFollowingTransaction or valueOwnedFollowingTransaction, and underlyingSecurityShares or underlyingSecurityValue. These value variants are used for notes, debt and other non-share instruments. Only the share variants are read, so those rows silently end up with shares/value 0.0 and no underlying amount.

*Suggestion:* Read the *Value alternatives as well, keep them in separate fields (or in sec_detail_json), and use transactionTotalValue as the trade value when shares are absent.

#### SECPARSE-20 · LOW · bug · effort S · partially

**Remarks read only .text while footnotes use itertext()**  
`src/insider_scanner/core/sec_ownership_parser.py:512`

_parse_footnotes correctly flattens mixed content with itertext() (line 311). _remarks reads only el.text, so remarks containing any child markup are cut off at the first child element, or come back None when they start with markup.

*Suggestion:* Use the same whitespace-normalised itertext() join as footnotes, through a shared helper.

*Verifier:* Reproduced: '<remarks>Hello <b>bold</b> tail</remarks>' gave 'Hello'. The ownership schema defines remarks as plain string content, so child markup does not occur in EDGAR-validated filings. It is only a consistency fix.

#### SECPARSE-07 · LOW · bug · effort S · partially

**Decimal accepts NaN/Infinity/sNaN; sNaN escapes as a non-recoverable exception**  
`src/insider_scanner/core/sec_ownership_parser.py:574`

Decimal('NaN'), Decimal('Infinity') and Decimal('1E+99999') are all under the 128-char numeric limit and are accepted (scratch test 'nan' parsed shares=Decimal('NaN')). NaN and inf then reach the shares, price and value floats in the database. Decimal('sNaN') is worse: sec_trade_mapping computes txn.shares * txn.price_per_share and float(txn.shares), which raise decimal.InvalidOperation or ValueError (verified). Neither is a SecTradeMappingError, and neither is in _RECOVERABLE in services/sec_downloads.py. One crafted or corrupt filing therefore aborts the whole backfill or daily loop instead of being counted as a failure.

*Suggestion:* After conversion, reject non-finite values (if not d.is_finite(): raise SecOwnershipParseError) and optionally cap the exponent range. As defense in depth, catch ArithmeticError/ValueError in ownership_filing_to_trades and re-raise as SecTradeMappingError.

*Verifier:* Mechanism verified. A price of 'sNaN' parses, then mapping raises ValueError ('cannot convert signaling NaN to float'). Neither sink catches it (sec_backfill catches only SecTradeMappingError, process_filing_row only_RECOVERABLE and PersistenceError), so the run aborts. In practice this is very unlikely: EDGAR schema validation rejects NaN, Infinity and sNaN as xs:decimal, and content comes only from sec.gov. It is a defense-in-depth fix.

#### SECPARSE-M4 · LOW · bug · effort S · verifier-found

**insider_title drops the role flags when officerTitle is present, and the isOther description is never parsed**  
`src/insider_scanner/core/sec_trade_mapping.py:78`

A reporting owner who is both a director and an officer (very common for CEOs and chairs) is stored only as e.g. 'CEO', so the Director role is lost for any title-based filtering or display. The parser also never reads reportingOwnerRelationship/otherText (sec_ownership_parser.py:285-289), so isOther owners only get the label 'Other', with no description.

*Suggestion:* Build the title as the officer title plus the other active flags (e.g. 'CEO, Director, 10% Owner'). Parse otherText in _parse_reporting_owner and use it in place of 'Other'. Consider storing the flags as separate columns or in sec_detail_json.

#### SECPARSE-12 · LOW · bug · effort M · confirmed

**Missing shares, price or holdings stored as 0.0, which looks the same as a real zero**  
`src/insider_scanner/core/sec_trade_mapping.py:252`

Prices often appear only in a footnote (weighted-average ranges, 'see footnote'). The parser correctly returns None, but mapping turns that into price=0.0 and value=0.0, the same as a genuine $0 award or gift. Value-threshold filters, alerts and analytics then treat these as zero-value trades. The footnote_ids for the price element are not kept apart from the others, so the reason cannot be recovered.

*Suggestion:* Make the InsiderTrade/us_trades numeric fields nullable, or add explicit flags (price_missing, value_missing). Record which footnotes are attached to transactionPricePerShare in sec_detail_json so a weighted-average or range note can be shown.

#### SECPARSE-19 · LOW · simplification · effort S · confirmed

**Duplicated builders and validators, dead row_id checks, and the ownership form set defined three times**  
`src/insider_scanner/core/sec_trade_mapping.py:296`

_build_non_derivative_trade and _build_derivative_trade (239-350) are about 55 identical lines that differ only in is_derivative and the detail builder. *build**_detail (183-231) and *validate** (114-148) repeat the same pattern. The empty-row_id checks (120, 134) can never fire because the parser always creates a non-empty row_id. The form set {'3','3/A','4','4/A','5','5/A'} is defined separately in sec_ownership_document.py:28, sec_index.py:11 and sec_bulk.py:36, so a change such as dropping Form 3 must be made in three places.

*Suggestion:* Write one _build_trade(txn, is_derivative, extra_detail) and one shared-context dataclass in place of the ten positional parameters. Drop the dead row_id checks. Export a single OWNERSHIP_FORM_TYPES constant and import it in sec_index and sec_bulk.

#### SECPARSE-15 · LOW · bug · effort S · partially

**Company fallback can take the reporting owner's name from the index row**  
`src/insider_scanner/core/sec_trade_mapping.py:384`

The master index lists each ownership filing under both the issuer CIK and every reporting-owner CIK, each with its own archive path, so sec_index's archive_path de-duplication does not collapse them. When issuerName is empty and the row being processed is the owner's row, company becomes the insider's name, e.g. 'COOK TIMOTHY D'.

*Suggestion:* Use the fallback only when index_row.cik equals the normalised issuer CIK. Otherwise leave it empty, or resolve the name from the issuer CIK.

*Verifier:* The fallback at line 384 only fires when issuerName is empty. The schema requires issuerName, so this is rare. The premise is true and is a bigger problem than the reviewer says: the master index lists each filing under the issuer and every owner CIK, with distinct archive paths, so the same filing is processed once per CIK (see SECPARSE-M1).

#### SECPARSE-13 · LOW · practice · effort S · partially

**Issuer and owner CIKs are stored unnormalised while the index CIK is zero-padded**  
`src/insider_scanner/core/sec_trade_mapping.py:394`

sec_index normalises CIKs with edgar.normalize_cik (10-digit zero pad), but the XML values go into us_trades.cik_issuer/cik_insider exactly as written. Filers use both '0000320193' and '320193', and the parser does not check for digits. Joins or filters on cik_issuer (it is indexed: ix_us_trades_cik_issuer) and entity pages will miss or split the same company.

*Suggestion:* Apply edgar.normalize_cik in_parse_issuer and_parse_reporting_owner (or in mapping). On ValueError, either raise SecOwnershipParseError or store '' with a logged warning. Consider cross-checking the issuer CIK against index_row.cik.

*Verifier:* Lines 394-395 do store the XML CIK without normalising it, and the parser does not check for digits. The claim that filers use both '0000320193' and '320193' is unverified. EDGAR-generated ownership XML consistently uses the 10-digit zero-padded form, so this is defensive normalisation, not an observed split.

### US services, models and merging (USSVC) — 24 items

> The US service layer is cleanly split into adapters, coverage-gated scan services, SEC daily ingestion and a comparison report. Its weak spot is that coverage bookkeeping trusts every adapter result without checking it. The openinsider and secform4 scrapers turn network errors, unresolved CIKs, layout changes and openinsider's 100-row cap into a normal-looking (possibly empty) list. UsScanService/CongressScanService then mark that interval as permanently covered, and it also marks today and future dates. As a result, data can go missing quietly and is never fetched again. The unbounded ticker scan (the GUI/CLI default) never scrapes the requested ticker. The Congress query uses exact name equality, so trades it just fetched are not returned. Cross-source dedup and the SEC comparison use keys that collapse distinct transactions (exercise and sale of the same quantity) while splitting real duplicates (openinsider signs sales negative, and probably stores filing timestamps that parse_date cannot read). On performance, the SEC sink does one BEGIN IMMEDIATE transaction per filing, with a SELECT per row and two json.dumps calls per existing row. The bulk backfill uses the same pattern, which is a major contributor to the slow full-history import. The legacy importer has the same per-record commit problem. Smaller issues: dead parameters (clock, the ticker branch in latest), a checkpoint that duplicates coverage, hand-written serialization boilerplate, and alert evaluation that can miss new rows.

#### USSVC-06 · HIGH · bug · effort M · confirmed

**Fuzzy dedup key ignores trade type and sign, so it merges distinct trades and misses real duplicates**  
`src/insider_scanner/core/merger.py:25`

The same key is duplicated in persistence/mappings.py:79-87 (us_canonical_key for non-SEC rows), so it controls both GUI merging and DB identity. (a) trade_type and price are not part of the key. An option exercise (M) and the same-day sale (S) of the same quantity, which is very common, collapse into one row; merge_trade_pair keeps only one trade_type, so data is lost. The same happens to two secform4 rows of N shares at different prices. (b) openinsider reports sales with negative Qty (parse_number keeps the '-'), while secform4 reports positive quantities, so the same sale from both sources gets buckets -5000 and 5000 and is never merged. (c) round() uses banker's rounding: 104 and 105 both bucket to 100 but 106 goes to 110. The module docstring promises 'within 1%', which the code does not do.

*Suggestion:* Make one shared key function (used by both merger and mappings) that includes trade_type and abs(shares). Treat the bucketing as a separate fuzzy-matching step, or match on exact shares plus price. Delete the duplicated key logic in mappings.py, and fix the docstring. Changing the key means re-keying existing rows in a migration.

#### USSVC-02 · HIGH · bug · effort S · confirmed

**Scraper failures return [] and the interval is still marked covered for good**  
`src/insider_scanner/core/openinsider.py:65`

openinsider.scrape_ticker/scrape_latest and secform4.scrape_ticker (lines 54-56 for an unresolved CIK, 63-65 for fetch errors) catch every exception and return an empty list. UsScanService.scan (us.py:114-115) then upserts nothing and calls coverage.add for the gap. The ScanError path in us.py:104-107 is never reached. A timeout, HTTP 429/5xx, or a CIK lookup failure therefore permanently records that ticker/source/date range as covered with no data, and later scans never retry it. In_refresh_latest the refresh marker is set in the same way.

*Suggestion:* Let fetch and CIK-resolution errors propagate from the scrapers, or return a result object that distinguishes 'fetched, zero rows' from 'failed', so that the service raises ScanError and skips coverage.add. Keep the log line, but re-raise.

*Also reported as:* USSVC-03, ARCH-02

#### USSVC-05 · HIGH · bug · effort S · confirmed

**Filing timestamps from openinsider probably parse to None, which hides rows from date-bounded queries**  
`src/insider_scanner/core/openinsider.py:202`

On the live openinsider screener, the Filing Date column appears to show a timestamp ('2023-12-15 20:18:05'). The test fixture only uses a bare date, so this is not covered by tests. parse_date tries date.fromisoformat, which I confirmed raises 'Invalid isoformat string' on that value, and then splits on '-', where int('15 20:18:05') fails, so it returns None. Every repository query filters on filing_date (repositories.py:123-126, 166-169) and drops NULLs, so openinsider rows would never be returned by bounded scans or by latest-in-range queries, would sort last in the latest feed, and would disappear from the SEC comparison window.

*Suggestion:* Check one live openinsider page to confirm the format. If confirmed, cut the cell at the first space (or try datetime.fromisoformat(...).date()) before parsing, and add a test fixture that matches the live format, including the timestamp and the negative Qty/Value used for sales.

*Also reported as:* USSVC-25

#### USSVC-M1 · HIGH · bug · effort S · verifier-found

**openinsider sales (negative Qty/Value) violate us_trades CHECK constraints and abort the whole upsert**  
`src/insider_scanner/core/openinsider.py:203`

parse_number keeps a leading '-', and openinsider shows sales as '-12,345' / '-$1,234,567'. us_trades has CheckConstraint('shares >= 0') and ('value >= 0') (schema.py:84-86), created by migrations via v1_metadata.create_all. Nothing in us_to_values or InsiderTrade normalizes the sign. One sale row in a batch raises IntegrityError, which becomes PersistenceError, and the single IMMEDIATE transaction rolls back every row of that openinsider fetch. The exception is not a ScanError, so it propagates to the GUI or CLI as a crash. The test fixture only uses positive quantities, which hides this.

*Suggestion:* In the openinsider parser, store abs() of shares and value, and derive direction from trade_type (or acquired_disposed). Add a fixture row that matches the live format with a negative Qty/Value. Consider failing per row instead of per batch.

#### USSVC-07 · HIGH · bug · effort M · confirmed

**Congress query uses exact official_name equality, so fetched trades are not returned**  
`src/insider_scanner/services/congress.py:85`

The scrapers match names fuzzily (House search_filings does a case-insensitive substring match on 'Last First'/'First Last'), but they store official_name as f"{prefix} {first} {last} {suffix}" (for example 'Hon. Nancy Pelosi'). The repository then filters with official_name == official (repositories.py:256), using the user's raw input. Searching 'Pelosi', 'pelosi nancy' or 'Pelosi Nancy' (the format of the default congress list) fetches and stores trades, records coverage, and then returns zero rows. The coverage identifier is also case-sensitive, so 'pelosi' and 'Pelosi' are scanned separately.

*Suggestion:* Normalize the identifier (casefold, collapse whitespace, strip honorifics) for both the coverage key and the query. Query with a normalized official_name column, or with a case-insensitive LIKE on name tokens, so the results match what the scrapers matched.

*Also reported as:* CONGRESS-01

#### USSVC-M5 · HIGH · bug · effort S · verifier-found

**SEC daily ingest permanently marks today and future dates as covered when the daily index is not yet published**  
`src/insider_scanner/services/sec_daily.py:258`

_process_date treats a 404 on the daily master index (SecFilingNotFoundError) as 'date fully handled' (sec_downloads.py:201-203), so on_progress fires and this recorder marks the date covered. `sec-daily` with no --date defaults to date.today() (cli.py:442). SEC publishes a day's index only after that day ends, so running the command during the day (or giving a future end in sec-range) records the date as covered with zero filings, and later runs never retry it. The unused self._clock (sec_daily.py:128) was meant for exactly this kind of clamp.

*Suggestion:* Clamp the requested range to dates before clock() (or to the last business day whose index can exist). Do not record coverage for a 404 index on today or later. Record it only for past dates, such as weekends and holidays, where a missing index is expected.

#### USSVC-01 · HIGH · bug · effort S · confirmed

**Unbounded ticker scan (GUI/CLI default) never fetches the requested ticker**  
`src/insider_scanner/services/us.py:67`

When start_date and end_date are both None (the GUI scan tab when 'use dates' is unchecked, and `scan TICKER` in the CLI with no --since/--until), scan() only refreshes the global openinsider 'latest 200' feed (scrape_latest takes no ticker) and then queries the DB for the ticker. secform4 passes validate_sources but is silently ignored because it is not a latest adapter. So for a ticker never scanned before, the result is empty or stale even though the user selected sources that could fetch it.

*Suggestion:* For unbounded scans, either choose a default bounded window (for example the last N months, as CongressScanService does) and go through the coverage-gated adapter path, or call the per-ticker bounded adapters. Raise an error, rather than silently accepting, for sources that cannot serve the requested mode.

#### USSVC-04 · HIGH · bug · effort S · confirmed

**Coverage is recorded for today and future dates, so later filings are never fetched**  
`src/insider_scanner/services/us.py:115`

Gaps come straight from the requested interval, and nothing clamps them to the dates that are complete (persistence/coverage.py has no today logic). The GUI end date defaults to QDate.currentDate(), so every scan marks today as covered: Form 4s filed later the same day are never fetched for that ticker/source. If the user picks a future end date, those future days are marked covered permanently. CongressScanService (congress.py:163,190) defaults end_date to today and has the same problem.

*Suggestion:* Before calling coverage.add, clamp the recorded interval to end at clock().date() - 1 (or the last fully elapsed business day). The open tail should always be treated as a gap, refreshed through the TTL/refresh-state mechanism.

#### USSVC-10 · MEDIUM · bug · effort S · confirmed

**openinsider latest-in-range uses td=7 and filters by trade date, while the DB filters by filing date**  
`src/insider_scanner/core/openinsider.py:98`

scrape_ticker selects a custom range with fd=-1 (line 51). scrape_latest instead sends td=7, which in openinsider's screener seems to mean the 'last 7 days' preset, not a custom range, so tdr may be ignored. Even if the range is honoured, it filters by trade date, while UsScanService._latest_in_range then calls query_latest, which filters by filing date. A late filing whose trade date is before the range is never fetched, and rows fetched by trade date may fall outside the filing-date window that is returned.

*Suggestion:* Use fd=-1&fdr=<range> (filing-date custom range) in scrape_latest so it matches scrape_ticker and the repository query. Factor URL building into one helper with urllib.parse.urlencode, which also URL-encodes the ticker.

#### USSVC-14 · MEDIUM · bug · effort S · confirmed

**Congress flagging on every query: false positives for empty or short names, disk read and O(N*M) scan per call**  
`src/insider_scanner/core/senate.py:100`

flag_congress_copies (services/common.py:83-85) runs this on every US query result. When insider_name is empty, which both scrapers allow when shares != 0, '' in norm_member is always true, so the trade is flagged as the first member. Short or partial names ('dan', 'bill') also match by substring. _normalize_name blindly removes ' sr', ' jr', ' ii' anywhere, so 'john srinivasan' becomes 'johninivasan'. Each call also re-reads congress_members.json from disk and copies every trade. As the SEC tables grow, a ticker query returns thousands of rows checked against every member.

*Suggestion:* Skip empty names. Require token-level matches (the set of last and first name tokens) instead of raw substring containment, and strip suffixes only as whole trailing tokens. Cache the member lookup, invalidated by file mtime, in a module-level helper used by flag_congress_copies.

*Also reported as:* CONGRESS-10, ARCH-15

#### USSVC-15 · MEDIUM · bug · effort M · confirmed

**Form 4/A amendments are stored alongside the originals, so transactions are double counted**  
`src/insider_scanner/persistence/mappings.py:77`

The SEC canonical identity is (accession, row_id). An amendment has a new accession and restates the original's rows, so both are inserted. merge_trade_pair explicitly does not coalesce SEC fields (merger.py:75-81), and nothing in the US services, queries or comparison filters out superseded originals. us.scan, latest and SecComparisonService therefore count both the original and the restated transactions.

*Suggestion:* When an amendment is ingested, mark original rows from the same issuer/owner/period_of_report as superseded (new column), or exclude them at query time. Do not count superseded rows in sec_comparison.

*Also reported as:* PERSIST-M2

#### USSVC-11 · MEDIUM · bug · effort M · confirmed

**Alert evaluation can miss new records: top-N candidate window sorted by trade date, and 'mark all seen' uses now()**  
`src/insider_scanner/services/alerts.py:78`

Candidates are the first 200 rows in feed sort order (transaction/filing date), and recent_by_issuer/recent_by_person (line 68) take 100 rows ordered by transaction_date. The created_at watermark filter is applied in Python afterwards (line 103). Rows inserted recently with older trade dates, which is exactly what a backfill, a late filing or a legacy import produces, fall outside that window and never alert. Separately, the GUI marks rules seen with datetime.now(UTC) rather than the newest created_at it showed, so rows ingested between evaluation and the click are skipped without being shown.

*Suggestion:* Push `created_at > last_seen_at` into the SQL query and order by created_at, with a cap and an 'and N more' count. Have evaluate_alerts return each rule's max created_at among its hits, and use that value as the new watermark.

#### USSVC-12 · MEDIUM · bug · effort S · confirmed

**Legacy importer accepts the 'edgar' source but rejects 'sec_edgar'; imported 'edgar' rows can never be queried**  
`src/insider_scanner/services/importer.py:22`

The SEC pipeline writes source='sec_edgar' (sec_trade_mapping.py:276), and save_scan_results exports that value, so re-importing the app's own exports fails with 'invalid source'. Rows imported with 'edgar' are stored, but UsScanService.validate_sources only knows secform4/openinsider/sec_edgar, and repository queries filter on provenance, so those rows never appear in any scan or latest view.

*Suggestion:* Accept 'sec_edgar' and map the legacy alias 'edgar' to it, the same way_EU_SOURCE_ALIASES handles EU sources. Keep the source names in one shared constant used by the importer, services and mapping.

#### USSVC-13 · MEDIUM · performance · effort S · confirmed

**Legacy import commits once per record**  
`src/insider_scanner/services/importer.py:326`

Each record is upserted in its own BEGIN IMMEDIATE/COMMIT. A 50 MB file (the allowed maximum) holds on the order of 100k records, which means 100k fsyncs. On Windows that takes minutes, and it holds the write lock in short bursts that compete with GUI scans.

*Suggestion:* Validate all records first, collecting per-record errors, then upsert the valid records per type in chunks (for example 1000) inside a single transaction. If a chunk hits a persistence error, fall back to per-record upserts for that chunk only, to keep the per-record error reporting.

#### USSVC-09 · MEDIUM · bug · effort M · partially

**SEC comparison fingerprint reports mismatches for matching trades**  
`src/insider_scanner/services/sec_comparison.py:216`

SEC rows have positive shares and value = shares*price to the cent (sec_trade_mapping.py). openinsider rows carry negative shares/value for sales and whole-dollar values, and openinsider combines a filing's same-day rows into one line with a weighted price. SEC also emits derivative and 'Other' (F/G/A code) rows that the legacy sources do not show. With exact sign-sensitive shares and cent-exact value, nearly every openinsider sale, and every multi-row filing, lands in both SEC-only and legacy-only, so the validation report says the sources disagree when they do not.

*Suggestion:* Fingerprint on abs(shares) and drop value, or compare it within a tolerance. Optionally group SEC rows by (accession, trade_date, trade_type) before comparing with openinsider, and filter SEC rows to non-derivative P/S/M codes (or let the caller choose) so both sides cover the same transaction types.

*Verifier:* The cent-exact value comparison (SEC uses shares*price while secform4/openinsider show whole-dollar totals), the unaggregated SEC rows and the derivative/Other rows do cause false mismatches. The sign part is wrong: negative openinsider shares/value cannot be persisted, because us_trades has CHECK shares>=0 and value>=0 (schema.py:84-86), so no negative legacy rows exist for the comparison to read.

#### USSVC-08 · MEDIUM · performance · effort M · partially

**SEC persist sink opens one IMMEDIATE transaction per filing, which slows bulk ingest**  
`src/insider_scanner/services/sec_daily.py:243`

Each parsed filing gets its own upsert() call, which runs BEGIN IMMEDIATE, then for every row a SELECT by canonical_key, us_from_row, merge_trade_pair (two json.dumps of the full record) and an INSERT or UPDATE, then COMMIT (an fsync on Windows). sec_backfill.py:148 uses the same pattern. Across the full history of Form 4s, that means millions of commits plus per-row round trips. This is likely a major part of why processing the downloaded all-history batch runs for hours. Re-runs are no cheaper, because every row still goes through select + merge just to be skipped.

*Suggestion:* Buffer the mapped trades in the sink and flush every N filings (for example 500-5000 rows), or once per date, in a single transaction. Coverage stays per date, so the flush must happen before the date's on_progress fires. For SEC-keyed rows, which are deterministic and immutable per accession/row_id, use a bulk INSERT ... ON CONFLICT(canonical_key) DO NOTHING, or a single SELECT canonical_key IN (...) per batch, instead of fetching and merging row by row. Consider PRAGMA synchronous=NORMAL with WAL for bulk runs.

*Verifier:* The per-filing upsert (BEGIN IMMEDIATE, one SELECT per row, merge, COMMIT, with no WAL and default synchronous) is real at sec_daily.py:243 and sec_backfill.py:148. Calling it 'a major contributor' to the slow full-history import is overstated. Each filing first needs a rate-limited HTTP fetch (8 req/s, so at least 125 ms) plus a cache promote, and the backfill rewrites its whole checkpoint after every filing (USSVC-M2), which dwarfs one SQLite commit.

#### USSVC-18 · LOW · performance · effort S · confirmed

**merge_trade_pair serializes both records to JSON on every existing-row upsert**  
`src/insider_scanner/core/merger.py:60`

UsTradeRepository.upsert calls merge_trade_pair for every row that already exists. That runs to_dict() and json.dumps(sort_keys=True) twice, including the potentially large sec_detail_json, just to pick a tie-breaker, and then compares merged == existing. On re-ingests or overlapping backfills, where almost every row is a skip, this is pure overhead in the hottest loop.

*Suggestion:* Return early when the incoming trade equals the existing one (for SEC rows, compare document_sha256 or a stored content hash) before ranking. Compute the JSON tie-breaker only when the richness scores are equal.

#### USSVC-23 · LOW · simplification · effort S · confirmed

**InsiderTrade/CongressTrade to_dict/from_dict re-list every field by hand**  
`src/insider_scanner/core/models.py:52`

About 28 fields are written out twice in InsiderTrade and 16 twice in CongressTrade, while persistence/mappings.py already uses asdict and **dataclass_fields**. Every new field (the 13 SEC fields were added this way) must be added in four places. A missed field means it is silently dropped on export or import, and the merger's ranking (which uses to_dict) would ignore it.

*Suggestion:* Build to_dict from dataclasses.fields() with one date-to-ISO conversion. Build from_dict from the field types (parse date-typed fields, coerce float, require bool), keeping today's defaults.

#### USSVC-16 · LOW · bug · effort S · partially

**Scrapers fall back to the 'largest table' when the expected table is missing, so layout changes produce garbage that gets covered**  
`src/insider_scanner/core/openinsider.py:137`

openinsider (137-144) and secform4 (101-113) pick the table with the most rows when the known table is missing (site redesign, captcha or rate-limit page). Column mapping then matches loosely ('type', 'title', 'price'), and rows are kept whenever any name text exists, so unrelated tables can produce plausible-looking junk trades, or an empty list. Either way the service records coverage, and the problem stays hidden until someone inspects the data.

*Suggestion:* Treat a missing known table, or missing required headers (trade date and ticker or insider), as a parse error and raise, so that the service raises ScanError and does not record coverage. Log the page title or the first bytes to help diagnose.

*Verifier:* The largest-table fallback exists in both scrapers. However, on an error, captcha or redesign page, col_map would mostly fail to map name/qty, and rows are dropped unless the name is set or shares are non-zero. The realistic outcome is an empty list, which is the same issue as USSVC-02, rather than plausible junk trades.

#### USSVC-21 · LOW · bug · effort S · partially

**Importer does not validate other boolean fields and fails on UTF-8 BOM files**  
`src/insider_scanner/services/importer.py:171`

Only is_congress is type-checked. InsiderTrade.from_dict passes is_amendment and is_derivative through unchanged, so a JSON value of "false" becomes a truthy string that ends up in a Boolean column. Numeric strings are coerced by float(), but null raises TypeError, so the whole record is rejected. In addition, path.read_text(encoding='utf-8') (line 299) rejects JSON saved with a BOM, which is common for files edited on Windows, and reports the whole file as malformed_json.

*Suggestion:* Validate every boolean field (is_amendment, is_derivative) the same way as is_congress. Read with encoding='utf-8-sig'.

*Verifier:* The BOM case is real: read_text('utf-8') leaves '﻿' and json.loads fails, so the file is reported as malformed_json. A string 'false' for is_amendment/is_derivative is not stored: SQLAlchemy's strict Boolean processor raises StatementError, so the record is rejected as persistence_error instead of invalid_record. That is a misleading classification, not silent corruption.

#### USSVC-19 · LOW · simplification · effort S · confirmed

**Unused clock parameter and a checkpoint that duplicates coverage in SecDailyIngestionService**  
`src/insider_scanner/services/sec_daily.py:126`

clock is stored but never used. checkpoint_path is passed unchanged to every gap, and download_filings_in_range only resumes when the saved range exactly equals the request. Coverage already removes completed dates, so the gap range almost never matches a previous checkpoint. With several gaps, each one overwrites the previous gap's file, so the checkpoint adds file I/O without providing any resume.

*Suggestion:* Remove clock (or use it to clamp the end date to yesterday, see USSVC-04). Drop checkpoint_path from the daily service and rely on coverage for resume, or give each gap its own checkpoint file.

*Also reported as:* SECIO-M5

#### USSVC-17 · LOW · bug · effort S · partially

**continue_on_error=False is not honoured across coverage gaps**  
`src/insider_scanner/services/sec_daily.py:173`

With continue_on_error=False, the downloader stops the current gap at the first failed date. ingest_range does not check for that and moves straight on to the next gap, so the run keeps going past the first error, which the flag is meant to prevent. SecDownloadReport has no interrupted flag, so the service cannot tell a stopped gap from a finished one.

*Suggestion:* Add an `interrupted`/`stopped_on_error` field to SecDownloadReport. In ingest_range, break when it is set and continue_on_error is False, or when report.dates_completed < _day_count(gap).

*Verifier:* ingest_range ignores a gap that stopped on error and moves on to the next gap, and SecDownloadReport has no interrupted flag. However, no production caller sets continue_on_error=False (application.py and sec_daily both default it to True), so this is currently unreachable.

#### USSVC-24 · LOW · practice · effort M · confirmed

**One failing source aborts the whole multi-source scan and discards results already fetched**  
`src/insider_scanner/services/us.py:104`

If secform4 succeeds and openinsider raises, the secform4 rows are persisted, but the caller gets only an exception and no data. The GUI multi-ticker loop (scan_tab.py work()) then loses every ticker already scanned. CongressScanService (congress.py:181-184) behaves the same way.

*Suggestion:* Collect per-source failures and continue with the remaining sources and gaps. Return the queried rows together with a list of failed (source, gap) entries, or raise a ScanError subclass that carries the partial result, so the UI can show the data alongside a warning.

#### USSVC-20 · LOW · simplification · effort S · confirmed

**UsScanService.latest() has an unused ticker parameter that is ignored when no dates are given**  
`src/insider_scanner/services/us.py:133`

No caller (cli.py:121, gui/scan_tab.py:342) passes ticker. If one did without dates, the ticker would be silently ignored and the global latest feed returned. The asserts at lines 144-145 are also redundant after validate_range, and validate_range (common.py:58-61) checks the None case twice.

*Suggestion:* Remove the ticker parameter and its branch; callers that want a ticker should use scan(). Replace the asserts with the DateInterval that validate_range returns, and merge the duplicated None checks.

### Persistence layer (SQLite) (PERSIST) — 26 items

> The persistence layer is tidy and defensive: SQLAlchemy Core, typed UTC datetimes, ordered migrations that run in a single BEGIN IMMEDIATE transaction, parameterized SQL everywhere (LIKE input is escaped, so I found no injection risk), and atomic writes for the JSON state files. It was built for small data, though, and does not scale to a full SEC backfill. I benchmarked it on a copy of the real schema with synthetic rows in the scratchpad (scratchpad/persist/bench.py, alt.py, pool.py). Saving one filing the way the backfill does costs about 30 ms. That breaks down into a new SQLite connection per call (NullPool, about 2.4 ms each), the default rollback journal with synchronous=FULL (one fsync-heavy commit per filing), and about 1 ms per row of SQLAlchemy statement building plus a SELECT-then-INSERT for every row. Batching the writes measured about 59 us per row, roughly 17x faster. Switching to WAL with synchronous=NORMAL alone cut the per-filing cost from 29.8 ms to 10.7 ms. Over millions of Form 4 filings, the persistence cost alone comes to tens of hours. The read side has the same scaling problem. Every feed page counts, sorts and takes MAX over a UNION ALL of all trade tables, with no supporting indexes and with OFFSET paging (1.5-2.7 s per page at 300k rows; it grows linearly with row count). query_latest loads the whole table to return N rows (5.6 s at 300k). Correctness problems: SEC-native trades and scraped trades get different canonical keys, so the same trade is stored twice. The fuzzy key collapses an option exercise and a same-day sale into one row. Scan coverage marks today (and any future dates) as permanently covered. Several JSON state paths can crash the app at startup or silently lose user data.

#### PERSIST-07 · HIGH · bug · effort S · confirmed

**Scan coverage ignores covered_at, so a range ending today (or later) is permanently 'covered' and later filings are never fetched**  
`src/insider_scanner/persistence/coverage.py:135`

services/us.py:115 and congress.py:77 call coverage.add() with the whole requested gap after one fetch. The GUI's default end date is QDate.currentDate() (scan_tab.py:140, congress_tab.py:298). A scan at 09:00 therefore marks today as covered. A rescan at 18:00 finds no gap and never fetches the Form 4s filed later that day. The same happens for any future end date and, for Congress, for the 45-day disclosure lag. The schema stores covered_at (schema.py:242), but gaps() never reads it. add() also deletes and reinserts every interval, which resets covered_at to now and destroys the information that would be needed.

*Suggestion:* In CoverageRepository.add, clamp interval.end to min(end, today - settle_days), where settle_days is defined per source (about 2 for Form 4, about 45 for Congress), or skip recording when the clamped interval is empty. Alternatively, have gaps() treat any interval whose end_date >= covered_at.date() - settle_days as uncovered. Preserve covered_at when compacting, for example the minimum of the merged intervals.

#### PERSIST-05 · HIGH · performance · effort L · confirmed

**Every feed page does a full UNION ALL scan three times: COUNT, sort on unindexed columns, and MAX(created_at)**  
`src/insider_scanner/persistence/feed.py:478`

_projection() wraps us_trades, congress_trades and european_trades in a UNION ALL subquery. Sorting is on expressions such as `transaction_date IS NULL, transaction_date DESC, ..., key` (where key is computed as 'us:' || CAST(id)). No index covers trade_date, insider_name or created_at, so each page builds the full union, sorts all of it in a temporary B-tree, counts all of it again, and scans it a third time for MAX(created_at). Measured at 300k rows: 1.55 s for page 0 and 1.07 s for a search. Time grows linearly, so at full EDGAR scale (tens of millions of rows) each page load and each criteria-based alert evaluation (services/alerts.py:78) takes tens of seconds to minutes. The search predicates (`lower(coalesce(col,'')) LIKE '%x%'` over 7 columns) can never use an index.

*Suggestion:* Add indexes that match the default sorts, at least us_trades(trade_date DESC, id) and us_trades(filing_date), plus equivalents on the other tables. When only one market is selected, query that table directly instead of the union; when several are, push ORDER BY and LIMIT into each branch (each branch returns at most offset+limit rows) and merge those small sets. Read freshness from refresh_state or from a cached `MAX(id)`, not a full scan. Cache total_count per criteria, or make it optional or approximate. For text search, add an FTS5 table (trigram tokenizer for substring search) over issuer, person, identifier and role.

*Also reported as:* BULK-15, ARCH-24

#### PERSIST-01 · HIGH · performance · effort M · partially

**Row-by-row upsert (SELECT, then INSERT/UPDATE per trade, built through SQLAlchemy each time) dominates the bulk backfill cost**  
`src/insider_scanner/persistence/repositories.py:76`

Each trade runs a full-row SELECT. A new trade then gets a fresh insert(...).values(dict) statement with about 31 bind parameters, so SQLAlchemy rebuilds the cache key and coerces parameters for every row (cProfile: coercions.expect, _gen_cache_key and_get_crud_kv_pairs dominate). An existing trade instead goes through us_from_row, merge_trade_pair (to_dict plus json.dumps of both records) and an UPDATE. Measured: about 1,077 us per row even inside one transaction (300k rows took 321 s), and 270-365 us per row when every row is skipped. us_canonical_key is also computed twice per insert (once here and again inside us_to_values). sec_backfill.py:148 calls upsert once per filing (about 4 rows), so the whole per-call overhead repeats millions of times. Together with PERSIST-02 and PERSIST-03 this is about 30 ms per filing, which is enough by itself to keep a full-history backfill from finishing in a reasonable time.

*Suggestion:* Add a batch path. Compute the canonical keys once. Preload the existing rows with `WHERE canonical_key IN (...)` in chunks of about 500-900. Insert the new rows with one executemany: `connection.execute(insert(us_trades), list_of_dicts)`, or `insert(...).on_conflict_do_nothing(index_elements=['canonical_key'])` for SEC-native keys. Merge only the rows that already exist and update them with executemany too. Give the backfill a buffering sink that flushes every N filings, for example 200-1,000 filings or 5-10k rows, in one transaction, and save the checkpoint after each flush. A prototype of this pattern measured 59 us per row, about 17x faster.

*Verifier:* The row-by-row SELECT then INSERT/UPDATE inside one immediate transaction per call is real (repositories.py:76-105), and sec_backfill.py:148 calls it once per filing. But with about 4 rows per filing, the per-row SQLAlchemy cost is only about 4 ms of the claimed 30 ms; the per-call commit/fsync (PERSIST-02) and the new connection (PERSIST-03) are the larger shares. The backfill is also network-bound: process_filing_row fetches each filing through SecClient's fair-access rate limiter (at least about 100 ms per uncached filing), and the per-filing checkpoint rewrite (see PERSIST-M1) grows quadratically. So this finding does not 'dominate', and 'critical' is inflated, although batching the writes is still a worthwhile fix.

#### PERSIST-03 · MEDIUM · performance · effort S · confirmed

**NullPool opens a new sqlite3 connection (and re-parses the schema) for every repository call**  
`src/insider_scanner/persistence/engine.py:46`

Every engine.connect() or engine.begin() opens the database file, runs the connect-listener pragmas, makes SQLite re-read the schema on the first statement, and starts with a cold page cache. Measured: 2.38 ms for connect plus one indexed SELECT, against 0.09 ms on a reused connection (about 25x). Each upsert call (one per filing in the backfill), each coverage.add, and each of the up-to-1,000 recent_by_* calls in watchlist alert evaluation pays this cost.

*Suggestion:* Use a pool. SQLAlchemy's default QueuePool for file databases works here because each connection stays in its acquiring thread for the length of a with-block; add check_same_thread=False so pooled connections can move between worker threads. Alternatively use SingletonThreadPool or StaticPool for a dedicated backfill engine. If NullPool was chosen so tests can delete database files on Windows, call engine.dispose() in test teardown instead. Long-running bulk jobs should also hold one connection for the whole run.

#### PERSIST-22 · MEDIUM · bug · effort M · confirmed

**FeedQuery has no created_at filter, so criteria alerts only check the 200 newest trades by transaction date**  
`src/insider_scanner/persistence/feed.py:114`

services/alerts.py:78 takes `to_query(limit=200)`, sorted by transaction_date, and filters on created_at > last_seen_at in Python. Rows inserted after the watermark but with older transaction dates never reach the top 200: a late-disclosed Congress trade, a Form 4/A, or any backfilled history. Those alerts are missed. In addition, created_at uses `server_default CURRENT_TIMESTAMP` (schema.py:68-72), which has one-second resolution, while the watermark is datetime.now(UTC) with microseconds. A row inserted in the same second just after 'mark all seen' is stored with an earlier timestamp and is never reported.

*Suggestion:* Add an optional `created_after: datetime | None` field to FeedQuery and _filter_conditions, and have alerts query with that filter sorted by created_at. Index created_at, or use the monotonically increasing id as the watermark, which is more robust. If timestamps stay, write created_at from Python with microseconds (or use `strftime('%Y-%m-%d %H:%M:%f','now')`).

#### PERSIST-13 · MEDIUM · performance · effort M · confirmed

**Feed pagination uses LIMIT/OFFSET over a sorted UNION**  
`src/insider_scanner/persistence/feed.py:473`

With OFFSET, SQLite must produce and discard all earlier rows of the fully sorted union. Measured: 2.65 s at offset 100k against 1.55 s at offset 0 (300k rows). Infinite scrolling in gui/feed_page.py:488 gets slower with every page. If rows are inserted between page loads (for example by a running backfill), rows shift and the user sees duplicates or skipped rows.

*Suggestion:* Switch to keyset (seek) pagination. Return the last row's sort tuple (sort value, filing_date, key) as a cursor and filter `WHERE (sort, filing_date, key) < (:a, :b, :c)` using SQLite row-value comparison. Use a numeric (market, id) tiebreaker instead of the string-concatenated key.

#### PERSIST-12 · MEDIUM · performance · effort M · confirmed

**recent_by_issuer and recent_by_person wrap the columns in lower(coalesce()), which prevents index use**  
`src/insider_scanner/persistence/feed.py:698`

Tickers are stored uppercase (us_to_values normalizes them) and ix_us_trades_ticker_filing_date exists. Wrapping the column in functions on top of the UNION makes SQLite scan every row; EXPLAIN shows 'SCAN us_trades'. recent_by_person (line 652) has the same problem on an unindexed insider_name. Watchlist alert evaluation (services/alerts.py:64-72) calls one of these per entry, for up to 1,000 entries, so one evaluation can mean 1,000 full scans. Entity pages call them with limit=200.

*Suggestion:* Compare the raw column with a pre-normalized parameter (`us_trades.c.ticker == identifier.upper()` and `european_trades.c.isin == ...`) per table, not across the union. Add an index on us_trades(insider_name COLLATE NOCASE, trade_date) or a normalized insider_name_key column. For alerts, run one query with `ticker IN (...)` over all watchlist entries instead of one query per entry.

#### PERSIST-16 · MEDIUM · bug · effort S · confirmed

**JSON state loaders that are documented as 'never raises' crash on a non-UTF-8 file**  
`src/insider_scanner/persistence/json_state.py:56`

read_text raises UnicodeDecodeError, a ValueError subclass, when the file contains invalid UTF-8. That can happen after a partial copy, an antivirus quarantine stub or a hand edit in another encoding. Neither the OSError nor the JSONDecodeError handler catches it. feed_state.load_feed_state (feed_state.py:204-210) has the same gap. MainWindow.**init** calls load_feed_state, load_watchlists and load_alerts (main_window.py:95-101), so a bad file prevents the GUI from starting.

*Suggestion:* Catch `(OSError, UnicodeDecodeError)`, or `ValueError` broadly, around the read in both loaders. Better, make load_feed_state reuse read_json_dict (see PERSIST-18) so the fix lives in one place.

*Also reported as:* SECIO-M4

#### PERSIST-04 · MEDIUM · bug · effort M · partially

**SEC-native trades and scraped trades get different canonical keys, so the same trade is stored twice**  
`src/insider_scanner/persistence/mappings.py:77`

Only sec_trade_mapping.py sets sec_row_id; openinsider and secform4 trades never do. A transaction ingested by the SEC daily or backfill pipeline is keyed on ('sec', accession, row). The same transaction scraped from openinsider or secform4 is keyed on (ticker, name, date, shares bucket). The two keys never collide, so the repository stores two rows. Feed totals, value filters, investigation lists and alerts all count the trade twice. merge_trade_pair is never invoked across the two sources, so the provenance merge the schema was designed for never happens either.

*Suggestion:* Keep one identity per economic transaction. For example, store the fuzzy key in its own indexed column (fuzzy_key) on every row. On upsert, when the SEC key misses, look up by fuzzy_key; when a scraped trade arrives, try the fuzzy match against SEC rows first. Then merge into the SEC row and add the source to provenance instead of inserting. A one-off migration should collapse the duplicates that already exist.

*Verifier:* SEC-native rows really are keyed on (accession, sec_row_id) and scraped rows on the fuzzy key (mappings.py:77-87), so the same transaction appears twice in the feed and in alerts. This is deliberate, though: the docstring says 'so that existing third-party rows are unaffected', and services/sec_comparison.py depends on SEC and legacy rows coexisting so it can compare them. The proposed merge-on-upsert would break that report. A dedup or 'preferred source' view at feed level is a safer fix than collapsing rows.

#### PERSIST-08 · MEDIUM · bug · effort M · confirmed

**The fuzzy US canonical key ignores trade_type and price, so a same-day exercise and sale collapse into one row**  
`src/insider_scanner/persistence/mappings.py:79`

A very common Form 4 pattern is an option exercise (M, mapped to 'Exercise') followed by a sale (S, 'Sell') of the same share count on the same day. From scraped sources these produce the same key. merge_trade_pair keeps only the 'richer' record, so one transaction is lost and the survivor's direction can be wrong. Trades with trade_date=None and the same share bucket also collapse together. Round-half-to-even bucketing adds inconsistent edge cases (15 rounds to 20, 25 rounds to 20).

*Suggestion:* Add trade_type to the fuzzy key, and possibly a price bucket or transaction_code when available. Changing the key requires a migration that recomputes canonical_key; do it together with PERSIST-04. Use floor bucketing or a tolerance-based match instead of round().

#### PERSIST-10 · MEDIUM · practice · effort S · confirmed

**Upsert failures lose their cause, and one bad row rolls back the whole batch**  
`src/insider_scanner/persistence/repositories.py:41`

The `error` argument is unused. Callers log `str(exc)`; sec_downloads.py:297 logs 'stage=persist ... reason=%s'. The log therefore shows only 'US trade upsert failed'. It cannot distinguish 'database is locked' from a CHECK violation (for example ck_us_trades_price_nonnegative on a negative parsed value) or a malformed value, which makes backfill failures very hard to diagnose. Because each upsert is one transaction, one constraint-violating trade discards every other valid trade in the filing (or in the importer batch, which is why importer.py:326 upserts one record at a time).

*Suggestion:* Put the cause in the message: `PersistenceError(f"{operation} failed: {type(error).__name__}: {error.orig if hasattr(error,'orig') else error}")`. Add a structured attribute such as `is_access_denied`, or a subclass for lock contention so callers can retry. Use a SAVEPOINT per row (`connection.begin_nested()`), or validate before inserting, so bad rows are counted and skipped without aborting the batch, and return them in UpsertResult.

#### PERSIST-14 · MEDIUM · performance · effort S · confirmed

**Unused indexes on us_trades slow every insert while the indexes the queries need are missing**  
`src/insider_scanner/persistence/schema.py:383`

A grep of the codebase finds no query that filters on us_trades.accession_number, cik_issuer or source; source filtering happens in Python over source_provenance_json. ix_us_trades_source_filing_date (line 93-97), ix_us_trades_accession and ix_us_trades_cik_issuer therefore add write amplification to every backfill insert without serving any read. ix_refresh_state_refreshed_at (line 280) and refresh_state.metadata_json are never used either. Meanwhile there is no index for the columns actually sorted or filtered on: trade_date (the feed's default sort), insider_name (recent_by_person, entity pages) and created_at (freshness and alerts).

*Suggestion:* In a new migration, drop the unused indexes (or keep accession only if a planned 'already ingested?' check will use it). Add indexes that match the queries: us_trades(trade_date), us_trades(filing_date, trade_date), us_trades(insider_name COLLATE NOCASE), and created_at if freshness stays as a query. For very large backfills, consider dropping the secondary indexes before the bulk load and recreating them afterwards.

#### PERSIST-19 · MEDIUM · bug · effort S · confirmed

**Renaming a watchlist allows duplicate names and leaves alert rules pointing at the old name**  
`src/insider_scanner/persistence/watchlists.py:102`

There is no collision check here or in gui/watchlist_page.py:218-227. Renaming 'A' to an existing 'B' produces two lists named 'B'. get() then returns only the first, with_entry adds the entry to both, and without_list deletes both. AlertRule.watchlist stores the name as a string, so after any rename the rule's watchlist lookup (services/alerts.py:61) returns None and the alert silently stops firing.

*Suggestion:* Reject or merge when `new` already exists (raise WatchlistError and let the GUI show it). Propagate renames to AlertStore, for example with an `AlertStore.renamed_watchlist(old, new)` called in the same GUI action, or reference watchlists by a stable id instead of their display name.

#### PERSIST-17 · MEDIUM · bug · effort S · confirmed

**An unreadable or corrupt state file is silently replaced with defaults on the next save, losing watchlists, alerts or saved screens**  
`src/insider_scanner/persistence/watchlists.py:221`

read_json_dict returns None for invalid JSON or a wrong top-level type, and load_watchlists, load_alerts and load_feed_state then return empty stores. The GUI saves those stores back: on any edit (main_window.py:375/382) and always on close for feed state (main_window.py:612). A single malformed byte therefore permanently erases every watchlist, alert rule or saved screen, with only a log warning.

*Suggestion:* When a file exists but cannot be parsed, rename it to `<name>.corrupt-<timestamp>` (or copy it to .bak) before returning the default, and tell the user. Optionally keep the previous good version as `<name>.bak` in atomic_write_json and fall back to it on load.

#### PERSIST-20 · LOW · bug · effort S · confirmed

**Adding a rule, list or entry at the cap silently drops the new item**  
`src/insider_scanner/persistence/alerts.py:63`

With 200 rules, adding a new one appends it and then truncates to 200, so the rule just added is the one dropped. The GUI shows no error and the user believes it was saved. Updating an existing rule also moves it to the end of the list. watchlists.with_list (line 96) and with_entry (line 119) truncate the same way: with_entry on a missing list at the 200-list cap silently does nothing.

*Suggestion:* Check the cap before adding and raise AlertStoreError or WatchlistError (or return a status) so the GUI can tell the user. Replace in place to keep the order: `tuple(rule if r.name == rule.name else r for r in self.rules)`.

#### PERSIST-21 · LOW · bug · effort S · confirmed

**An alert rule with unparseable criteria quietly becomes a 'match everything' rule**  
`src/insider_scanner/persistence/alerts.py:118`

If the stored criteria fail validation (for example after a FeedMarket rename, a bad date, or value_min > value_max from a hand edit), the rule loads with the default FeedCriteria, which has no filters. The next evaluation reports up to 200 'new' matches for a rule that was meant to be narrow, and the user is not told why. The broad `except Exception` also hides programming errors.

*Suggestion:* Catch FeedStateError only. Load the rule disabled (enabled=False) and mark it invalid, for example with an `error` field, so the alerts page can show it, instead of silently widening it.

#### PERSIST-M3 · LOW · bug · effort S · verifier-found

**A naive last_seen_at in alerts.json makes every record 'new' on every evaluation**  
`src/insider_scanner/persistence/alerts.py:129`

rule_from_dict accepts an offset-less ISO string, for example from a hand edit or another tool, and returns a naive datetime. services/alerts.py:40-44 then compares it with aware created_at values, catches the TypeError and returns True. Every candidate is then reported as new on every evaluation, until the user happens to run 'mark seen', which writes an aware value.

*Suggestion:* In rule_from_dict, normalise naive timestamps to UTC (`dt.replace(tzinfo=UTC)` when dt.tzinfo is None), or reject them, so the comparison never sees mixed naive and aware datetimes.

#### PERSIST-25 · LOW · simplification · effort S · confirmed

**FeedQuery and FeedCriteria duplicate their fields and validation**  
`src/insider_scanner/persistence/feed.py:152`

Nine fields, the **post_init** normalization and the to_query copy are written twice (lines 113-149 and 152-199). Every new filter (for example created_after from PERSIST-22) must be added in four places, plus criteria_to_dict and criteria_from_dict. Missing one silently drops the filter on the way from GUI to SQL.

*Suggestion:* Compose them: `FeedQuery(criteria: FeedCriteria, limit: int, offset: int)`, or have FeedQuery hold a FeedCriteria. Validate in FeedCriteria only, and have _filter_conditions take the criteria.

#### PERSIST-23 · LOW · bug · effort S · confirmed

**Case-insensitive search uses Python casefold() against SQLite lower(), which only folds ASCII**  
`src/insider_scanner/persistence/feed.py:356`

SQLite's built-in lower() leaves non-ASCII characters unchanged, while str.casefold() folds them ('MÜLLER' becomes 'müller', 'ß' becomes 'ss'). A stored 'MÜLLER' becomes 'mÜller' in SQL and never matches the search 'müller'. Names containing ß can never be found. The same mismatch affects recent_by_person and search_entities. Separately, wrapping columns in lower() is redundant, because SQLite LIKE is already case-insensitive for ASCII, and it adds per-row function calls.

*Suggestion:* Register a Python function (for example `casefold`) on each connection in the connect listener and use it on both sides, or store normalized search columns. FTS5 with `unicode61 remove_diacritics` handles this naturally. Drop the redundant lower() around LIKE.

#### PERSIST-24 · LOW · bug · effort S · confirmed

**Feed 'is_stale' is based on the last inserted row, not the last refresh, and costs a full scan**  
`src/insider_scanner/persistence/feed.py:490`

freshness_at is MAX(created_at) across all trades. A scan that succeeds but finds no new trades (common on weekends and holidays, or for quiet watchlists) leaves the feed marked stale even though data was just refreshed. A backfill of old trades, by contrast, makes the feed look fresh. The refresh_state table, which records actual refreshes, is not consulted.

*Suggestion:* Take freshness from refresh_state (MAX(refreshed_at) for the relevant domains) or a dedicated last-successful-scan record. That is also an O(1) indexed lookup, unlike the current full scan.

#### PERSIST-11 · LOW · performance · effort S · confirmed

**detail() looks up a record by a computed key over the whole UNION, then queries the table again by id**  
`src/insider_scanner/persistence/feed.py:553`

The prefix and integer id have already been parsed (row_id and prefix), but the base record is fetched by filtering `'us:' || CAST(id AS VARCHAR) = :key` across all three tables, which forces a full scan of each (0.10 s at 300k rows, linear in size). The method then runs a second, indexed primary-key query on the right table. The investigation drawer calls this on every row click.

*Suggestion:* Select the projection branch for the right table directly, for example `select(<us branch>).where(us_trades.c.id == row_id)`. Better, build the FeedRecord from the single `select(us_trades).where(id == row_id)` row that is already fetched. That removes the scan and one round trip.

#### PERSIST-18 · LOW · simplification · effort S · confirmed

**feed_state duplicates the atomic write and read helpers already in json_state**  
`src/insider_scanner/persistence/feed_state.py:229`

save_feed_state copies atomic_write_json line for line, and load_feed_state (lines 202-226) re-implements read_json_dict. Fixes such as PERSIST-16 and PERSIST-17 have to be made twice, and the two copies are already the kind of thing that drifts.

*Suggestion:* Implement both as `atomic_write_json(path, state_to_dict(state))` and `data = read_json_dict(path); return state_from_dict(data) if data else FeedState()`, then drop the os, tempfile and json imports.

#### PERSIST-26 · LOW · bug · effort S · partially

**Congress merge picks the 'larger' value by comparing JSON strings, so numbers are ordered lexicographically**  
`src/insider_scanner/persistence/mappings.py:47`

When both records have a value for a non-key field (amount_low, amount_high, party, source_url, comment, chamber), the winner is the larger JSON string. For floats, '50001.0' sorts above '250001.0', so the smaller amount wins. Choosing by string order also means a corrected re-scrape never replaces an older value unless its string happens to sort higher.

*Suggestion:* Prefer the incoming (newer) record when both values are populated, or define rules per field (numeric max for amounts, newest for URLs and comments). Keep the populated-over-empty rule as it is.

*Verifier:* _deterministic_value does pick the larger JSON string (mappings.py:47-55). The amount_low/amount_high example is effectively unreachable, though, because amount_range is part of congress_canonical_key, so two rows that merge have the same range and the same parsed bounds. The real impact is limited to party, source_url, comment and chamber, where a corrected re-scrape only wins if its string sorts higher.

#### PERSIST-M4 · LOW · bug · effort S · verifier-found

**is_fresh treats any future refreshed_at as fresh until the clock catches up, contradicting its comment**  
`src/insider_scanner/persistence/refresh.py:35`

The age is not clamped: a refreshed_at that is D in the future gives a negative age, so the 'latest' refresh for that source is skipped for D + ttl, not ttl. One write made under a skewed clock (a wrong system time that is later corrected) suppresses the openinsider/secform4 latest refresh in services/us.py:193-195 for as long as the skew lasts.

*Suggestion:* Treat a timestamp more than a few minutes in the future as stale (return False), so the refresh runs and overwrites it, or clamp the age with max(current - refreshed, timedelta(0)) only within a small skew tolerance.

#### PERSIST-09 · LOW · bug · effort S · partially

**Re-ingesting the same SEC row picks the stored version by lexicographic JSON order, so parser corrections may be skipped**  
`src/insider_scanner/persistence/repositories.py:93`

For SEC-native keys (accession plus row id) the incoming record is the authoritative re-parse of the same document. merge_trade_pair nonetheless chooses a whole-record winner by richness score, and on a tie by `json.dumps(stable_values, sort_keys=True)` string order (core/merger.py:59-69). If a parser fix changes price from 12.5 to 12.05, or rewrites sec_detail_json or document_sha256 without changing the richness score, the old row wins whenever its JSON sorts higher. The upsert then reports 'skipped' and the stale value stays forever. For fuzzy keys the merge is all-or-nothing: fields the losing record had populated (for example insider_title) are discarded instead of coalesced. That contradicts the merger docstring.

*Suggestion:* When both the existing and incoming rows carry SEC-native identity, prefer the incoming row (or the one with the newer filing_date or document hash) and only union provenance and edgar_url. For fuzzy-key merges, coalesce field by field (fill empty fields from the other record) instead of replacing the whole record.

*Verifier:* The tie-break really is score first, then json.dumps string order (merger.py:60-69), so a corrected re-parse with the same richness score can lose and be reported as 'skipped'. The claim that this contradicts the merger docstring is wrong: the module docstring says the richer record wins, and merge_trade_pair's docstring explicitly says SEC fields are intentionally not coalesced (deferred). Re-ingesting the same accession is also uncommon, because the backfill checkpoint skips completed accessions.

#### PERSIST-15 · LOW · performance · effort S · confirmed

**Duplicate indexes: price_history and scan_coverage each have an explicit index identical to their UNIQUE constraint**  
`src/insider_scanner/persistence/schema.py:322`

UniqueConstraint('symbol','price_date') already creates sqlite_autoindex_price_history_1 on the same columns in the same order. ix_scan_coverage_lookup (line 261-268) likewise duplicates uq_scan_coverage_interval. The benchmark database lists both pairs. Each price or coverage write maintains two identical B-trees and the file is bigger for nothing.

*Suggestion:* Drop ix_price_history_symbol_date and ix_scan_coverage_lookup in a new migration (`DROP INDEX IF EXISTS`), and remove them from the runtime metadata so fresh databases do not create them.

### Congress scrapers, prices, update script (CONGRESS) — 28 items

> The Congress pipeline (House index/PDF scraper, Senate eFD scraper, CongressScanService) has a clean structure, but several bugs in the common path make its results wrong or incomplete. (1) Named scans store and query official names in incompatible formats. The House scraper stores "Hon. Nancy Pelosi" while the GUI passes official_full ("Nancy Pelosi"), and the repository filters on exact equality, so named House scans return nothing. Senate name splitting also breaks for about 180 of 538 members, those with middle names, initials or ", Jr.". (2) The service marks date ranges as covered even when the scrapers swallowed errors, hit the silent 1000-filing Senate cap, or read a House current-year index that is never refreshed. Those trades are then lost for good. (3) House "S (partial)" sales are classified as "Other". In the price subsystem, cache coverage is just min/max of the stored dates, so holes inside that span are never filled. Stored split/dividend-adjusted values go stale after corporate actions. A single null or slightly inconsistent Yahoo bar throws away a whole fetch. Empty weekend or holiday ranges are treated as errors and fetched again on every call. scripts/update_congress.py writes to a path the app never reads. Its Open States call leaves out a parameter the API requires, and a failed federal fetch overwrites the member file with an empty list. There is also duplicated helper code and some dead code across the House and Senate scrapers and the Yahoo and Tiingo clients.

#### CONGRESS-03 · HIGH · bug · effort S · confirmed

**House current-year index is downloaded once and never refreshed; the refresh helpers are dead code**  
`src/insider_scanner/core/congress_house.py:129`

scrape_house_trades calls ensure_house_index(year) without force. refresh_current_year() and refresh_all_indexes() have no callers outside tests, and the adapter ignores use_cache (`_use_cache`). Once {year}FD.xml exists, every later filing for that year is invisible. The legacy copy at data/house_disclosures/2026FD.xml shows this pattern: it is dated 2026-02-17, has its latest FilingDate on 2026-02-15, and holds only 111 members, yet today is 2026-11-14.

*Suggestion:* Treat indexes for the current year and the previous year (for January filings) as expiring. Re-download when the file mtime is older than N hours, or send If-Modified-Since. Route the service's use_cache=False to force=True, and delete or wire up the unused refresh helpers.

*Also reported as:* ARCH-10

#### CONGRESS-06 · HIGH · bug · effort S · confirmed

**House 'S (partial)' transaction codes normalize to 'Other'**  
`src/insider_scanner/core/congress_house.py:367`

House electronic PTRs use the codes P, S, S (partial) and E. _TX_TYPE_MAP has 'sale (partial)' but not 's (partial)', so every partial sale is stored as trade_type 'Other'. These rows drop out of Purchase/Sale filters and statistics. The test only covers the spelled-out 'sale (partial)' form.

*Suggestion:* Normalize with a regex on the leading code (e.g. r'^(P|S|E)\b' after lowercasing), or add 's (partial)' and 's (full)'. Add a test using the literal 'S (partial)' value from a real House PTR.

#### CONGRESS-02 · HIGH · bug · effort S · partially

**Senate _split_name sends the wrong first/last name for middle names, suffixes and the 'Last First' seed format**  
`src/insider_scanner/core/congress_senate.py:500`

The GUI passes official_full values. "Katie Boyd Britt" becomes last_name="Boyd Britt", and "Nicholas J. Begich III" becomes last_name="J. Begich III". "Gilbert Ray Cisneros, Jr." is read as a "Last, First" name, so first="Jr." and last="Gilbert Ray Cisneros". 183 of the 538 federal entries contain more than one space. The built-in DEFAULT_CONGRESS_MEMBERS seed has no official_name, so the GUI falls back to the "Pelosi Nancy" name field, which becomes first="Pelosi", last="Nancy". In all of these cases the eFD search returns nothing, the coverage window is still marked as scanned, and no error is shown. The docstring's own examples ("Pelosi Nancy" -> ("Nancy","Pelosi")) do not match what the code does.

*Suggestion:* Pass the structured first_name/last_name fields that update_congress.py already writes to congress_members.json, instead of splitting a display string. If a string must be parsed, strip suffixes (Jr., Sr., II, III), quoted nicknames and middle initials, and search eFD by last name only, then filter by first name locally.

*Verifier:* The core claim holds. _split_name returns (parts[0], rest), so 'Katie Boyd Britt' gives last='Boyd Britt', and 'Angus S. King, Jr.' takes the comma branch and gives first='Jr.'. 183 seed entries have more than two tokens, and the docstring examples contradict the code. The DEFAULT_CONGRESS_MEMBERS fallback is effectively unreachable, though: ensure_dirs() copies the packaged seed (which has official_name) before init_default_congress_file in both main.py and cli.py.

#### CONGRESS-17 · HIGH · bug · effort S · confirmed

**One null or slightly inconsistent Yahoo bar discards the whole fetched range**  
`src/insider_scanner/core/prices/yahoo.py:73`

Yahoo regularly returns null entries in the quote arrays (trading halts, thin listings, the in-progress current-day bar), and sometimes bars whose open or close falls slightly outside low/high. PriceBar raises TypeError/ValueError for these (model.py:27-28, 63-70). The list comprehension is inside a single try, so one bad element turns the whole response into PriceSourceError and the chart for that symbol shows no data. Tiingo has the same all-or-nothing behavior (tiingo.py:61-65).

*Suggestion:* Validate each bar separately: skip bars with a null close, clamp or log OHLC inconsistencies instead of raising (or relax _validate_range to a tolerance), and count skipped rows in a debug log.

#### CONGRESS-04 · HIGH · bug · effort M · confirmed

**Coverage is recorded even when scrapers silently skip failed filings or years, so missed trades are never retried**  
`src/insider_scanner/services/congress.py:77`

Both scrapers catch per-item failures, log a warning and continue: a PDF download timeout (congress_house.py:583-585), an index download failure for a whole year (548-553), a PTR page fetch error (congress_senate.py:644-646), and a non-ok search response (547-549). They return a partial list, which the service cannot tell apart from success. The service then marks the whole gap as covered, so later scans never look at it again. Unlike the US services (common.py:16, "refresh a recent overlapping tail"), Congress coverage also has no recent-tail re-check. Filings submitted later on a day already marked covered, or missing from a stale index (CONGRESS-03), are lost for good.

*Suggestion:* Have the scrapers return (trades, complete: bool) or raise a partial-failure error that lists the failed doc ids. Record coverage only for complete runs, or exclude the failed years. Always re-fetch a recent tail (e.g. the last 45 days, the PTR filing deadline) regardless of coverage.

#### CONGRESS-12 · MEDIUM · bug · effort S · confirmed

**update_congress.py writes to a path the application never reads**  
`scripts/update_congress.py:57`

At runtime CONGRESS_FILE resolves to platformdirs user_data_path/congress_members.json (here %LOCALAPPDATA%/Insider Scanner/Insider Scanner/). That file is copied from the packaged seed src/insider_scanner/resources/seeds/congress_members.json only when it is missing (config.py _copy_seed_if_missing). Running the script updates data/congress_members.json, which neither the app nor the seed uses, so member lists and sectors go stale unless someone copies the file by hand.

*Suggestion:* Default --output to insider_scanner.utils.config.CONGRESS_FILE, imported via the sys.path insert the script already does. Optionally add --update-seed to also refresh resources/seeds/congress_members.json.

#### CONGRESS-14 · MEDIUM · bug · effort M · confirmed

**Open States /people request omits the required jurisdiction parameter, so state legislators are never fetched**  
`scripts/update_congress.py:378`

The Open States v3 OpenAPI description for GET /people says: "Must provide either jurisdiction, name, or one or more id parameters." The request sends none, so the first page fails. The generic handler prints 'HTTP error on page 1' and breaks out of the loop, leaving zero state legislators. This matches the current congress_members.json: all 538 entries are federal even though scripts/api_key.txt exists. The loop also has no throttling for the free-tier rate limit. person.get('current_role', {}) returns None when the key exists with a null value, which raises AttributeError and aborts the remaining pages.

*Suggestion:* Loop over the 50 state jurisdictions and pass jurisdiction=<state>, plus org_classification for legislators. Honor 429/Retry-After with a short sleep, use `(person.get('current_role') or {})`, and report an error count instead of stopping at the first failure.

#### CONGRESS-07 · MEDIUM · bug · effort M · confirmed

**Identical transactions within one filing collapse into a single row (row id is parsed but discarded)**  
`src/insider_scanner/core/congress_senate.py:459`

_map_senate_columns maps the '#' column (line 424), but_parse_senate_row never emits it, and the House parser ignores its ID column too. The canonical key has no row index. Two separate same-day purchases of the same asset in the same amount band, which is common for multiple lots or accounts, get the same key and are merged on upsert, so trade counts and amount totals are understated.

*Suggestion:* Emit the row ordinal or '#' value from both parsers, store it on CongressTrade (e.g. line_no), and include it in congress_canonical_key.

#### CONGRESS-M2 · MEDIUM · bug · effort M · verifier-found

**Amended Senate PTRs are scraped as separate filings, double-counting their transactions**  
`src/insider_scanner/core/congress_senate.py:564`

search_senate_filings keeps every electronic PTR, including amendments that eFD lists as separate reports with their own UUID (report_title contains '(Amendment)'). report_title is parsed but never used. congress_canonical_key includes doc_id (mappings.py:96), so transactions repeated in an amendment are stored as new rows next to the originals. This inflates trade counts and amount totals.

*Suggestion:* Detect amendments from report_title. Either prefer the latest amendment and drop the superseded original's rows, or key Senate transactions without doc_id (official, trade_date, asset, type, owner, amount, plus a row ordinal) so a re-filed transaction merges with the original.

*Also reported as:* CONGRESS-05

#### CONGRESS-15 · MEDIUM · bug · effort M · confirmed

**Price cache coverage is min/max of stored dates, so interior holes are never filled**  
`src/insider_scanner/core/prices/repository.py:138`

Suppose bars for 2015 were cached (CLI `prices` with an old range) and later the GUI cached the last 2 years. Coverage is then 2015-01..2026-11, so a request for 2018-2020 finds no gaps and returns an empty or partial list with no fetch. The docstring explains skipping interior non-trading days, but the same logic also skips multi-year holes.

*Suggestion:* Store explicit fetched intervals per symbol (the generic scan_coverage interval table could be reused) and compute gaps against them. Or treat any interior run of missing dates longer than ~5 calendar days as a gap.

*Also reported as:* CONGRESS-M1

#### CONGRESS-19 · MEDIUM · performance · effort S · confirmed

**get_price_history opens a fresh engine and runs bootstrap migrations (IMMEDIATE lock) on every call**  
`src/insider_scanner/core/prices/service.py:41`

gui/analysis_tab.py:116 calls get_price_history(symbol, start, end) without an engine. Every chart load therefore creates a new SQLite engine and runs bootstrap_database inside an IMMEDIATE (write-locking) transaction. That can contend with a running SEC backfill and produce 'database is locked' errors. get_price_source() also creates a new provider and requests.Session per call, so HTTP connections are never reused.

*Suggestion:* Pass in the application's existing PersistenceContext engine and keep one PriceSource instance for the session (e.g. injected into AnalysisTab). Remove the own_engine path and the `"ctx" in locals()` workaround.

#### CONGRESS-16 · MEDIUM · bug · effort M · partially

**Cached prices are never re-adjusted after splits or dividends, mixing adjustment bases**  
`src/insider_scanner/core/prices/service.py:48`

Yahoo chart OHLC values are split-adjusted, and adjclose and Tiingo adjClose are split- and dividend-adjusted, all as of the moment of the fetch. The service only fetches the edge gaps, so after a split the stored history stays on the old basis while new bars use the new basis. The analysis chart (plot_close prefers adjusted_close) then shows an artificial cliff, e.g. a 10x drop on a 10:1 split, and any return calculation near the event is wrong.

*Suggestion:* Fetch splits and dividends (Yahoo events=div,split, or compare an overlap bar) on each tail refresh. If the stored adjusted_close on an overlapping date differs from the newly fetched value beyond a tolerance, invalidate and re-fetch that symbol's full history. Alternatively store raw prices plus split factors and compute adjustments when reading.

*Verifier:* This is real. Only edge gaps are fetched (events=history, no split or dividend detection), and plot_close prefers adjusted_close, so after a split or dividend the stored history uses a different basis from new bars. Splits are rare for any one symbol, so this is medium rather than high.

#### CONGRESS-18 · MEDIUM · performance · effort M · confirmed

**Empty edge ranges (weekends, holidays, pre-IPO) are treated as errors and fetched again on every call**  
`src/insider_scanner/core/prices/tiingo.py:55`

The analysis tab always requests [today-730d, today]. On weekends, holidays, or before the close, end > cov_max (the last trading day), so a tail gap is fetched on every chart load. A start date before the IPO or on a weekend likewise produces a permanent head gap. Tiingo raises on an empty list, and Yahoo raises on a missing or empty timestamp array (yahoo.py:51-53). Each of these legitimately empty responses costs a network round trip and logs a 'Price fetch failed' warning. validate_coverage also reports such symbols as uncovered.

*Suggestion:* Return [] for valid empty responses. Record the last checked date per symbol (or clamp the tail gap to the last completed trading session) so a gap known to be empty is not requested again on the same day.

#### CONGRESS-30 · LOW · practice · effort S · confirmed

**scripts/api_key.py depends on the current directory and shells out to an arbitrary 'python'**  
`scripts/api_key.py:9`

It only works when run from scripts/. os.system('python ...') may resolve to an interpreter other than the project venv (python 3.15 is first on PATH here, and the venv is broken), and the exit status is ignored. The redundant f.close() inside the with block is harmless.

*Suggestion:* Resolve paths relative to Path(**file**).parent and run subprocess.run([sys.executable, str(script), '--include-state'], env={**os.environ, 'OPENSTATES_API_KEY': key}, check=True). Or have update_congress.py read the key file itself as a fallback.

#### CONGRESS-28 · LOW · simplification · effort S · confirmed

**update_congress.py duplicates the member-building loop for its YAML and JSON sources**  
`scripts/update_congress.py:325`

Lines 284-310 and 325-346 build the same dict from YAML and JSON. yaml is imported inside three functions, and errors are print()ed and swallowed everywhere. Runtime code (senate.py DEFAULT_CONGRESS_MEMBERS) keeps a separate seed schema that lacks official_name/first_name/last_name, which contributes to CONGRESS-02.

*Suggestion:* Extract `_member_from_legislator(person) -> dict`, used by both branches. Import yaml once at module level. Make DEFAULT_CONGRESS_MEMBERS use the same schema as the script output, or remove it in favor of the packaged seed.

#### CONGRESS-13 · LOW · bug · effort S · partially

**Failed federal fetch overwrites congress_members.json with an empty list**  
`scripts/update_congress.py:607`

fetch_federal_legislators swallows all errors and returns []. main() prints a warning and still calls merge_and_save, which writes [] (or only state members) over the existing file with a non-atomic write_text. A network outage while running the script wipes the member list and disables congress flagging and the GUI dropdown.

*Suggestion:* Exit with a non-zero status when the federal list is empty, or refuse to write when the new list is much smaller than the existing file. Write to a temp file and os.replace() it into place.

*Verifier:* When the fetch fails, main() does warn and still call merge_and_save(federal=[]), overwriting the output with a non-atomic write. Because of CONGRESS-12, though, the default output is a file the app never reads, so flagging and the dropdown are only broken when --output points at the real runtime file.

#### CONGRESS-27 · LOW · simplification · effort S · confirmed

**Dead code and ignored parameters in the congress/prices modules**  
`src/insider_scanner/core/congress_house.py:68`

_AMOUNT_RANGES and _index_txt_path are unused. refresh_all_indexes and refresh_current_year are used only by tests. EFDSession.is_authenticated and prices.validate_coverage have no callers. The congress adapters accept use_cache and ignore it (`_use_cache`). update_congress.py's `--federal-only` is store_true with default=True, so it does nothing. progress_callback is annotated with the builtin `callable` rather than a type (Callable[[int, int, str], None]).

*Suggestion:* Remove the unused symbols, or wire them up (use_cache -> force index/PDF refresh). Drop --federal-only, and fix the progress_callback annotations.

#### CONGRESS-24 · LOW · bug · effort S · confirmed

**House name filter uses bidirectional substring matching, so empty or short names match broadly**  
`src/insider_scanner/core/congress_house.py:296`

A filing with empty First/Last gives last_first == '' and matches every query ('' in name_lower). A short typed name such as 'Lee' matches 'Leger' or 'Fleeson'. Multi-token names with middle initials or nicknames ('Eric A. "Rick" Crawford') fail unless the XML has exactly the same spelling.

*Suggestion:* Tokenize both sides and require the last name to match and the first name or initial to match (ignoring prefixes, suffixes and quoted nicknames). Skip filings with empty names.

#### CONGRESS-11 · LOW · bug · effort M · partially

**House PDF/index cache writes are non-atomic and unvalidated, with no session, User-Agent, retry or throttle**  
`src/insider_scanner/core/congress_house.py:346`

If a download or write is interrupted (or the app is closed mid-scan), a truncated PDF or XML is left in the cache. Because the code only checks exists(), it is reused forever. A truncated XML makes ET.fromstring raise on every scan of that year, and a bad PDF makes parse_ptr_pdf quietly return []. Responses are not checked as being a PDF (%PDF- magic or content-type), so an HTML error page would also be cached. Each of hundreds of PDFs opens a fresh connection with the default python-requests UA, with no backoff on 429/5xx and no delay between requests. The Senate scraper does rate-limit, so the two are inconsistent.

*Suggestion:* Use one module-level requests.Session with a descriptive User-Agent and urllib3 Retry (backoff, 429/5xx), plus a small politeness delay. Write to a temp file and os.replace() it into place. Check the %PDF- header before caching. When parsing a cached file fails, delete it and download again once.

*Verifier:* Each download uses bare requests.get with no Session, retry, User-Agent, delay or content validation, and cache writes are not atomic. The truncated-cache risk is narrow, though: resp.content is fully read before write_bytes, and urllib3 raises on an incomplete body. Corruption needs a crash during the write or a 200 response with an HTML body.

#### CONGRESS-23 · LOW · bug · effort S · confirmed

**House parse_ptr_pdf discards all rows already parsed when any page raises**  
`src/insider_scanner/core/congress_house.py:443`

One malformed table on page 3 of a 5-page PTR drops the transactions already gathered from pages 1-2. The warning gives no doc id, because the function only receives bytes.

*Suggestion:* Wrap each page in its own try, keep the rows parsed so far, and pass doc_id in for logging. Report a partial-parse flag to the caller so coverage logic can treat the filing as incomplete.

#### CONGRESS-26 · LOW · simplification · effort M · confirmed

**Duplicated helpers across the House/Senate scrapers and the Yahoo/Tiingo clients**  
`src/insider_scanner/core/congress_senate.py:60`

_TICKER_RE, _extract_ticker, _TX_TYPE_MAP and_normalize_tx_type are defined twice with different behavior: the House map handles single-letter codes, the Senate map does not, and owner normalization exists only for the House. The House version of the bug in CONGRESS-06 exists because of this divergence. yahoo.py:127-138 and tiingo.py:122-133 repeat the same status/HTML/empty-body validation block. The ticker regex also misses class shares such as BRK.B and BF-B.

*Suggestion:* Move ticker extraction, transaction-type normalization, owner normalization and amount parsing into a shared congress_common module (or utils/parsing). Extract a `_checked_json(response, provider)` helper for the price clients. Extend the ticker regex to r'\(([A-Z]{1,5}(?:[.-][A-Z])?)\)'.

*Also reported as:* ARCH-26

#### CONGRESS-09 · LOW · bug · effort S · partially

**Senate end-date filter sent as midnight, likely excluding filings submitted on date_to**  
`src/insider_scanner/core/congress_senate.py:239`

The end bound is the start of date_to. If eFD compares submission timestamps, which this datetime format suggests, filings submitted during date_to (often today) are excluded. The service still marks date_to as covered, so they are never fetched.

*Suggestion:* Send '23:59:59' for the end bound, or (date_to + 1 day) 00:00:00, and add a test that checks the form payload.

*Verifier:* Line 239 does send 'MM/DD/YYYY 00:00:00' as the end bound. Whether eFD compares against full submission timestamps, and so excludes filings made on date_to, cannot be confirmed from the code. The defect is plausible but unverified.

*Also reported as:* ARCH-14

#### CONGRESS-08 · LOW · bug · effort M · partially

**Expired eFD session or agreement redirect yields an empty result instead of re-authenticating**  
`src/insider_scanner/core/congress_senate.py:368`

When the Django session expires during a long run (1 second between requests across hundreds of PTRs), eFD redirects to the prohibition-agreement page with HTTP 200. fetch_page returns that HTML, parse_ptr_page logs only at DEBUG level, and the filing contributes zero trades. Combined with CONGRESS-04, those filings are lost for good. search() also never checks for an HTML or agreement response before calling resp.json().

*Suggestion:* In fetch_page and search, detect a redirect to /search/home/ or the presence of the prohibition_agreement form, then call authenticate() again and retry once. Treat a PTR page with no transaction table as an error (WARNING level, counted as incomplete), not as an empty filing.

*Verifier:* It is true that a PTR page with no table is logged only at DEBUG and counted as an empty filing, and that there is no re-authentication path. That eFD sessions expire within one run is unverified. A non-JSON search response would raise from resp.json() and propagate to a ScanError, so no coverage is recorded in that case and there is no data loss there.

#### CONGRESS-25 · LOW · bug · effort S · confirmed

**Amount ranges 'Spouse/DC Over $1,000,000' parse to (0, 0)**  
`src/insider_scanner/core/models.py:217`

House disclosures include the category 'Spouse/DC Over $1,000,000', which does not start with 'over' and contains no '-'. amount_low and amount_high become 0, so the trade disappears from min-value filters (congress_tab filter `t.amount_low >= min_value`) and amount totals.

*Suggestion:* Match with a regex: r'over\s*\$([\d,]+)' anywhere in the string, and r'\$([\d,]+)\s*-\s*\$([\d,]+)' for ranges. Keep the original label in amount_range.

#### CONGRESS-29 · LOW · performance · effort S · confirmed

**upsert_bars does a SELECT plus INSERT/UPDATE per bar while the sqlite upsert construct is already imported**  
`src/insider_scanner/core/prices/repository.py:37`

A first load of 2 years (~500 bars) runs about 1,000 statements inside an IMMEDIATE transaction, holding the write lock longer than needed. `insert` is imported from sqlalchemy.dialects.sqlite but on_conflict_do_update is not used. CongressTradeRepository.upsert has the same per-row SELECT pattern.

*Suggestion:* Use a single executemany: insert(price_history).values([...]).on_conflict_do_update(index_elements=[symbol, price_date], set_={...}).

#### CONGRESS-21 · LOW · bug · effort S · confirmed

**price_history.source is always 'unknown': providers have no name attribute**  
`src/insider_scanner/core/prices/service.py:53`

Neither YahooSource nor TiingoSource defines `name`, so every stored row has source='unknown'. You cannot tell which provider a cached bar came from, or invalidate bars after switching PRICE_SOURCE. Bars from different providers with different adjustment methods are silently merged.

*Suggestion:* Add `name: str` to the PriceSource Protocol and set name='yahoo' or 'tiingo' on the classes. Consider limiting coverage and reads to the active provider.

#### CONGRESS-22 · LOW · practice · effort S · confirmed

**PriceSourceError throws away cause/context and callers use `from None`, so failures cannot be diagnosed**  
`src/insider_scanner/core/prices/source.py:33`

Every failure (HTTP 401 bad Tiingo key, 429 rate limit, symbol not found, schema change, a null value) produces the same message 'Yahoo price request failed' with no chained exception. service.py:66-69 builds a descriptive cause that is then discarded, and its provider argument 'get_price_history' turns into a misleading message. Users cannot tell an invalid API key from a missing symbol.

*Suggestion:* Keep a sanitized reason code (status code, 'empty', 'schema', 'auth') in the exception and log the original exception at DEBUG level. Chain with `from exc` and redact only the API key, which is never part of the URL anyway.

#### CONGRESS-20 · LOW · bug · effort S · confirmed

**Daily bar date derived from UTC timestamp is off by one day for exchanges east of UTC**  
`src/insider_scanner/core/prices/yahoo.py:37`

Yahoo daily timestamps are the session open in exchange-local time. For US listings the UTC date matches, but ASX opens at 10:00 AEDT (23:00 UTC the previous day), so bars are labelled one day early, and some other Asia-Pacific listings are affected the same way. The symbol regex accepts suffixed tickers like 'BHP.AX', so this path is reachable.

*Suggestion:* Apply chart.result[0].meta.gmtoffset (or exchangeTimezoneName via zoneinfo) before taking .date().

### GUI (Qt) and threading (GUI) — 31 items

> The GUI layer is built carefully in places. The newer feed-first pages (Feed, drawer, entity pages, global search, alerts, watchlists) run their DB work off the GUI thread with request-id guards, use immutable stores, and handle errors in a way users can act on. The older tool tabs (Scan and Congress, built on the pandas-backed SortableTableModel) are much weaker:
>
> - In ScanTab, rows are matched to the wrong trade once filters are applied.
> - Numeric columns sort as text.
> - The pandas model is slow per cell, and with ResizeToContents it gets slow on large result sets.
> - CIK resolution makes blocking network calls on the GUI thread.
>
> Threading is cooperative cancellation over one shared QThreadPool. The shutdown path cancels all work, blocks the GUI for up to 5 s, and then silently refuses to close if workers are still running. Lambdas that capture `self` are not tied to widget lifetime. The `Worker`/request-id boilerplate is copied about 8 times even though a `dispatch()` helper exists. MainWindow is a god object: it owns navigation, three persistence stores, alert evaluation, screens and two duplicate theme controls. The ThemeManager sets the Fusion QPalette only once, before it restores the saved mode, so QSS and QPalette can drift apart. There are also smaller cross-page problems: the feed and alerts do not refresh after a scan, the alert badge goes stale, and buy/sell classification and value formatting are duplicated and inconsistent.

#### GUI-05 · HIGH · bug · effort M · confirmed

**Global search popup (Qt.Popup) takes keyboard focus while the user is typing; Enter can act on a stale item**  
`src/insider_scanner/gui/global_search.py:29`

A widget with the Qt.Popup flag grabs mouse and keyboard when shown. `_on_search_result` shows the popup 200 ms after typing pauses. From then on, keystrokes go to the QListWidget (keyboard search) instead of the top-bar QLineEdit, so the user cannot keep refining the query without clicking back in. QCompleter avoids this with an event filter that forwards keys, and nothing here does that. Separately, `_on_return_pressed` (line 192) uses `popup.list_widget.currentItem()` without checking `popup.isVisible()`. After the popup is dismissed, Enter can activate an item, such as the 'Filter feed for "<old text>"' row, from an earlier search.

*Suggestion:* Use a QCompleter with a custom model, or show the popup with `Qt.ToolTip`/`Qt.Tool | FramelessWindowHint` and `WA_ShowWithoutActivating`, with an eventFilter on the line edit that forwards Up/Down/Enter/Esc to the list. In `_on_return_pressed`, only use currentItem when `self.popup.isVisible()`.

#### GUI-01 · HIGH · bug · effort S · confirmed

**ScanTab maps the selected/double-clicked row to the wrong trade after filtering (and saves unfiltered data)**  
`src/insider_scanner/gui/scan_tab.py:530`

`_apply_filters` (line 520) calls `_display_trades(filtered)`, so the model now shows the filtered list, but `self._trades` still holds the full unfiltered list. `_on_row_double_click` (530) and `_open_edgar` (548-551) take the source-model row and index it into `self._trades`. Once any filter is applied, the detail label shows a different insider's trade and 'Open EDGAR Filing' opens the wrong filing. The bounds check at 529 also uses the proxy row instead of the source row. `_save_results` (586) writes `self._trades`, so it saves the unfiltered set even though the user sees filtered rows. CongressTab handles this correctly with `_filtered_trades`.

*Suggestion:* Keep a `_displayed_trades` list that `_display_trades` sets, and use it for detail, EDGAR and save. Better still, store the trade object in the model (for example under Qt.UserRole) and read it with `index.data(Qt.UserRole)`, so there is no parallel list to keep in sync. Add a regression test: filter, then double-click row 0, then check that the detail matches the displayed row.

*Also reported as:* ARCH-19

#### GUI-02 · HIGH · bug · effort S · confirmed

**Numeric columns in the Scan/Congress tables sort as text**  
`src/insider_scanner/gui/widgets.py:106`

SortableTableModel is a plain QSortFilterProxyModel with no sortRole and no lessThan override, so it sorts on DisplayRole. DisplayRole returns formatted strings, which makes 'shares', 'price' and 'value' sort as text: '1,000,000.00' < '200.00' < '95.00'. NaN becomes '' and always sorts first. In the Congress tab, 'amount_range' strings such as '$1,001 - $15,000' also sort as text, and the numeric `amount_low` is not displayed. Sorting by value, the most common way to triage insider buys, gives wrong results.

*Suggestion:* Return the raw value for a custom sort role (for example Qt.UserRole) in PandasTableModel.data and call `setSortRole(Qt.UserRole)` in SortableTableModel. Alternatively, override lessThan to compare the underlying df values. For Congress, sort `amount_range` by `amount_low`.

#### GUI-17 · MEDIUM · simplification · effort M · confirmed

**Scan and Congress tabs are near-duplicates and hold business logic and file I/O in GUI modules**  
`src/insider_scanner/gui/congress_tab.py:64`

congress_tab.py defines domain functions: `filter_congress_trades`, `save_congress_results` (CSV/JSON written on the GUI thread), `congress_trades_to_dataframe`, and two loaders that each parse CONGRESS_FILE (`_load_congress_names` at construction, `_load_member_sectors` on every scan). Both swallow errors silently. ScanTab and CongressTab also duplicate the date-range group, `_get_start_date`/`_get_end_date`, the filter group, the progress/stop handling, the cancel-event lifecycle, the results splitter and the save flow almost line for line. Bug fixes then have to be made twice, and they have already diverged: GUI-01 exists only in ScanTab.

*Suggestion:* Move the filter/save/dataframe helpers and a single `load_congress_members()` (returning names and sectors from one parse, with logged errors) into core/services. Extract a `DateRangeGroup` widget and a `BaseScanTab` (progress, stop/cancel, results table, save) that both tabs subclass, and run saves through the thread pool.

#### GUI-14 · MEDIUM · bug · effort S · confirmed

**Entity page summary counts only the first 200 rows, and a stale worker can hide the progress bar**  
`src/insider_scanner/gui/entity_pages.py:247`

`_build_summary` computes '{n} transactions · {buys} buys · {sells} sells' from the capped result. For any active company or insider it shows '200 transactions' and buy/sell counts for that subset only, which looks like a complete total. In addition, `worker.signals.finished.connect(self.progress.hide)` (line 165) has no request-id guard. When request A finishes while a newer request B is still loading, the progress bar disappears early.

*Suggestion:* Fetch the aggregate counts with a COUNT/GROUP BY query, or label the summary 'latest 200 of N'. Route `finished` through a request-id-checked slot, as `_on_result`/`_on_error` already do.

#### GUI-09 · MEDIUM · bug · effort S · confirmed

**Feed investigation drawer shows a stale record after the model reloads**  
`src/insider_scanner/gui/feed_page.py:638`

`replace_records` resets the model (reload, sort, filter, search). QItemSelectionModel clears the selection on reset without emitting selectionChanged, so `_drawer_origin_row` and the open drawer keep the old record. If the user then clicks the same row index, which now holds a different transaction, `_show_drawer_for_row` returns early because the row number matches and the drawer is visible. The drawer keeps showing the previous transaction's details and actions (Open company/insider/Watch act on the wrong entity).

*Suggestion:* In `_on_result` with append=False (or on `modelReset`), set `_drawer_origin_row = -1` and hide the drawer or reset its state. Better, compare record identity (`record_at(row) is self.drawer._record`) instead of the row number.

#### GUI-15 · MEDIUM · simplification · effort L · partially

**MainWindow is a god object mixing shell, persistence, alert evaluation, screens and theme control**  
`src/insider_scanner/gui/main_window.py:78`

The ~730-line class loads and saves three JSON stores (feed state, watchlists, alerts) and runs and versions alert evaluation workers. It also builds and manages the Screens menu, keeps two redundant theme controls in sync (View>Theme actions and a top-bar combo), wires every page's signals, and orders shutdown. The knowledge graph shows 127 edges for this class. Pages reach back through it for everything (for example `criteria_provider=self.feed_page.criteria`), so features cannot be tested in isolation and every new page adds more wiring here. Page indices also depend on dict insertion order (`list(self.navigation_buttons).index(name)` at line 494).

*Suggestion:* Split into: (1) a `UserStateStore` / controller that owns load and save for feed state, watchlists and alerts plus the save-failure policy; (2) an `AlertController(QObject)` that owns request ids, evaluation, the badge count and re-check triggers; (3) a `ScreensMenu` widget; (4) a single theme control. Register pages in a small `{name: widget}` registry and use `page_stack.indexOf(widget)`.

*Verifier:* MainWindow does own feed-state, watchlist and alert persistence, alert evaluation, the Screens menu and two theme controls, so the god-object description is fair. The '127 edges' figure was not verified. Page indexing by dict order is consistent today because every page goes through `_add_page`, so that part is fragile but not a bug.

#### GUI-11 · MEDIUM · practice · effort S · confirmed

**Feed, entity pages and alerts are not refreshed after Scan/Congress tabs persist new trades**  
`src/insider_scanner/gui/main_window.py:465`

`UsScanService.scan/latest` upsert trades into SQLite (services/us.py:114, 207), and the Congress service does the same. ScanTab and CongressTab define no signals, and MainWindow only reloads the feed and evaluates alerts at startup or on a manual Reload. After a scan the Feed shows outdated counts, the freshness label is wrong, and new matches for alert rules are not raised. The alerts feature misses exactly the case it exists for.

*Suggestion:* Add a `dataChanged`/`scanCompleted` signal to the tool tabs, emitted from `_on_scan_done`. MainWindow then calls `feed_page.reload()` (or shows the stale banner) and `_check_alerts()`.

#### GUI-06 · MEDIUM · bug · effort M · confirmed

**closeEvent cancels all background work, blocks the GUI for up to 5 s, then silently refuses to close**  
`src/insider_scanner/gui/main_window.py:714`

Clicking X first calls request_cancellation() on every page, then blocks the event loop in `waitForDone(5000)`. If a scan is still inside a network request (cancellation is cooperative and only checked between steps), the wait times out and the close is ignored with no message to the user. By then the side effects have already happened: the running scan is cancelled, every page's request_id has been bumped so in-flight results are discarded (the Feed can stay stuck on 'Loading transactions...' with the progress bar showing), and the UI was frozen for 5 s. Each further click repeats the cycle.

*Suggestion:* Make closing asynchronous: on the first close, ask for confirmation if work is running, request cancellation, show 'Stopping background tasks...', disable the UI and poll `activeThreadCount()` with a QTimer. Close when it reaches 0, or offer 'Force quit' after a timeout. Do not call request_cancellation unless shutdown is actually going ahead.

#### GUI-04 · MEDIUM · performance · effort S · confirmed

**ResizeToContents header mode on potentially large result tables**  
`src/insider_scanner/gui/scan_tab.py:204`

ResizeToContents makes QHeaderView measure up to resizeContentsPrecision (1000) rows per column on every model reset, sort and layout change. Each measurement goes through the slow PandasTableModel.data() (GUI-03). The same setting is in congress_tab.py:358. Large scans, Apply Filters and every header-click sort trigger a full re-measure on the GUI thread.

*Suggestion:* Use Interactive mode with one `resizeColumnsToContents()` call after set_dataframe (or `setResizeContentsPrecision(100)`), and set stretchLastSection like the Feed table does.

#### GUI-M1 · MEDIUM · bug · effort S · verifier-found

**Watchlist scan is all-or-nothing: one failing ticker throws away every result**  
`src/insider_scanner/gui/scan_tab.py:403`

`UsScanService.scan` raises ScanError for any adapter failure (services/us.py:104-107). The watchlist loop has no per-ticker try/except, so one bad or delisted ticker, or one transient HTTP error, makes the worker emit `error`. `_on_scan_error` then shows a generic 'Scan failed', and the results already gathered for the earlier tickers are never displayed (their rows were persisted but `_trades` is not updated). On a long watchlist the user loses the whole run.

*Suggestion:* Wrap each `self._service.scan(...)` call in try/except, log the failure, collect the failing tickers, and keep going. Return the merged trades together with the failure list, and show 'N tickers failed: ...' in the status label instead of aborting.

#### GUI-07 · MEDIUM · performance · effort S · confirmed

**Resolve CIK does blocking SEC network I/O on the GUI thread**  
`src/insider_scanner/gui/scan_tab.py:567`

`resolve_cik` calls `fetch_url` for SEC company_tickers.json (several MB of JSON, parsed in-process) and, on a miss, a second HTML fetch parsed with BeautifulSoup. Both go through the SEC rate limiter. Running this in a button slot freezes the whole window for seconds, or until the HTTP timeout when SEC is slow or unreachable.

*Suggestion:* Run it through `dispatch(self._thread_pool, resolve_cik, args=(ticker,), on_result=..., on_error=...)`, disable the button while it runs, and call webbrowser.open in the result slot.

*Also reported as:* ARCH-18

#### GUI-08 · MEDIUM · bug · effort S · confirmed

**Fusion QPalette is built once, before the saved theme mode is restored, and never updated**  
`src/insider_scanner/gui/theme/manager.py:66`

`install()` builds the QPalette with `self._resolve()` while `_mode` is still the default SYSTEM, because the QSettings restore happens later, at lines 78-83. `apply()` only resets the QSS and never calls `app.setPalette`. Example: a user saved DARK and the OS is light. The QSS is dark but the QPalette is light. Any later theme switch (menu, combo or OS change) leaves the palette on the old theme. Fusion draws anything the QSS does not fully cover from the QPalette (indicators, scrollbar parts, dialogs, item delegates that read Base/Text/Highlight), so these areas get mixed light/dark colours.

*Suggestion:* Restore the mode before building the palette, and move `app.setPalette(self._build_fusion_palette())` into `apply()` so the palette and QSS are always applied together. Have `_build_fusion_palette` take the already-resolved palette instead of calling `_resolve()` again.

#### GUI-03 · MEDIUM · performance · effort M · confirmed

**PandasTableModel.data() does expensive pandas indexing and per-cell recomputation**  
`src/insider_scanner/gui/widgets.py:118`

Each data() call does `self._df.iloc[r, c]` (tens of µs) and `str(self._df.columns[...])`. For ForegroundRole it builds a whole-row pandas Series on every cell paint. TextAlignmentRole calls `column_alignment()`, which re-runs stem matching and lazy `from PySide6.QtCore import Qt` / `import pandas` statements on every call. Painting, scrolling, ResizeToContents (GUI-04) and QSortFilterProxyModel sorting (O(n log n) data() calls) all go through this path. With a Congress 'All' scan or a 500-row watchlist scan, sorting and resizing take seconds and the GUI freezes.

*Suggestion:* In set_dataframe, precompute the per-column state once: display strings (a list of lists or numpy object arrays), alignment and monospace flags per column, and the per-row foreground colour (from trade_type/is_congress). data() then only indexes Python lists. Keep raw values for the sort role (GUI-02).

#### GUI-23 · LOW · bug · effort S · confirmed

**AlertsPage caches the palette once, and the filter error label uses a hard-coded colour**  
`src/insider_scanner/gui/alerts_page.py:99`

Unlike FeedTableModel and InvestigationDrawer, AlertsPage never subscribes to `paletteChanged`. After a theme switch, newly rendered alert matches keep the old theme's purchase/sale colours, which can have poor contrast on the new background. In feed_filters.py:190, `self.lbl_error.setStyleSheet("color: red;")` likewise ignores the `palette.error` token.

*Suggestion:* Connect `get_theme_manager().paletteChanged` to a slot that updates `_palette` and calls `_refresh_results()`. Style lbl_error through an objectName rule in build_stylesheet that uses `p.error`.

#### GUI-13 · LOW · bug · effort S · confirmed

**'Mark all seen' clears the results but the top-bar alert badge keeps the old count**  
`src/insider_scanner/gui/alerts_page.py:224`

The badge is only updated in `MainWindow._on_alerts_evaluated` (main_window.py:426) or when no rule is enabled. `rulesChanged` goes to `_on_alerts_changed`, which only persists. After 'Mark all seen' the page shows 'No new matches.' while the badge still shows 'Alerts (N)' until the next manual check or reload. The same applies after toggling or deleting rules.

*Suggestion:* Have AlertsPage emit `matchCountChanged(int)` from `display_hits` and connect it to `_update_alert_badge`. Alternatively, call `_check_alerts()` from `_on_alerts_changed`.

#### GUI-12 · LOW · performance · effort S · confirmed

**Analysis tab creates a new engine and bootstraps the DB on every chart load, and loads every Congress trade in the window**  
`src/insider_scanner/gui/analysis_tab.py:33`

Each 'Load Chart' runs `open_persistence()`: a new SQLAlchemy engine plus `bootstrap_database` (schema/migration checks), separate from the application's shared engine. That is extra startup cost and a second SQLite writer/reader that can contend with a running scan. It also bypasses the injected services, which makes the tab hard to test. The Congress query has no ticker filter, so every congress trade in the 2-year window is hydrated into objects and filtered by ticker in Python (lines 44-50).

*Suggestion:* Pass `services` (or the persistence context) into AnalysisTab as the other tabs receive theirs, and reuse its repositories. Add a `ticker` parameter to `CongressTradeRepository.query`, or a dedicated `query_by_ticker`, so the filter runs in SQL.

#### GUI-M2 · LOW · bug · effort S · verifier-found

**Entity page keeps the previous entity's table and summary while loading and after an error**  
`src/insider_scanner/gui/entity_pages.py:205`

`load()` (155-156) only shows a state label. It leaves `header_label`, `summary_label` and the model holding the previous entity's data. If the new load fails, the page shows 'Could not load company activity.' above the previous company's full transaction table and summary, and the new entity is named nowhere. While loading, the old rows can be read as the new entity's history.

*Suggestion:* At the start of `load()`, set the header to the requested value, clear the summary, and call `model.replace_records(())`. Do the same in `_on_error` so the table never shows a different entity than the one requested.

#### GUI-25 · LOW · practice · effort S · confirmed

**Filter chip is destroyed synchronously inside its own button's clicked handler**  
`src/insider_scanner/gui/feed_filters.py:505`

`setParent(None)` hands ownership to Python. When `_on_chip_removed` returns, the last reference goes away and the chip, together with the `btn_remove` that is still emitting `clicked`, is deleted while QAbstractButton's click/mouseRelease code is still on the stack. The re-entrant `chipRemoved` then rebuilds all chips through `set_criteria` -> `_remove_all_chips` within the same call chain. Qt's internal QPointer guards usually prevent a crash, but this pattern is fragile and can cause intermittent crashes.

*Suggestion:* Use `chip.hide(); chip.deleteLater()` in both `_on_chip_removed` and `_remove_all_chips`. Optionally emit `chipRemoved` through a queued connection.

#### GUI-27 · LOW · bug · effort S · partially

**Feed header sort indicator is hard-coded and not synced with criteria**  
`src/insider_scanner/gui/feed_page.py:321`

The indicator is always set to Trade Date descending, whatever `initial_criteria.sort_field`/`descending` say. `set_criteria` (applying a saved screen, or a reset that keeps the sort) never updates it. The arrow can point at a different column or direction from the server-side sort, and the next header click flips the wrong state.

*Suggestion:* Add `_sync_sort_indicator(criteria)` that maps `sort_field` back to its column (reverse of `_SORTABLE_COLUMNS`) and calls `setSortIndicator` with signals blocked. Call it from `__init__` and `set_criteria`.

*Verifier:* The indicator is hard-coded at line 321, and `set_criteria` never syncs it. But MainWindow calls `restore_column_layout` (main_window.py:238) right after construction, and QHeaderView state includes the sort indicator, which was saved together with the criteria. So the startup mismatch mostly goes away. The real mismatch happens when a saved screen with a different sort is applied through `set_criteria`.

#### GUI-22 · LOW · bug · effort M · partially

**Feed 'Load More' uses offset paging over live data and appends with quadratic tuple copies**  
`src/insider_scanner/gui/feed_page.py:486`

Offset is the number of rows currently loaded. If a scan inserts new rows that sort before the loaded window (the default sort is newest trade date first), the next page repeats rows already shown or skips rows. `FeedTableModel.append_records` (line 136) rebuilds the whole tuple on every page (`(*self._records, *records)`), so loading k pages costs O(k²·page).

*Suggestion:* Use keyset pagination (pass the last row's sort key and id), or snapshot `max(id)` at the first query and filter on it. Store records in a list and extend it.

*Verifier:* Offset paging over data that can change is real: rows can be duplicated or skipped if inserts land before the loaded window. The quadratic tuple-copy claim does not matter in practice, because the page size is 200 (feed.py:31) and copying a few thousand references costs microseconds.

#### GUI-10 · LOW · performance · effort M · partially

**Investigation drawer rebuilds ~40 widgets and starts an undebounced 3-query DB worker on every selection change**  
`src/insider_scanner/gui/investigation_drawer.py:100`

`FeedPageWidget._on_row_selected` (feed_page.py:633) calls `show_record` for every selection change. Holding the Down arrow starts one `load_investigation` worker per row, each running detail + recent_by_person + recent_by_issuer queries. Stale results are thrown away by request_id, but the queries still run, fill the shared thread pool (delaying feed pages and alert checks) and extend shutdown waits. Each result then tears down and rebuilds the entire content tree (`_clear_content` + deleteLater) with a per-label `setStyleSheet`, which forces a style recalculation. `_on_palette_changed` repeats the full rebuild on theme changes.

*Suggestion:* Debounce `show_record` with a single-shot QTimer (~150 ms) and render the record summary right away from the FeedRecord. Build the drawer widgets once and update their text and colours in place: set colours through QPalette or a dynamic property plus QSS, not per-label setStyleSheet.

*Verifier:* It is true that there is no debounce and the drawer is fully rebuilt on each change. But `load_investigation` runs three queries limited to 8 rows each (feed.py:813), stale results are dropped, and only the transaction label and recent rows call setStyleSheet per label. The cost is small and not a medium problem.

#### GUI-M3 · LOW · bug · effort S · verifier-found

**Theme change during a drawer load renders the new record with the previous record's bundle**  
`src/insider_scanner/gui/investigation_drawer.py:171`

`show_record` assigns `self._record = record` right away (line 88), but `self._bundle` is only replaced in `_render` once the async load finishes. If the palette changes while a load is in flight, `_on_palette_changed` renders the new record's header together with the previous record's ownership, footnotes, filing metadata and recent activity, and hides the 'Loading…' state. If that load then fails or is cancelled, the mixed view stays.

*Suggestion:* Clear `self._bundle = None` in `show_record` before starting the worker. Alternatively, keep the record and bundle together as one `(record, bundle)` pair that is set only in `_render`, and re-render from that pair.

#### GUI-26 · LOW · simplification · effort S · partially

**Dead or redundant code in the shell and theme manager**  
`src/insider_scanner/gui/main_window.py:509`

`MainWindow._apply_global_search` has no callers. Search is routed through GlobalSearchController. `ThemeManager.apply` (manager.py:167-180) unpolishes and repolishes visible top-level widgets right after `app.setStyleSheet`, which already repolishes every widget, so the loop is extra work on each theme switch. View>Theme and the top-bar theme combo are two controls for one setting and need sync code (`_sync_theme_actions`, `_sync_theme_selector`, `_on_theme_selected`).

*Suggestion:* Delete `_apply_global_search` and the re-polish loop. Keep one theme control, or have both controls driven by `paletteChanged` through one handler.

*Verifier:* `_apply_global_search` has no production callers, but tests/test_application_shell.py:119 calls it, so removing it means updating that test. The re-polish loop after `app.setStyleSheet` and the two theme controls are redundant as described.

#### GUI-24 · LOW · bug · effort S · confirmed

**Screens menu leaks QMenu submenus each time it opens**  
`src/insider_scanner/gui/main_window.py:535`

`_rebuild_screens_menu` runs on every `aboutToShow`. `QMenu.clear()` deletes the menu's own actions but not child QMenu objects parented to `screens_menu`. Each time the menu opens, it adds one orphan QMenu (with its actions and partial-bound connections) per saved screen, up to 200 screens, and they live until the window is destroyed.

*Suggestion:* Before clearing, call `deleteLater()` on the existing submenus (for example `for m in self.screens_menu.findChildren(QMenu): m.deleteLater()`). Or rebuild only when `_feed_state.screens` changes, not on every show.

#### GUI-19 · LOW · bug · effort S · confirmed

**Price chart draws every non-buy trade (gifts, exchanges, 'Other') as a sell marker**  
`src/insider_scanner/gui/price_chart.py:163`

`is_buy` only recognises Buy/Purchase/Exercise. Everything else, including Congress 'Exchange', InsiderTrade 'Other' and gifts, is plotted with the red sell triangle. That overstates selling pressure on the chart. `marker_y_for_trade` also scans the bars linearly per trade (O(trades×bars)), when a bisect on the date-sorted bars would do.

*Suggestion:* Classify into buy/sell/other (see GUI-18). Skip 'other' or draw it with a neutral marker, and use `bisect` on the bar dates.

#### GUI-M4 · LOW · bug · effort S · verifier-found

**New scan results ignore the active filter controls in the Scan and Congress tabs**  
`src/insider_scanner/gui/scan_tab.py:432`

`_on_scan_done` always shows the unfiltered result set. The type combo, min-value spin and congress-only checkbox keep the values the user set (for example 'Buy', ≥100k), so the controls say one thing and the table shows another until 'Apply Filters' is clicked again. Save is also enabled even when `trades` is empty. CongressTab does the same at congress_tab.py:504-509, where it also resets `_filtered_trades` to the full list.

*Suggestion:* End `_on_scan_done` by calling `_apply_filters()`, or by running the same filter path, so the displayed and saved sets match the visible controls. Enable Save only when there are trades to save.

#### GUI-20 · LOW · bug · effort S · confirmed

**Amber highlighting for congress-flagged rows in the Scan tab never fires**  
`src/insider_scanner/gui/scan_tab.py:475`

`cell_foreground` (table_style.py:174-183) reads `row.get("is_congress")` from the model's DataFrame. `_display_trades` passes only `df[display_cols]`, which does not include `is_congress`, so the check is always False. The congress emphasis the code was written for (widgets.py:123-126) never appears, even though the status label reports 'N congress-flagged'.

*Suggestion:* Keep `is_congress` in the model as a hidden column (`table.setColumnHidden`), or precompute the row colours from the full trade list when setting the data (see GUI-03).

#### GUI-18 · LOW · simplification · effort S · confirmed

**Buy/sell classification and value formatting duplicated and inconsistent across pages**  
`src/insider_scanner/gui/theme/table_style.py:152`

There are at least six separate definitions: table_style (case-sensitive, includes Exercise), price_chart.BUY_TYPES (includes Exercise), and feed_page.py:113, investigation_drawer.py:32, alerts_page.py:37 and entity_pages.py:31 (casefolded, without Exercise). The same 'Exercise' trade is green in the Scan tab and on the chart but neutral in the Feed, drawer and alerts. It is counted as neither buy nor sell in entity summaries. `_format_value`/value_display formatting is likewise copied in feed_page.py:178-184, investigation_drawer.py:436, alerts_page.py:52, and the FeedTableModel column-width/table setup is duplicated in entity_pages.py:116-131.

*Suggestion:* Add one `classify_direction(trade_type) -> TransactionDirection` (matching the persistence-layer `TransactionDirection`) and one `format_value(record)` in a shared module, and use them everywhere. Extract a `make_feed_table(model)` factory for the shared QTableView setup.

#### GUI-21 · LOW · bug · effort S · confirmed

**Renaming a watchlist to an existing name creates duplicate lists**  
`src/insider_scanner/gui/watchlist_page.py:227`

`WatchlistStore.renamed` renames without checking for a collision, and the page does not validate either. Two lists end up with the same name. `store.get(name)` always returns the first, so the second list's entries cannot be reached in the UI or by watchlist-based alert rules, yet both are persisted. `_create_watchlist` at the `_MAX_WATCHLISTS` cap fails silently in a similar way.

*Suggestion:* Reject names that already exist (case-insensitive) with a message box before applying, and also raise in `renamed()`. Tell the user when the list or entry cap is reached.

#### GUI-16 · LOW · simplification · effort M · partially

**Worker boilerplate copied in ~8 places, `dispatch()` underused, and lambda receivers not tied to widget lifetime**  
`src/insider_scanner/utils/threading.py:21`

`WorkerSignals()` is created with no parent, so it is never destroyed with a widget, and `_safe_emit` does not provide the protection the docstring claims. The real risk is on the receiver side. feed_page.py:499, entity_pages.py:159, investigation_drawer.py:101, global_search.py:145 and main_window.py:414 connect `lambda ...: self._on_x(request_id, ...)`. Lambdas are not disconnected automatically when the widget is deleted, so a late result calls into a deleted C++ object and raises 'Internal C++ object already deleted'. Today only the MainWindow-level waitForDone prevents this. The 'create Worker, connect lambdas, start, check request_id' pattern is repeated in each of those files. `dispatch()` already exists for it but is used only by Scan/Congress. The watchlist scan (scan_tab.py:413) calls `worker.signals.progress.emit` directly, bypassing both `_safe_emit` and the built-in `emit_progress`/`progress_callback` support. FeedPage also keeps a `_workers` dict for auto-deleting runnables that nothing reads.

*Suggestion:* Add a small helper, for example `LatestRequest(owner: QObject, pool)`, with `start(fn, *args, on_result, on_error)`. It bumps the id, wraps the callbacks with the stale check, and connects them through a `QObject` context (or bound methods) so connections die with the owner. Use it in all pages. Fix the Worker docstring, use `dispatch(on_progress=...)` with `progress_callback` in the watchlist scan, and drop `FeedPageWidget._workers`.

*Verifier:* These parts are true: the Worker docstring overstates what `_safe_emit` protects, the boilerplate is repeated in six sites (analysis, entity, feed, global_search, drawer, main_window), and the watchlist scan calls `worker.signals.progress.emit` directly (scan_tab.py:413). The deleted-receiver risk is mostly theoretical. Every page lives as long as MainWindow, and a late call into a deleted widget raises a Python RuntimeError rather than crashing the process.

### CLI, config, utils, packaging, CI (CLI) — 27 items

> The CLI, config and packaging layer is generally careful. Paths come from platformdirs, seed files are copied atomically, the SEC user agent is validated before network work starts, release publishing uses OIDC Trusted Publishing with SHA-pinned actions, and the API key files are gitignored and were never committed. The most serious problems are wrong results in the common path. (1) The shared parse_date helper returns None for OpenInsider's "YYYY-MM-DD HH:MM:SS" filing dates, which I confirmed against the live site. Every OpenInsider row is therefore stored without a filing date and then dropped by --since/--until filters. (2) `sec-daily` defaults to today, whose index has not been published yet, and the downloader treats a 404 index as a completed day, so today is permanently recorded as covered with zero filings. (3) `scan TICKER` without dates never queries the ticker sources; it only refreshes the market-wide OpenInsider latest feed. For the slow full SEC backfill the CLI gives no help: it shows no progress, Ctrl+C has no clean handling, and it exits 0 even when filings failed. The likely cause of the slowdown sits in services/sec_backfill.py (reported by the services area): it rewrites the whole sorted checkpoint after every filing. Other gaps: logging is stderr-only with no file, even though six messages say "see logs"; the HTTP helper's rate limiter is not thread-safe and it reuses no connections; CI ignores uv.lock, so Dependabot lockfile bumps are untested; mypy and pip-audit are installed but never run; the release workflow does not check that the tag matches the package version; and a stale duplicate data/ seed copy exists that the congress update script writes to.

#### CLI-M1 · HIGH · performance · effort L · verifier-found

**Full backfill design makes one rate-limited HTTP request per filing, so it cannot finish in hours however the checkpoint is fixed**  
`src/insider_scanner/cli.py:30`

submissions.zip contains only filing metadata. For every Form 3/4/5 accession, SecBackfillService.run calls process_filing_row, which calls fetch_filing, which calls client.fetch_bytes (one archive GET per filing, sec_downloader.py:98). SecClient's shared RateLimiter defaults to DEFAULT_REQUESTS_PER_SECOND = 8.0 (sec_fair_access.py:23), so throughput is capped at about 28,800 filings per hour. Several hundred thousand ownership filings are made each year, and history goes back to the early 2000s, so a full backfill needs millions of fetches, i.e. many days of wall-clock time. On top of that, the services-area defect rewrites the sorted checkpoint after every filing, which slows each step further as the set grows. This matches the user's report that it takes hours without reaching half. Fixing only the checkpoint I/O leaves the network floor in place.

*Suggestion:* For historical backfill, use SEC's quarterly Form 3/4/5 'Insider Transactions Data Sets' (structured TSV per quarter: submissions, reporting owners, non-derivative and derivative transactions). Ingest them offline in bulk with batched upserts, and keep the per-filing XML path for the recent window and for gaps. If the per-filing path is kept, document the realistic rate in SEC_BULK_INSTRUCTIONS, encourage --cik scoping, and skip accessions already in the DB from sec-daily/catchup before fetching.

#### CLI-03 · HIGH · bug · effort S · confirmed

**`scan TICKER` without --since/--until never queries ticker sources; it returns only what is already in the local DB**  
`src/insider_scanner/cli.py:81`

With no dates, UsScanService.scan (services/us.py:65-84) takes the unbounded path. That path only refreshes `_latest_adapters` (OpenInsider's market-wide latest feed, LATEST_OVERLAP_COUNT=200 rows, not filtered by ticker) and then queries the local DB. 'secform4' is selected by default but has no latest adapter, so it is never fetched. On a fresh database, the first README example (`insider-scanner-cli scan AAPL`, README.md:126) and `scan AAPL --type Buy --min-value 1000000 --save` return almost nothing for AAPL even though the command says it is 'Scanning insider trades for AAPL'.

*Suggestion:* Give scan a bounded default window when no dates are given, like cmd_price does with 365 days (until=today, since=until-365d), so the per-ticker adapters run. Or add an explicit `--local-only` flag for the current behaviour and document it.

#### CLI-02 · HIGH · bug · effort S · confirmed

**sec-daily defaults to today; a 404 index for today is recorded as covered, leaving a permanent gap**  
`src/insider_scanner/cli.py:442`

The help text and README both say the SEC daily index lags about one business day, yet the command defaults to today. In services/sec_downloads.py:201-203 `except SecFilingNotFoundError: ... return True` treats a missing index as a fully handled empty day. download_range then adds it to `completed` and emits progress, and the coverage recorder in sec_daily.py:258-264 writes coverage for that date. When the index is published later, running `sec-daily --date <that day>` or a `sec-catchup` over the range finds no gap and skips the day, so the filings are never ingested and nothing warns about it. `sec-catchup --until <today>` has the same problem, and so does any range that includes a day whose index is not up yet.

*Suggestion:* In the CLI, default --date to the previous business day, and reject or warn on dates >= today. In the downloader, return False (not covered) for a 404 on recent dates, e.g. within 3 business days of today, and only treat 404 as an empty day for weekends, holidays or older dates. Add a regression test: a 404 for today must not create coverage.

#### CLI-01 · HIGH · bug · effort S · confirmed

**parse_date returns None for OpenInsider datetime filing dates, so OpenInsider rows lose filing_date and are dropped by date filters**  
`src/insider_scanner/utils/parsing.py:24`

OpenInsider shows the Filing Date column as a datetime. A live check of the AAPL screener returned values like '2026-11-12 18:58:20'. date.fromisoformat rejects a string that includes a time. The fallback then splits on '-' and gets ['2026','11','12 18:58:20'], and int('12 18:58:20') raises, so parse_date returns None (verified locally: parse_date('2024-01-15 10:30:00') -> None). openinsider.py:202 sends every filing date through this helper, so every OpenInsider trade is stored with filing_date NULL. Effects: merger.filter_trades (merger.py:189-191) requires `t.filing_date and t.filing_date >= since`, so `scan --since/--until` and `latest --since/--until` drop all OpenInsider rows. Repository range queries on us_trades.c.filing_date (repositories.py:124-126, 167-169) exclude them. Dedup in merger.py:49 and sec-compare also lose the field. No test fixture contains a datetime filing date.

*Suggestion:* Strip a time part before parsing, e.g. take text.split()[0] or text[:10] when the text matches a YYYY-MM-DD prefix, or try datetime.fromisoformat(text).date() first. Add a parsing test and an OpenInsider fixture row with '2026-11-12 18:58:20'. Then backfill filing_date for rows already persisted, by re-scanning or with a migration that re-parses from source.

*Also reported as:* ARCH-12

#### CLI-14 · MEDIUM · testing · effort S · confirmed

**CI installs unpinned dependencies with pip and ignores uv.lock, so Dependabot lockfile bumps are untested**  
`.github/workflows/ci.yml:35`

The repository commits uv.lock (90 packages), and recent history consists of merged Dependabot PRs that bump only uv.lock (e.g. 1d46281 'bump msgpack'). CI resolves fresh versions from pyproject ranges with pip, so these PRs run the same tests as before and the locked versions are never exercised. Equally, an upstream release inside an allowed range (pandas <4, numpy <3, PySide6 <7) can break CI or users on any day without a code change. The editable install also means CI never tests the built wheel; packaging is only checked at release time.

*Suggestion:* Use astral-sh/setup-uv with `uv sync --locked --all-extras` (or `uv export` piped into pip install -r) in the test job. Optionally keep a separate weekly job against the latest resolved dependencies. Add a cheap `python -m build && pip install dist/*.whl && python scripts/verify_install.py` job to CI so packaging regressions show up before tagging.

#### CLI-13 · MEDIUM · simplification · effort S · confirmed

**init-congress is always a no-op, seed updates never reach users, and data/ holds a stale duplicate seed**  
`src/insider_scanner/cli.py:168`

run() calls ensure_dirs() before every command, and ensure_dirs already copies the packaged congress_members.json seed into CONGRESS_FILE. init_default_congress_file (senate.py:127-129) then sees that the file exists and does nothing. The command still prints 'created', so the init-congress command in README.md:140 never does anything._copy_seed_if_missing (config.py:101) also never replaces an existing user file, so a newer packaged seed never reaches existing installs, and there is no refresh or reset command. Meanwhile scripts/update_congress.py:57 writes `DEFAULT_OUTPUT = _project_root / "data" / "congress_members.json"`. That repo copy is tracked and currently byte-identical to src/insider_scanner/resources/seeds/congress_members.json, but the app never reads it, so running the update script changes nothing in the app or the package.

*Suggestion:* Replace init-congress with `init-congress --force` (or `reset-seeds`), which overwrites the user copy from the packaged seed after a backup, and print accurate messages. Point update_congress.py's default output at the packaged seed path, delete the tracked data/congress_members.json and data/tickers_watchlist.txt duplicates, and keep data/ only for untracked local artifacts.

#### CLI-M2 · MEDIUM · bug · effort S · verifier-found

**A scoped `sec-backfill --cik` run shares the full-backfill checkpoint and deletes it on completion**  
`src/insider_scanner/cli.py:518`

cmd_sec_backfill always passes the same checkpoint file (EDGAR_CACHE_DIR/sec_backfill_checkpoint.json), whether or not --cik is given. SecBackfillService._load_completed validates only version and zip name (sec_backfill.py:190-193), and run() calls_delete_checkpoint() whenever the loop was not cancelled (lines 164-166). Sequence: a user starts a full backfill, interrupts it after hours, then runs `sec-backfill --zip same.zip --cik 0000320193 --confirm-full-backfill`. The scoped run finishes 'fully' and deletes the checkpoint, so all resume state from the full run is lost. The next full run starts from zero, and only filings whose cached copy is still fresh avoid re-fetching.

*Suggestion:* Use a separate checkpoint per scope (e.g. include a hash of the sorted CIK set in the filename), or record the ciks filter in the checkpoint and only delete it when the completed run had the same scope. Add a test that a --cik run does not clear an unfinished full-run checkpoint.

#### CLI-04 · MEDIUM · practice · effort M · confirmed

**sec-backfill gives no progress during a multi-hour run and passes no on_progress/cancel hook**  
`src/insider_scanner/cli.py:521`

The full backfill is the process the user reports as taking hours without reaching half. The CLI prints one line and then nothing until the end, so the user cannot tell whether it is progressing, stalled or slowing down. sec-daily and sec-catchup have per-date progress and --quiet; sec-backfill has neither. SecBackfillService.run accepts on_progress but only calls it once, after the loop (sec_backfill.py:180-181). The CLI also passes no `cancelled` callback, so the only way to stop is Ctrl+C (see CLI-06). Without progress output, the slowdown cannot be diagnosed from the CLI. The likely cause is in services/sec_backfill.py:159-160 and 199-210: after every filing it sorts the whole completed set and rewrites it atomically, which is O(n^2) I/O over hundreds of thousands of accessions. That defect belongs to the services area.

*Suggestion:* Make the service emit progress every N filings or every few seconds (discovered/parsed/failed/skipped_resume, plus rate and ETA). Give sec-backfill the same `_make_cli_progress`-style stderr reporter and a --quiet flag. Wire a cancellation flag set by a SIGINT handler so Ctrl+C stops cleanly after the current filing and prints the partial summary.

#### CLI-M3 · MEDIUM · bug · effort S · verifier-found

**`--no-cache` does not bypass DB coverage or refresh freshness, so scans still return stale persisted rows**  
`src/insider_scanner/cli.py:544`

The flag only flows to `use_cache`, which disables the HTTP file cache inside adapters. In UsScanService.scan, a bounded scan fetches only `coverage.gaps(...)` (us.py:91-94), and an unbounded scan skips sources whose refresh timestamp `is_fresh` (us.py:193-195). Neither check looks at use_cache. So `scan AAPL --since X --until Y --no-cache` over an already-covered range makes no network request and returns the old DB rows, including rows stored with a NULL OpenInsider filing_date (CLI-01). The flag has no help text explaining this, and the same applies to `latest --no-cache` (line 563) and `resolve-cik --no-cache`.

*Suggestion:* Either have use_cache=False force a re-fetch (ignore coverage and refresh freshness for that call and re-upsert), or rename the flag to `--no-http-cache` with help text and add a separate `--refresh` option that bypasses coverage.

#### CLI-12 · MEDIUM · practice · effort S · confirmed

**Logging is stderr-only, with no file or rotation, yet errors say 'See logs for details'**  
`src/insider_scanner/utils/logging.py:10`

No FileHandler exists anywhere in src. Six user-facing messages, including cli.py:806 and the main.py QMessageBox dialogs, tell users to 'See logs for details'. For the GUI, logs exist only in the console window and disappear when it closes. For a multi-hour backfill there is no persistent record of which filings failed. The level is fixed at INFO with no --verbose/--log-level option, third-party loggers inherit INFO, and the time format omits the date, so logs from runs that cross midnight are ambiguous.

*Suggestion:* Add a RotatingFileHandler (e.g. 5 MB x 5) under platformdirs user_log_path and put that path in the 'see logs' messages. Add `--log-level`/`-v` to the CLI parser, or an INSIDER_SCANNER_LOG_LEVEL env var. Use '%Y-%m-%d %H:%M:%S' and set noisy libraries (urllib3, charset_normalizer) to WARNING.

#### CLI-15 · LOW · practice · effort S · confirmed

**mypy and pip-audit are dev deps but never run in CI; ruff is unpinned; workflow token permissions are unset**  
`.github/workflows/ci.yml:65`

pyproject's dev extra installs mypy>=1.10 and pip-audit>=2.0 (pyproject.toml:63,67), but no workflow runs them, and there is no [tool.mypy] or [tool.ruff] config. Type errors and vulnerable dependencies therefore go undetected. The lint job installs the latest ruff instead of the pinned or dev version, so a ruff release that changes formatting or adds default rules can turn CI red with no repo change. ci.yml has no top-level `permissions:` block (release.yml does), so jobs get the repository's default GITHUB_TOKEN scope. .github/ also has no dependabot.yml for the github-actions ecosystem, so the SHA-pinned actions are never bumped.

*Suggestion:* Add a `mypy src/insider_scanner` step, starting with a [tool.mypy] baseline config, and a `pip-audit` step against the locked requirements. Pin ruff (e.g. `ruff==0.15.x`, or install via `.[dev]`/uv). Add `permissions: contents: read` to ci.yml and a .github/dependabot.yml covering the github-actions and uv ecosystems.

*Also reported as:* TESTS-19

#### CLI-16 · LOW · practice · effort S · partially

**Release workflow does not check that the tag matches the package version and runs no tests before publishing**  
`.github/workflows/release.yml:22`

Any pushed tag matching v*.*.* builds whatever version pyproject.toml declares (currently hard-coded as 2.0.0, and duplicated in src/insider_scanner/**init**.py:3). If the tag is v2.0.1 and the version was not bumped, the job uploads 2.0.0 to TestPyPI, or fails because the file already exists. verify-testpypi (line 135) then installs `==2.0.1` and fails after about 3.5 minutes of retries. Tests and lint are not part of the release pipeline, so a tag pushed on an unreviewed commit can reach PyPI after only import smoke tests. verify-testpypi uses `--extra-index-url https://pypi.org/simple/` together with TestPyPI, which allows dependency confusion from squatted TestPyPI names during verification.

*Suggestion:* Add a first step that fails unless `${GITHUB_REF_NAME#v}` equals the version read from pyproject. Make **version** derive from importlib.metadata.version() so there is one source of truth, and add `--version` to the CLI. Gate build on the test and lint jobs, via a reusable workflow or `needs:`. In verify-testpypi, install dependencies from PyPI first, then install insider-scanner from TestPyPI with --no-deps.

*Verifier:* There is no tag/version check and no test or lint gate before publishing, and the version is duplicated in **init**.py and pyproject. However, a tag/version mismatch cannot reach PyPI: verify-testpypi installs ==${tag} and fails (about 210 s of sleeps), and publish-pypi needs it. The damage is limited to TestPyPI pollution or a failed run. The --extra-index-url dependency-confusion point applies only to the verification install.

#### CLI-24 · LOW · practice · effort S · confirmed

**README CLI reference is incomplete or misleading for several commands**  
`README.md:140`

The README documents init-congress, which never does anything (CLI-13), and `scan AAPL` without dates, which is local-only (CLI-03). It does not mention two commands that exist: `price` (cli.py:636) and `sec-compare` (cli.py:728), which is only described in docs/edgar_planning. Lines 170-171 say sec-daily 'defaults to today' while also saying the index lags, which is exactly the setup behind CLI-02. `latest --save` (README.md:133) never prints where the file went; cmd_latest at cli.py:140 discards save_scan_results' return value, unlike cmd_scan.

*Suggestion:* Regenerate the CLI section from `build_parser()` help, or extend tests/test_readme_commands.py to assert that every subparser is documented. Fix the default-date wording, and print the saved path in cmd_latest.

#### CLI-22 · LOW · simplification · effort S · confirmed

**Runtime dependencies include packages the package itself never imports**  
`pyproject.toml:42`

No module under src/insider_scanner imports yaml, cryptography or numpy. pyyaml is used only by scripts/update_congress.py, a maintainer script that is not shipped in the wheel, yet every end user installs it. cryptography and numpy are probably meant as security or compatibility floors for transitive deps (pdfminer and pandas), but nothing says so. README.md:44 presents them as direct dependencies.

*Suggestion:* Move pyyaml to a `scripts` or `maintainer` extra. If cryptography and numpy are kept as floors, add a comment explaining why, or move them to a constraints section or uv `constraint-dependencies` so their purpose is clear. Update the README dependency list.

#### CLI-17 · LOW · security · effort S · confirmed

**scripts/api_key.py: CWD-relative plaintext key file, shell os.system with bare 'python', exit status ignored, key duplicated**  
`scripts/api_key.py:3`

The wrapper only works when run from scripts/ (the paths are CWD-relative). It runs whichever `python` comes first on PATH rather than sys.executable, which on this machine is a 3.15 interpreter outside .venv. It goes through a shell, discards the child's exit code (so failures look like success), and the redundant f.close() inside `with` adds nothing. The same OpenStates key is stored in plaintext in two places, scripts/api_key.txt and stateCongess_apiKey.txt in the repo root. Both are gitignored and absent from git history and graphify-out (verified), but every copy is one ignore-rule change away from being committed. update_congress.py already reads OPENSTATES_API_KEY from the environment, so the wrapper adds risk without adding function.

*Suggestion:* Delete scripts/api_key.py and the duplicate stateCongess_apiKey.txt. Have update_congress.py read the key from the env var, falling back to the OS keyring or a .env file outside the repo, and document `$env:OPENSTATES_API_KEY=...` for PowerShell. If a wrapper is kept, use pathlib relative to **file**, subprocess.run([sys.executable, ...], check=True) and pass the env explicitly.

#### CLI-23 · LOW · testing · effort S · partially

**Install smoke test only runs `--help`, so DB bootstrap and seed copying in an installed wheel are never exercised**  
`scripts/verify_install.py:41`

`--help` exits inside parse_args, before ensure_dirs() and open_application_services() run. The release gate therefore never checks that seeds copy, migrations run and SQLite opens from the installed wheel, which is the part most likely to break from packaging (package-data globs, importlib.resources paths). The font check is also duplicated between this script and main.verify_gui_install.

*Suggestion:* Add one offline command run with temp data and cache dirs (needs the env override from CLI-20), e.g. `insider-scanner-cli import-legacy <tiny fixture>` or a dedicated `insider-scanner-cli doctor` that runs ensure_dirs and opens and migrates the DB. Let check_fonts reuse `insider-scanner --verify-install` only.

*Verifier:* check_cli runs only --help, which exits before ensure_dirs and DB open, which is true. However, check_resources (verify_install.py:79-98) already verifies that the packaged seeds resolve from the installed wheel, and the migrations are pure Python (persistence/migrations.py), so they are imported by --help. The remaining packaging risk is small. The duplicated font check is real.

#### CLI-M4 · LOW · practice · effort S · verifier-found

**resolve-cik exits 0 when the CIK cannot be resolved**  
`src/insider_scanner/cli.py:157`

cmd_resolve_cik returns None on both paths, so run() returns 0 even when resolution failed, including when the edgar fetch failed and was swallowed (fetch errors are logged and None is returned). Scripts that chain `resolve-cik` cannot detect failure. The other commands (import-legacy, sec-compare) do return non-zero on failure.

*Suggestion:* Return 1 (or a documented code) and print to stderr when cik is None. Add a test_cli case for it.

#### CLI-05 · LOW · bug · effort S · partially

**SEC ingestion commands exit 0 even when dates or filings failed**  
`src/insider_scanner/cli.py:470`

cmd_sec_daily (line 445), cmd_sec_catchup (line 470) and cmd_sec_backfill (line 523) always return 0. The exit code is the same when summary.failures > 0, when dates_completed < dates_total (for example, the daily index fetch failed on a 5xx and the date was left uncovered), and when the backfill was interrupted. A scheduled job (Task Scheduler or cron) cannot tell a clean run from a run that missed whole days. README.md:181-183 documents only codes 0, 1 and 2.

*Suggestion:* Return a distinct non-zero code (e.g. 3) when dates_completed < dates_total or the backfill did not finish. Optionally add a `--strict` flag that also fails on per-filing failures > 0. Document the codes in the README and cover them in test_cli.py.

*Verifier:* All three commands return 0 unconditionally, which is true. However, README.md:180-181 explicitly documents that per-filing failures do not fail the command, so that part is intended behaviour. The 'backfill interrupted' case cannot happen through the CLI: no cancelled callback is passed, and Ctrl+C raises instead. What remains valid: dates_completed < dates_total (e.g. an index 5xx) still exits 0.

#### CLI-07 · LOW · practice · effort S · confirmed

**Invalid user input (bad --source, --count 0, only --since) is reported as a crash with exit 1**  
`src/insider_scanner/cli.py:559`

--count accepts 0 or negative numbers, --source (lines 545-554, 564-573) has no `choices`, and scan/latest/price accept a lone --since or --until. All of these are only checked deep in the services: `count must be positive` (us.py:130), `unknown source(s)` (common.py:79), `start_date and end_date must both be present` (common.py:58-61). The resulting ValueError reaches the generic handler in run(), which logs a full traceback and prints 'Command failed. See logs for details.' with exit 1. For example, `scan AAPL --since 2025-01-01` fails this way. argparse usage errors use exit 2, and sec-compare already maps ValueError to exit 2 (lines 400-402). cmd_price also never checks since <= until.

*Suggestion:* Use `type=_parse_positive_int` for --count and `choices=('secform4','openinsider','sec_edgar')` for --source. Default a missing --until to today (or reject a lone bound with parser.error). In run(), catch ValueError from commands and print the message to stderr with exit 2, without a traceback.

#### CLI-06 · LOW · bug · effort S · partially

**KeyboardInterrupt skips services.close() and shows a raw traceback**  
`src/insider_scanner/cli.py:804`

`except Exception` does not catch KeyboardInterrupt. Close is done in straight-line code after the try block, not in `finally`. Ctrl+C is the only way to stop a long sec-backfill or sec-catchup run. When it is pressed, services.close() (the persistence engine) is skipped, the user sees a Python traceback instead of a summary, and the exit code is not the conventional 130. SQLite rolls back the open transaction, but close/dispose work (connection cleanup and any WAL checkpoint) is skipped every time a long run is interrupted.

*Suggestion:* Move services.close() into a `finally:` block and add `except KeyboardInterrupt: print('Interrupted', file=sys.stderr); result = 130`. Better still, combine this with a cooperative cancel flag (CLI-04) so the services can print their partial summary.

*Verifier:* `except Exception` at line 804 does not catch KeyboardInterrupt, and close() runs in straight-line code rather than a finally block, so close is skipped and a traceback is shown. The impact is overstated: upserts are committed per filing, the backfill checkpoint is saved per filing, and SQLite recovers the WAL on the next open, so nothing is lost. This is mainly a UX and cleanliness issue.

#### CLI-18 · LOW · bug · effort S · confirmed

**HTTP file cache writes are not atomic and expired entries are never pruned**  
`src/insider_scanner/utils/caching.py:47`

The body is overwritten in place before the metadata. If the process dies mid-write, or two threads write the same key, a truncated .txt is left next to the still-valid old .meta. get_cached then returns the truncated HTML within the TTL, and the scraper parses partial pages silently. get_cached also does not catch OSError or UnicodeDecodeError. clear_cache (line 52) has no callers, so expired .txt/.meta pairs accumulate in the scraper cache directory indefinitely.

*Suggestion:* Store body and timestamp in one file (or use the file's mtime), written to a temp file and published with os.replace. Treat read errors as a cache miss. Purge expired entries on startup or in an explicit `cache clear` command.

*Also reported as:* SECIO-16

#### CLI-20 · LOW · practice · effort S · confirmed

**No env override for data/cache roots; on Windows the app name is doubled in the path**  
`src/insider_scanner/utils/config.py:47`

APP_NAME and APP_AUTHOR are both 'Insider Scanner', so on Windows the paths become %LOCALAPPDATA%\Insider Scanner\Insider Scanner\ and ...\Insider Scanner\Cache. More importantly, the roots can only be overridden by injection in tests. Users cannot move the SQLite database or the large EDGAR cache used by backfill to another drive, and cannot run an isolated profile, for example for sec-compare experiments. DEFAULT_PATHS is computed at import time, so callers cannot change it after import either.

*Suggestion:* Honour INSIDER_SCANNER_DATA_DIR and INSIDER_SCANNER_CACHE_DIR env vars (and/or `--data-dir` on the CLI) in resolve_runtime_paths. Pass appauthor=False to PlatformDirs to avoid the doubled folder; if existing installs matter, add a one-time migration or keep the old path.

#### CLI-21 · LOW · simplification · effort S · confirmed

**Dead aliases, duplicated constants and import-time env read in config**  
`src/insider_scanner/utils/config.py:71`

PROJECT_ROOT and DB_PATH have no references anywhere in src or scripts, and PROJECT_ROOT misleadingly points at the user data dir, not the project. SEC_MAX_REQUESTS_PER_SECOND is defined twice: as int 10 here (line 93) and as float 10.0 in core/sec_fair_access.py:22, so the two can drift apart. SEC_USER_AGENT (lines 89-92) is read from os.environ once at import, so tests and the GUI cannot change it at runtime, and there is no .env support even though the README requires setting it every session. fetch_url's `cache_ttl: int = 3600` (http.py:35) duplicates DEFAULT_CACHE_TTL.

*Suggestion:* Remove PROJECT_ROOT and DB_PATH. Keep one SEC rate constant, in sec_fair_access, and import it. Read SEC_USER_AGENT through a function (e.g. get_sec_user_agent()) at the point of use. Default cache_ttl to DEFAULT_CACHE_TTL.

#### CLI-19 · LOW · bug · effort S · partially

**Seed publishing relies on os.link, which fails on filesystems without hard-link support**  
`src/insider_scanner/utils/config.py:120`

Hard links raise a generic OSError (not FileExistsError) on FAT32/exFAT drives, many network shares and some sync or virtual filesystems. ensure_dirs() runs at every CLI and GUI start, so the error turns into 'Could not initialize local database' (cli.py:797, main.py:92) and the app cannot start if the data directory is relocated to such a volume.

*Suggestion:* Fall back to `os.replace` guarded by a re-check of target.exists() (or open the target with O_CREAT|O_EXCL and copy) when os.link raises OSError other than FileExistsError.

*Verifier:* os.link raising a non-FileExistsError OSError would propagate, which is true. In practice the data dir is always platformdirs' user_data_path (%LOCALAPPDATA% on NTFS on Windows), the code offers no way to relocate it (see CLI-20), and the copy runs only when the seed is missing. Reaching it requires an unusual home filesystem such as some CIFS mounts.

#### CLI-11 · LOW · performance · effort S · partially

**fetch_url opens a new connection per request and has no retry or backoff**  
`src/insider_scanner/utils/http.py:85`

Each call to requests.get creates a throwaway Session, which means a new TCP and TLS handshake for every page of openinsider, secform4 and EDGAR. Paged scans pay that latency on every request. A transient 429, 503 or connection reset fails the whole source scan immediately, with no retry and no honouring of Retry-After. `resp.text` also triggers charset detection on large pages that have no charset header.

*Suggestion:* Use a module-level or injected requests.Session with an HTTPAdapter and urllib3 Retry (total=3, backoff_factor, status_forcelist={429,500,502,503,504}, respect_retry_after_header=True). Decode with resp.content plus a known encoding where possible.

*Verifier:* There is no Session reuse and no retry/backoff, which is true. However, neither scraper pages: openinsider fetches one URL with cnt=100 and secform4 fetches one CIK page, so a scan makes only about 1-3 requests. The 'paged scans pay latency on every request' claim does not hold, and this path is not hot. The missing retry for transient 429/503 is the real part of the finding.

#### CLI-09 · LOW · bug · effort S · confirmed

**parse_date turns two-digit years into year 24 AD**  
`src/insider_scanner/utils/parsing.py:32`

For 'MM/DD/YY' input the fallback builds date(24, 1, 15). I verified that parse_date('01/15/24') -> datetime.date(24, 1, 15). The result is a valid but absurd date that passes silently into persistence and sorting. parse_ptr_date in the same module handles %m/%d/%y correctly, so the two helpers behave differently on the same input.

*Suggestion:* Use strptime with an explicit list of formats ('%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%m/%d/%y'), or reject years < 1900. Share one implementation with parse_ptr_date and keep only the placeholder handling separate.

#### CLI-08 · LOW · bug · effort S · partially

**classify_trade maps OpenInsider 'M - OptEx'/'X - OptEx' to Other instead of Exercise**  
`src/insider_scanner/utils/parsing.py:68`

OpenInsider labels trade types with a code plus an abbreviation, e.g. 'M - OptEx', 'X - OptEx', 'S - Sale+OE', 'D - Sale to issuer'. 'optex' contains neither 'exercise' nor 'option', and the `t == 'm'` check only matches a bare 'm'. I verified that classify_trade('M - OptEx') and classify_trade('X - OptEx') both return 'Other', so `scan --type Exercise` never matches OpenInsider option exercises. Matching on substrings also means 'D - Sale to issuer' (a disposition back to the issuer) is labelled Sell.

*Suggestion:* Parse the leading transaction code first (the text before ' - ': P->Buy, S->Sell, M/X->Exercise, everything else Other), then fall back to keyword matching. Add test cases for the actual OpenInsider labels.

*Verifier:* classify_trade('M - OptEx') and classify_trade('X - OptEx') do return 'Other' (verified). The claimed impact is not reachable today, though: both OpenInsider scrapers send `xp=1` (openinsider.py:59,106), which is OpenInsider's Purchase-only filter, so OptEx and 'Sale to issuer' rows are never fetched. secform4 also has a CSS-class fallback to Exercise. The bug is latent.

### Cross-cutting architecture and duplication (ARCH) — 16 items

> The layering is mostly sound: core scrapers and parsers, then persistence repositories, then DB-first services, then GUI/CLI, and the SEC path has a hardened, injectable client. The main cross-cutting defect is a contract mismatch between scrapers and services. Services treat "the adapter returned without raising" as "the date range is fully covered" and write that permanently to scan_coverage. But the legacy scrapers (secform4, openinsider, House, Senate) swallow network and parse errors, silently cap results at 100 or 1000 rows, and use inclusive or exclusive end dates inconsistently. The House index for the current year is also never refreshed. Each of these leaves a permanent hole in local data that later scans never refill. The slow full backfill you reported has several compounding causes. (1) The resume checkpoint is a sorted JSON of every completed accession, rewritten and fsynced after every filing, which is O(n^2). (2) Every filing is fetched over the network one at a time at about 8 requests per second, with no HTTP keep-alive. (3) Every filing also gets a 24-hour cache file with its own fsync, which is useless for backfill. (4) SQLite runs with NullPool, no WAL and one transaction per filing. (5) There is no progress output. Beyond that, the code has three independent rate limiters, two dedup-key definitions that disagree, duplicated House/Senate helpers, and two watchlist systems. Some layering is violated: core/prices opens its own DB, the GUI builds repositories directly and imports the CLI module, and the GUI thread makes a blocking network call. There is also dead code in edgar.py, config.py and congress_house.py, plus an unused dependency.

#### ARCH-08 · HIGH · performance · effort S · confirmed

**No HTTP connection reuse in any SEC or scraper path**  
`src/insider_scanner/core/sec_client.py:84`

Every SEC request (daily index, each filing, backfill) does a fresh DNS lookup, TCP connect and TLS handshake to <www.sec.gov>, because `requests.get` creates a throwaway Session. The same is true of utils/http.fetch_url (http.py:85, used by edgar, secform4 and openinsider) and congress_house (138, 343). At 8 req/s, the handshake (about 100-300 ms) is a large share of each request's latency, so the effective rate drops well below the limiter ceiling.

*Suggestion:* Give RequestsSecTransport a long-lived `requests.Session` with an HTTPAdapter (pool_maxsize, keep-alive) owned by the SecClient or ApplicationServices and closed in close(). Do the same for a shared scraper session in utils/http.

#### ARCH-11 · HIGH · bug · effort M · confirmed

**Congress official-name contract mismatch: query identifier never equals the stored official_name**  
`src/insider_scanner/persistence/repositories.py:256`

The Congress tab passes `official_name` from congress_members.json (e.g. 'Nicholas J. Begich III'; congress_tab.py:78). The House scraper matches by substring but stores names as 'prefix first last suffix' (e.g. 'Hon. Nicholas Begich III'). The Senate scraper stores f"{first} {last}" from EFD, and _split_name turns 'Nicholas J. Begich III' into first='Nicholas', last='J. Begich III' (congress_senate.py:492-500), so the EFD search itself misses. CongressScanService._query then uses exact string equality, so even successfully fetched rows are not returned for a single-official scan. Coverage is still recorded under the GUI string.

*Suggestion:* Introduce a stable official key, such as bioguide_id from the seed or a normalised 'last|first-initial' key, set by the scrapers (match the index row back to the member list) and used for coverage and queries. At minimum, normalise (casefold, strip prefixes, suffixes and middle initials) on both write and query, and split names using the seed's first_name/last_name fields instead of guessing.

#### ARCH-06 · HIGH · performance · effort L · partially

**Full backfill fetches millions of filings one by one at 8 req/s with no batching, and caches each to a 24h flat-dir cache with fsync**  
`src/insider_scanner/services/sec_backfill.py:150`

submissions.zip contains only metadata, so every Form 3/4/5 accession costs one serial HTTP GET of the full .txt submission. SecClient's limiter defaults to 8 req/s (sec_fair_access.py:23). Roughly 10M historical ownership filings is about 2 weeks of wall-clock time at best. Each accession also appears in both the issuer's and every reporting owner's CIK JSON, so the 1.5 GB archive is fully decoded and `discovered` is inflated. Every downloaded filing is also written into a single flat directory `validated-v1/` with fsync (sec_downloader.py:298-308, called via promote_validated_filing at sec_downloads.py:282). That cache expires after 24h (cache_freshness_seconds), so for backfill it is pure I/O cost plus potentially hundreds of GB of disk use until the run finishes.

*Suggestion:* For history, switch to SEC's quarterly 'Insider Transactions Data Sets' (Form 3/4/5 structured TSV ZIPs from DERA). That is about 80 files instead of millions of requests, and turns weeks into minutes. Keep per-filing fetch only for incremental daily ingest. If per-filing backfill stays: skip the validated cache for backfill (promote is optional), deduplicate accessions before fetching, default to issuer CIKs only (or the --cik filter), and use a pooled keep-alive session (ARCH-08).

*Verifier:* Each accession is fetched serially through SecClient at 8 req/s or less. Each one is then promoted into the flat validated-v1 directory with fsync (sec_downloader._write_atomically), and those files are purged only when the full run ends. Duplicate accessions across issuer and owner CIK files inflate 'discovered' and the decode work, but they are not fetched twice: once the first copy succeeds it goes into `completed`, and the later copy is skipped as skipped_resume. The suggestion to 'deduplicate before fetching' is therefore mostly already in place. The DERA data-set recommendation is sound.

#### ARCH-M1 · HIGH · bug · effort S · verifier-found

**SEC daily/catch-up marks a date covered even when individual filings failed to download or parse**  
`src/insider_scanner/services/sec_downloads.py:258`

process_filing_row returns True for fetch, parse and promote failures (lines 253-258, 271-279, 283-288). Only a sink exception returns False. _process_date therefore returns True for a day where, say, 30 of 400 filings failed with SecTransportError, SecHttpError 500 or 403, or a 429 after the retries ran out. download_filings_in_range then fires on_progress, and SecDailyIngestionService._record_coverage writes the date into scan_coverage. Every later sec-daily or sec-catchup skips that date, so the failed filings are never retried. The hardened SEC path has the same 'returned means covered' hole as the legacy scrapers. Combined with ARCH-23 (no retry of transport or 5xx errors), one network blip during a catch-up permanently drops filings.

*Suggestion:* Have process_filing_row return a tri-state (ok / transient_failure / permanent_failure). Treat SecTransportError, 5xx and exhausted 429/503 as transient and make _process_date return False when any transient failure happened, so the date stays uncovered and is retried. Count only deterministic parse failures as filing-level skips.

#### ARCH-01 · HIGH · bug · effort M · partially

**Scrapers swallow failures and return [] or partial data, but services then mark the gap covered permanently**  
`src/insider_scanner/services/us.py:115`

UsScanService and CongressScanService (services/congress.py:77) only refuse to record coverage when the adapter raises. The adapters never raise for common failures. secform4.scrape_ticker returns [] when CIK resolution or the fetch fails (secform4.py:53-56, 61-65). openinsider.scrape_ticker and scrape_latest return [] on any fetch error (openinsider.py:63-67, 110-114). scrape_house_trades skips a year whose index failed, and any PDF that failed, with `continue` (congress_house.py:551-553, 583-585), and parse_ptr_pdf returns [] on any exception (443-445). scrape_senate_trades does the same per PTR (congress_senate.py:644-646). One timeout, a 403 or a temporary CIK lookup failure therefore marks the ticker/official + date range as covered for good, and later scans never refetch it. The user just sees fewer trades than really exist.

*Suggestion:* Make the failure contract explicit. Have scrapers raise a typed error (e.g. SourceFetchError) on fetch or index failures instead of returning []. For per-document failures, return a result object such as (trades, failed_documents) and only call coverage.add when failed_documents is empty. Keep the services' `except Exception -> ScanError` path as the single place where errors are turned into 'not covered'.

*Verifier:* I confirmed it. Neither service (us.py:114-115, congress.py:74-77) records coverage when the adapter raises, but secform4 (53-56, 61-65), openinsider (63-67, 110-114), House (550-553, 580-585, 443-445) and Senate (641-646) all catch errors and return [] or partial lists, so the range is marked covered. The defect is real. 'Critical' is too strong: no stored data is corrupted, and the result is permanently incomplete results, which is high.

#### ARCH-16 · MEDIUM · practice · effort M · confirmed

**core/prices imports the services layer and opens and bootstraps a new database on every call; the analysis tab does the same**  
`src/insider_scanner/core/prices/service.py:41`

A core module depends upward on services, which inverts the layering. Every call without an engine creates an engine and runs bootstrap_database (BEGIN IMMEDIATE plus schema validation) before disposing it. The GUI analysis tab does this on every chart load (it calls get_price_history without an engine, and load_trades_for_ticker opens yet another persistence, analysis_tab.py:30-52). load_trades_for_ticker also fetches every Congress trade in the range and filters by ticker in Python. MainWindow builds AnalysisTab without the ApplicationServices it already holds (main_window.py:486). The `"ctx" in locals()` cleanup is fragile.

*Suggestion:* Make `engine` (or a PriceRepository) a required dependency of get_price_history. Expose prices through a PriceService on ApplicationServices, pass services into AnalysisTab, and add a ticker filter to CongressTradeRepository.query.

#### ARCH-07 · MEDIUM · performance · effort S · partially

**SQLite configured with NullPool, rollback journal and no synchronous tuning, with one transaction per filing**  
`src/insider_scanner/persistence/engine.py:46`

With NullPool, every `engine.begin()`/`connect()` opens a new sqlite3 connection and re-runs the pragmas. No `journal_mode=WAL` or `synchronous=NORMAL` is set anywhere (grep finds none). The backfill and daily sinks call us_trades.upsert once per filing, which is a new connection plus BEGIN IMMEDIATE plus a rollback-journal commit with several fsyncs. The coverage and refresh repos also do this for every call. In rollback-journal mode, a GUI feed query blocks writers (and vice versa) for up to the 5 s busy timeout, which shows up as 'database is locked' PersistenceErrors while a CLI backfill runs and the GUI is open.

*Suggestion:* Enable `PRAGMA journal_mode=WAL` once at bootstrap and `PRAGMA synchronous=NORMAL` per connection. Use a small pool (SingletonThreadPool or QueuePool with check_same_thread=False). In bulk paths, batch upserts, e.g. commit every 500 filings in one immediate_transaction.

*Verifier:* NullPool (line 46), no WAL and no synchronous pragma (grep finds none), and one BEGIN IMMEDIATE transaction per upsert are all true. Reader/writer contention in rollback-journal mode is plausible. Per filing, though, this costs a few to a few tens of milliseconds against network latency of 150 ms or more, so it is not a dominant backfill cost. Medium, not high.

#### ARCH-20 · MEDIUM · bug · effort M · partially

**Two diverging dedup identities for US trades; SEC rows never deduplicate against secform4/openinsider rows**  
`src/insider_scanner/persistence/mappings.py:77`

merger._dedup_key (merger.py:25-32) and us_canonical_key re-implement the same (ticker, name, trade_date, shares bucket) rule. They must be kept in sync by hand: merger lowercases while mappings casefolds. The DB key switches to (accession, row) for SEC rows, so the same transaction from sec_edgar and from openinsider is stored twice, and the unified feed and alerts show duplicates. round(x/10) also uses banker's rounding, so 14 and 15 shares fall into different buckets.

*Suggestion:* Keep a single dedup function in core (used by both merger and mappings). Store accession_number for scraper rows when available: secform4 and openinsider links contain the accession, so the same key can apply. Otherwise add a secondary fuzzy-match pass on insert that links scraper rows to SEC rows.

*Verifier:* merger._dedup_key and us_canonical_key do duplicate the fuzzy rule (lower vs casefold), and SEC rows are keyed by (accession, row), so they never collapse with scraper rows, leaving cross-source duplicates in the feed. The us_canonical_key docstring says this split is deliberate. The '14 vs 15 shares' example is just a normal bucket edge, not banker's rounding. Banker's rounding shows at .5 boundaries: 25 goes to 20 but 35 goes to 40.

#### ARCH-04 · MEDIUM · bug · effort M · partially

**Coverage model has no freshness for open-ended or recent intervals**  
`src/insider_scanner/services/congress.py:77`

Bounded US and Congress scans record [start, end] as covered forever, even when `end` is today. The default Congress scan is the last 6 months up to today (congress.py:49-51). Any PTR or Form 4 filed later on the end date, or a late filing whose filing_date falls in an already-covered range (common for House/Senate, which often disclose weeks late), is never fetched again for that identifier. The `latest` path has a TTL (refresh_state + is_fresh), but bounded scans do not.

*Suggestion:* Clamp recorded coverage to `min(gap.end, today - N days)`, with N of about 2 for SEC-sourced data and about 45 for Congress PTRs, so the recent tail is always refetched. Or keep a refreshed_at per coverage row and treat tail intervals as stale after the cache TTL.

*Verifier:* Bounded scans do record [start, end] up to today with no TTL (congress.py:77, us.py:115). So filings for the end date that are published or indexed later, for example through House or Senate index lag, are never refetched. However, coverage and queries are keyed on filing_date, not trade_date. A PTR disclosed weeks late carries its own recent filing_date, so it is not hidden by an older covered range. The 'late disclosure' argument is wrong; only the recent-tail problem is real.

#### ARCH-M2 · MEDIUM · performance · effort S · verifier-found

**Backfill resume re-reads and JSON-decodes the whole submissions ZIP before reaching new work**  
`src/insider_scanner/services/sec_backfill.py:129`

Resume works by filtering accessions after iter_ownership_filings has already decompressed and json.loads-ed each CIK member (sec_bulk.py:201 `data = _read_member_json(zf, info, policy)`). Every restart of an interrupted backfill, which the user does often because it 'does not get to half', re-decodes all previously processed members, several GB of JSON, plus the O(n) checkpoint load before the first new fetch. Restart cost grows with progress.

*Suggestion:* Persist a cursor of the last fully processed ZIP member (index or filename; infolist order is deterministic), and have iter_ownership_filings take a `start_member` argument that skips earlier members without reading them. Keep the per-accession set only for the current member, or drop it.

#### ARCH-21 · MEDIUM · simplification · effort M · confirmed

**Duplicated resume and checkpoint machinery across sec_downloads, sec_daily and sec_backfill**  
`src/insider_scanner/services/sec_daily.py:216`

SecDailyIngestionService already records coverage per completed date (sec_daily.py:258-264), yet it also passes one JSON checkpoint path to every gap._load_checkpoint discards it whenever the range differs (sec_downloads.py:358-364) and deletes it when a gap finishes, so with several gaps the checkpoint is overwritten or deleted between gaps and adds nothing beyond coverage. sec_backfill duplicates_CHECKPOINT_VERSION, the load/save/delete helpers and the counters dict. It also reaches into sec_downloads for process_filing_row and new_counters and re-implements the persist sink that sec_daily._make_sink already has.

*Suggestion:* Use DB coverage as the single resume mechanism for daily and catch-up runs and drop the JSON checkpoint there. Extract a small `checkpoint.py` (versioned load/save/delete) and a shared `make_persist_sink(persistence)` used by both services. Make process_filing_row a public function in a core pipeline module rather than a service-to-service import.

#### ARCH-25 · LOW · simplification · effort M · confirmed

**Inconsistent domain vocabularies across trade models force string matching downstream**  
`src/insider_scanner/core/models.py:145`

InsiderTrade and CongressTrade use different names for the same concepts (insider_name vs official_name, edgar_url vs source_url, Buy/Sell vs Purchase/Sale). The feed therefore needs runtime LIKE matching over 'buy','purchase','sell','sale' (feed.py:36-37, 374-383), and the projection must remap every column. to_dict/from_dict are hand-written field lists (models.py:52-120, 157-198) that silently drop any new field someone forgets to add, and persistence/mappings repeats them again.

*Suggestion:* Add a normalised `direction` enum (BUY/SELL/OTHER) column populated at insert time for all markets. Generate to_dict/from_dict with dataclasses.fields() plus a date-field set rather than listing fields by hand.

#### ARCH-17 · LOW · practice · effort M · partially

**GUI bypasses the services layer: it builds persistence repositories directly, calls core scrapers, and imports the CLI module**  
`src/insider_scanner/gui/main_window.py:229`

An import scan shows 9 GUI modules importing insider_scanner.persistence.* directly: feed, feed_state, alerts, watchlists, errors. scan_tab calls core.edgar and core.merger. sec_backfill_info imports the whole CLI module (argparse, services.application and so on) just for one URL constant. The GUI is therefore coupled to the SQL schema and engine lifecycle, which makes service-level concerns (caching, read-only mode, a different DB) impossible without touching widgets. The tests already need a fake engine to build pages.

*Suggestion:* Add a FeedService (query, detail, search_entities, recent_by_*) and a WatchlistService/AlertService on ApplicationServices, and inject them into the pages. Move SEC_BULK_SUBMISSIONS_URL and the instructions into core/sec_bulk.py (or config) and import it from both the CLI and the GUI.

*Verifier:* Two things are real: main_window builds FeedRepository from persistence.engine (229), and sec_backfill_info imports the whole cli module for one constant (line 8). However, most of the nine 'persistence imports' in the GUI are DTO and enum types (FeedRecord, FeedMarket, EntityHit, FeedCriteria), not repository access, so the coupling is overstated. This is a maintainability issue, low.

#### ARCH-28 · LOW · simplification · effort M · confirmed

**Two unrelated watchlist systems (tickers_watchlist.txt vs watchlists.json)**  
`src/insider_scanner/gui/scan_tab.py:361`

The Scan tab's 'Watchlist' button and the Analysis tab's symbol list read the legacy text file (config.load_watchlist, TICKERS_FILE). The Watchlists page and alerts use persistence/watchlists.py (watchlists.json). A company the user adds to a watchlist in the UI is ignored by 'Scan watchlist' and is missing from the Analysis symbol picker, and vice versa. In the same watchlist scan, one ticker failure raises ScanError and throws away the results already collected for the other tickers (scan_tab.py:400-415).

*Suggestion:* Make watchlists.json the single source: migrate the txt file once on startup and have the Scan and Analysis tabs read US company entries from the WatchlistStore. Catch per-ticker ScanError in the watchlist loop and report partial failures.

#### ARCH-29 · LOW · security · effort S · confirmed

**Backfill ZIP containment check is a no-op because cache_root is derived from the ZIP path itself**  
`src/insider_scanner/services/sec_backfill.py:120`

_validate_zip_location (sec_bulk.py:216-225) checks that zip_path resolves within cache_root. Passing zip_path.parent makes that always true for any path, so the 'trusted cache root' check is security theatre that adds complexity and suggests a guarantee that does not exist. Only the symlink checks have any effect.

*Suggestion:* Either drop the cache_root parameter and keep only the symlink and regular-file checks, or pass a real trusted directory (e.g. EDGAR_CACHE_DIR / 'bulk') and document that the ZIP must be placed there.

*Also reported as:* TESTS-M1, SECIO-20

#### ARCH-13 · LOW · bug · effort M · partially

**Three independent, inconsistent rate limiters; the SEC limit is per client instance, not per process**  
`src/insider_scanner/utils/http.py:21`

utils/http._rate_limit is a module global, not thread-safe and uses wall-clock time, at 10 req/s. congress_senate._rate_limit (congress_senate.py:72-79) is a copy of it. sec_fair_access.RateLimiter is thread-safe but created per SecClient via `field(default_factory=default_rate_limiter)` (sec_client.py:161), at 8 req/s. SEC traffic from edgar.resolve_cik (legacy fetch_url) and from one or more SecClient instances, e.g. GUI workers running in parallel in a QThreadPool, is never coordinated, so the combined rate can exceed SEC's 10 req/s and get the IP throttled. SEC_MAX_REQUESTS_PER_SECOND is also defined twice (config.py:93 int and sec_fair_access.py:22 float). The legacy path also sends the placeholder '<contact@example.com>' User-Agent that SecClient explicitly rejects (config.py:89-92 vs sec_client.py:386).

*Suggestion:* Create a single process-wide SEC limiter (a module-level RateLimiter in sec_fair_access) shared by SecClient and the legacy edgar path. Move edgar's company_tickers and submissions lookups onto SecClient. Remove the duplicate constant. Reuse RateLimiter for the Senate scraper instead of a hand-rolled global.

*Verifier:* There are three limiters, a duplicate SEC_MAX_REQUESTS_PER_SECOND constant (config.py:93 and sec_fair_access.py:22), and a placeholder User-Agent on the legacy path. All of that is real. But SecClient is constructed in exactly one place (cli.py:313, once per command) and the GUI never constructs one, so the scenario of parallel SecClient instances in GUI workers exceeding 10 req/s cannot happen. What remains is duplication and an unsafe global limiter, which is low.

### Test suite quality and speed (TESTS) — 21 items

> The suite is large (about 28k lines, 2,045 tests) and passes: 2032 passed, 5 skipped, 8 live deselected in 107s, run on Python 3.13 with QT_QPA_PLATFORM=offscreen. The repo's own .venv is broken (it points at a missing Python313), so I built a separate venv in the scratchpad. Nothing reaches the network unmocked: a second full run with sockets, DNS and curl_cffi blocked logged zero attempts. The slowest tests are a European test that is out of scope (8.6s), two packaging tests that each build a wheel (7.0s + 4.6s), and seven congress_senate tests that sleep for real (about 16s), so roughly 30% of the runtime is avoidable. The main problems are isolation and gaps on the paths that matter:
>
> - Tests write into the developer's real platformdirs data directory. My runs created %LOCALAPPDATA%/Insider Scanner/Insider Scanner/ (seed files, Cache/, exports/) and rewrote feed_state.json, watchlists.json and alerts.json there.
> - The packaging tests rewrite build/ and src/insider_scanner.egg-info inside the repo.
> - Bulk backfill is tested only with a 4-record ZIP and loose `>= 1` assertions. A measurement shows the per-filing checkpoint rewrite costs about 0.4s per filing once 500k accessions are done. That is quadratic and very likely the cause of the known "hours and not even half-way" backfill problem.
> - The concurrency tests' barrier never actually synchronises (it times out every time, as instrumentation showed).
> - One full run with coverage hung for more than 9 minutes on a test_application_shell test and I could not reproduce it. There is no pytest timeout and no CI timeout-minutes to catch a hang like that.
> - CI never installs from uv.lock, so the Dependabot uv.lock PRs are effectively untested.
> Upsert semantics and the SEC rate limiter/retry logic are well covered by deterministic tests.

#### TESTS-M2 · HIGH · performance · effort M · verifier-found

**Each resume re-decompresses and JSON-parses every archive member before reaching new work**  
`src/insider_scanner/core/sec_bulk.py:186`

Resume is keyed only by the accession set (sec_backfill.py:129). On restart, iter_ownership_filings preflights and then fully reads and json.loads every member of the multi-GB submissions.zip (one per CIK), including members whose filings were all completed earlier. The service then discards them one by one through `if meta.accession_number in completed`. Every interrupted run therefore re-pays the decompress-and-parse cost of all earlier members, and this compounds the per-filing checkpoint cost (TESTS-02) for the known slow backfill.

*Suggestion:* Checkpoint a member-level high-water mark (index or name of the last fully completed member) and let iter_ownership_filings take a start_after/skip_members argument so completed members are skipped without being read. Keep the accession set (or a SQLite table) only for the partially done member.

#### TESTS-01 · HIGH · bug · effort M · confirmed

**Tests write into the developer's real user data directory (feed state, watchlists, alerts, seeds)**  
`tests/test_gui.py:179`

DEFAULT_PATHS is fixed at import time from platformdirs (config.py:68), and no fixture redirects it. test_gui.py builds MainWindow() without state_path four times (lines 179, 188, 206, 214). The autouse conftest fixture and pytest-qt then close these windows, and MainWindow.closeEvent (main_window.py:713-719) saves feed_state.json, watchlists.json and alerts.json into the real user data directory. Separately, every cli.run() test calls ensure_dirs() on the real paths, which creates the directories and copies the seed files. Observed: after my runs, %LOCALAPPDATA%/Insider Scanner/Insider Scanner/ held congress_members.json, tickers_watchlist.txt, eu_watchlist.txt, Cache/{edgar,scrapers,eu_scrapers} and exports/scans, all created at 13:07:14. feed_state.json, watchlists.json and alerts.json were rewritten at 13:10:39 and again at 13:11:15, once per suite run. On a developer machine this round-trips, and can overwrite (for example after a schema-normalising load), the user's real watchlists and alerts. Test results can also depend on whatever state the user has locally.

*Suggestion:* Add a session-scoped autouse fixture in conftest, or a pytest_configure hook, that points the runtime paths at tmp_path_factory before insider_scanner is imported. The cleanest way is an env-var override (for example INSIDER_SCANNER_DATA_DIR / INSIDER_SCANNER_CACHE_DIR) read by resolve_runtime_paths. Otherwise, monkeypatch config.DEFAULT_PATHS and the derived constants. Pass state_path=tmp_path in test_gui.py as test_application_shell.py already does. Add a guard test that fails if anything under the real platformdirs paths changes during the session.

#### TESTS-03 · HIGH · testing · effort M · confirmed

**Bulk backfill is only tested with a 4-record fixture: no scale, progress, cancellation or checkpoint-edge tests**  
`tests/test_sec_backfill.py:13`

Every backfill test runs against sec_submissions_bulk_small.zip, which has 4 ownership rows. The quadratic checkpoint cost (TESTS-02) and per-filing overheads are therefore invisible. The following are also untested:

- on_progress, which run() only calls once at the very end (sec_backfill.py:180-181), so a multi-hour CLI run prints nothing until it finishes. cli.py:521 calls service.run(zip_path, confirm=True, ciks=ciks) with no progress or cancellation.
- The 'different archive -> start fresh' branch, which keys only on zip_path.name (sec_backfill.py:192).
- A version-mismatched or corrupt checkpoint.
- KeyboardInterrupt mid-run preserving the checkpoint, which is how CLI users actually stop a run.
- A SecBulkError raised mid-archive.

*Suggestion:* Add a fixture factory that synthesises a ZIP with N members (for example 5k-20k rows), using a stub transport and an in-memory or tmp DB. Assert the number of checkpoint writes (spy on atomic_write_json) is bounded, for example at most N/K + 2, rather than asserting wall time. Add tests that on_progress is called incrementally, that a checkpoint for a different or renamed archive and a wrong version are ignored, and that an exception or KeyboardInterrupt from the sink leaves a loadable checkpoint.

#### TESTS-06 · MEDIUM · practice · effort S · confirmed

**No per-test timeout and no job timeout; one full run hung for more than 9 minutes**  
`.github/workflows/ci.yml:10`

pytest-timeout is not a dependency, pyproject sets no timeout, and the CI jobs have no timeout-minutes, so the default 6-hour GitHub limit applies to each of the 8 matrix jobs. Many tests involve threads, Qt event loops, modal QMessageBox paths and SQLite busy timeouts. Observed: my full run under --cov stopped after 55 tests, inside test_application_shell.py (collection position 56, test_buttons_feed_only_visibility), and sat idle for more than 9 minutes until I killed it. The same tests passed with and without coverage when re-run in smaller groups, so this is an intermittent, order- or timing-dependent hang that nothing would surface quickly.

*Suggestion:* Add pytest-timeout to the dev extras and set `timeout = 60` (with timeout_method = thread on Windows) under [tool.pytest.ini_options]. Add `timeout-minutes: 20` to the test job. When a GUI test hangs, check for an unpatched QMessageBox or exec() modal under offscreen, and for workers still running when persistence.close() is called.

#### TESTS-05 · MEDIUM · practice · effort S · confirmed

**CI installs with unpinned pip, so uv.lock (and every Dependabot uv.lock PR) is never tested**  
`.github/workflows/ci.yml:35`

CI resolves the latest versions allowed by pyproject ranges and ignores uv.lock entirely. The recent Dependabot commits (for example 1d46281 'bump msgpack') change only uv.lock, so their CI runs exercise exactly the same environment as before, and the green check says nothing about the bumped versions. In the other direction, a breaking upstream release inside the allowed ranges can turn CI red with no repo change. Developers run from the lockfile, so CI and local environments diverge.

*Suggestion:* Use astral-sh/setup-uv and run `uv sync --frozen --extra gui --extra dev` followed by `uv run pytest ...`, so CI tests the lockfile. If you want to keep coverage of unpinned resolution, add one separate 'latest deps' job (for example weekly) instead.

#### TESTS-04 · MEDIUM · practice · effort S · confirmed

**Live network tests run by default; `-m "not live"` is only applied in CI**  
`pyproject.toml:85`

The live marker is registered but not deselected by default. A plain `pytest` or `uv run pytest` runs the 8 live tests in test_live.py and test_live_prices.py, which hit secform4.com, openinsider.com, SEC EDGAR and Yahoo (8 were deselected in my run only because I passed -m). Local runs then depend on the network, become slow or flaky offline, and send unsolicited traffic to rate-limited third-party sites. --strict-markers is also missing, so a typo such as @pytest.mark.lvie would silently stop gating.

*Suggestion:* Change addopts to `-ra --strict-markers -m "not live"`, and run live tests explicitly with `pytest -m live` (a later -m overrides the default). Optionally, also gate them behind an env var (skip unless INSIDER_SCANNER_LIVE=1).

#### TESTS-08 · MEDIUM · performance · effort S · confirmed

**Senate EFD tests sleep for real through the module-global rate limiter (about 16s per run)**  
`tests/test_congress_senate.py:336`

Measured durations: TestEFDSession::test_search 3.00s, test_fetch_page 3.00s, TestSearchSenateFilings::test_empty_results 3.00s, test_filters_paper_filings 3.00s, test_authenticate_retries_403_with_browser_session 2.00s, test_authenticate 1.00s, test_authenticate_no_csrf 1.00s. That is about 16s of pure time.sleep, roughly 15% of the whole suite, on every matrix job. Because _last_request_time is a module global, the amount of sleeping also depends on test order.

*Suggestion:* Add an autouse fixture in test_congress_senate.py that monkeypatches insider_scanner.core.congress_senate._MIN_REQUEST_INTERVAL to 0, or patches time.sleep there. Better, inject a RateLimiter (as SecClient does with sec_fair_access.RateLimiter) so tests pass a no-op limiter. Also move the repeated landing/home/CSRF `responses.add` setup, duplicated 4 times, into a fixture.

#### TESTS-11 · MEDIUM · testing · effort S · confirmed

**GUI tests that pass regardless of behaviour (no assertion, or sort order not checked)**  
`tests/test_gui.py:57`

test_empty_ticker_warning never calls_run_scan and asserts nothing; it only constructs a widget. TestSortableTableModel.test_set_and_sort (line 43-44) sorts and then only asserts rowCount()==3, which holds even if sort() does nothing. Several other tests only assert that attributes exist (`assert tab.btn_scan is not None`), or test Qt itself (test_latest_count_spin_change sets 250 and reads back 250). An AST scan found 19 test functions with no assert, raises or wait.

*Suggestion:* In test_empty_ticker_warning, monkeypatch QMessageBox.warning with a recorder, call tab._run_scan(), assert the warning was shown and that no worker was dispatched. In the sort test, assert model.data(model.index(0, 0)) == 'A' after an ascending sort and 'C' after a descending one. Delete the pure 'exists' and Qt round-trip tests, or fold them into one construction smoke test.

#### TESTS-15 · MEDIUM · testing · effort S · confirmed

**Backfill resume tests use loose `>= 1` assertions that tolerate double-processing**  
`tests/test_sec_backfill.py:71`

With 4 known records and a cancel-after-one callback, the exact expected values are fixed: 1 skipped, 3 parsed, and transactions_inserted equal to the remaining filings' rows. The `>= 1` checks would still pass if resume re-processed already-completed filings (for example skipped_resume=1 with filings_parsed=4), or if the checkpoint held more or fewer accessions than were really completed. test_mapping_error_is_checkpointed_and_skipped_on_resume (lines 132, 138) has the same weakness. None of the tests reads the checkpoint file to check its contents.

*Suggestion:* Assert exact counters (skipped_resume == 1, filings_parsed == 3, failures == 0). Load backfill.json after the interrupted run and assert that completed_accessions equals exactly the first processed accession. Assert that the total DB row count after both runs equals a single uninterrupted run.

#### TESTS-14 · MEDIUM · testing · effort M · confirmed

**Tests enshrine that one malformed archive member aborts the entire bulk iteration**  
`tests/test_sec_bulk.py:219`

sec_bulk.py:308-309 raises SecBulkError out of the generator for one bad JSON member, and SecBackfillService.run (sec_backfill.py:124) does not catch it. A single malformed or truncated member among the roughly 900k in a real submissions.zip therefore stops the multi-hour backfill. Every resume stops at the same point again, after re-iterating all earlier members. The tests assert this abort behaviour and have no test that the backfill survives, skips and counts a bad member.

*Suggestion:* Decide on the intended policy: per-member errors should probably be counted (skipped_metadata or a new skipped_members counter) and iteration should continue, while security violations stay fatal. Update test_sec_bulk to expect a skip, and add a backfill test with a valid / malformed / valid member ZIP that asserts both valid filings are processed.

#### TESTS-18 · LOW · practice · effort S · confirmed

**CI's Windows 'skip GUI tests' ignores only test_gui.py, while about 20 other GUI modules still run; xvfb and offscreen are redundant**  
`.github/workflows/ci.yml:47`

About 20 modules use qtbot or QApplication (test_application_shell, test_feed_page, test_investigation_drawer, test_main_window_theme and others) and they all run on Windows (and passed locally on Windows). Only test_gui.py is excluded, so it is the one GUI file with no Windows coverage, and the step name is misleading. On Linux, xvfb-run is combined with QT_QPA_PLATFORM=offscreen, which needs no X server, so the libegl1/xvfb install and the wrapper are wasted.

*Suggestion:* Drop --ignore=tests/test_gui.py, since it uses the same patterns as the modules already running on Windows. If a real Windows-specific issue exists, add a `gui` marker and an explicit skipif with a reason. Remove xvfb-run on Linux, or drop offscreen and keep xvfb, but not both.

#### TESTS-10 · LOW · bug · effort S · partially

**Autouse conftest fixture imports PySide6 in every test, contradicting its docstring**  
`tests/conftest.py:22`

The docstring says the fixture 'no-ops when no QApplication exists (so non-GUI tests are unaffected and PySide6 is only imported when an app is already live)'. In fact it imports PySide6.QtWidgets unconditionally in the teardown of all ~2,000 tests. A contributor who installs only the base or dev extras (without [gui]) gets an ImportError teardown error on every test, including pure parsing and persistence tests. It also forces Qt to load for non-GUI subsets.

*Suggestion:* Guard the import with `if 'PySide6.QtWidgets' not in sys.modules: return`, or wrap it in try/except ImportError, so the fixture really is a no-op when no Qt app was ever created. Optionally register a `gui` marker and apply it to the GUI modules (see TESTS-18).

*Verifier:* The docstring is wrong: line 22 imports PySide6.QtWidgets unconditionally on every teardown. The claimed impact is overstated. The dev extras include pytest-qt, which needs a Qt binding to configure, and many test modules import PySide6, so a dev install without [gui] fails before this fixture matters. This is a docstring/guard mismatch with a small Qt import cost.

#### TESTS-20 · LOW · simplification · effort S · confirmed

**Duplicate sec-backfill CLI tests; SecBackfillSummary built positionally**  
`tests/test_cli.py:754`

TestCmdSecBackfill (lines 654-735) and the module-level tests at lines 738-800 cover the same confirm, dispatch and CIK-normalisation behaviour twice, through two entry points. Both construct SecBackfillSummary with 8 positional ints (also at line 710). If the fields are reordered or a field is added, the tests still construct a valid but semantically wrong summary, and the 'discovered=10' print check may still pass by coincidence. The cli.run variants also call the real ensure_dirs() (see TESTS-01).

*Suggestion:* Keep one set (cmd_unit tests, plus one cli.run end-to-end test) and build the summary with keyword arguments (filings_discovered=10, ...). Also assert the full printed summary line.

#### TESTS-22 · LOW · testing · effort S · confirmed

**Calendar-dependent assertions in house year tests**  
`tests/test_congress_house.py:211`

These tests depend on the system clock: the production function and the assertion each read date.today() separately, so a run straddling midnight on 31 December fails. The same applies at line 205, test_from_only, and line 425. They also cannot test a fixed scenario such as 'today is 2027-01-02'.

*Suggestion:* Inject `today` into _determine_years (a parameter defaulting to date.today), or freeze time with monkeypatch on the module's date, and assert fixed years.

#### TESTS-21 · LOW · practice · effort S · confirmed

**try/assert False/except pattern instead of pytest.raises**  
`tests/test_congress_senate.py:396`

This pattern is used here and again at lines 429-433. It works only because AssertionError is not a ConnectionError. If the code raised a broader exception type, or if `assert` statements were stripped with python -O, the test would give misleading results. It is also more verbose than the idiom.

*Suggestion:* Use `with pytest.raises(ConnectionError, match="CSRF"):` (and match="not authenticated" for the second case).

#### TESTS-17 · LOW · practice · effort S · confirmed

**tests/fixtures.py module and tests/fixtures/ data directory share a name**  
`tests/test_edgar.py:25`

`tests.fixtures` resolves to fixtures.py only because the fixtures/ directory has no **init**.py: a namespace package loses to a module in the same directory. The directory already holds Python code (make_sec_bulk_fixture.py). If someone adds **init**.py there, for example to import the generator, the imports in test_edgar, test_openinsider and test_secform4 break with confusing ImportErrors.

*Suggestion:* Rename the module to tests/html_samples.py, or the directory to tests/data/, and update the imports and FIXTURE_DIR paths.

#### TESTS-13 · LOW · testing · effort S · confirmed

**Live smoke tests pass even when scrapers are completely broken**  
`tests/test_live.py:20`

The scrapers swallow HTTP and parse errors and return []. Every live test therefore passes when the site layout changes or the request is blocked, which is exactly the regression live tests exist to catch. test_resolve_cik_aapl (line 48) also passes when resolve_cik returns None.

*Suggestion:* Assert non-empty results for a ticker that always has insider activity (AAPL), and assert that cik == '0000320193' without the `if`. If you want tolerance, use pytest.xfail or skip with an explicit reason when the response status indicates blocking, rather than passing silently.

#### TESTS-12 · LOW · testing · effort S · confirmed

**openinsider tests use vacuous `>= 1` assertions; the ticker-override test cannot fail**  
`tests/test_openinsider.py:55`

The fixture HTML is fixed, so the exact counts and tickers are known. test_ticker_override passes whether the override is applied, ignored or inverted. test_scrape_ticker_mocked, test_scrape_latest_mocked, test_scrape_with_date_range_url and test_scrape_latest_with_dates (lines 72, 83, 117, 132) all assert only `len(trades) >= 1`.

*Suggestion:* Assert the exact expected trade count and the exact ticker set, for example that tickers == the set of fixture tickers, which proves the HTML column wins over the argument. Add a case where the HTML has no ticker column to prove the override is used.

#### TESTS-09 · LOW · performance · effort S · partially

**Packaging tests build the wheel twice, inside the repo tree (writes build/ and egg-info)**  
`tests/test_packaging.py:23`

Each of the two tests runs a full `python -m build --wheel --no-isolation` (measured 6.97s and 4.64s). With --no-isolation and cwd=project_root, setuptools writes build/ and src/insider_scanner.egg-info into the working tree; after my run both had fresh mtimes. A stale build/lib can also leak removed files into later wheels. The cost repeats on all 8 CI jobs.

*Suggestion:* Use a module- or session-scoped fixture that builds the wheel once and shares the ZipFile between both assertions, or merge them into one test. Copy the project to tmp_path first, or pass `-C--build-option=--build-base=<tmp>`, so the repo stays clean. Consider running packaging tests in one CI job only (for example ubuntu / 3.12) through a marker.

*Verifier:* Both tests do run a full `python -m build --wheel --no-isolation` with cwd=project_root (lines 23-40), so the duplicate build cost is real. The repo-pollution part is overstated: build/ and *.egg-info/ are gitignored (.gitignore lines 15 and 28). The real harms are the roughly 11s per job and possible stale build/lib contents.

#### TESTS-07 · LOW · testing · effort S · partially

**Concurrency tests' barrier never synchronises: every wait times out, adding 0.5s and testing no interleaving**  
`tests/test_repositories.py:119`

The helper is meant to force two upserts to interleave their SELECTs. Writers use BEGIN IMMEDIATE (persistence/engine.py:66-70), so the second thread blocks on the SQLite write lock before it ever issues a SELECT, and the barrier can never fill. I instrumented threading.Barrier while running test_us_concurrent_upserts_preserve_all_source_provenance, test_european_concurrent_upserts_preserve_complementary_enrichment and test_coverage_concurrent_adds_preserve_complete_union: all six waits ended BROKEN/timeout and none passed. Each test pays about 0.5s (measured 0.59-0.61s). The race the tests describe is never constructed. They only show that serialised writes are correct, which plain sequential calls would show too. The same pattern is in tests/test_coverage.py:124.

*Suggestion:* Remove the barrier and keep a simple two-thread test that asserts the final state, which is what is actually verified. Alternatively, build a real read-modify-write race test: run the barrier before the transaction begins (for example by wrapping repository internals), or assert explicitly that BEGIN IMMEDIATE is issued, which is the actual protection mechanism.

*Verifier:* With BEGIN IMMEDIATE (immediate_transaction, engine.py:20-26) the barrier does time out, costing about 0.5s per test. However, it is a regression trap rather than dead code. If upsert ever dropped the IMMEDIATE option, both threads would SELECT under deferred BEGIN, the barrier would force the lost-update interleaving, and the test would fail. Plain sequential calls would not catch that, so removing the barrier as suggested would weaken the test. At most, shorten the timeout or document the intent.

#### TESTS-16 · LOW · simplification · effort M · confirmed

**Test modules import helpers from other test modules; fixtures and factories are duplicated across files**  
`tests/test_sec_backfill.py:12`

StubResponse, make_transport, make_client, VALID_FILING and INDEX_TWO_ROWS live in test_sec_downloads.py and are imported by test_sec_backfill, test_sec_daily_service (4 places), test_gui_service_integration and test_cli_persistence_integration. This couples test files together, and renaming or deleting a test module breaks unrelated tests. Duplication: there are only 19 fixtures in about 28k lines; the `engine` fixture is defined 4 times (test_feed_detail, test_feed_repository, test_persistence, test_repositories) and `persistence` 4 times; InsiderTrade/CongressTrade/EU trade factories (_us,_congress, _eu,_trade, _make_trade, _sample_trade) are defined in about 12 files.

*Suggestion:* Create tests/helpers/ or tests/sec_stubs.py for the SEC transport stubs and tests/factories.py for trade builders. Put the engine and persistence fixtures in conftest.py (or in a tests/persistence/conftest.py if you split the directory).

### Refuted by the verifiers (not included above)

- SECPARSE-21 — Without an accession, row keys fall back to the fuzzy canonical key, so distinct transactions can collide: No production path has a missing accession. process_filing_row always passes _accession_from_path(row.archive_path), which is non-empty because build_filing_archive_url rejects empty segments. Full submissions also carry the ACCESSION NUMBER header. Only a direct library call without an accession reaches the fuzzy key, and no in-repo caller does that.
- USSVC-22 — secform4 CSS trade-type hint uses substring matching on the joined class string: This is speculative. The hint runs only when the text classification already gave 'Other', and secform4 uses single-letter code classes (the fixture uses class="S"). No class containing a capital S that is not the code S has been shown. Exact token membership is cleaner but fixes no demonstrated misclassification.
