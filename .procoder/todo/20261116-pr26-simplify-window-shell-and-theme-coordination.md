# PR26 simplify window shell and theme coordination

Status: open
Created: 2026-11-16

## Description

Implement PR 26 from `docs/review/PR_GROUPS_2026-11-14.md` (GUI-15,
GUI-26, GUI-24, GUI-08 and GUI-23). Move user-state persistence, alert
request ownership and saved-screen menu management out of MainWindow.
Keep saved data and cooperative shutdown intact, select registered page
widgets directly, and coordinate one theme control with Qt palettes,
stylesheets and themed alert/filter rendering.

## Acceptance criteria

- [ ] User-state coordination preserves feed/screens/watchlists/alerts, transactional rename recovery, migration and actionable save-failure behavior.
- [ ] Alert evaluation lives in a controller with stale-result protection, consistent badges, refresh triggers and cooperative cancellation; page navigation uses registered widgets.
- [ ] The saved-screen menu preserves save/apply/delete behavior and bounds submenu lifetime across repeated opening and state updates.
- [ ] Saved and switched themes apply matching Fusion palettes and stylesheets; alert colors and filter errors update; redundant theme controls, dead search handling and manual repolishing are removed.
- [ ] Focused red/green regressions, independent review, offline tests and required Procoder gate/test checks pass on the completed changes.

## Implementation plan

1. Establish focused regression coverage and extract theme and saved-screen ownership; verify and commit each implementation step.
2. Extract user-state and alert coordination, integrate the window shell and page registry, and update affected integration tests; verify and commit.
3. Independently review the complete changes, fix findings in separate commits, and run offline tests plus Procoder gate/test.
4. Record evidence, close the task with Procoder, push the branch and open a pull request against master.

## Evidence

Pending implementation and verification.
