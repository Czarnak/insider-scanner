# SEC EDGAR Session 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task. Every production change follows RED-GREEN-REFACTOR.

**Goal:** Add minimal, offline-tested SEC clients and immutable parsers for daily-index-selected filings and bulk-submissions metadata.

**Architecture:** Keep transport, download/cache, ownership-document extraction, ownership normalization, and bulk metadata parsing in focused modules. Preserve SEC-native fields in frozen intermediate records; do not change persistence, services, CLI, GUI, or `InsiderTrade` in Session 2.

**Tech stack:** Python 3.11+, Requests, lxml, standard-library `dataclasses`, `decimal`, `zipfile`, pytest, Ruff, mypy.

**Source specification:** `docs/edgar_planning/SEC_EDGAR_TASK_AND_TEST_PLAN.md`, Tasks 3-7 and Session 2.

## Tasks

1. Add an injected, typed SEC HTTP client that requires a real User-Agent and wraps transport/status/decode failures without leaking response bodies.
2. Add an atomic filing downloader keyed by validated archive path with explicit fresh-cache reuse.
3. Add immutable ownership models and extract direct XML or the ownership document inside a full SEC submission.
4. Parse Form 3/4/5 issuer, owner, non-derivative, derivative, footnote, and amendment data into immutable filing bundles.
5. Parse main and continuation submissions JSON members directly from ZIP streams without extracting files.
6. Add an offline integration test from daily-index row through fake download, extraction, classification, and normalized output.

## Locked Semantics

- Transaction categories: `P` purchase; `S` sale; `A` award; `C/M/O/X` exercise/conversion; `F` tax withholding; `G/W/Z` gift/transfer; all others `other`.
- Raw SEC transaction codes and acquired/disposed flags are preserved.
- Original and amended filings coexist with explicit amendment metadata.
- Missing optional values become `None`; malformed required transaction data fails the whole document with a safe typed error.
- Footnotes are normalized to plain text and row-level references are retained.
- Stable row identity uses accession number, or an XML SHA-256 fallback, plus table kind and source row index.

## Quality Gates

- Unit tests for each module, an offline integration test, and at least 80% scoped coverage.
- Full pytest suite, Ruff, mypy, and `pip-audit` pass.
- No live SEC calls, credentials, raw filing bodies in errors/logs, or changes to persistence/default-source behavior.
- Run `graphify update . --no-viz` after code changes and review the final diff before completion.
