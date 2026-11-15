# PR18 quarterly SEC datasets

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 18 from docs/review/PR_GROUPS_2026-11-14.md (BULK-02,
SECIO-03, CLI-M1 and ARCH-06). Import quarterly DERA Form 3/4/5 TSV
archives through shared SEC mapping, atomic batches and the durable accession
ledger. Preserve XML evidence, amendments, missing values and user data.
Expose bounded dates, cancellation, progress and explicit historical gap and
unpublished-quarter guidance. Use submissions archives for targeted XML repairs.

## Acceptance criteria

- [x] Quarterly TSV archives stream into shared ownership records with all owners, derivatives, footnotes, explicit missing values and bounded archive validation.
- [x] Dataset identities do not assume surrogate keys equal XML positions; complete XML evidence takes precedence and successful XML repairs atomically replace dataset rows without duplicate totals.
- [x] Dataset imports use bounded atomic filing batches and durable retry/resume state, with committed progress, cancellation and contextual failures.
- [x] The CLI supports local quarterly archives and SEC downloads, inclusive filing-date bounds and targeted CIK repairs; invalid input fails before network work.
- [x] Documentation explicitly covers the 2006 Q1 boundary, 2003–2005 XML path, unpublished quarters and quarter-end publication overlap.
- [x] RED/GREEN evidence, independent review, the offline coverage suite, Procoder check/test and Git checks pass; implementation and fixes are committed incrementally.

## Implementation plan

1. Add archive parsing and SEC dataset discovery/download boundaries with focused
   failing tests, then shared ownership mapping and resource checks.
2. Add representation-aware atomic ledger persistence and test XML precedence,
   failed repair rollback, empty repairs and repeated imports.
3. Add the dataset service, scoped XML date/force repair support and CLI commands;
   test interruption, persistence failure, resume and invalid input.
4. Update existing SEC documentation, perform independent/adversarial review,
   resolve defects, run the full offline suite and Procoder gates, then push and
   open the requested PR against master.

## Evidence

- Fresh `git fetch origin --prune` and `git rev-list --left-right --count master...origin/master` reported 0 ahead / 0 behind; branch created from master at 18ffb5f.
- Primary SEC format sources: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets and https://www.sec.gov/files/insider_transactions_readme.pdf. Surrogate transaction keys have no documented XML-position equivalence; dataset identities must remain explicit.
- Persistence RED after adding the representation contract: 9 assertion failures and 1 existing pass. Independent review added two reproduced regressions (partial XML incorrectly promoted to success; mismatched XML accession deleting dataset evidence). GREEN: Procoder test passed 80 focused persistence/effective-transaction tests.
- Download RED: 16 expected missing-feature assertions. GREEN: parent Procoder test passed 183 download, SEC client, atomic-file, security and cache-I/O tests. Focused gate: 6 clean, zero unchecked or blocking findings. Independent review found no actionable defect.
- Parser RED: missing reader, aggregate per-accession memory bounds, dropped corrupt requested-owner records, encrypted ZIPs and oversized/multiline records. GREEN: 39 parser cases; Ruff and mypy passed. All five imported tables are conservatively required because absence is not documented as proof of an empty table.
- Service RED: 6 assertion failures for the missing ingestion service. GREEN: parent ran 64 parser/service/persistence/batch tests. Progress, cancellation, failed commits, retry, reimport, missing values and explicitly unavailable XML hashes/URLs are covered.
- Amendment review RED/GREEN: an uncapped correction failed to supersede; missing reference headers incorrectly hid an original; a 2,000-character narrative ending in whitespace incorrectly appeared complete. Each failed before its fix. Final parser/service/effective suite: 102 passed. Native notes and remarks retain storage length; capped or incomplete narratives preserve original observations.
- CLI RED: eight missing-command assertions plus unrecognized repair flags; date/force behavior then failed two fetch-count assertions and two invalid-scope checks. GREEN: 88 CLI, repair, archive, progress and GUI tests. Holiday cutoff and actionable archive-error regressions each failed before fixes. README/GUI now recommend datasets for published history and explain the early/unpublished intervals.
- Compatibility RED: the first full coverage run found the historical-schema archive-progress upgrade failing on a newly selected original_submission_date column (3,150 other tests passed). Narrowed representation reads to the five required existing columns; all 30 migration/ledger/representation regressions then passed through Procoder test.
- Symbol RED: four dataset cases preserved placeholders, lowercase or multiple symbols incorrectly. GREEN: 210 dataset/XML parser/ownership/mapping tests after sharing the existing normalizer. Two cases also verify documented nullable dataset transaction codes remain unknown/Other.
- Independent read-only reviews and Procoder adversarial/edge-case and four-perspective passes completed. Concrete findings were resolved with regressions. Manual mutation review covers wrong native identities, removed XML precedence, partial replacement commits, omitted reference evidence, lost response truncation/reset, and skipped cancellation/ledger writes.
- Live archive inspection could not start: the approved SEC client reports SEC_USER_AGENT is unset. No placeholder identity or user credential was substituted.
- Final offline suite: `uv run --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr18-coverage.json --cov-fail-under=80` — 3,161 passed, 5 skipped, 8 live tests deselected; coverage 91.03%. The migration compatibility failure is resolved.
- Final `ruff check src tests`, `ruff format --check src tests`, `mypy src/insider_scanner` and `git diff --check master...HEAD` passed: 277 formatted files and 119 typed source files. No dependency, lockfile, schema migration, credential or user-data changes were needed.
- Final Procoder gate over the complete 24-file branch diff: 24 clean, zero unformatted/unchecked/out-of-scope files, zero blocking findings. The explicit integration `procoder test` passed 166 tests. All implementation/fix steps have separate conventional commits with no attribution trailers. `procoder git` confirms the feature branch and registered templates; the existing release concurrency advisory remains unchanged.
