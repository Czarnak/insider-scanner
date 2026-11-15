# PR16 effective transactions and amendment supersession

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 16 from docs/review/PR_GROUPS_2026-11-14.md. Preserve raw SEC and
Congress observations while exposing effective transactions to scans, feeds,
totals and alerts. Record explicit amendment and duplicate replacement links
atomically, migrate existing data, and compare equivalent SEC/scraper scope.

## Acceptance criteria

- [x] Supported SEC amendment lines replace matched observations and preserve unchanged/added lines; complete unambiguous Senate reports supersede originals in either ingestion order. Ambiguous and partial evidence remains visible.
- [x] Matching SEC/scraper transactions count once in effective reads, including weighted fills, while raw queries retain source observations and repeated native lots.
- [x] Migration preserves raw keys, timestamps and user data, repairs existing visibility, and remains idempotent.
- [x] SEC comparison tolerates whole-dollar rounding, aggregates only within a filing/security, excludes incomparable SEC scope and retains mismatch evidence.
- [x] Focused regressions demonstrate RED then GREEN; offline tests, coverage, Procoder check/test and repository quality checks complete successfully.

## Evidence

- RED comparison: `uv run --extra dev --extra gui pytest tests/test_sec_comparison.py -q` — 4 assertion failures (rounding, weighted aggregate, scope), 10 passed before implementation.
- RED Senate: `uv run --extra dev --extra gui pytest tests/test_congress_senate.py -q -k 'search_preserves_explicit_amendment_metadata or scrape_retains_original_and_amendment_with_linkage_metadata'` — 7 assertion failures for missing amendment metadata before implementation.
- Policy correction: SEC Form 4 General Instruction 9 requires line-level treatment, not whole-filing replacement. See docs/review/PR16_EFFECTIVE_TRANSACTIONS.md for the primary source and conservative inference limits.
- RED line policy and metadata: focused effective/parser/mapping selection — 10 expected assertion failures, 1 baseline pass. GREEN: `uv run --extra dev --extra gui pytest tests/test_effective_transactions.py tests/test_transaction_identity_migration.py tests/test_sec_ownership_parser.py tests/test_sec_trade_mapping.py -q` — 176 passed.
- Review RED/GREEN: five initial correction/source/metadata regressions failed then passed; issuer-ticker amendment failed then passed; partial SEC completion failed then passed. Comparator security/assignment cases failed then `pytest tests/test_sec_comparison.py -q` passed all 16 tests.
- Final review RED: stale aggregate insertion-order and incomplete narrative/mapping cases — 4 assertion failures, 1 baseline pass. GREEN: `uv run --extra dev --extra gui pytest tests/test_effective_transactions.py tests/test_sec_trade_mapping.py tests/test_sec_missing_values.py -q` — 141 passed. Covers migration fixtures, unchanged lines, additions, repeated lots, chain resolution and ambiguous original dates.
- `uv run --extra dev --extra gui mypy src/insider_scanner` — no issues in 114 source files; Ruff check and formatting pass.
- Independent review used isolated workers and read-only review; identified defects received regression tests and fixes. Mutation checks cover missing replacement predicates, lost raw rows, absent addition/completeness vetoes, aggregation across accessions/securities and source-generator reuse.
- Final wording RED/GREEN: unrelated address correction mentioning unchanged shares failed the new assertion before the fix; all 39 effective-transaction tests then passed after requiring correction phrases tied directly to the changed financial field.
- Pre-review full suite: `procoder test` with `PYTEST_ADDOPTS="--cov=insider_scanner --cov-report=term:skip-covered --cov-fail-under=80"` — 3,003 offline tests passed; coverage JSON records 12,035/13,246 lines covered (90.86%).
- Final `procoder check --paths-from .cache/pr16-changed-files.txt` — 26 clean, 0 unformatted, 0 unchecked, 0 out of scope, 0 blocking findings. Informational findings concern docstrings, complexity and the existing release workflow concurrency advice.
- `procoder git` — branch correct, templates present/registered, no attribution or hygiene block. `git diff --check` passes; master/origin/master remained synchronized at the branch base.
- Fresh-context full-diff review found 2 Important issues: incomplete joint-owner fallback and assumed legacy narrative completeness. Both were fixed after 4 expected RED failures / 2 baseline passes; all 46 effective-transaction tests pass, including migration preservation and complete-source reparse. Explicit narrative fixtures retain affirmative completeness so addition/field-specific guards remain independently tested. The reviewer confirmed: "Nothing found — open the PR."
- Post-review final `procoder test` with the coverage options above — 3,010 offline tests passed; 12,044/13,258 application lines covered (90.84%), exceeding the 80% gate. Final Ruff lint/format and mypy also pass.
- Post-review final `procoder check --paths-from .cache/pr16-changed-files.txt` — 26 clean, 0 unformatted, 0 unchecked, 0 out of scope, 0 blocking findings. Final `procoder git` reports the correct feature branch, registered templates and only the existing release-workflow concurrency advice.
