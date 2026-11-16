# PR25 consistent search and investigation views

Status: closed 2026-11-16
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

- [x] Search supports continued typing and keyboard selection, dismisses cleanly, and cannot activate hidden or superseded results.
- [x] Entity loads clear previous data immediately, retain the requested header on errors/unavailability, label summary counts as the displayed latest transactions, and ignore stale completion callbacks.
- [x] Feed resets discard obsolete drawer state; rapid selection dispatches only the latest detail load, and loading/error/theme changes never mix records and bundles.
- [x] Drawer widgets are reused, pending work is invalidated on cancellation, and removing filter chips defers QObject destruction safely.
- [x] Focused red/green regressions, independent review, full offline tests, typing/lint, Procoder test and the Procoder gate pass.

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
- Drawer RED reproduced ten debounce, identity, stale-bundle/theme, widget-reuse, current-action and deferred-chip-deletion failures (`.cache/pr25-drawer-red.txt`). Review added a parent hide/show regression: cancellation must resume unfinished details when the drawer becomes visible again; it failed before the show-resume fix (`.cache/pr25-drawer-resume-red.txt`). GREEN: the eleven new cases plus existing drawer/feed/filter/vocabulary coverage passed 101 tests (`.cache/pr25-drawer-green.txt`). The stable widget tree keeps summary/actions on the selected record, and theme changes update one drawer stylesheet without rerendering stale details.
- Search teardown RED: deleting the anchor while a broader controller parent survived made an unrelated window event raise a deleted-QLineEdit RuntimeError (`.cache/pr25-search-destruction-red.txt`). Parenting the controller to the anchor ties the application filter, timer and request relays to the widgets they use. GREEN passed 43 search tests (`.cache/pr25-search-destruction-green.txt`). Root independently ran Procoder `test` over all nine relevant test files: 189 passed (`.cache/pr25-procoder-test.txt`), including all 34 new lifecycle regressions and existing request/rendering behavior.
- Final independent review of the complete branch found no unresolved actionable findings after the teardown fix. Mutation review: removing visibility checks or request invalidation breaks search stale-action tests; restoring external controller ownership breaks DeferredDelete; removing entity clearing or bounded labels breaks entity regressions; dropping timer dispatch, record/bundle reset, stable widgets, model-reset handling or deferred chip disposal breaks their focused cases. Final Ruff lint/format passed all 336 Python files; mypy passed 124 source files; `git diff --check master` and Procoder `git` hygiene passed.
- Initial full offline validation passed 3,464 cases with 91.93% coverage but the installed-wheel console launcher exited without diagnostics from the sandbox temporary directory (`.cache/pr25-offline.txt`). Re-running that exact packaging test with `--basetemp=.cache/pr25-packaging-temp` passed (`.cache/pr25-packaging-retry.txt`), without a source change. Final full-suite verification uses a workspace-local temporary directory for generated executables.
- Final offline GREEN: `uv run --locked --extra dev --extra gui pytest -m "not live" -q --basetemp=.cache/pr25-offline-temp --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr25-coverage.json --cov-fail-under=80` passed 3,465 tests, with 5 skipped and 8 deselected (`.cache/pr25-offline-final.txt`). Coverage is 91.93% overall and 239/240 changed executable production lines (99.58%). Live-provider and manual native-desktop acceptance were not run.
- Final Procoder `check --paths-from .cache/pr25-paths.txt` passed all 13 branch files: zero unformatted, unchecked, out-of-scope or blocking findings (`.cache/pr25-procoder-gate-verified.txt`). Its saved JUnit report independently confirms 3,465 passed, 5 skipped, zero failures/errors (`.cache/pr25-gate-results.xml`). The gate uses the project environment, existing trusted CA bundle and process-local workspace TEMP/TMP directories. An earlier gate needed README CRLF-to-LF normalization; another reported one embedded test failure without useful diagnostics. The isolated cached case passed on retry, and the final saved full-suite report is green. Informational test docstrings and existing complexity observations require no behavior change.
- Procoder `todo close` passed in an isolated checkout of the verified revision with the completed evidence and existing generated host rules (`.cache/pr25-procoder-close.txt`). The closer updated the status; its output was copied back and verified before removing the temporary checkout. This keeps the two pre-existing untracked backup archives outside the closer's default scope.
