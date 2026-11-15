# PR22 alert ingestion progress and view refresh

Status: closed 2026-11-15
Created: 2026-11-16

## Description

Implement PR 22 from docs/review/PR_GROUPS_2026-11-14.md: USSVC-11,
PERSIST-22, GUI-13 and GUI-11. Alerts must find newly ingested transactions
regardless of trade date, acknowledge only presented matches, and refresh
the feed, entity pages and badge after scans and alert changes. Existing
database contents and saved rules remain readable.

## Acceptance criteria

- [x] An additive migration tracks committed ingestion with a monotonic sequence across all markets; existing rows, rollback and reused source row IDs remain safe.
- [x] Criteria and watchlist alerts filter unseen rows in SQL before a bounded ascending ingestion page, respect market and exact entity identity, and report remaining matches.
- [x] Saved rules round-trip sequence watermarks and read legacy timestamps conservatively; acknowledgement advances only through presented records without skipping concurrent insertions.
- [x] US, Congress and European scan completion refreshes feed, selected entities and alerts; rule/watchlist edits and acknowledgement update the badge and invalidate stale async results.
- [x] Focused red/green regressions, offline suite, Procoder test/gate and independent review verify the final combined change.

## Implementation plan

1. Reproduce ingestion-window, timestamp-tie and pagination failures; add the ingestion ledger, migration and bounded alert query; verify and commit.
2. Reproduce sequence acknowledgement and service failures; persist validated cursors and evaluate bounded ingestion pages; verify and commit.
3. Reproduce stale views, badge and callback failures; wire scan signals, selection reloads and presented-only acknowledgement; verify and commit.
4. Review the combined diff, commit any fixes separately, run offline and Procoder checks, record evidence and close through Procoder; push and open the requested PR.

## Evidence

- Backend RED: `.venv/Scripts/python.exe -m pytest tests/test_alert_ingestion_repository.py -q --tb=short` produced 13 failures before the query existed (`.cache/pr22-ingestion-red.txt`). Separate failing regressions reproduced SQLite expression-depth overflow for 1,000 watchlist entries and omission of bare-second historical timestamps (`.cache/pr22-ingestion-watchlist-red.txt`, `.cache/pr22-ingestion-legacy-red.txt`). Backend GREEN passed 46 backend/feed tests; after the full suite exposed historical schema expectations, `pytest tests/test_persistence.py tests/test_read_indexes.py tests/test_alert_ingestion_repository.py -q --tb=short` passed all 48 cases (`.cache/pr22-schema-green.txt`). These verify migration 13 source preservation, idempotent restart, upgrade rollback, all-market sequencing, timestamp ties, bounded pages, exact market-scoped identities, no-op upserts and source-ID reuse.
- Store and service RED/GREEN: invalid persisted sequences were accepted before validation; the dedicated service command `pytest tests/test_pr22_alert_evaluation.py -q --tb=short` failed three assertions because evaluation used trade-date candidates and never requested ingestion pages (`.cache/pr22-service-red.txt`). The combined store/service regression command over `test_pr22_alert_evaluation.py`, `test_pr22_alert_progress.py`, `test_alert_evaluation.py`, `test_alert_store.py` and `test_pr21_alert_boundaries.py` passed 57 tests (`.cache/pr22-alert-green.txt`). Sequence acknowledgements are immutable and monotonic, untouched rules retain their watermark, and legacy timestamps stop filtering after the first sequence acknowledgement.
- GUI RED/GREEN: 19 initial failures reproduced stale pages, acknowledgement and badge issues (`.cache/pr22-gui-red.txt`); three additional failures reproduced persisted rows left invisible after scan errors (`.cache/pr22-gui-error-red.txt`). The focused GUI/entity/shell/service run passed 110 tests. A queued-worker lifetime regression failed before retaining alert workers (`.cache/pr22-alert-worker-red.txt`); all 25 PR22 GUI cases passed after the fix (`.cache/pr22-alert-worker-green.txt`). The real SQLite shell integration verifies feed, selected company/insider history and alerts after scan completion; stale callbacks, toggles, deletes, watchlist changes and unseen overflow remain covered.
- Mutation review: removing ingestion predicates or ascending order breaks pagination/timestamp-tie tests; dropping AUTOINCREMENT breaks deleted-row reuse; broad watchlist matching breaks exact-market tests; advancing through undisplayed records breaks acknowledgement tests; removing scan error signals or worker retention breaks the corresponding GUI regressions. The independent Brooks/Procoder review reread both fixes and the historical schema-test update and reported no unresolved actionable finding.
- Full offline GREEN: `uv run --locked --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr22-coverage.json --cov-fail-under=80` passed 3,360 tests with 5 skipped and 8 deselected; total coverage is 91.45% (`.cache/pr22-offline.txt`, `.cache/pr22-coverage.json`). The earlier run's three schema-expectation failures are retained in `.cache/pr22-offline-initial.txt`; they were fixed and are not counted as a pass. Live provider calls were not exercised; tests use disposable databases and profiles.
- Final `ruff check .`, `ruff format --check .` (320 files), `mypy src/insider_scanner` (122 source files) and `git diff --check master` passed. The Procoder Git report has clean hygiene, the intended feature branch and registered templates (`.cache/pr22-procoder-git.txt`). The two pre-existing untracked backup archives remain excluded from commits.
- Final `procoder test` over the seven changed regression files passed 94 tests (`.cache/pr22-procoder-test.txt`). `procoder check --paths-from .cache/pr22-paths.txt` passed all 20 branch files: zero unformatted, unchecked or blocking findings (`.cache/pr22-procoder-gate-final.txt`). Informational findings concern docstrings and complexity. These final checks ran on the host with the existing Windows-trusted CA bundle; no security rule was disabled. Earlier sandbox/initial gate runs are superseded by this clean report and are not counted as passing verification.
