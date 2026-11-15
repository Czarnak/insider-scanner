# PR18 quarterly SEC datasets

Status: open
Created: 2026-11-15

## Description

Implement PR 18 from docs/review/PR_GROUPS_2026-11-14.md (BULK-02,
SECIO-03, CLI-M1 and ARCH-06). Import quarterly DERA Form 3/4/5 TSV
archives through shared SEC mapping, atomic batches and the durable accession
ledger. Preserve XML evidence, amendments, missing values and user data.
Expose bounded dates, cancellation, progress and explicit historical gap and
unpublished-quarter guidance. Use submissions archives for targeted XML repairs.

## Acceptance criteria

- [ ] Quarterly TSV archives stream into shared ownership records with all owners, derivatives, footnotes, explicit missing values and bounded archive validation.
- [ ] Dataset identities do not assume surrogate keys equal XML positions; complete XML evidence takes precedence and successful XML repairs atomically replace dataset rows without duplicate totals.
- [ ] Dataset imports use bounded atomic filing batches and durable retry/resume state, with committed progress, cancellation and contextual failures.
- [ ] The CLI supports local quarterly archives and SEC downloads, inclusive filing-date bounds and targeted CIK repairs; invalid input fails before network work.
- [ ] Documentation explicitly covers the 2006 Q1 boundary, 2003–2005 XML path, unpublished quarters and quarter-end publication overlap.
- [ ] RED/GREEN evidence, independent review, the offline coverage suite, Procoder check/test and Git checks pass; implementation and fixes are committed incrementally.

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
- Persistence RED after adding the representation contract: 9 assertion failures and 1 existing pass. Independent review added two reproduced regressions (partial XML incorrectly promoted to success; mismatched XML accession deleting dataset evidence). GREEN: Procoder test passed 80 focused persistence/effective-transaction tests. The initial broad gate reported one additional suite failure, which remains to be identified and resolved before completion.
- Download RED: 16 expected missing-feature assertions. GREEN: parent Procoder test passed 183 download, SEC client, atomic-file, security and cache-I/O tests. Focused gate: 6 clean, zero unchecked or blocking findings. Independent review found no actionable defect.
- Parser RED: missing reader, aggregate per-accession memory bounds, dropped corrupt requested-owner records, encrypted ZIPs and oversized/multiline records. GREEN: 39 parser cases; Ruff and mypy passed. All five imported tables are conservatively required because absence is not documented as proof of an empty table.
- Service RED: 6 assertion failures for the missing ingestion service. GREEN: parent ran 64 parser/service/persistence/batch tests. Progress, cancellation, failed commits, retry, reimport, missing values and explicit unavailable XML hash/URL/narrative are covered.
- Live archive inspection could not start: the approved SEC client reports SEC_USER_AGENT is unset. No placeholder identity or user credential was substituted.
