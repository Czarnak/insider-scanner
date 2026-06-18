# SEC EDGAR Data Ingestion Plan

Date: 2026-06-17
Branch: `codex/sec-edgar-data-ingestion`

## Context

We are considering replacing third-party US insider data sources such as
`secform4` and `openinsider` with SEC-native ingestion. The goal is to make SEC
EDGAR the source of truth, keep the database lightweight, and let users query
already-loaded data instead of depending on live third-party pages.

## Agreed Direction

- Use SEC EDGAR as the primary source for US insider data.
- Use daily EDGAR index files for normal daily updates.
- Keep the SEC bulk submissions ZIP available only for explicit full backfills
  or repair workflows.
- Parse filings into normalized database records, then clean temporary raw
  downloads.
- Run ingestion in the background so users can continue analysis while new data
  loads.

## SEC Sources

### Daily Operations

Use the daily master index:

```text
https://www.sec.gov/Archives/edgar/daily-index/YYYY/QTRn/master.YYYYMMDD.idx
```

This file lists filing metadata, including CIK, company name, form type, filing
date, and archive file path. Normal daily ingestion should filter this index for
ownership forms:

- `3`
- `3/A`
- `4`
- `4/A`
- `5`
- `5/A`

Then fetch only the matching filing documents from SEC Archives.

### Full Backfill

Use the nightly bulk submissions ZIP only when the user explicitly requests a
full database backfill:

```text
https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
```

This archive is a full submissions snapshot, not a daily delta. It contains
per-CIK JSON submission-history files, including metadata such as company data,
tickers, exchanges, filing dates, form types, accession numbers, and primary
document names. It does not contain parsed insider transaction rows.

### Optional Search Support

The EDGAR full-text search web endpoint can be useful for prototyping or
manual comparison, but it should not be the primary ingestion contract:

```text
https://efts.sec.gov/LATEST/search-index
```

## Proposed Daily Flow

1. Determine the target filing date.
2. Download the corresponding daily master index.
3. Filter for Forms `3`, `3/A`, `4`, `4/A`, `5`, and `5/A`.
4. For each matching filing, fetch the SEC archive `.txt` or primary document.
5. Parse ownership XML/HTML into normalized insider transaction records.
6. Upsert records by stable SEC identifiers, primarily accession number plus
   transaction/table row identity.
7. Store SEC source URLs, filing metadata, issuer CIK, reporting owner CIK,
   filing date, report date, transaction date, form type, and parsed transaction
   details.
8. Delete temporary raw files after successful processing.

## Performance Assumptions

- SEC fair access guidance allows no more than 10 requests per second.
- A daily batch of about 800 to 2,000 ownership filings is reasonable.
- At 10 requests per second, network transfer and parsing should usually fit
  comfortably into a few minutes; slower connections may take longer.
- CPU cost should be modest. The harder parts are retries, deduplication,
  amendments, footnotes, derivative tables, and parser correctness.

## Design Implications

- The local database becomes the application query surface.
- Online SEC access is needed for ingestion and backfill, not for every user
  analysis action.
- The ingestion process should be resumable and idempotent.
- The UI/CLI should distinguish between:
  - daily update,
  - date-range catch-up,
  - full backfill,
  - repair/reparse existing filings.

## Risks and Questions

- Daily indexes are built after the filing day and may lag real-time EDGAR
  search results.
- Some late ownership submissions may appear in the next business day's index.
- Form amendments must be handled carefully to avoid duplicate or stale data.
- Form 4 derivative and non-derivative tables need separate parsing paths.
- Footnotes may change transaction interpretation and should not be discarded
  blindly.
- Full backfill via `submissions.zip` is large and should require explicit user
  intent.

## References

- SEC EDGAR APIs: <https://www.sec.gov/search-filings/edgar-application-programming-interfaces>
- Accessing EDGAR Data: <https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data>
- SEC Developer Resources and Fair Access: <https://www.sec.gov/about/developer-resources>
