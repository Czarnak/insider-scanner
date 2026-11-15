# PR21 unified watchlists and protected alert configuration

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 21 from docs/review/PR_GROUPS_2026-11-14.md: PERSIST-19,
PERSIST-20, PERSIST-21, PERSIST-M3, ARCH-28, GUI-21 and GUI-M1. Named JSON
watchlists become the shared source for monitoring, scanning and analysis;
legacy symbols migrate once without losing existing data. Alert rules retain
their targets after renames and malformed configuration cannot widen matches.

## Acceptance criteria

- [x] Watchlist names reject case-insensitive collisions, updates retain order, and list/entry/rule capacity failures are explicit without losing stored items.
- [x] Legacy US symbols migrate atomically once into JSON alongside existing lists; deleting migrated symbols persists across restarts and migration failures preserve user data.
- [x] Invalid alert criteria remain visibly disabled across save/load; timestamps normalize to UTC; watchlist renames preserve rule references.
- [x] Scan and analysis use the shared store and refresh after edits; per-ticker scan failures retain successful rows and visibly identify failures.
- [x] GUI mutation failures show messages, retain selection/input and avoid reporting unsuccessful additions as saved.
- [x] Focused red/green regressions, offline suite, Procoder test/gate and independent review verify the combined change.

## Implementation plan

1. Reproduce watchlist collision/cap and migration failures; implement store operations and migration; run focused tests and commit.
2. Reproduce alert cap/order, criteria and timestamp failures; implement validated immutable alert updates; run focused tests and commit.
3. Reproduce per-ticker scan failure; inject shared watchlists into scan/analysis and retain partial results; run focused tests and commit.
4. Reproduce GUI rename/error and startup migration issues; wire pages, migration and alert references; run focused tests and commit.
5. Review the combined diff, fix any findings in separate commits, run offline tests and Procoder checks, record evidence and close through Procoder; push and open the requested PR.

## Evidence

- Store and GUI RED: `.venv/Scripts/python.exe -m pytest tests/test_watchlist_store.py tests/test_alert_store.py tests/test_alert_evaluation.py tests/test_pr21_watchlist_pages.py -q --tb=short` produced 36 failures and 49 passes before implementation (`.cache/pr21-red-stores-pages.txt`). Assertions exposed collisions, silently dropped additions, widened invalid criteria, naive timestamp comparisons, missing migration and orphaned rename references.
- Watchlist GREEN: 42 focused store tests passed, covering atomic migration, existing JSON plus text, one-time import after deletion, BOM/comments, unique names, capacity rejection, write/encoding failure preservation and historical data beyond edit caps. Three additional preservation/decoding cases were seen red before their fixes.
- Alert GREEN: 46 store/evaluation/boundary tests passed. Boundary RED reproduced UTC overflow on both datetime limits and lost matches for merged historical names; fixes disable bad timestamps and resolve trimmed/case-insensitive references without losing other rules.
- Scan RED reproduced calls stopping at `BAD` before `MSFT`; the managed Analysis symbol regression read `LEGACY` rather than the JSON symbols. GREEN covered continued scanning, successful row retention, incomplete metadata, filtering warnings and refreshed symbols. A separate chart regression saw an active request become `None` after an unrelated edit; its fix passed 17 focused tests and still invalidates results when the selected symbol is removed.
- Rename/UI RED: `.venv/Scripts/python.exe -m pytest tests/test_pr21_watchlist_pages.py -k 'rename_recovers or failed_rename_journal' -q --tb=short` failed both cases (`.cache/pr21-rename-ui-red.txt`); a third regression confirmed startup previously continued after failed recovery. Journal helper RED/GREEN: 21 failing then 21 passing cases (`.cache/pr21-rename-journal-red.txt`, `.cache/pr21-rename-journal-green.txt`). The journal preserves both intended snapshots, rejects malformed state, replays interrupted writes, and aborts a rename if its initial journal cannot be saved.
- Combined GREEN: `.venv/Scripts/python.exe -m pytest tests/test_watchlist_rename.py tests/test_pr21_watchlist_pages.py tests/test_pr21_alert_boundaries.py tests/test_pr21_analysis_refresh.py tests/test_application_shell.py tests/test_gui_service_integration.py -q --tb=short` passed all 80 cases (`.cache/pr21-review-green.txt`). This verifies restart recovery, rejection before initial publication, safe failed initialization, visible capacity/validation errors and unchanged input/selection on failure.
- Mutation review: removing cap guards or in-place replacement breaks store tests; dropping the migration marker reimports deleted symbols; broadening malformed criteria or bypassing disabled rules breaks alert tests; removing UTC conversion breaks watermark/boundary tests; dropping the journal/recovery breaks restart tests; clearing combo signals breaks chart-load preservation; aborting the ticker loop breaks partial-result tests. Independent Brooks Review reread all four reported findings after their fixes and found no unresolved actionable issue.
- `ruff check src tests` and `python -m mypy src` pass (122 source files).
- Full host GREEN: `uv run --extra dev --extra gui pytest -m 'not live' --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr21-coverage.json --cov-fail-under=80` passed 3,307 tests, with 5 skipped and 8 deselected; coverage is 91.33% (`.cache/pr21-offline-host.txt`, `.cache/pr21-coverage.json`). The first sandbox run passed 3,306 tests but its nested installed CLI launcher exited without output; the identical installed-wheel smoke test passed on the host, followed by the complete clean host run. That failed sandbox attempt is not counted as a pass.
- `procoder test` over the 14 affected regression files passed 201 tests (`.cache/pr21-procoder-test.txt`). `procoder check --paths-from .cache/pr21-paths.txt` passed all 21 branch files, with zero unformatted, unchecked or blocking findings (`.cache/pr21-procoder-gate-final.txt`). The first host gate found only README line endings; its formatter output was reviewed and applied before the successful rerun. Host checks use the project virtual environment and an existing Windows-trusted CA bundle, with Semgrep diagnostics in the ignored cache; no security rule was disabled.
- `procoder git` passed with clean hygiene and registered templates (`.cache/pr21-procoder-git.txt`). Live provider calls were not exercised; all runtime tests used isolated disposable profiles. Two pre-existing untracked backup archives were preserved and excluded from commits.
