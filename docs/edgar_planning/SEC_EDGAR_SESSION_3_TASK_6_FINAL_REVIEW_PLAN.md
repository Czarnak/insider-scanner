# SEC EDGAR Session 3 Task 6 and Final Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox syntax for tracking.

**Goal:** Complete the remaining cross-boundary SEC security proofs and perform
the final Session 3 compliance, quality, and security review.

**Architecture:** Extend the existing offline SEC and bulk ZIP integration
suites without adding public APIs or production logging. Exercise the existing
HTTP, extraction, parsing, cache-promotion, and ZIP-preflight boundaries with
malicious fixtures, then review the complete Session 3 diff and refresh its
verification evidence.

**Tech stack:** Python 3.11+, pytest, lxml, stdlib `zipfile`, Ruff, mypy,
pip-audit, pytest-cov, Graphify.

## Locked Decisions

- Extend `tests/test_sec_offline_integration.py` and `tests/test_sec_bulk.py`;
  do not create a separate Session 3 integration suite.
- Keep Task 6 proof-only. Do not introduce new production logging in the bulk,
  XML, or downloader boundaries; record structured logging as future work.
- Do not expand Session 3 into repository-wide mypy remediation. Require all
  Session 3 changed Python files to be clean and prove the full-repository
  diagnostic set does not regress from the verified pre-Task-6 baseline.
- Current pre-Task-6 evidence: 239 SEC-focused tests pass, 2 skip. Full mypy
  reports 191 errors in 50 files; the three SEC-named errors are in the
  pre-existing, untouched `core/secform4.py` module.
- No live SEC requests are permitted. The offline chain is the critical
  end-to-end flow.

## Task 1: Capture the Pre-Change Baseline

**Files:**

- Write only untracked verification output under `build/`.

- [ ] Confirm the worktree and current commit before editing.

```powershell
git status --short
git rev-parse --short HEAD
```

Expected: clean worktree at the Session 3 Tasks 4-5 completion commit.

- [ ] Capture full mypy output for exact no-regression comparison.

```powershell
New-Item -ItemType Directory -Force build | Out-Null
.\.venv\Scripts\python.exe -m mypy src tests 2>&1 |
    Tee-Object build\mypy-session3-before.txt
```

Expected: non-zero exit with 191 errors in 50 files and no errors in the
Session 3 modules changed since `df584e6`.

- [ ] Re-run the current targeted baseline.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_security.py tests\test_sec_client.py tests\test_sec_downloader.py tests\test_sec_ownership_document.py tests\test_sec_ownership_parser.py tests\test_sec_bulk.py tests\test_sec_offline_integration.py -v -p no:cacheprovider
```

Expected: 239 passed, 2 skipped.

## Task 2: Add Static Cross-Boundary Fixtures

**Files:**

- Create: `tests/fixtures/sec_form4_xxe_dtd.xml`
- Create: `tests/fixtures/sec_form4_deep.xml`
- Create: `tests/fixtures/sec_form4_markup_footnote.xml`
- Create: `tests/fixtures/sec_submissions_malformed_metadata.json`

- [ ] Add fixture-loading tests or references before creating the fixtures and
  run the narrow tests to confirm RED through missing fixture files.
- [ ] Add a compact ownership XML fixture containing a DTD/external-entity
  declaration and a unique `TASK6_XXE_SENTINEL` marker. The fixture must be
  rejected before entity expansion.
- [ ] Add a small ownership XML fixture whose element nesting exceeds the
  default depth limit of 64 while remaining below the byte and element limits.
- [ ] Add a valid Form 4 fixture containing nested markup in a footnote and a
  unique `TASK6_FOOTNOTE_SENTINEL`. Its expected parsed value is
  whitespace-normalized plain text with no markup.
- [ ] Add intentionally malformed submissions metadata containing a unique
  `TASK6_METADATA_SENTINEL`. It will be placed in a ZIP at test time so archive
  safety fixtures remain runtime-generated.
- [ ] Keep fixtures deterministic, minimal, non-sensitive, and small enough for
  repository storage.

## Task 3: Complete Offline Fetch/XML/Cache Integration Proofs

**Files:**

- Modify: `tests/test_sec_offline_integration.py`
- Test fixtures: the XML files created in Task 2.

- [ ] Extend `_fetch_parse_promote()` with a keyword-only security policy that
  defaults to `DEFAULT_SEC_SECURITY_POLICY`, and pass the same immutable policy
  to `extract_ownership_document()` and `parse_ownership_document()`.
- [ ] Add a helper that reports validated-cache files without mutating the
  cache. Rejection tests must assert the validated cache is absent or empty.
- [ ] Write and run
  `test_http_redirect_rejection_leaves_no_validated_cache_and_stops_after_one_request`.
  Configure the first response as a redirect to an unapproved host, require a
  typed security failure, exactly one transport call, and no cache artifact.
- [ ] Write and run parameterized extraction-stage tests for the DTD/XXE and
  excessive-depth fixtures. Each must fail before parsing or promotion and
  leave no cache artifact.
- [ ] Write and run
  `test_parser_security_rejection_leaves_no_validated_cache_or_diagnostics`.
  Use the valid markup-footnote fixture with an immutable policy copy whose
  long-text limit is smaller than the footnote. Extraction must succeed,
  parsing must fail, promotion must not run, and the sentinel must be absent
  from exception and captured-log text.
- [ ] Write and run
  `test_markup_footnote_pipeline_flattens_text_without_log_leakage` under the
  default policy. Assert normalized plain text, absence of markup in the parsed
  footnote, and absence of the sentinel from captured logs.
- [ ] Re-run the existing valid fetch, parse, promotion, and cache-reuse tests.
  They remain the acceptance evidence for successful cache reuse and must not
  be duplicated.

Run after each RED-GREEN cycle:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_offline_integration.py -v -p no:cacheprovider
```

Expected GREEN: all offline integration tests pass. Do not modify production
code if the existing security boundaries already satisfy these tests. If a
real defect appears, use systematic debugging, add a focused failing regression
test, and commit the minimal production fix separately.

## Task 4: Prove Whole-Archive ZIP Preflight

**Files:**

- Modify: `tests/test_sec_bulk.py`
- Test fixture: `tests/fixtures/sec_submissions_malformed_metadata.json`

- [ ] Add a test-only archive builder that places one valid recognized member
  before a later unsafe member. Support four runtime cases: traversal name,
  symlink entry, excessive compression ratio, and oversized member.
- [ ] Instrument `zipfile.ZipFile.open` with a test-local counting wrapper that
  delegates to the original method.
- [ ] Add a parameterized
  `test_unsafe_archives_fail_preflight_before_any_member_read`. Inject small
  immutable policy limits, consume the generator, require
  `SecBulkSecurityError`, and assert the open count remains zero for every case.
- [ ] Zip the static malformed-metadata fixture under a recognized CIK member
  name and add
  `test_malformed_metadata_error_does_not_leak_payload_or_logs`. Require
  `SecBulkError` and prove the metadata sentinel is absent from exception and
  captured-log text.
- [ ] Preserve the existing traversal, symlink, count, size, ratio, total-size,
  duplicate-member, and valid-policy tests.

Run after each RED-GREEN cycle:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_bulk.py -v -p no:cacheprovider
```

Expected GREEN: all bulk tests pass and every unsafe archive is rejected before
the first member read.

## Task 5: Review Task 6 and Commit It

- [ ] Run the focused Task 6 tests together.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_bulk.py tests\test_sec_offline_integration.py -v -p no:cacheprovider
```

- [ ] Run Ruff and targeted mypy over the Task 6 files.

```powershell
.\.venv\Scripts\python.exe -m ruff check tests\test_sec_bulk.py tests\test_sec_offline_integration.py
.\.venv\Scripts\python.exe -m mypy tests\test_sec_bulk.py tests\test_sec_offline_integration.py
```

- [ ] Review the Task 6 diff for fixture size, accidental secrets, raw payload
  output, mutable policy updates, and test assertions that could pass
  vacuously.
- [ ] Use code-reviewer and security-reviewer agents. Resolve every critical or
  high finding before continuing.
- [ ] Commit the test-only change.

```powershell
git add tests\fixtures\sec_form4_xxe_dtd.xml tests\fixtures\sec_form4_deep.xml tests\fixtures\sec_form4_markup_footnote.xml tests\fixtures\sec_submissions_malformed_metadata.json tests\test_sec_bulk.py tests\test_sec_offline_integration.py
git commit -m "test: complete SEC Session 3 cross-boundary integration"
```

If production code required a genuine defect fix, commit that fix separately
with a `fix:` conventional commit before this test commit.

## Task 6: Final Session 3 Review

- [ ] Dispatch independent reviewers in parallel:
  - spec compliance against every locked Session 3 security semantic;
  - code quality across `df584e6..HEAD`;
  - security analysis of redirects, cache containment/promotion, XML limits,
    ZIP preflight, diagnostics, and dependency changes.
- [ ] Resolve every critical/high finding and rerun all affected tests.
- [ ] Inspect `git diff df584e6..HEAD` for secrets, payload or footnote logging,
  unsafe paths, remote-controlled cache names, archive extraction APIs, and
  exception messages that expose untrusted data.
- [ ] Run the complete quality gates.

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_security.py tests\test_sec_client.py tests\test_sec_downloader.py tests\test_sec_ownership_document.py tests\test_sec_ownership_parser.py tests\test_sec_bulk.py tests\test_sec_offline_integration.py -v
.\.venv\Scripts\python.exe -m pytest -v --cov=insider_scanner --cov-report=term-missing --cov-fail-under=80 -p no:cacheprovider --basetemp=build\pytest-tmp-session3
.\.venv\Scripts\python.exe -m ruff check src tests
$session3Python = git diff --name-only df584e6..HEAD -- '*.py'
.\.venv\Scripts\python.exe -m mypy $session3Python
.\.venv\Scripts\python.exe -m mypy src tests 2>&1 |
    Tee-Object build\mypy-session3-after.txt
.\.venv\Scripts\python.exe -m pip_audit
git diff --check
```

Acceptance:

- All targeted and full tests pass.
- Repository coverage is at least 80%.
- Ruff and changed-file mypy checks are clean.
- Full mypy adds no diagnostic relative to
  `build/mypy-session3-before.txt`; the inherited count does not exceed 191
  errors in 50 files.
- Dependency audit has no unresolved vulnerability.
- No critical/high review finding remains.

## Task 7: Refresh Graphify and Close Session 3

**Files:**

- Modify: `graphify-out/GRAPH_REPORT.md`
- Modify: `graphify-out/graph.json`
- Modify: `docs/edgar_planning/SEC_EDGAR_SESSION_3_IMPLEMENTATION_PLAN.md`

- [ ] Run Graphify after the Task 6 implementation commit.

```powershell
graphify update . --no-viz
```

- [ ] Verify the graph represents the Task 6 code/test commit. A later
  documentation commit may legitimately be ahead of the source commit recorded
  by Graphify.
- [ ] Update the existing Session 3 implementation plan with:
  - Task 6 test and review evidence;
  - corrected extraction-stage versus true parser-stage rejection coverage;
  - final test, coverage, Ruff, mypy, and dependency-audit results;
  - the accepted mypy no-regression policy;
  - the existing accepted same-user cache-read TOCTOU risk;
  - structured logging for bulk/XML/downloader deferred to future work.
- [ ] Do not create another top-level status document and do not duplicate the
  same conclusions in unrelated project documentation.
- [ ] Review the final diff, rerun `git diff --check`, and commit the evidence.

```powershell
git add graphify-out\GRAPH_REPORT.md graphify-out\graph.json docs\edgar_planning\SEC_EDGAR_SESSION_3_IMPLEMENTATION_PLAN.md
git commit -m "docs: close SEC Session 3 security review"
```

## Completion Criteria

- Every Task 6 bullet has direct integration evidence.
- HTTP, extraction, and true parser rejection create no validated-cache
  artifact.
- Unsafe redirects stop before the second host is contacted.
- ZIP traversal, symlink, ratio, and size failures occur before any member read.
- Exceptions and logs contain no payload or footnote text.
- All quality gates and independent reviews satisfy the acceptance rules above.
- Graphify and the existing Session 3 status are current.
- No public API, schema, or production logging behavior was added unless a
  separately reviewed defect fix proved necessary.
