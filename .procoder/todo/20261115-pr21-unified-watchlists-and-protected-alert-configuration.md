# PR21 unified watchlists and protected alert configuration

Status: open
Created: 2026-11-15

## Description

Implement PR 21 from docs/review/PR_GROUPS_2026-11-14.md: PERSIST-19,
PERSIST-20, PERSIST-21, PERSIST-M3, ARCH-28, GUI-21 and GUI-M1. Named JSON
watchlists become the shared source for monitoring, scanning and analysis;
legacy symbols migrate once without losing existing data. Alert rules retain
their targets after renames and malformed configuration cannot widen matches.

## Acceptance criteria

- [ ] Watchlist names reject case-insensitive collisions, updates retain order, and list/entry/rule capacity failures are explicit without losing stored items.
- [ ] Legacy US symbols migrate atomically once into JSON alongside existing lists; deleting migrated symbols persists across restarts and migration failures preserve user data.
- [ ] Invalid alert criteria remain visibly disabled across save/load; timestamps normalize to UTC; watchlist renames preserve rule references.
- [ ] Scan and analysis use the shared store and refresh after edits; per-ticker scan failures retain successful rows and visibly identify failures.
- [ ] GUI mutation failures show messages, retain selection/input and avoid reporting unsuccessful additions as saved.
- [ ] Focused red/green regressions, offline suite, Procoder test/gate and independent review verify the combined change.

## Implementation plan

1. Reproduce watchlist collision/cap and migration failures; implement store operations and migration; run focused tests and commit.
2. Reproduce alert cap/order, criteria and timestamp failures; implement validated immutable alert updates; run focused tests and commit.
3. Reproduce per-ticker scan failure; inject shared watchlists into scan/analysis and retain partial results; run focused tests and commit.
4. Reproduce GUI rename/error and startup migration issues; wire pages, migration and alert references; run focused tests and commit.
5. Review the combined diff, fix any findings in separate commits, run offline tests and Procoder checks, record evidence and close through Procoder; push and open the requested PR.

## Evidence

Pending verification.
