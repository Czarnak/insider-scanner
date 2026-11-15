# PR16 effective transactions and amendment supersession

Status: open
Created: 2026-11-15

## Description

Implement PR 16 from docs/review/PR_GROUPS_2026-11-14.md. Preserve raw SEC and
Congress observations while exposing effective transactions to scans, feeds,
totals and alerts. Record explicit amendment and duplicate replacement links
atomically, migrate existing data, and compare equivalent SEC/scraper scope.

## Acceptance criteria

- [ ] Complete, unambiguous SEC and Senate amendments supersede earlier filings in either ingestion order; ambiguous and partial evidence remains visible.
- [ ] Matching SEC/scraper transactions count once in effective reads, including weighted fills, while raw queries retain source observations and repeated native lots.
- [ ] Migration preserves raw keys, timestamps and user data, repairs existing visibility, and remains idempotent.
- [ ] SEC comparison tolerates whole-dollar rounding, aggregates only within a filing, excludes incomparable SEC scope and retains mismatch evidence.
- [ ] Focused regressions demonstrate RED then GREEN; offline tests, coverage, Procoder check/test and repository quality checks complete successfully.

## Evidence

- RED comparison: `uv run --extra dev --extra gui pytest tests/test_sec_comparison.py -q` — 4 assertion failures (rounding, weighted aggregate, scope), 10 passed before implementation.
- RED Senate: `uv run --extra dev --extra gui pytest tests/test_congress_senate.py -q -k 'search_preserves_explicit_amendment_metadata or scrape_retains_original_and_amendment_with_linkage_metadata'` — 7 assertion failures for missing amendment metadata before implementation.
