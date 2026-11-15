# PR22 alert ingestion progress and view refresh

Status: open
Created: 2026-11-16

## Description

Implement PR 22 from docs/review/PR_GROUPS_2026-11-14.md: USSVC-11,
PERSIST-22, GUI-13 and GUI-11. Alerts must find newly ingested transactions
regardless of trade date, acknowledge only presented matches, and refresh
the feed, entity pages and badge after scans and alert changes. Existing
database contents and saved rules remain readable.

## Acceptance criteria

- [ ] An additive migration tracks committed ingestion with a monotonic sequence across all markets; existing rows, rollback and reused source row IDs remain safe.
- [ ] Criteria and watchlist alerts filter unseen rows in SQL before a bounded ascending ingestion page, respect market and exact entity identity, and report remaining matches.
- [ ] Saved rules round-trip sequence watermarks and read legacy timestamps conservatively; acknowledgement advances only through presented records without skipping concurrent insertions.
- [ ] US, Congress and European scan completion refreshes feed, selected entities and alerts; rule/watchlist edits and acknowledgement update the badge and invalidate stale async results.
- [ ] Focused red/green regressions, offline suite, Procoder test/gate and independent review verify the final combined change.

## Implementation plan

1. Reproduce ingestion-window, timestamp-tie and pagination failures; add the ingestion ledger, migration and bounded alert query; verify and commit.
2. Reproduce sequence acknowledgement and service failures; persist validated cursors and evaluate bounded ingestion pages; verify and commit.
3. Reproduce stale views, badge and callback failures; wire scan signals, selection reloads and presented-only acknowledgement; verify and commit.
4. Review the combined diff, commit any fixes separately, run offline and Procoder checks, record evidence and close through Procoder; push and open the requested PR.

## Evidence

