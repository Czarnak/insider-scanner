# PR26 simplify window shell and theme coordination

Status: closed 2026-11-16
Created: 2026-11-16

## Description

Implement PR 26 from `docs/review/PR_GROUPS_2026-11-14.md` (GUI-15,
GUI-26, GUI-24, GUI-08 and GUI-23). Move user-state persistence, alert
request ownership and saved-screen menu management out of MainWindow.
Keep saved data and cooperative shutdown intact, select registered page
widgets directly, and coordinate one theme control with Qt palettes,
stylesheets and themed alert/filter rendering.

## Acceptance criteria

- [x] User-state coordination preserves feed/screens/watchlists/alerts, transactional rename recovery, migration and actionable save-failure behavior.
- [x] Alert evaluation lives in a controller with stale-result protection, consistent badges, refresh triggers and cooperative cancellation; page navigation uses registered widgets.
- [x] The saved-screen menu preserves save/apply/delete behavior and bounds submenu lifetime across repeated opening and state updates.
- [x] Saved and switched themes apply matching Fusion palettes and stylesheets; alert colors and filter errors update; redundant theme controls, dead search handling and manual repolishing are removed.
- [x] Focused red/green regressions, independent review, offline tests and required Procoder gate/test checks pass on the completed changes.

## Implementation plan

1. Establish focused regression coverage and extract theme and saved-screen ownership; verify and commit each implementation step.
2. Extract user-state and alert coordination, integrate the window shell and page registry, and update affected integration tests; verify and commit.
3. Independently review the complete changes, fix findings in separate commits, and run offline tests plus Procoder gate/test.
4. Record evidence, close the task with Procoder, push the branch and open a pull request against master.

## Evidence

- Shell RED: `python -m pytest tests/test_pr26_shell_coordination.py -q` failed all three regressions against the original shell: reordered stack selected the wrong page, the duplicate top-bar theme selector remained, and an external theme change left the menu selection stale (`.cache/pr26-shell-red.txt`). Shell/menu GREEN: the same regressions plus saved-screen, application-shell and theme-menu integration tests passed all 33 cases (`.cache/pr26-shell-green.txt`). Saved-screen ownership regression initially retained old child menus; after deferred submenu disposal all four menu tests pass, including repeated opens and state replacement.
- Theme RED/GREEN: five regressions were observed failing before the theme changes and passing afterwards. Root reran `python -m pytest tests/test_pr26_theme_coordination.py tests/test_theme_manager.py tests/test_theme_stylesheet.py tests/test_alerts_page.py tests/test_feed_filters.py -q`: 135 passed (`.cache/pr26-theme-green.txt`). This covers saved DARK startup on a light OS, explicit/OS theme changes, native palette/QSS consistency and existing/new alert/filter colors.
- User-state GREEN: `python -m pytest tests/test_pr26_user_state.py tests/test_main_window_persistence.py tests/test_application_shell.py tests/test_pr21_watchlist_pages.py tests/test_pr23_shutdown.py -q`: 51 passed (`.cache/pr26-state-green.txt`). The eleven new store tests cover profile loading, failed migration, save failures retaining current memory, screen replacement/capping and current alert watermarks across coordinated rename. The store module measured 100% line coverage in its focused run.
- Alert RED/GREEN: removing refresh invalidation and clearing caused eight focused failures (`.cache/pr26-alert-red.txt`); removing shutdown invalidation delivered an obsolete hit (`.cache/pr26-alert-shutdown-red.txt`). Both mutations were restored. Root reran the ten controller tests alongside shell, alert-refresh, shutdown and service integration tests: 84 passed (`.cache/pr26-alert-integration.txt`).
- Independent read-only review found no actionable correctness or lifecycle issues. Procoder review perspectives were applied to the controller boundaries, state compatibility and failure behavior. `mypy src/insider_scanner` passed all 127 source files (`.cache/pr26-mypy.txt`); focused Ruff checks and `git diff --check` passed. Procoder `git` reported clean hygiene (`.cache/pr26-procoder-git.txt`), and `agents --host codex` confirmed generated host rules match the unchanged canonical instructions.
- Final offline suite: `uv run --locked --extra dev --extra gui pytest -m "not live" -q --basetemp=.cache/pr26-offline-temp --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr26-coverage.json --cov-fail-under=80` passed 3,498 tests, with 5 skipped and 8 deselected (`.cache/pr26-offline.txt`). Overall line coverage is 92.05%; 234/236 changed executable production lines are covered (99.15%). `ruff check src tests` and `ruff format --check src tests` passed all 312 files. Live-provider and manual native-desktop acceptance were not run.
- Final Procoder `check --paths-from .cache/pr26-paths.txt` passed all 19 branch files: zero unformatted, unchecked, out-of-scope or blocking findings (`.cache/pr26-procoder-gate-verified.txt`). Its saved JUnit report confirms 3,498 passed, 5 skipped, zero failures/errors. Procoder `test` separately passed all 3,498 cases (`.cache/pr26-procoder-test.txt`). Semgrep scanned the ten selected Python files with zero findings and zero scan errors. Earlier sandboxed gate attempts failed on user-cache writes and the scanner's required local socketpair; final checks use workspace-local logs/settings/version cache/temp, the existing trusted CA bundle and approved execution outside the restricted sandbox. No scan or gate policy was relaxed.
- Procoder `todo close` succeeded in an isolated local checkout of the verified revision with the completed evidence and existing generated host rules (`.cache/pr26-procoder-close.txt`). The closer updated the task status; its result was copied back to this workspace. This preserves the two pre-existing untracked backup archives outside the closer's default scope. An initial isolated attempt correctly refused missing ignored host rules; a second refused a checkout under a cache path, so the successful checkout used a normal workspace subdirectory.
