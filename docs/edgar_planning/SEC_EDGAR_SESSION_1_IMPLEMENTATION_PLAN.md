# SEC EDGAR Session 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic SEC URL/date helpers and an immutable parser for EDGAR daily master indexes without introducing live-network behavior.

**Architecture:** Extend `core/edgar.py` only with pure validated helpers. Add `core/sec_index.py` as a separate batch-parser boundary that returns frozen filing metadata, skips malformed rows, filters ownership forms, and deduplicates by archive path while preserving first-seen order.

**Tech Stack:** Python 3.11+, frozen dataclasses, standard-library `datetime`, pytest, Ruff, mypy.

**Source specification:** `docs/edgar_planning/SEC_EDGAR_TASK_AND_TEST_PLAN.md`, Tasks 1-2 and Session 1.

---

### Task 1: SEC URL, Date, CIK, and Archive-Path Helpers

**Files:**
- Modify: `tests/test_edgar.py`
- Modify: `src/insider_scanner/core/edgar.py`

- [ ] **Step 1: Write failing helper tests**

Add tests covering these public functions:

```python
normalize_cik(cik: str | int) -> str
quarter_for_date(day: date) -> str
build_daily_master_index_url(day: date) -> str
build_filing_archive_url(index_path: str) -> str
```

Required assertions:

```python
assert quarter_for_date(date(2026, 1, 1)) == "QTR1"
assert quarter_for_date(date(2026, 4, 1)) == "QTR2"
assert quarter_for_date(date(2026, 7, 1)) == "QTR3"
assert quarter_for_date(date(2026, 10, 1)) == "QTR4"
assert build_daily_master_index_url(date(2026, 6, 15)) == (
    "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR2/"
    "master.20260615.idx"
)
assert build_filing_archive_url(
    "edgar/data/1000228/0001190297-26-000004.txt"
) == (
    "https://www.sec.gov/Archives/edgar/data/1000228/"
    "0001190297-26-000004.txt"
)
assert normalize_cik("320193") == "0000320193"
assert normalize_cik(320193) == "0000320193"
assert normalize_cik("0000320193") == "0000320193"
```

Use parametrized invalid-input tests. Wrong types raise `TypeError`; blank, non-digit, overlong CIKs and unsafe/non-SEC archive paths raise `ValueError`. Reject leading slashes, backslashes, `..`, drive-letter fragments, query/fragment characters, and paths outside `edgar/data/`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_edgar.py -v -p no:cacheprovider --basetemp=build\pytest-tmp-session1-red-helpers
```

Expected: collection fails because the four new helpers do not exist.

- [ ] **Step 3: Implement minimal pure helpers**

Add constants for the SEC archive and daily-index base URLs. Keep existing CIK lookup and browse-page behavior unchanged. Validate before formatting; perform no I/O and mutate no inputs.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command with `session1-green-helpers` as the temporary directory. Expected: all `tests/test_edgar.py` tests pass.

- [ ] **Step 5: Refactor and run static checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m ruff check src\insider_scanner\core\edgar.py tests\test_edgar.py
.\.venv\Scripts\python.exe -m mypy src\insider_scanner\core\edgar.py tests\test_edgar.py
```

Expected: both commands exit 0.

### Task 2: Immutable Daily Master Index Parser

**Files:**
- Create: `tests/fixtures/sec_master_20260615_excerpt.idx`
- Create: `tests/test_sec_index.py`
- Create: `src/insider_scanner/core/sec_index.py`

- [ ] **Step 1: Create deterministic fixture and failing tests**

The fixture must include realistic headers, all six retained forms (`3`, `3/A`, `4`, `4/A`, `5`, `5/A`), one non-ownership form, malformed rows, an invalid date/path, and one duplicate archive path.

Tests must import and exercise:

```python
OWNERSHIP_FORMS = frozenset({"3", "3/A", "4", "4/A", "5", "5/A"})

@dataclass(frozen=True)
class SecMasterIndexRow:
    cik: str
    company_name: str
    form_type: str
    filing_date: date
    archive_path: str

def parse_master_index(text: str) -> tuple[SecMasterIndexRow, ...]: ...
```

Assert header skipping, whitespace trimming, ownership-form filtering, CIK normalization, ISO date parsing, malformed-row skipping, first-seen archive-path deduplication, stable order, tuple output, and frozen-row assignment failure. Non-string parser input raises `TypeError`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sec_index.py -v -p no:cacheprovider --basetemp=build\pytest-tmp-session1-red-index
```

Expected: collection fails because `insider_scanner.core.sec_index` does not exist.

- [ ] **Step 3: Implement minimal parser**

Split each candidate line into exactly five pipe-delimited fields. Ignore non-ownership forms before expensive validation. Construct a row only after CIK, date, and archive path validate. Catch row-level `TypeError`/`ValueError` and continue without logging the raw row. Deduplicate using a new set and append to a new list; return `tuple(rows)`.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Step 2 command with `session1-green-index` as the temporary directory. Expected: all parser tests pass.

- [ ] **Step 5: Run combined Session 1 checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_edgar.py tests\test_sec_index.py -v -p no:cacheprovider --basetemp=build\pytest-tmp-session1-combined
.\.venv\Scripts\python.exe -m pytest tests\test_edgar.py tests\test_sec_index.py --cov=insider_scanner.core.edgar --cov=insider_scanner.core.sec_index --cov-report=term-missing --cov-fail-under=80
.\.venv\Scripts\python.exe -m ruff check src\insider_scanner\core\edgar.py src\insider_scanner\core\sec_index.py tests\test_edgar.py tests\test_sec_index.py
.\.venv\Scripts\python.exe -m mypy src\insider_scanner\core\edgar.py src\insider_scanner\core\sec_index.py tests\test_edgar.py tests\test_sec_index.py
```

Expected: tests pass, scoped coverage is at least 80%, Ruff exits 0, and mypy exits 0.

### Task 3: Integration Verification and Graph Refresh

**Files:**
- Update generated graph under `graphify-out/`

- [ ] **Step 1: Run the repository test suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -v -p no:cacheprovider --basetemp=build\pytest-tmp-session1-full
```

Record any pre-existing Windows `pytest-qt`/COM crash separately from Session 1 failures. Do not suppress or misreport it.

- [ ] **Step 2: Run repository static checks and dependency audit**

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
.\.venv\Scripts\python.exe -m pip_audit
```

- [ ] **Step 3: Refresh graphify**

```powershell
graphify update . --no-viz
```

- [ ] **Step 4: Review final diff**

Confirm no credential values, generated secrets, unrelated `.gitignore` content, live-network tests, or service-layer changes are included.
