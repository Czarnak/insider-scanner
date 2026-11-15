# PR20 import compatibility and installed releases

Status: closed 2026-11-15
Created: 2026-11-15

## Description

Implement PR 20 from docs/review/PR_GROUPS_2026-11-14.md, covering
USSVC-12, USSVC-21, CLI-16, CLI-22, CLI-23 and TESTS-09. Preserve imported
data compatibility, exercise installed startup offline, and gate releases on
matching versions and verified code before publication.

## Acceptance criteria

- [x] Canonical and legacy SEC source imports remain queryable; imported boolean fields are validated and UTF-8 BOM files are accepted, with failing-then-passing regression evidence.
- [x] Packaging tests share one build from an isolated clean source copy; the installed CLI copies seeds and migrates SQLite under temporary data/cache roots, and GUI font validation reuses its launcher.
- [x] Installed metadata supplies the package and CLI version; release tag mismatches fail before build or publication; offline tests, lint and types gate release artifacts.
- [x] Runtime dependencies omit maintainer-only PyYAML, retained transitive version floors are explained, the lockfile is consistent, and dependency audit passes.
- [x] TestPyPI verification gets dependencies from PyPI and installs the exact candidate from TestPyPI without dependency resolution across indexes.
- [x] The offline suite meets 80% coverage; focused Procoder tests, full-branch gate and Git checks pass; independent review has no unresolved actionable findings.

## Implementation plan

1. Import compatibility: reproduce source/BOM/boolean failures, normalize and validate at the boundary, run focused tests and commit.
2. Installed packaging: share a clean wheel fixture, reproduce missing startup verification, validate isolated installed bootstrap and commit.
3. Version/release configuration: reproduce version behavior, derive installed version, enforce early tag checks and release quality gates, isolate TestPyPI resolution and commit.
4. Dependencies: move maintainer PyYAML to an extra, explain retained floors, refresh lock and documentation, audit and commit.
5. Verify combined work, address review findings in separate commits, record evidence and close through Procoder; push and open the requested PR.

## Evidence

- Fresh fetch and fast-forward updated master to 3f01ee3, then created codex/pr20-import-release-compatibility. The two pre-existing untracked backup archives are preserved.
- Source RED (`.cache/pr20-import-sources-red.txt`) reproduced five failures for rejected canonical sources and unqueryable legacy imports. GREEN (`.cache/pr20-import-sources-green.txt`): 208 importer, scan-service, SEC-mapping and SEC-comparison tests passed. Commit 68f3d89 normalizes incoming aliases and shares canonical source constants.
- Validation RED (`.cache/pr20-import-validation-red.txt`) reproduced 17 failures for misleading boolean error classification and BOM JSON. GREEN (`.cache/pr20-import-validation-green.txt`): 66 importer/validation tests passed. Commit 47ba84b validates all seven US and two Congress boolean fields, preserves valid flags and good sibling records, accepts BOM files and leaves input bytes unchanged.
- Version RED (`.cache/pr20-version-red.txt`) reproduced hard-coded package metadata and missing CLI version support. GREEN (`.cache/pr20-version-green.txt`): 15 version and CLI-outcome tests passed; commit 2176e15 reads distribution metadata and exits before startup for `--version`.
- Installed verification RED (`.cache/pr20-packaging-red.txt` and `.cache/pr20-packaging-version-red.txt`) reproduced missing offline startup dispatch and accepted mismatched CLI versions. GREEN (`.cache/pr20-packaging-green.txt`): 19 script/packaging tests passed. Commits f54c95d and 3c5c6fa verify real installed-wheel provenance, help/version, migrations, SQLite, seed bytes and isolated cache. The shared wheel builds once from clean temporary sources; offline installation reuses dependency files without activating the host editable package.
- Windows validation uses workspace `.cache/pr20-temp` for TEMP/TMP and a workspace pytest base directory. The sandbox default contains a literal `#!sandbox` component that makes a generated console executable exit silently; normal workspace paths pass the genuine entry-point checks. This changes only the verification environment.
- Release configuration GREEN (`.cache/pr20-release-green.txt`): 20 release-workflow, CI-contract and README-command tests passed. Inline tag validation was executed with matching and mismatched synthetic versions. Actionlint validated both workflows; dependency graph checks require the existing CI workflow before build and preserve job-scoped publication credentials. TestPyPI assertions require PyPI dependency installation followed by an exact-version, dependency-free forced replacement. No registry publication was performed.
- Dependency verification: `uv lock --check --python .venv/Scripts/python.exe` passed, resolving the existing 91-package lock. `uv export --locked --extra dev --extra gui --extra maintainer --no-emit-project --format requirements.txt --output-file .cache/pr20-audit-requirements.txt` followed by `python -m pip_audit --requirement .cache/pr20-audit-requirements.txt --require-hashes --disable-pip --strict` reported no known vulnerabilities. Explicit cryptography/NumPy compatibility floors remain documented; PyYAML is limited to maintainer/dev extras. The audit reported an unwritable optional cache but completed successfully.
- Full offline verification: `uv run --locked --extra dev --extra gui pytest -m 'not live' -q --basetemp .cache/pr20-pytest-full --cov=insider_scanner --cov-report=term:skip-covered --cov-report=json:.cache/pr20-coverage.json --cov-fail-under=80` passed 3,231 tests, skipped five, deselected eight live tests; coverage was 91.11%. All changed production modules have 90.2% to 100% coverage.
- Fresh `ruff check .`, `ruff format --check .` (308 files), `mypy src/insider_scanner` (121 source files), Actionlint for both workflows and `git diff --check` passed. Independent Brooks reviews of release/dependency changes and importer/installed-packaging changes found no actionable defects, including the final installed version check.
- Mutation review: omitting alias normalization, any boolean field validation or BOM decoding breaks regression cases; accepting an incorrect installed version breaks smoke tests; dropping seed/migration checks breaks bootstrap failure tests; building from stale source inputs breaks copy-isolation checks; removing release dependencies or mixing indexes breaks workflow contracts. Hosted publication and live provider calls remain unexercised locally.
- Final `procoder test` over all changed regression files plus source services/mapping/comparison passed 270 tests (`.cache/pr20-procoder-test.txt`). Full-branch `procoder check --paths-from .cache/pr20-paths.txt` passed: 20 clean, zero unformatted/unchecked, two unsupported formatting types (TOML/lockfile, independently verified by uv), zero blocking findings (`.cache/pr20-gate-verified.txt`). The gate's test leg also completed successfully. An initial Python 3.7 compatibility finding was judged inapplicable to the declared Python >=3.11.5 floor and annotated at that exact import using the existing project convention; security rules remain enabled.
- `procoder git` passed with the existing templates registered (`.cache/pr20-git.txt`). Its reusable-job timeout advisory is inapplicable to the caller: Actionlint accepts the workflow and every concrete called CI job already has a timeout. GitHub's repository API confirmed the canonical repository is Czarnak/insider-scanner despite the local remote's older insider-scan alias; the release runbook uses the canonical publisher identity.
