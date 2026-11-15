# PR19 price services and fetched coverage

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 19 from docs/review/PR_GROUPS_2026-11-14.md. Reuse application
persistence and price providers, repair fetched-interval coverage and refresh
adjustment bases without mixing providers. Preserve existing user data.

## Acceptance criteria

- [x] Shared application price services and SQL ticker filtering eliminate per-chart engine/bootstrap and provider creation, proved by lifecycle and analysis tests.
- [x] Invalid provider rows are isolated, legitimate empty ranges succeed, exchange-local dates and sanitized actionable diagnostics are preserved, proved by provider tests.
- [x] Successful fetched intervals fill interior holes, remember empty ranges, isolate providers and refresh unfinished bars, proved by cache regressions.
- [x] Adjustment changes trigger atomic full-history replacement; failed refreshes retain a consistent cache, proved by split/dividend and rollback tests.
- [x] Offline suite with at least 80% coverage, Procoder check/test and independent review pass; implementation and fix steps have separate commits.

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
- Lifecycle RED (`.cache/pr19-lifecycle-red.txt`) reproduced missing application price services and an unexpected per-chart database bootstrap. GREEN (`.cache/pr19-lifecycle-cache-green.txt`): 69 lifecycle/cache tests passed, including exact application-service injection, provider reuse and cleanup, shared engine forwarding, SQL ticker filtering before Congress row hydration, and existing stale-result/cancellation checks.
- Provider GREEN (`.cache/pr19-provider-final.txt`): 201 tests passed for isolated invalid rows, explicit incomplete responses, valid empty intervals, stable provider identity, sanitized reason/status diagnostics, and exchange-local Yahoo dates. RED (`.cache/pr19-padding-red.txt`) reproduced an invalid adjacent padding bar incorrectly marking requested coverage incomplete; the requested-range completeness regression then passed.
- Cache RED (`.cache/pr19-cache-red.txt`) reproduced interior holes hidden by cached min/max, missing empty-range coverage, provider mixing, unfinished-bar freshness and stale adjustment bases. GREEN: the 69-test lifecycle/cache run passed; successful fetched intervals are provider-scoped, malformed partial results cannot claim coverage, and current-day bars expire for rechecking.
- Adjustment RED (`.cache/pr19-adjustment-red.txt`) reproduced unnecessary full-history refresh on intraday movement and truncated replacement losing the old cache. Missing-overlap RED (`.cache/pr19-overlap-red.txt`) reproduced merging prices without a verified adjustment basis. GREEN (`.cache/pr19-atomic-green.txt`): 25 cache/repository tests passed, including full-history replacement and rollback when coverage persistence fails.
- Manual mutation review: dropping dependency injection or engine forwarding breaks lifecycle tests; removing SQL ticker filtering breaks hydration-count assertions; trusting min/max or partial/provider-mismatched coverage breaks gap/cache tests; accepting an unverified overlap, dropping historical replacement bars or committing bars before coverage breaks adjustment/rollback regressions; UTC-only date conversion or treating malformed padding as requested data breaks Yahoo boundary tests.
- Full offline verification: `uv run --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr19-coverage.json --cov-fail-under=80` — 3,192 passed, 5 skipped, 8 live tests deselected; coverage 91.10%.
- `ruff check src tests`, `ruff format --check src tests` (278 files) and `mypy src/insider_scanner` (120 source files) passed. Independent review of commit 54c8909 reported no actionable findings; implementation and fixes have separate conventional commits.
- Final full-branch Procoder gate: 23 clean, 0 unformatted, 0 unchecked, 0 out of scope, 0 blocking findings. Initial README formatting was normalized with Prettier without a Git content diff. Explicit `procoder test` over provider, cache, lifecycle, analysis, shell and CLI regression files passed 277 tests.
- Changed price modules have 93.1% to 100% coverage (repository 98.4%, orchestrator 93.2%, Yahoo 96.0%). `procoder git` passed with the existing release-workflow concurrency advisory. Live price providers were not exercised; fixtures verify boundary and cache behavior offline.
