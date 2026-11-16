# PR25 consistent search and investigation views

Status: open
Created: 2026-11-16

## Description

Implement PR 25 from `docs/review/PR_GROUPS_2026-11-14.md`: GUI-05,
GUI-09, GUI-14, GUI-M2, GUI-10, GUI-M3 and GUI-25. Search must keep
typing focus and activate only current visible results. Entity pages must
identify the requested entity throughout loading and errors, and label
bounded summaries accurately. Feed drawers must follow record identity,
debounce detail work, reuse widgets and keep record/bundle state together.
All asynchronous updates use the request ownership established by PR 23.

## Acceptance criteria

- [ ] Search supports continued typing and keyboard selection, dismisses cleanly, and cannot activate hidden or superseded results.
- [ ] Entity loads clear previous data immediately, retain the requested header on errors/unavailability, label summary counts as the displayed latest transactions, and ignore stale completion callbacks.
- [ ] Feed resets discard obsolete drawer state; rapid selection dispatches only the latest detail load, and loading/error/theme changes never mix records and bundles.
- [ ] Drawer widgets are reused, pending work is invalidated on cancellation, and removing filter chips defers QObject destruction safely.
- [ ] Focused red/green regressions, independent review, full offline tests, typing/lint, Procoder test and the Procoder gate pass.

## Implementation plan

1. Drive search focus, navigation and request lifecycle fixes with regressions; verify and commit.
2. Drive entity loading, bounded summary and stale completion regressions; verify and commit.
3. Drive feed/drawer identity, debouncing, reusable rendering and chip disposal regressions; verify and commit.
4. Independently review the integrated diff, commit any fixes, run required validation, record evidence and close this task through Procoder. Push the branch and open a pull request against master.

## Evidence

- Entity RED: `uv run --locked --extra dev --extra gui pytest tests/test_pr25_entity_lifecycle.py -q --tb=short` reproduced all eight intended loading/header, capped-summary and cancellation failures (`.cache/pr25-entity-red.txt`). GREEN: the new regressions plus `tests/test_entity_pages.py` and `tests/test_gui_trade_vocabulary.py` passed all 61 cases (`.cache/pr25-entity-green.txt`). Existing superseded-result tests also verify stale completion cannot hide a newer progress bar. Summary counts explicitly describe the latest displayed transactions; the existing bounded repository queries are preserved.
- An early Procoder gate was stopped after discovering it runs pytest internally against the partially integrated tree. The final gate will run after all implementation steps are ready, with test processes serialized.
- Search RED reproduced five navigation/activation/cancellation failures, then two outside-click/window-move failures and two hidden-activation/direct-replacement failures (`.cache/pr25-search-red.txt`, `.cache/pr25-search-dismiss-red.txt`, `.cache/pr25-search-activation-red.txt`). GREEN: `uv run --locked --extra dev --extra gui pytest tests/test_pr25_global_search.py tests/test_global_search.py -q --tb=short` passed all 42 cases (`.cache/pr25-search-green.txt`). Real Qt mouse clicks verify a single activation and retained line-edit focus. The offscreen platform cannot reproduce native keyboard grabs; the nonactivating window flags remove that grab, with typing and keyboard navigation covered in Qt tests.
- Independent read-only review of the entity implementation found no actionable issues; latest-request QObject relays guard results, errors and finished callbacks. Fresh whole-source mypy passed all 124 source files (`.cache/pr25-mypy.txt`).
