# PR23 shared GUI request ownership and cooperative shutdown

Status: closed 2026-11-16
Created: 2026-11-16

## Description

Implement PR 23 from the review grouping: share GUI worker ownership and stale
callback handling, resolve CIKs in the background, drain workers without blocking
window close, and remove the focused GUI/CLI dependency leaks. Preserve current
scan semantics and persistence lifetimes while establishing the request contract
used by subsequent GUI work.

## Acceptance criteria

- [x] One owner-bound latest-request helper dispatches page, search, drawer, analysis, alert and scan work; stale result/error/progress/finish callbacks and callbacks after owner destruction cannot mutate widgets.
- [x] CIK lookup uses a service and background worker; controls recover on success/error and ticker changes or shutdown suppress stale lookup side effects. Watchlist progress uses the shared callback contract.
- [x] Window close requests cancellation once, keeps the GUI event loop responsive, reports delayed shutdown and automatically closes after workers drain; services remain open until then.
- [x] GUI backfill information does not import the CLI and the shell consumes the application-owned feed repository rather than building one from its engine.
- [x] Focused red/green tests, the full offline suite, coverage, independent review and Procoder test/gate verify the final implementation.

## Implementation plan

1. Reproduce request lifetime/order failures, implement the shared helper, verify and commit.
2. Migrate GUI requests and background CIK/service boundaries, verify and commit.
3. Reproduce blocking/repeated close behavior, implement asynchronous cooperative shutdown, verify and commit.
4. Review the combined change, commit fixes separately, run required checks and record evidence, close this task through Procoder, then push and open the requested PR.

## Evidence

- Helper RED: `pytest tests/test_pr23_requests.py -q --tb=short` recorded four failures and three setup errors before `LatestRequest` existed (`.cache/pr23-helper-red.txt`). GREEN: `pytest tests/test_threading.py tests/test_pr23_requests.py -q --tb=short` passed all 13 cases (`.cache/pr23-helper-green.txt`). Real Qt pools verify GUI-thread delivery, owner destruction and out-of-order completion; controlled pools verify stale result/error/progress/finish suppression and retention. The existing alert-worker weak-reference regression also passed in the combined GUI run.
- CIK and boundary RED: `pytest tests/test_pr23_scan_requests.py tests/test_pr23_boundaries.py -q --tb=short` recorded 13 failures and one pass (`.cache/pr23-scan-red.txt`). All 14 cases passed in the combined GUI run (`.cache/pr23-focused-green.txt`): lookup queues service work, restores controls on success/miss/error, drops obsolete ticker/shutdown results before opening a browser, preserves per-scan cancellation, and uses application-owned feed access and shared SEC instructions. The initial combined run passed 255 tests; its single old synchronous-close expectation was updated and passed separately (`.cache/pr23-close-state-green.txt`).
- Other requests RED: five failures reproduced reused cancellation events, late teardown callbacks and manual watchlist progress (`.cache/pr23-other-red.txt`). Independent review found Stop incorrectly invalidated Congress/European callbacks; two additional regressions reproduced discarded partial results (`.cache/pr23-stop-red.txt`). After separating Stop from teardown, `pytest tests/test_pr23_other_requests.py -q --tb=short` passed all seven cases, including partial results, restored controls and restart (`.cache/pr23-other-green.txt`).
- Shutdown RED: `pytest tests/test_pr23_shutdown.py -q --tb=short` recorded two failures and one pass for GUI-thread waiting and absent delayed-shutdown status (`.cache/pr23-shutdown-red.txt`); all three passed in the focused GUI run. The first full suite exposed a partial-initialization cleanup error and two legacy chart-test assumptions (`.cache/pr23-offline-initial.txt`); an explicit partial-window close regression failed before the guard (`.cache/pr23-partial-window-red.txt`). After the guard and behavioral chart-test updates, `pytest tests/test_pr23_shutdown.py tests/test_pr21_analysis_refresh.py tests/test_pr21_watchlist_pages.py -q --tb=short` passed all 17 cases (`.cache/pr23-partial-window-green.txt`).
- Independent Brooks and Procoder-perspective review found the Stop regression described above, then reread both its correction and the partial-window fix with no remaining actionable findings. Mutation review: dropping generation checks permits stale callbacks, dropping relay ownership touches deleted widgets, dropping retention loses queued alerts, reusing cancellation events restarts old work, invalidating Stop discards partial rows, waiting in close blocks the heartbeat test, and dropping the central-widget guard breaks partial-initialization cleanup; focused regressions cover these changes.
- Full offline GREEN: `uv run --locked --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr23-coverage.json --cov-fail-under=80` passed 3,392 tests, with 5 skipped and 8 deselected (`.cache/pr23-offline.txt`). Coverage is 91.58% overall; comparison of changed production lines with the coverage JSON records 227/227 executable changed lines covered. The earlier full-run failures are retained separately and are superseded by this clean run. Live SEC/provider calls were not exercised; an active network request still finishes under the existing timeout/retry behavior before shutdown completes.
- `ruff check .`, `ruff format --check .` (326 files), `mypy src/insider_scanner` (122 source files) and `git diff --check master` passed after the final production fix. `procoder test tests/test_pr23_requests.py tests/test_pr23_scan_requests.py tests/test_pr23_other_requests.py tests/test_pr23_shutdown.py tests/test_pr23_boundaries.py` passed all 32 cases (`.cache/pr23-procoder-test.txt`). The two pre-existing local backup archives remain excluded from commits.
- Final `procoder check --paths-from .cache/pr23-paths.txt` passed all 31 branch files: zero unformatted, unchecked, out-of-scope or blocking findings (`.cache/pr23-procoder-gate-final.txt`). Its repository tests and security checks ran with the project environment and existing Windows-trusted CA bundle. The initial gate reported only README CRLF line endings; applying the formatter's identical text with canonical LF resolved it. Informational docstring and complexity observations are outside this request's behavior changes. The Procoder Git report confirms the intended branch, registered templates and clean hygiene.
