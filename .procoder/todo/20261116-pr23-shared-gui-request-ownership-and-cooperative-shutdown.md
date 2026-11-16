# PR23 shared GUI request ownership and cooperative shutdown

Status: open
Created: 2026-11-16

## Description

Implement PR 23 from the review grouping: share GUI worker ownership and stale
callback handling, resolve CIKs in the background, drain workers without blocking
window close, and remove the focused GUI/CLI dependency leaks. Preserve current
scan semantics and persistence lifetimes while establishing the request contract
used by subsequent GUI work.

## Acceptance criteria

- [ ] One owner-bound latest-request helper dispatches page, search, drawer, analysis, alert and scan work; stale result/error/progress/finish callbacks and callbacks after owner destruction cannot mutate widgets.
- [ ] CIK lookup uses a service and background worker; controls recover on success/error and ticker changes or shutdown suppress stale lookup side effects. Watchlist progress uses the shared callback contract.
- [ ] Window close requests cancellation once, keeps the GUI event loop responsive, reports delayed shutdown and automatically closes after workers drain; services remain open until then.
- [ ] GUI backfill information does not import the CLI and the shell consumes the application-owned feed repository rather than building one from its engine.
- [ ] Focused red/green tests, the full offline suite, coverage, independent review and Procoder test/gate verify the final implementation.

## Implementation plan

1. Reproduce request lifetime/order failures, implement the shared helper, verify and commit.
2. Migrate GUI requests and background CIK/service boundaries, verify and commit.
3. Reproduce blocking/repeated close behavior, implement asynchronous cooperative shutdown, verify and commit.
4. Review the combined change, commit fixes separately, run required checks and record evidence, close this task through Procoder, then push and open the requested PR.

## Evidence
Verification is in progress; completed command results will be recorded here.
