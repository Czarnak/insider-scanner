# SEC EDGAR Session 3 Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` task-by-task. Every production change follows
> RED-GREEN-REFACTOR.

## Goal

Harden every untrusted SEC ingestion boundary created in Sessions 1 and 2
before downloads are scaled or SEC-native records are written to the database.

Session 3 changes HTTP validation, cache promotion, ownership XML parsing, bulk
ZIP parsing, and safe diagnostics. It does not add database persistence, GUI
behavior, retry loops, background jobs, source-default changes, or live-network
tests.

## Locked Security Semantics

- Only HTTPS URLs on exact hosts `www.sec.gov` and `data.sec.gov` are allowed.
- Automatic redirects are disabled. At most three redirects are followed, and
  every target is validated before the next request is made.
- Connect and read timeouts are 15 seconds each. Responses are streamed in
  64 KiB chunks.
- Filing responses are limited to 32 MiB. Declared and actual sizes are both
  enforced, and missing or unexpected content types fail closed.
- XML limits are 8 MiB, 100,000 elements, depth 64, 2 MiB total text,
  4,096-character scalar fields, 256 KiB remarks/footnotes, and 128-character
  numeric lexemes.
- ZIP limits are 512-character member names, 2,000,000 entries, 64 MiB per
  member, 16 GiB total declared output, and a 200:1 compression ratio.
- DTD/entity declarations, external XML resolution, unsafe archive names,
  symlinks, path traversal, absolute/drive paths, malformed metadata, and
  oversized values are rejected.
- Rejected payloads are never stored or logged. Network bytes remain in memory
  until extraction and parsing succeed.
- Validated raw filings are promoted atomically under
  `<edgar-cache>/validated-v1` and are fresh for 24 hours. A stale entry is
  removed when accessed; a global cache sweep remains Session 4 work.
- SEC temporary files live only inside the configured EDGAR application cache.
- Footnote markup is flattened to normalized plain text. Raw markup is neither
  persisted nor rendered.
- Logs contain stage, stable reason code, validated accession/CIK, and safe
  URL/path labels only. Raw payloads and third-party exception text are absent.

## Task 1: Security Policy and Threat Model

Create `src/insider_scanner/core/sec_security.py` with frozen, slotted policy,
resource-profile, and reason-code types. Secure defaults are immutable and may
be replaced only through explicit dependency injection in tests. Invalid policy
construction fails before any I/O.

Write policy tests first in `tests/test_sec_security.py`, run them to verify the
expected failure, implement the minimum policy, then rerun tests, Ruff, and mypy.

## Task 2: HTTP Boundary

Update `sec_client.py` and its tests so transports return streamed responses
with headers, chunk iteration, and explicit close behavior. Disable automatic
redirects, validate every redirect target, normalize media types, enforce
resource-profile limits, and preserve typed, payload-free failures.

Migrate current SEC client callers and test doubles to the new protocol in the
same task so the repository remains green.

## Task 3: Validated Cache Promotion

Replace direct-to-cache downloading with two explicit operations:

- `fetch_filing(...) -> PendingSecFiling`
- `promote_validated_filing(...) -> DownloadedSecFiling`

Unvalidated bytes stay in memory. Only callers that have completed ownership
document extraction and transaction parsing may promote bytes into the
versioned cache. Validate root containment and reject symlinks and unsafe local
paths. Temporary files use randomized names in the destination directory and
are removed on every failure path.

## Task 4: Ownership XML and Field Limits

Pass the security policy into `extract_ownership_document()` and
`parse_ownership_document()`. Reject declarations that can load or expand
entities, retain hardened lxml flags, and enforce byte, element, depth, total
text, scalar, long-text, and numeric limits before expensive conversion.

Flatten footnote markup with normalized whitespace and keep all errors and logs
accession-scoped without embedding XML or field values.

## Task 5: Bulk ZIP Boundary

Require an injected trusted cache root for `iter_ownership_filings()`. Preflight
every member before reading any content. Reject unsafe names, directory/symlink
entries, excessive counts, sizes, totals, or compression ratios. Stream bounded
JSON members and never call ZIP extraction APIs.

## Task 6: Cross-Boundary Integration

Extend the offline SEC integration tests to prove:

- a valid filing is fetched, parsed, promoted, and reused from cache;
- HTTP, extraction, or parsing rejection leaves no cache artifact;
- an unsafe redirect is rejected before the second host is contacted;
- unsafe bulk archives fail preflight before any member is read; and
- errors and logs never contain payload or footnote text.

Static malicious fixtures cover XXE/DTD, deep XML, markup footnotes, and
malformed metadata. ZIP traversal, symlink, ratio, and size fixtures are built
at test time with small injected limits.

## Quality Gates

Run targeted tests after every RED-GREEN cycle. Before completion run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_security.py tests\test_sec_client.py tests\test_sec_downloader.py tests\test_sec_ownership_document.py tests\test_sec_ownership_parser.py tests\test_sec_bulk.py tests\test_sec_offline_integration.py -v
.\.venv\Scripts\python.exe -m pytest -v --cov=insider_scanner --cov-report=term-missing --cov-fail-under=80 -p no:cacheprovider --basetemp=build\pytest-tmp-session3
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m pip_audit
graphify update . --no-viz
```

Perform spec-compliance, code-quality, and security-diff reviews. Resolve every
critical or high-severity issue, inspect `git diff` for secrets and raw payloads,
and use conventional commits.

## Baseline and Acceptance

The implementation baseline is commit `df584e6`; 206 SEC-focused tests pass
before Session 3 changes. Completion requires all targeted and full tests to
pass, repository coverage of at least 80%, clean Ruff and mypy results, a clean
dependency audit, refreshed Graphify output, and no unresolved security-review
findings.

## Implementation Status — 2026-06-20

Work intentionally stopped after Task 3 at the user's requested checkpoint.
Tasks 4-6 remain planned and must start with fresh failing tests in a later
session.

Completed:

- Threat model and this implementation plan persisted in commit `eb84a78`.
- Task 1 security policy and malicious-limit test foundation completed in
  commit `2ef509d` (`62` policy tests passed; Ruff and mypy clean).
- Task 2 HTTP boundary completed in commit `a5c46f1`. The client now validates
  exact hosts before transport, manually validates bounded redirects, streams
  and closes responses, enforces profile media types and declared/actual byte
  limits, and emits sanitized typed failures.
- Task 3 completed in commit `a142885`, replacing direct-to-cache writes with
  `fetch_filing()` and `promote_validated_filing()`. Pending objects carry their
  originating byte limit, cache filenames are locally generated SHA-256 names
  with a fixed `.filing` suffix, parser rejection leaves no cache directory,
  and atomic I/O errors are sanitized.

Review corrections incorporated:

- Rejected redirect targets no longer appear in public diagnostics.
- Response-close warnings include stable stage and reason fields.
- Downloader tests use a filing-only media type, proving the explicit resource
  profile is selected.
- Cache promotion preserves the originating policy bound, revalidates forged
  rows/paths, removes remote-controlled suffixes, and wraps filesystem errors.
- Offline integration now executes the real fetch → extract → parse → promote
  order and explicitly proves parser rejection cannot create validated cache.

Current Task 3 verification:

```text
tests/test_sec_downloader.py + tests/test_sec_offline_integration.py:
35 passed, 1 skipped
Ruff: clean
mypy: clean
```

Checkpoint-wide verification after Graphify refresh and dependency remediation:

```text
Full pytest: 1757 passed, 2 skipped
Coverage: 87.34% (required: 80%)
Ruff: clean
Targeted mypy for all Session 3 files: clean
pip-audit: clean
```

The initial audit found `msgpack 1.2.0` through the development-only
`pip-audit[filecache] -> cachecontrol` chain. `uv.lock` and the environment were
updated to `msgpack 1.2.1`, then the audit and full verification were rerun.
The lock refresh also reconciled existing `pyproject.toml` constraints,
including `cryptography 48.0.1` and previously missing development typing-tool
entries.
Repository-wide mypy still reports 192 pre-existing errors across 51 files;
none are in the Session 3 modules or tests, whose targeted mypy gate passes.

Remaining work:

1. Task 4: XML declaration/tree/text/numeric limits and plain-text footnotes.
2. Task 5: full ZIP central-directory preflight and bounded JSON member reads.
3. Task 6: additional cross-boundary malicious fixtures and final Session 3
   integration coverage.
4. Final repository coverage, dependency audit, full spec/code/security review,
   and Session 3 completion declaration after Tasks 4-6.

Known residual risk accepted for this checkpoint: cache reads have a same-user
TOCTOU window between metadata validation and file reading. The threat model
places an attacker who already controls the user's local account out of scope;
this remains a defense-in-depth candidate rather than a release blocker.
