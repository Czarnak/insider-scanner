"""Stream SEC quarterly Form 3/4/5 TSV archives into shared ownership records.

Tables are joined in a disposable, disk-backed SQLite database rather than
retaining an entire quarter in memory. No archive member is extracted. Native
dataset surrogate keys are explicitly namespaced: SEC does not promise that
their order equals the XML transaction order.

Schema: https://www.sec.gov/files/insider_transactions_readme.pdf
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Generator, Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, TextIO

from insider_scanner.core.edgar import normalize_cik
from insider_scanner.core.sec_bulk import (
    SecBulkError,
    _open_archive,
    _preflight_members,
    _validate_zip_location,
)
from insider_scanner.core.sec_forms import OWNERSHIP_FORM_TYPES
from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_ownership_parser import (
    DerivativeTransaction,
    Footnote,
    Issuer,
    NonDerivativeTransaction,
    OwnershipFiling,
    ReportingOwner,
    category_for_code,
    normalize_issuer_symbol,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)

DATASET_SECURITY_POLICY = replace(
    DEFAULT_SEC_SECURITY_POLICY,
    zip_max_member_bytes=1024 * 1024 * 1024,
)

_ACCESSION = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}\Z")
_FOOTNOTE_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        ),
        start=1,
    )
}
_TRANSACTION_COLUMNS = frozenset(
    {
        "ACCESSION_NUMBER",
        "SECURITY_TITLE",
        "TRANS_DATE",
        "TRANS_CODE",
        "TRANS_SHARES",
        "TRANS_PRICEPERSHARE",
        "TRANS_ACQUIRED_DISP_CD",
        "SHRS_OWND_FOLWNG_TRANS",
        "VALU_OWND_FOLWNG_TRANS",
        "DIRECT_INDIRECT_OWNERSHIP",
    }
)
_COLUMNS = {
    "SUBMISSION": frozenset(
        {
            "ACCESSION_NUMBER",
            "FILING_DATE",
            "PERIOD_OF_REPORT",
            "DATE_OF_ORIG_SUB",
            "DOCUMENT_TYPE",
            "ISSUERCIK",
            "ISSUERNAME",
            "ISSUERTRADINGSYMBOL",
            "REMARKS",
        }
    ),
    "REPORTINGOWNER": frozenset(
        {
            "ACCESSION_NUMBER",
            "RPTOWNERCIK",
            "RPTOWNERNAME",
            "RPTOWNER_RELATIONSHIP",
            "RPTOWNER_TITLE",
            "RPTOWNER_TXT",
        }
    ),
    "NONDERIV_TRANS": _TRANSACTION_COLUMNS | {"NONDERIV_TRANS_SK"},
    "DERIV_TRANS": _TRANSACTION_COLUMNS
    | {
        "DERIV_TRANS_SK",
        "TRANS_TOTAL_VALUE",
        "CONV_EXERCISE_PRICE",
        "EXCERCISE_DATE",
        "EXPIRATION_DATE",
        "UNDLYNG_SEC_TITLE",
        "UNDLYNG_SEC_SHARES",
        "UNDLYNG_SEC_VALUE",
    },
    "FOOTNOTES": frozenset({"ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"}),
}
_KEY_COLUMNS = {
    "REPORTINGOWNER": "RPTOWNERCIK",
    "NONDERIV_TRANS": "NONDERIV_TRANS_SK",
    "DERIV_TRANS": "DERIV_TRANS_SK",
    "FOOTNOTES": "FOOTNOTE_ID",
}
_REFERENCE_COLUMNS = frozenset(
    {
        "SECURITY_TITLE_FN",
        "TRANS_DATE_FN",
        "DEEMED_EXECUTION_DATE_FN",
        "EQUITY_SWAP_TRANS_CD_FN",
        "TRANS_TIMELINESS_FN",
        "TRANS_SHARES_FN",
        "TRANS_PRICEPERSHARE_FN",
        "TRANS_ACQUIRED_DISP_CD_FN",
        "SHRS_OWND_FOLWNG_TRANS_FN",
        "VALU_OWND_FOLWNG_TRANS_FN",
        "DIRECT_INDIRECT_OWNERSHIP_FN",
        "NATURE_OF_OWNERSHIP_FN",
    }
)
_DERIVATIVE_REFERENCES = _REFERENCE_COLUMNS | {
    "CONV_EXERCISE_PRICE_FN",
    "TRANS_TOTAL_VALUE_FN",
    "EXCERCISE_DATE_FN",
    "EXPIRATION_DATE_FN",
    "UNDLYNG_SEC_TITLE_FN",
    "UNDLYNG_SEC_SHARES_FN",
    "UNDLYNG_SEC_VALUE_FN",
}


class SecDatasetError(SecBulkError):
    """The quarterly archive cannot be safely or completely interpreted."""


@dataclass(frozen=True, slots=True)
class DatasetFiling:
    """A complete mapped filing or an attributable, retryable dataset failure."""

    accession: str
    index_row: SecMasterIndexRow | None
    filing: OwnershipFiling | None
    error: str = ""


def iter_dataset_filings(
    zip_path: Path,
    *,
    since: date | None = None,
    until: date | None = None,
    ciks: frozenset[str] | None = None,
    policy: SecSecurityPolicy = DATASET_SECURITY_POLICY,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int], None] | None = None,
) -> Generator[DatasetFiling, None, None]:
    """Yield date/CIK-selected filings after validating all imported tables.

    ``on_progress`` receives the cumulative number of staged physical rows,
    including excluded filings. Cancellation closes both the ZIP and temporary
    database. Required schema failures and orphan records reject the archive
    before any filing can be reported successful; corrupt attributable rows
    fail their entire accession while other filings remain usable.
    """
    if since is not None and until is not None and since > until:
        raise ValueError("since must not be after until")
    selected_ciks = (
        frozenset(normalize_cik(cik) for cik in ciks) if ciks is not None else None
    )
    _validate_zip_location(zip_path, None)
    try:
        with _open_archive(zip_path) as archive:
            _preflight_members(archive.infolist(), policy)
            members = _table_members(archive)
            with (
                tempfile.TemporaryDirectory(prefix="sec-dataset-") as temporary,
                closing(sqlite3.connect(Path(temporary) / "tables.sqlite3")) as db,
            ):
                _create_staging(db)
                if not _stage_tables(
                    db, archive, members, policy, cancelled, on_progress
                ):
                    return
                orphan = db.execute(
                    "SELECT 1 FROM dataset_rows AS child "
                    "LEFT JOIN dataset_rows AS parent ON parent.kind='SUBMISSION' "
                    "AND parent.accession=child.accession "
                    "WHERE child.kind!='SUBMISSION' AND parent.accession IS NULL LIMIT 1"
                ).fetchone()
                if orphan:
                    raise SecDatasetError("Dataset contains orphan child rows")
                orphan_error = db.execute(
                    "SELECT 1 FROM dataset_errors AS child "
                    "LEFT JOIN dataset_rows AS parent ON parent.kind='SUBMISSION' "
                    "AND parent.accession=child.accession "
                    "WHERE parent.accession IS NULL LIMIT 1"
                ).fetchone()
                if orphan_error:
                    raise SecDatasetError("Dataset contains orphan failed child rows")
                yield from _selected_filings(
                    db, since, until, selected_ciks, policy, cancelled
                )
    except (
        OSError,
        UnicodeError,
        csv.Error,
        zipfile.BadZipFile,
        zlib.error,
        sqlite3.Error,
    ) as error:
        raise SecDatasetError("Unable to read SEC quarterly dataset archive") from error


def _table_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = {}
    for info in archive.infolist():
        path = Path(info.filename)
        table = path.stem.upper()
        if path.suffix.lower() not in {".tsv", ".txt"} or table not in _COLUMNS:
            continue
        if info.flag_bits & 1:
            raise SecDatasetError("Dataset contains an encrypted table")
        if info.compress_type not in {
            zipfile.ZIP_STORED,
            zipfile.ZIP_DEFLATED,
            zipfile.ZIP_BZIP2,
            zipfile.ZIP_LZMA,
        }:
            raise SecDatasetError("Dataset table uses unsupported compression")
        if table in members:
            raise SecDatasetError("Dataset contains a duplicate table")
        members[table] = info
    if set(members) != set(_COLUMNS):
        raise SecDatasetError("Dataset is missing a required table")
    return members


def _create_staging(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-4096")
    db.execute(
        "CREATE TABLE dataset_rows (kind TEXT NOT NULL, accession TEXT NOT NULL, "
        "item_key TEXT NOT NULL, payload TEXT NOT NULL, "
        "PRIMARY KEY(kind,accession,item_key)) WITHOUT ROWID"
    )
    db.execute(
        "CREATE TABLE dataset_errors (accession TEXT PRIMARY KEY, reason TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX dataset_rows_accession ON dataset_rows(accession)")


def _stage_tables(
    db: sqlite3.Connection,
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    policy: SecSecurityPolicy,
    cancelled: Callable[[], bool],
    on_progress: Callable[[int], None] | None,
) -> bool:
    count = 0
    for table, required_columns in _COLUMNS.items():
        if cancelled():
            return False
        with (
            archive.open(members[table]) as member,
            io.TextIOWrapper(member, encoding="utf-8-sig", newline="") as stream,
        ):
            reader = _tsv_records(stream, policy.xml_max_text_bytes)
            header = next(reader, [])
            if (
                len(header) != len(set(header))
                or not required_columns.issubset(header)
                or len(header) > 256
            ):
                raise SecDatasetError("Dataset table has missing or duplicate columns")
            for values in reader:
                if cancelled():
                    return False
                count += 1
                if not values:
                    continue
                accession_index = header.index("ACCESSION_NUMBER")
                accession = (
                    values[accession_index].strip()
                    if accession_index < len(values)
                    else ""
                )
                if _ACCESSION.fullmatch(accession) is None:
                    raise SecDatasetError("Dataset contains an invalid accession")
                if len(values) != len(header):
                    _remember_error(
                        db, accession, "Dataset TSV row has an invalid column count"
                    )
                    _store_failed_identity(
                        db, table, accession, dict(zip(header, values, strict=False))
                    )
                    continue
                row = dict(
                    zip(header, (value.strip() for value in values), strict=True)
                )
                try:
                    _validate_fields(row, policy)
                    key = _row_key(table, row)
                except ValueError as error:
                    _remember_error(db, accession, str(error))
                    _store_failed_identity(db, table, accession, row)
                    continue
                _store_row(db, table, accession, key, row)
                if count % 1000 == 0:
                    db.commit()
                    if on_progress is not None:
                        on_progress(count)
            if on_progress is not None:
                on_progress(count)
    db.commit()
    return True


def _tsv_records(stream: TextIO, limit: int) -> Iterator[list[str]]:
    """Bound total text consumed by csv.reader for one possibly quoted record."""
    remaining = limit

    def lines() -> Iterator[str]:
        nonlocal remaining
        while line := stream.readline(remaining + 1):
            remaining -= len(line.encode("utf-8"))
            if remaining < 0:
                raise SecDatasetError("Dataset TSV record exceeds its byte limit")
            yield line

    reader = csv.reader(lines(), delimiter="\t", strict=True)
    while True:
        remaining = limit
        row = next(reader, None)
        if row is None:
            return
        yield row


def _store_failed_identity(
    db: sqlite3.Connection, table: str, accession: str, row: dict[str, str]
) -> None:
    # Retain enough identity to report corrupt requested owners as failures
    # rather than silently excluding their accession during CIK selection.
    if table == "SUBMISSION":
        _store_row(db, table, accession, "", {"ACCESSION_NUMBER": accession})
    elif table == "REPORTINGOWNER":
        try:
            cik = normalize_cik(row.get("RPTOWNERCIK", "").strip())
        except ValueError:
            return
        _store_row(db, table, accession, cik, {"RPTOWNERCIK": cik})


def _validate_fields(row: dict[str, str], policy: SecSecurityPolicy) -> None:
    for name, value in row.items():
        limit = (
            policy.xml_max_long_text_chars
            if name in {"FOOTNOTE_TXT", "REMARKS"}
            else policy.xml_max_scalar_chars
        )
        if len(value) > limit or "\x00" in value:
            raise ValueError("Dataset field exceeds its text limit or contains NUL")


def _row_key(table: str, row: dict[str, str]) -> str:
    if table == "SUBMISSION":
        return ""
    key = row[_KEY_COLUMNS[table]]
    if table == "REPORTINGOWNER":
        try:
            return normalize_cik(key)
        except ValueError:
            raise ValueError("Dataset owner CIK is invalid") from None
    if table == "FOOTNOTES":
        if _FOOTNOTE_ID.fullmatch(key) is None:
            raise ValueError("Dataset footnote identifier is invalid")
        return key
    if not key.isascii() or not key.isdigit() or len(key) > 38:
        raise ValueError("Dataset transaction surrogate key is invalid")
    return str(int(key))


def _remember_error(db: sqlite3.Connection, accession: str, reason: str) -> None:
    db.execute(
        "INSERT OR IGNORE INTO dataset_errors(accession,reason) VALUES (?,?)",
        (accession, reason),
    )


def _store_row(
    db: sqlite3.Connection, table: str, accession: str, key: str, row: dict[str, str]
) -> None:
    try:
        db.execute(
            "INSERT INTO dataset_rows(kind,accession,item_key,payload) VALUES (?,?,?,?)",
            (table, accession, key, json.dumps(row, separators=(",", ":"))),
        )
    except sqlite3.IntegrityError:
        _remember_error(db, accession, "Dataset contains a duplicate row identity")


def _rows(
    db: sqlite3.Connection, table: str, accession: str, limit: int
) -> list[dict[str, str]]:
    cursor = db.execute(
        "SELECT payload FROM dataset_rows WHERE kind=? AND accession=? "
        "ORDER BY item_key LIMIT ?",
        (table, accession, limit + 1),
    )
    rows = [json.loads(payload) for (payload,) in cursor]
    if len(rows) > limit:
        raise ValueError("Dataset filing exceeds its row limit")
    return rows


def _selected_filings(
    db: sqlite3.Connection,
    since: date | None,
    until: date | None,
    ciks: frozenset[str] | None,
    policy: SecSecurityPolicy,
    cancelled: Callable[[], bool],
) -> Iterator[DatasetFiling]:
    cursor = db.execute(
        "SELECT accession,payload FROM dataset_rows WHERE kind='SUBMISSION' ORDER BY accession"
    )
    for accession, payload in cursor:
        if cancelled():
            return
        row = json.loads(payload)
        index_row = None
        try:
            filing_date = _date(row.get("FILING_DATE", ""), required=True)
            assert filing_date is not None
            if (since is not None and filing_date < since) or (
                until is not None and filing_date > until
            ):
                continue
            payload_bytes = db.execute(
                "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) "
                "FROM dataset_rows WHERE accession=?",
                (accession,),
            ).fetchone()[0]
            if payload_bytes > policy.xml_max_bytes:
                raise ValueError("Dataset filing exceeds its aggregate byte limit")
            owners = _rows(db, "REPORTINGOWNER", accession, policy.xml_max_elements)
            issuer_cik = normalize_cik(row["ISSUERCIK"])
            if ciks is not None:
                owner_ciks = {normalize_cik(owner["RPTOWNERCIK"]) for owner in owners}
                if issuer_cik not in ciks and not ciks.intersection(owner_ciks):
                    continue
            error = db.execute(
                "SELECT reason FROM dataset_errors WHERE accession=?", (accession,)
            ).fetchone()
            if error:
                raise ValueError(error[0])
            form = row["DOCUMENT_TYPE"].upper()
            if form not in OWNERSHIP_FORM_TYPES:
                raise ValueError("Dataset document type is not an ownership form")
            if not row["ISSUERNAME"]:
                raise ValueError("Dataset issuer name is missing")
            index_row = SecMasterIndexRow(
                issuer_cik,
                row["ISSUERNAME"],
                form,
                filing_date,
                f"edgar/data/{int(issuer_cik)}/{accession}.txt",
            )
            filing = _filing(db, accession, row, owners, issuer_cik, policy)
        except (KeyError, ValueError, ArithmeticError):
            yield DatasetFiling(
                accession, index_row, None, "Invalid or incomplete dataset filing"
            )
        else:
            yield DatasetFiling(accession, index_row, filing)


def _date(value: str, *, required: bool = False) -> date | None:
    if not value:
        if required:
            raise ValueError("Dataset required date is missing")
        return None
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return date.fromisoformat(value)
        day, month, year = value.upper().split("-")
        if not day.isascii() or not day.isdigit() or len(year) != 4:
            raise ValueError
        return date(int(year), _MONTHS[month], int(day))
    except (KeyError, ValueError):
        raise ValueError("Dataset date is invalid") from None


def _number(
    row: dict[str, str], name: str, policy: SecSecurityPolicy
) -> Decimal | None:
    value = row.get(name, "")
    if not value:
        return None
    if len(value) > policy.xml_max_numeric_chars:
        raise ValueError("Dataset number exceeds its text limit")
    try:
        number = Decimal(value)
        if not number.is_finite() or not math.isfinite(float(number)):
            raise ValueError
        return number
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError("Dataset numeric value is invalid") from None


def _owner(row: dict[str, str]) -> ReportingOwner:
    relationship = row["RPTOWNER_RELATIONSHIP"].upper()
    pattern = r"TEN\s*PERCENT\s*OWNER|10\s*%\s*OWNER|OFFICER|DIRECTOR|OTHER"
    roles = {
        re.sub(r"\s+", "", match.group())
        for match in re.finditer(pattern, relationship)
    }
    remainder = re.sub(pattern, "", relationship)
    if not roles or re.sub(r"[\s,;|/]+", "", remainder):
        raise ValueError("Dataset owner relationship is invalid")
    if not row["RPTOWNERNAME"]:
        raise ValueError("Dataset owner name is missing")
    return ReportingOwner(
        cik=normalize_cik(row["RPTOWNERCIK"]),
        name=row["RPTOWNERNAME"],
        is_director="DIRECTOR" in roles,
        is_officer="OFFICER" in roles,
        is_ten_percent_owner=bool(roles.intersection({"TENPERCENTOWNER", "10%OWNER"})),
        is_other="OTHER" in roles,
        officer_title=row["RPTOWNER_TITLE"] or None,
        other_text=row["RPTOWNER_TXT"] or None,
    )


def _references(value: str, footnotes: set[str]) -> tuple[str, ...]:
    refs = tuple(dict.fromkeys(filter(None, re.split(r"[\s,;|]+", value))))
    if any(ref not in footnotes for ref in refs):
        raise ValueError("Dataset field references a missing footnote")
    return refs


def _transaction(
    row: dict[str, str],
    accession: str,
    kind: Literal["nonDerivative", "derivative"],
    footnotes: set[str],
    policy: SecSecurityPolicy,
) -> NonDerivativeTransaction | DerivativeTransaction:
    code = row["TRANS_CODE"].upper() or None
    if code is not None and not re.fullmatch(r"[A-Z]", code):
        raise ValueError("Dataset transaction code is invalid")
    direction = row["TRANS_ACQUIRED_DISP_CD"].upper() or None
    ownership = row["DIRECT_INDIRECT_OWNERSHIP"].upper() or None
    if direction not in {"A", "D", None} or ownership not in {"D", "I", None}:
        raise ValueError("Dataset transaction ownership code is invalid")
    references = tuple(
        dict.fromkeys(
            ref
            for name, value in row.items()
            if name.endswith("_FN")
            for ref in _references(value, footnotes)
        )
    )
    common = {
        "row_id": f"{accession}:dataset:{kind}:{_row_key('DERIV_TRANS' if kind == 'derivative' else 'NONDERIV_TRANS', row)}",
        "security_title": row["SECURITY_TITLE"] or None,
        "transaction_date": _date(row["TRANS_DATE"], required=True),
        "transaction_code": code,
        "category": category_for_code(code),
        "acquired_disposed": direction,
        "shares": _number(row, "TRANS_SHARES", policy),
        "price_per_share": _number(row, "TRANS_PRICEPERSHARE", policy),
        "shares_owned_following": _number(row, "SHRS_OWND_FOLWNG_TRANS", policy),
        "direct_or_indirect": ownership,
        "footnote_ids": references,
        "warnings": ()
        if (
            _DERIVATIVE_REFERENCES if kind == "derivative" else _REFERENCE_COLUMNS
        ).issubset(row)
        else ("dataset_reference_columns_missing",),
        "transaction_total_value": _number(row, "TRANS_TOTAL_VALUE", policy),
        "value_owned_following": _number(row, "VALU_OWND_FOLWNG_TRANS", policy),
        "price_footnote_ids": _references(
            row.get("TRANS_PRICEPERSHARE_FN", ""), footnotes
        ),
    }
    if kind == "nonDerivative":
        return NonDerivativeTransaction(**common)  # type: ignore[arg-type]
    return DerivativeTransaction(
        **common,  # type: ignore[arg-type]
        conversion_or_exercise_price=_number(row, "CONV_EXERCISE_PRICE", policy),
        exercise_date=_date(row["EXCERCISE_DATE"]),
        expiration_date=_date(row["EXPIRATION_DATE"]),
        underlying_security_title=row["UNDLYNG_SEC_TITLE"] or None,
        underlying_security_shares=_number(row, "UNDLYNG_SEC_SHARES", policy),
        underlying_security_value=_number(row, "UNDLYNG_SEC_VALUE", policy),
    )


def _filing(
    db: sqlite3.Connection,
    accession: str,
    row: dict[str, str],
    owner_rows: list[dict[str, str]],
    issuer_cik: str,
    policy: SecSecurityPolicy,
) -> OwnershipFiling:
    owners = tuple(_owner(owner) for owner in owner_rows)
    if not owners:
        raise ValueError("Dataset filing has no reporting owner")
    footnotes = tuple(
        Footnote(item["FOOTNOTE_ID"], item["FOOTNOTE_TXT"])
        for item in _rows(db, "FOOTNOTES", accession, policy.xml_max_elements)
    )
    footnote_ids = {footnote.footnote_id for footnote in footnotes}
    nonderivatives = tuple(
        _transaction(item, accession, "nonDerivative", footnote_ids, policy)
        for item in _rows(db, "NONDERIV_TRANS", accession, policy.xml_max_elements)
    )
    derivatives = tuple(
        _transaction(item, accession, "derivative", footnote_ids, policy)
        for item in _rows(db, "DERIV_TRANS", accession, policy.xml_max_elements)
    )
    return OwnershipFiling(
        accession_number=accession,
        document_type=row["DOCUMENT_TYPE"].upper(),
        is_amendment=row["DOCUMENT_TYPE"].upper().endswith("/A"),
        period_of_report=_date(row["PERIOD_OF_REPORT"], required=True),
        remarks=row["REMARKS"] or None,
        document_sha256="",
        issuer=Issuer(
            issuer_cik,
            row["ISSUERNAME"] or None,
            normalize_issuer_symbol(row["ISSUERTRADINGSYMBOL"] or None),
        ),
        reporting_owner=owners[0],
        reporting_owners=owners,
        non_derivative_transactions=nonderivatives,  # type: ignore[arg-type]
        derivative_transactions=derivatives,  # type: ignore[arg-type]
        footnotes=footnotes,
        warnings=("dataset_representation",),
        original_submission_date=_date(row["DATE_OF_ORIG_SUB"]),
    )
