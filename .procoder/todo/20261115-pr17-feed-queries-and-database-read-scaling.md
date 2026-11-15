# PR17 feed queries and database read scaling

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 17 from docs/review/PR_GROUPS_2026-11-14.md. Bound latest
reads in SQL, share feed validation, query selected markets directly, use stable
cursor pagination and indexed entity lookups, and derive feed freshness from
successful refresh state. Preserve user data and effective transaction semantics.

## Acceptance criteria

- [x] Latest source/provenance reads apply SQL filters and limits before mapping rows.
- [x] Feed criteria share validation; per-market pages are bounded and optional totals avoid repeated count scans.
- [x] Cursor pagination handles all sorts, ties, nulls and intervening inserts; the GUI uses continuation cursors and synchronizes saved sort indicators.
- [x] Direct details and normalized issuer/person lookups use relevant tables and indexes; Unicode search matches equivalent casefolded text.
- [x] An additive, repeatable migration installs read indexes while preserving data and indexes used by ingestion and amendments.
- [x] Freshness reflects relevant successful refreshes, including empty refreshes, rather than newly inserted historical rows.
- [x] Focused regressions demonstrate RED/GREEN and independent review is resolved; the offline suite, coverage, Procoder check/test and Git checks pass.

## Evidence

- Latest RED: `uv run --extra dev --extra gui pytest tests/test_latest_reads.py -q` — 6 expected assertion failures (mapping 7/4 rows for count 2), 2 baseline passes. GREEN with effective-transaction and repository regressions — 83 passed. Parent independently reran latest plus GUI tests — 37 passed.
- Feed RED: `uv run --extra dev --extra gui pytest tests/test_feed_scaling.py -q` — 19 expected assertion failures for missing cursors/validation, Unicode mismatches, two detail SELECTs, and insertion-based freshness.
- Schema/search RED: `uv run --extra dev --extra gui pytest tests/test_read_indexes.py tests/test_search_index.py -q` — 3 expected assertion failures for absent deterministic casefold, migration 12 and FTS tables.
- GUI RED: saved/initial sort and layout-restoration cases — 2 assertion failures. Cursor/unknown-total cases — 2 assertion failures. GREEN integrated command covering read indexes, search, feed scaling/repository/detail/filters/page and persistence — 173 passed. Reflection warnings were removed by inspecting SQLite schema directly; persistence and alerts then passed all 36 tests without warnings.
- Migration/read-index tests verify identical stored rows before/after upgrading version 11, repeat bootstrap, retained accession/CIK indexes and EXPLAIN use of ordered date/issuer/Unicode-person indexes. Search tests cover existing-row backfill, all-market triggers, transaction rollback, Unicode casefold, literal operators/wildcards/quotes and indexed MATCH plans.
- Freshness tests cover absent refresh state despite inserted rows, an empty successful refresh, selected-market isolation and stale/future clocks. Cursor tests independently specify expected ordering for all six fields in both directions and exclude intervening inserts.
- Price-fixture correction RED: 7 setup errors from creating transaction expression indexes without the application connection functions. GREEN after isolating price-only schema — all 10 price tests passed.
- Independent read-only review found no reportable correctness defects; its count-scan integration reminder was addressed in criteria alert evaluation. Manual mutation review covers omitted SQL LIMIT, dropped source provenance, skipped cursor boundary/snapshot predicates, missing FTS trigger writes and insertion-derived freshness.
- Full offline suite: `uv run --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr17-coverage.json --cov-fail-under=80` — 3,059 passed, 5 skipped, 8 live tests deselected; 12,168/13,382 lines covered (90.93%). The remaining normalization-fixture expression-index reflection warning was then removed; all 11 normalization migration tests passed without warnings.
- Final `ruff check src tests`, `ruff format --check src tests` and `mypy src` passed (266 formatted files, 115 typed source files).
- Procoder code/security gate over the complete changed-file list checked all source changes: 19 clean files, zero unchecked and zero blocking findings; README byte formatting was the sole remaining format issue. Applied the reviewed formatter output, then the documentation gate passed: 2 clean, zero unformatted/unchecked/blocking findings. Semgrep required targeted escalation because the sandbox denied its internal socketpair; it then completed successfully without changing security policy.
- Final explicit `procoder test` passed all 3,059 offline tests after fixture corrections, with the 80% coverage threshold enforced; its separate coverage JSON also records 90.93% coverage. Full source gate likewise produced the same successful coverage result.
- Final `procoder git` confirms the feature branch, registered templates and workflow rules; only the existing release-workflow concurrency advisory remains. `git diff --check` passed. No new dependency, user data or credential files were staged.
