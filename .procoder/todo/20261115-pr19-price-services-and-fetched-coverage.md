# PR19 price services and fetched coverage

Status: open
Created: 2026-11-15

## Description

Implement PR 19 from docs/review/PR_GROUPS_2026-11-14.md. Reuse application
persistence and price providers, repair fetched-interval coverage and refresh
adjustment bases without mixing providers. Preserve existing user data.

## Acceptance criteria

- [ ] Shared application price services and SQL ticker filtering eliminate per-chart engine/bootstrap and provider creation, proved by lifecycle and analysis tests.
- [ ] Invalid provider rows are isolated, legitimate empty ranges succeed, exchange-local dates and sanitized actionable diagnostics are preserved, proved by provider tests.
- [ ] Successful fetched intervals fill interior holes, remember empty ranges, isolate providers and refresh unfinished bars, proved by cache regressions.
- [ ] Adjustment changes trigger atomic full-history replacement; failed refreshes retain a consistent cache, proved by split/dividend and rollback tests.
- [ ] Offline suite with at least 80% coverage, Procoder check/test and independent review pass; implementation and fix steps have separate commits.

## Implementation plan

1. Add failing provider tests, then isolate invalid bars, preserve valid empty
   responses, identify providers and report safe diagnostic reasons/local dates.
2. Add failing cache tests, then persist provider-scoped successful intervals
   atomically with bars; expire unfinished dates and refresh adjustment bases.
3. Add failing lifecycle/GUI tests, then inject shared services and SQL ticker
   filters, reuse provider sessions and close owned resources.
4. Update existing documentation, review the complete diff independently,
   resolve reproduced defects, run offline and Procoder checks, then push and
   open the requested pull request against master.

## Evidence

- Fresh `git fetch origin master` and divergence check reported 0 ahead / 0 behind; branch created from master at d406ec5.
