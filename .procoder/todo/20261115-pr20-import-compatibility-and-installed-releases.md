# PR20 import compatibility and installed releases

Status: open
Created: 2026-11-15

## Description

Implement PR 20 from docs/review/PR_GROUPS_2026-11-14.md, covering
USSVC-12, USSVC-21, CLI-16, CLI-22, CLI-23 and TESTS-09. Preserve imported
data compatibility, exercise installed startup offline, and gate releases on
matching versions and verified code before publication.

## Acceptance criteria

- [ ] Canonical and legacy SEC source imports remain queryable; imported boolean fields are validated and UTF-8 BOM files are accepted, with failing-then-passing regression evidence.
- [ ] Packaging tests share one build from an isolated clean source copy; the installed CLI copies seeds and migrates SQLite under temporary data/cache roots, and GUI font validation reuses its launcher.
- [ ] Installed metadata supplies the package and CLI version; release tag mismatches fail before build or publication; offline tests, lint and types gate release artifacts.
- [ ] Runtime dependencies omit maintainer-only PyYAML, retained transitive version floors are explained, the lockfile is consistent, and dependency audit passes.
- [ ] TestPyPI verification gets dependencies from PyPI and installs the exact candidate from TestPyPI without dependency resolution across indexes.
- [ ] The offline suite meets 80% coverage; focused Procoder tests, full-branch gate and Git checks pass; independent review has no unresolved actionable findings.

## Implementation plan

1. Import compatibility: reproduce source/BOM/boolean failures, normalize and validate at the boundary, run focused tests and commit.
2. Installed packaging: share a clean wheel fixture, reproduce missing startup verification, validate isolated installed bootstrap and commit.
3. Version/release configuration: reproduce version behavior, derive installed version, enforce early tag checks and release quality gates, isolate TestPyPI resolution and commit.
4. Dependencies: move maintainer PyYAML to an extra, explain retained floors, refresh lock and documentation, audit and commit.
5. Verify combined work, address review findings in separate commits, record evidence and close through Procoder; push and open the requested PR.

## Evidence

- Fresh fetch and fast-forward updated master to 3f01ee3, then created codex/pr20-import-release-compatibility. The two pre-existing untracked backup archives are preserved.
