# PR24 shared Scan and Congress table workflows

Status: closed 2026-11-16
Created: 2026-11-16

## Description

Implement PR 24 from the review grouping. Share the Scan and Congress result
workflow so displayed rows, selected trades and exported data agree under sorting
and filtering. Keep raw values and metadata available to the table while caching
display work, and move Congress filtering, serialization and member loading into
services. Preserve the request ownership established by PR 23.

## Acceptance criteria

- [x] Numeric columns sort numerically, Congress amount ranges sort by their lower bound, and cell rendering uses cached display and style values with raw metadata retained.
- [x] Both tabs retain active filters on new results; detail, filing actions and background exports use the displayed trades, including empty filtered sets.
- [x] Both tabs share date, results, cancellation and export plumbing; Congress helpers live in services and member metadata is parsed once with logged failures.
- [x] Table headers use bounded initial sizing and interactive resizing; Congress-flagged Scan rows receive the expected emphasis.
- [x] Meaningful sort and empty-input tests, focused red/green regressions, independent review, full offline tests and Procoder test/gate pass.

## Implementation plan

1. Add failing table-model regressions, cache raw/display/style data and implement sorting and row identity; verify and commit.
2. Add failing helper/workflow regressions, extract services and common scan widgets, integrate both tabs and background exports; verify and commit.
3. Independently review and commit any fixes, run required checks, record evidence and close the task through Procoder, then push and open the PR.

## Evidence

- Model RED: seven intended numeric-sort/cache/metadata failures (`.cache/pr24-model-red.txt`), plus three missing-flag failures (`.cache/pr24-model-missing-red.txt`). GREEN: 71 model/theme/compatibility cases passed (`.cache/pr24-model-missing-green.txt`). Root independently ran all 10 model regressions in the combined 83-test pass (`.cache/pr24-focused-green.txt`). Numeric raw values, missing flags, amount bounds, row identity, cached styling and palette changes are covered. Existing GUI sort tests now assert actual order, and empty ticker tests assert a warning and no dispatch.
- Workflow RED: all 11 regressions reproduced active-filter, identity, export, sizing and highlighting failures (`.cache/pr24-workflows-red.txt`). Service RED reproduced silent corrupt seeds and ignored empty sector metadata (`.cache/pr24-services-red.txt`). GREEN: root ran `uv run --locked --extra dev --extra gui pytest tests/test_pr24_table_model.py tests/test_pr24_table_workflows.py tests/test_pr24_table_services.py tests/test_congress_tab.py tests/test_gui.py -q --tb=short`, passing 83 cases (`.cache/pr24-focused-green.txt`). The shared workflow clears obsolete details, disables empty exports, queues a snapshot and sizes one bounded sample per schema. Member names/sectors share one recoverable parse; raw CSV/JSON values remain separate from display strings.
- Export label RED: editable ticker/member paths reached export filenames (two failures in `.cache/pr24-export-label-red.txt`), and a direct service call wrote outside its output directory (`.cache/pr24-service-label-red.txt`). Labels are now restricted to single filenames at both boundaries. GREEN: the combined focused run passed 92 cases (`.cache/pr24-export-green.txt`), also covering duplicate-write suppression, independent snapshots, error recovery, shutdown callback suppression and retained user column widths. README documents the shared result/export behavior.
- Independent review identified lost generated EDGAR links and an old missing-number test that confused raw and visible column positions. Both failed before correction (`.cache/pr24-review-red.txt`); generated links now enrich only the displayed/export snapshot, and the test locates visible headers. The combined 113-case run passed, including PR 23 scan/cancellation compatibility (`.cache/pr24-review-green.txt`).
- Static validation initially found 15 missing base-class contract/date-return annotations. Explicit subclass hooks/widget declarations and typed date construction resolved them: `mypy src/insider_scanner` passed all 124 source files and the 47 GUI/workflow cases passed (`.cache/pr24-types-green.txt`).
- Initial full offline run passed 3,430 cases with 91.90% coverage and exposed one stale logger monkeypatch (`.cache/pr24-offline-initial.txt`). The integration test now verifies the actual diagnostic error record and preserved concise user message across the shared boundary; all 21 service-integration tests passed (`.cache/pr24-integration-green.txt`).
- Final offline GREEN: `uv run --locked --extra dev --extra gui pytest -m "not live" -q --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr24-coverage.json --cov-fail-under=80` passed 3,431 tests, with 5 skipped and 8 deselected (`.cache/pr24-offline.txt`). Coverage is 91.90% overall and 396/405 executable changed production lines (97.78%). Live service tests were not run.
- Final `ruff check .`, `ruff format --check .` (332 files), `mypy src/insider_scanner` (124 files) and `git diff --check master` passed. Procoder `test tests/test_pr24_table_model.py tests/test_pr24_table_workflows.py tests/test_pr24_table_services.py` passed all 43 cases (`.cache/pr24-procoder-test.txt`). `procoder check --paths-from .cache/pr24-paths.txt` passed all 13 branch files, with zero unformatted, unchecked, out-of-scope or blocking findings (`.cache/pr24-procoder-gate.txt`). Informational missing-docstring observations require no behavior change. The gate used the project environment and existing Windows-trusted CA bundle.
- Fresh independent review of the full branch, filing-link correction, typing declarations and diagnostic-test update found no remaining actionable issues. Mutation review: dropping raw sort roles breaks numeric/range order; dropping row identity or filtered snapshots breaks filing/export selection; ignoring active filters or empty results breaks completion/export tests; restoring per-cell pandas/style work breaks the cache sentinel; dropping metadata breaks Congress emphasis; synchronous saving, mutable snapshots, duplicate writes and stale callbacks break the export lifecycle regressions.
- Procoder task closure passed in an isolated checkout of the verified revision with the existing generated host rules (`.cache/pr24-procoder-close.txt`). This keeps the two unrelated untracked backup archives outside the closer's default scope. Semgrep initially failed under the execution sandbox; the authorized retry outside the sandbox passed with all checks enabled and its log/settings paths kept in the project cache.
