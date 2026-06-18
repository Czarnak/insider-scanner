# SEC EDGAR Task and Test Plan

Date: 2026-06-17
Branch: `codex/sec-edgar-data-ingestion`
Status: planning only

## Goal

Move US insider ingestion toward SEC-native data:

- Daily operation: use EDGAR daily index files.
- Full backfill: enable SEC bulk submissions ZIP only as an explicit user action.
- Query path: prefer local database after ingestion.
- Existing third-party sources: keep temporarily for comparison/fallback until SEC-native output is validated.

## Current Codebase Boundaries

Existing modules to respect:

- `src/insider_scanner/core/edgar.py` handles current EDGAR CIK and URL helpers.
- `src/insider_scanner/core/secform4.py` and `src/insider_scanner/core/openinsider.py` are current US third-party sources.
- `src/insider_scanner/core/models.py` defines `InsiderTrade`.
- `src/insider_scanner/core/merger.py` merges US insider records.
- `src/insider_scanner/services/us.py` orchestrates US scans.
- `src/insider_scanner/services/adapters.py` registers US source adapters.
- `src/insider_scanner/persistence/*` owns SQLite schema, migrations, repositories, mappings, and coverage.
- `src/insider_scanner/cli.py` owns CLI commands.
- `tests/test_edgar.py`, `tests/test_secform4.py`, `tests/test_openinsider.py`, `tests/test_merger.py`, `tests/test_repositories.py`, `tests/test_persistence.py`, and `tests/test_cli_persistence_integration.py` are the closest existing test patterns.

## Work Split

### User Tasks

1. Choose initial validation targets:
   - 5-10 tickers with known recent insider activity.
   - 3-5 filing dates with manageable Form 3/4/5 volume.
   - At least one date with amended ownership forms.

2. Define product semantics:
   - Whether awards such as RSU/PSU grants should show as trades, awards, or separate transaction types.
   - Whether derivative transactions should be shown by default or behind a filter.
   - How much footnote text should be visible in UI/exports.
   - Whether amended filings should replace, annotate, or coexist with original rows.

3. Provide SEC access identity:
   - App/company name for `User-Agent`.
   - Contact email for `User-Agent`.

4. Validate outputs manually:
   - Compare parsed transactions against SEC filing pages.
   - Compare temporary results against `openinsider` / `secform4` during transition.
   - Confirm that local DB query results match expected UX.

5. Decide rollout timing:
   - When SEC-native ingestion becomes default.
   - When third-party sources become fallback only.
   - When third-party source code can be removed.

### Codex Tasks

1. Build minimal SEC source primitives.
2. Build minimal clients/parsers for every future SEC input type.
3. Add security threat modeling and hardening before scaling.
4. Scale and polish downloads, retries, progress, and resumability.
5. Extend/adjust persistence for stable SEC-native identifiers.
6. Add daily ingestion service and CLI command.
7. Add background execution/progress hooks.
8. Add explicit bulk backfill workflow.
9. Add comparison tooling against old sources.
10. Update docs and deprecate old defaults.

## Detailed Task and Test Matrix

### Task 1: SEC URL and Date Helpers

Purpose: isolate deterministic URL/date logic before any network work.

Likely files:

- Modify: `src/insider_scanner/core/edgar.py`
- Test: `tests/test_edgar.py`

Tests:

- Quarter mapping:
  - `2026-01-01` -> `QTR1`
  - `2026-04-01` -> `QTR2`
  - `2026-07-01` -> `QTR3`
  - `2026-10-01` -> `QTR4`
- Daily master index URL builder:
  - `2026-06-15` -> `https://www.sec.gov/Archives/edgar/daily-index/2026/QTR2/master.20260615.idx`
- Filing archive URL builder from index path:
  - `edgar/data/1000228/0001190297-26-000004.txt` -> SEC archive URL.
- CIK normalization:
  - `320193` -> `0000320193`
  - already padded CIK remains unchanged.
- Invalid date/CIK inputs fail fast with clear exceptions.

### Task 2: Daily Master Index Parser

Purpose: parse `master.YYYYMMDD.idx` without network dependency.

Likely files:

- Create: `src/insider_scanner/core/sec_index.py`
- Test: `tests/test_sec_index.py`
- Fixture: `tests/fixtures/sec_master_20260615_excerpt.idx`

Tests:

- Header lines are skipped.
- Pipe-delimited rows parse into immutable filing metadata objects.
- Forms `3`, `3/A`, `4`, `4/A`, `5`, `5/A` are retained.
- Non-ownership forms are ignored.
- Malformed rows are skipped or reported consistently.
- Parser preserves:
  - CIK
  - company name
  - form type
  - filing date
  - archive path
- Duplicate rows are de-duplicated by archive path/accession evidence.

### Task 3: Minimal SEC HTTP Client

Purpose: create the smallest reusable network boundary needed by all SEC
download paths before adding scale or background behavior.

Likely files:

- Create: `src/insider_scanner/core/sec_client.py`
- Test: `tests/test_sec_client.py`

Tests:

- `User-Agent` header is always present.
- Missing configured user-agent fails before network calls.
- Client accepts an injected transport/session for deterministic tests.
- Client can fetch text and bytes responses.
- Non-200 responses return typed errors rather than raw library exceptions.
- No production test reaches the live SEC network.

### Task 4: Minimal SEC Filing Document Downloader

Purpose: fetch the exact filing files selected by the daily index, with only
minimal cache/temp behavior required for parser development.

Likely files:

- Create: `src/insider_scanner/core/sec_downloader.py`
- Test: `tests/test_sec_downloader.py`

Tests:

- Builds correct archive URL from parsed index row.
- Uses `sec_client` rather than direct `requests`.
- Saves temporary files only under configured cache/temp path.
- Uses accession/archive path as stable cache key.
- Reuses a fresh cached file when allowed.
- Returns downloaded content path plus source metadata for parsing.

### Task 5: Form 3/4/5 XML Extraction

Purpose: extract ownership XML from SEC `.txt` submissions and direct primary
XML files before any persistence work starts.

Likely files:

- Create: `src/insider_scanner/core/sec_ownership_document.py`
- Test: `tests/test_sec_ownership_document.py`
- Fixtures:
  - `tests/fixtures/sec_form4_submission.txt`
  - `tests/fixtures/sec_form4_primary.xml`
  - `tests/fixtures/sec_form4_with_derivatives.xml`
  - `tests/fixtures/sec_form4_amendment.xml`

Tests:

- Finds XML ownership document inside a full `.txt` submission.
- Handles direct XML primary documents.
- Rejects non-ownership documents with typed error.
- Preserves accession number and document type if present.
- Handles multiple `<DOCUMENT>` blocks and selects the ownership document.

### Task 6: Form 3/4/5 Transaction Parser

Purpose: convert SEC ownership XML into normalized in-memory records, without
locking database schema too early.

Likely files:

- Create: `src/insider_scanner/core/sec_ownership_parser.py`
- Modify: `src/insider_scanner/core/models.py` only if current `InsiderTrade` lacks required in-memory fields.
- Test: `tests/test_sec_ownership_parser.py`

Tests:

- Parses issuer CIK, issuer name, ticker when available.
- Parses reporting owner CIK, name, relationship, officer title.
- Parses non-derivative transaction table:
  - transaction date
  - security title
  - transaction code
  - acquired/disposed flag
  - shares
  - price
  - post-transaction shares
  - direct/indirect ownership
- Parses derivative transaction table without losing derivative-specific fields.
- Parses footnote references and footnote text.
- Handles missing optional values without crashing.
- Handles amendments and original filing references.
- Produces stable row identity for later idempotent database upserts.
- Does not mutate source parser objects while normalizing.

### Task 7: Minimal Bulk Submissions ZIP Metadata Parser

Purpose: cover the full-backfill input shape with small fixtures before the
security review, without implementing the heavy full backfill workflow yet.

Likely files:

- Create: `src/insider_scanner/core/sec_bulk.py`
- Test: `tests/test_sec_bulk.py`
- Fixture: `tests/fixtures/sec_submissions_bulk_small.zip`

Tests:

- Small fixture ZIP with `CIK##########.json` files is processed.
- Continuation file names like `CIK##########-submissions-001.json` are recognized.
- Ownership forms are selected from submissions metadata.
- Non-ownership forms are ignored.
- Parsed metadata exposes CIK, form type, filing date, accession number, and primary document.
- Parser can stream ZIP members instead of requiring whole-archive extraction.

### Task 8: SEC Ingestion Security Hardening

Purpose: harden every untrusted SEC input surface after minimal parsers exist
and before large downloads, database writes, or GUI rendering are built around
them.

Likely files:

- Modify: `src/insider_scanner/core/sec_client.py`
- Modify: `src/insider_scanner/core/sec_downloader.py`
- Modify: `src/insider_scanner/core/sec_ownership_document.py`
- Modify: `src/insider_scanner/core/sec_ownership_parser.py`
- Modify: `src/insider_scanner/core/sec_bulk.py`
- Test:
  - `tests/test_sec_security.py`
  - `tests/test_sec_client.py`
  - `tests/test_sec_downloader.py`
  - `tests/test_sec_ownership_document.py`
  - `tests/test_sec_ownership_parser.py`
  - `tests/test_sec_bulk.py`

Tests:

- Only approved SEC hosts are accepted for downloads.
- Redirects to non-SEC hosts are rejected.
- Archive paths cannot escape the expected SEC archive namespace.
- Local cache paths reject path traversal and absolute paths from remote data.
- Download size, timeout, and content-type limits are enforced.
- XML parsing disables external entities and network resolution.
- XML tree size, text length, and numeric field size are bounded.
- Footnotes containing HTML/script are stored as plain text, not executable markup.
- ZIP entries with `../`, absolute paths, drive letters, or oversized decompressed output are rejected.
- ZIP bomb-like compression ratios fail before extraction.
- Error messages do not include full raw filing documents.
- Logs include enough context to debug accession-level failures without leaking raw payloads.

### Task 9: Download Scaling and Operational Polish

Purpose: add fair-access behavior, retries, resumability, progress, and cleanup
only after the minimal parser/download surfaces are hardened.

Likely files:

- Modify: `src/insider_scanner/core/sec_client.py`
- Modify: `src/insider_scanner/core/sec_downloader.py`
- Create: `src/insider_scanner/services/sec_downloads.py`
- Test:
  - `tests/test_sec_client.py`
  - `tests/test_sec_downloader.py`
  - `tests/test_sec_downloads.py`

Tests:

- Request rate can be limited below SEC's 10 requests/sec ceiling.
- HTTP 429/503 retries use bounded backoff.
- 404 returns a typed recoverable error for missing filings.
- Date batches can resume from a saved checkpoint.
- Progress reports discovered, downloaded, parsed, skipped, and failed counts.
- Temporary raw files are deleted after successful parse when cleanup is enabled.
- Failed raw files are retained only when debug/diagnostic mode is enabled.

### Task 10: SEC-Native Model and Persistence Mapping

Purpose: keep enough SEC-native metadata for dedupe, audit, and future reparse
after parser output and security invariants are stable.

Likely files:

- Modify: `src/insider_scanner/core/models.py`
- Modify: `src/insider_scanner/persistence/schema.py`
- Modify: `src/insider_scanner/persistence/migrations.py`
- Modify: `src/insider_scanner/persistence/mappings.py`
- Modify: `src/insider_scanner/persistence/repositories.py`
- Tests:
  - `tests/test_models.py`
  - `tests/test_persistence.py`
  - `tests/test_persistence_mappings.py`
  - `tests/test_repositories.py`

Tests:

- New SEC metadata fields round-trip through dataclass serialization.
- Migration adds fields without breaking existing databases.
- Repository upsert is idempotent for the same accession/row identity.
- Amendments do not create uncontrolled duplicates.
- Old `secform4` and `openinsider` records still load.
- Query filters still work by ticker, source, filing date, and trade date.
- Database constraints reject invalid date ordering or malformed identity.

### Task 11: Daily Ingestion Service

Purpose: orchestrate index download, filing fetch, parse, upsert, and coverage.

Likely files:

- Create: `src/insider_scanner/services/sec_daily.py`
- Modify: `src/insider_scanner/services/application.py`
- Modify: `src/insider_scanner/services/__init__.py`
- Test: `tests/test_sec_daily_service.py`

Tests:

- Empty daily index records successful coverage without rows.
- Mixed index processes only Forms 3/4/5 and amendments.
- Failed filing download records recoverable failure and continues when configured.
- Parser failure does not mark that filing as successfully ingested.
- Re-running the same date is idempotent.
- Date-range catch-up processes dates in order.
- Cancellation leaves current interval uncovered.
- Temporary cache cleanup runs after successful ingestion.

### Task 12: CLI Commands

Purpose: let users run daily update, catch-up, and explicit backfill.

Likely files:

- Modify: `src/insider_scanner/cli.py`
- Test:
  - `tests/test_cli.py`
  - `tests/test_cli_persistence_integration.py`

Proposed commands:

- `sec-daily --date YYYY-MM-DD`
- `sec-catchup --since YYYY-MM-DD --until YYYY-MM-DD`
- `sec-backfill --confirm-full-backfill`

Tests:

- Parser registers commands and arguments.
- Invalid dates fail with existing CLI date parser behavior.
- Backfill command requires explicit confirmation flag.
- Daily command calls service with expected date and cache options.
- Catch-up command passes inclusive date range.
- Save/persistence path integrates with existing application services.
- CLI prints a concise summary:
  - filings discovered
  - filings parsed
  - transactions inserted/updated
  - failures

### Task 13: Background Execution and Progress

Purpose: allow ingestion while user continues analysis.

Likely files:

- Modify: `src/insider_scanner/utils/threading.py`
- Modify: GUI service or scan tab files only after CLI service is stable.
- Test:
  - `tests/test_threading.py`
  - `tests/test_gui_service_integration.py`
  - targeted GUI tests if controls are added.

Tests:

- Background job reports progress without blocking caller.
- Cancellation request is respected between filings.
- Exceptions surface as user-friendly status messages.
- Database writes remain serialized or transaction-safe.
- GUI can start ingestion and continue reading existing DB data.

### Task 14: Full Bulk Backfill Workflow

Purpose: explicit heavy workflow for full database bootstrap or repair.

Likely files:

- Extend: `src/insider_scanner/core/sec_bulk.py`
- Create or extend: `src/insider_scanner/services/sec_backfill.py`
- Test: `tests/test_sec_bulk.py`

Tests:

- Backfill refuses to run without explicit confirmation.
- Resume state prevents starting from scratch after interruption.
- Large archive path streams instead of loading entire ZIP into memory.
- Selected metadata rows are converted into filing download jobs.
- Full backfill uses the same hardened downloader and parser as daily ingestion.

### Task 15: Comparison and Validation Tooling

Purpose: compare SEC-native output with current sources before switching defaults.

Likely files:

- Create: `src/insider_scanner/services/sec_compare.py`
- Optional CLI command in `src/insider_scanner/cli.py`
- Test: `tests/test_sec_compare.py`

Tests:

- Same transaction from SEC and old source matches under normalized identity.
- Differences are classified:
  - missing in SEC-native
  - missing in old source
  - value mismatch
  - date mismatch
  - classification mismatch
- Comparison report is deterministic and stable for snapshots.

### Task 16: Default Source Transition

Purpose: switch normal US data flow to SEC-native once validated.

Likely files:

- Modify: `src/insider_scanner/services/adapters.py`
- Modify: `src/insider_scanner/services/us.py`
- Modify: `src/insider_scanner/core/merger.py` if source priority changes.
- Tests:
  - `tests/test_merger.py`
  - `tests/test_integration.py`
  - `tests/test_cli_persistence_integration.py`

Tests:

- SEC-native source is available as a named adapter.
- Existing `scan` behavior remains backward compatible until defaults change.
- Source priority is deterministic.
- Third-party fallback can be disabled.
- Old source tests still pass until removal is intentional.

### Task 17: Docs and Operations

Purpose: make the workflow understandable and maintainable.

Likely files:

- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Add project docs only after deciding final doc location.

Tests:

- README command snippets pass existing readme command tests.
- New docs explain SEC fair access:
  - declared user-agent
  - 10 requests/sec max
  - daily index lag
  - explicit full backfill cost

## Suggested Future Sessions

### Session 1: Minimal SEC Primitives and Daily Index Parser

Codex:

- Add SEC URL/date helpers.
- Add daily master index parser.
- Add deterministic daily-index fixtures.
- Add unit tests.

User:

- Pick validation tickers/dates.
- Provide SEC user-agent identity details.

Exit criteria:

- Unit tests for URL helpers and daily index parser pass.
- No production network dependency in tests.

### Session 2: Minimal Clients and Parsers for Every Input Type

Codex:

- Add minimal SEC client with injected transport.
- Add minimal filing downloader.
- Add ownership XML extraction.
- Add Form 3/4/5 non-derivative, derivative, footnote, and amendment parser coverage.
- Add minimal bulk submissions ZIP metadata parser using a small fixture ZIP.

User:

- Confirm transaction classification expectations.
- Validate parser output against a few SEC filing pages.

Exit criteria:

- Parser converts fixture ownership documents into normalized in-memory records.
- Daily index, filing `.txt`, direct XML, amendment, derivative, and bulk ZIP metadata input shapes are all represented in tests.
- Footnotes and amendments are not silently discarded.

### Session 3: SEC Ingestion Security Hardening

Codex:

- Threat model SEC ingestion trust boundaries.
- Harden host allowlists, redirects, archive paths, temp paths, download bounds, XML parsing, ZIP parsing, and log/error behavior.
- Add malicious fixtures for XML, ZIP, footnotes, paths, oversized values, and malformed filing metadata.

User:

- Review security assumptions:
  - whether raw filings may be retained for diagnostics,
  - where temporary downloads may live,
  - whether GUI should ever show raw footnote markup.

Exit criteria:

- Malicious fixtures are rejected safely.
- Security tests pass before any scaled live downloads or DB writes are added.

### Session 4: Download Scaling and Operational Polish

Codex:

- Add fair-access rate limiting.
- Add retry/backoff and recoverable failure handling.
- Add checkpoint/resume support.
- Add progress counters.
- Add cache cleanup policy.
- Add date-range daily orchestration without DB writes if possible.

User:

- Try a dry-run or fixture-backed daily batch summary.
- Confirm acceptable retry and retention behavior.

Exit criteria:

- Download orchestration can process a selected date range safely and resumably.
- It reports discovered, downloaded, parsed, skipped, and failed counts.

### Session 5: Persistence and Idempotent Daily Ingestion

Codex:

- Extend model/schema/mappings if needed.
- Add migrations.
- Add daily ingestion service with repository upserts.
- Add repository/idempotency tests.

User:

- Confirm DB storage choices for raw SEC metadata and amendment behavior.

Exit criteria:

- Re-running the same daily ingest does not duplicate records.
- Existing DB tests still pass.

### Session 6: CLI and Background Operation

Codex:

- Add `sec-daily`, `sec-catchup`, and guarded `sec-backfill` commands.
- Add progress summary.
- Add background execution hooks.

User:

- Try CLI against a selected live date.
- Decide whether GUI controls are needed immediately.

Exit criteria:

- Daily ingestion can run from CLI into local DB.
- User can continue analysis while ingestion runs, at least from service layer.

### Session 7: Full Bulk Backfill Workflow

Codex:

- Expand ZIP metadata parser into explicit full backfill workflow.
- Add resume state.
- Add explicit confirmation guard.
- Ensure backfill reuses hardened downloader/parser paths.

User:

- Decide acceptable disk/cache location and whether full backfill is worth exposing in GUI.

Exit criteria:

- Backfill path is safe, explicit, resumable, and tested with small fixtures.

### Session 8: Validation, Default Switch, and Third-Party Deprecation

Codex:

- Add comparison report tooling.
- Run side-by-side checks for selected dates/tickers.
- Change default source only after validation.
- Update docs.

User:

- Review comparison reports.
- Approve default switch and fallback/removal strategy.

Exit criteria:

- SEC-native source becomes trusted default.
- Third-party sources are either fallback-only or queued for removal.

## Test Command Set

Targeted commands during development:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_edgar.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_index.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_client.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_ownership_document.py tests\test_sec_ownership_parser.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_bulk.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_security.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_downloads.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_persistence.py tests\test_persistence_mappings.py tests\test_repositories.py -v
.\.venv\Scripts\python.exe -m pytest tests\test_sec_daily_service.py tests\test_cli.py tests\test_cli_persistence_integration.py -v
```

Full verification before merging:

```powershell
.\.venv\Scripts\python.exe -m pytest -v -p no:cacheprovider --basetemp=build\pytest-tmp
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
```

Coverage target remains 80% or higher.

## Open Decisions

1. Which SEC metadata fields must become first-class columns versus JSON/detail fields?
2. Should amendments replace original rows or appear as linked correction records?
3. Should derivative transactions be included in default analysis views?
4. Should raw filing documents ever be retained after successful parse?
5. Should GUI support full backfill, or keep it CLI-only?
6. When do we switch defaults away from `secform4` and `openinsider`?
