"""Small, synthetic SEC quarterly TSV archives; no downloaded user data."""

from __future__ import annotations

import csv
import io
import zipfile

ACCESSION = "0000000001-26-000001"
SECOND_ACCESSION = "0000000002-26-000002"

SUBMISSION_COLUMNS = (
    "ACCESSION_NUMBER",
    "FILING_DATE",
    "PERIOD_OF_REPORT",
    "DATE_OF_ORIG_SUB",
    "DOCUMENT_TYPE",
    "ISSUERCIK",
    "ISSUERNAME",
    "ISSUERTRADINGSYMBOL",
    "REMARKS",
)
OWNER_COLUMNS = (
    "ACCESSION_NUMBER",
    "RPTOWNERCIK",
    "RPTOWNERNAME",
    "RPTOWNER_RELATIONSHIP",
    "RPTOWNER_TITLE",
    "RPTOWNER_TXT",
)
TRANSACTION_COLUMNS = (
    "ACCESSION_NUMBER",
    "NONDERIV_TRANS_SK",
    "SECURITY_TITLE",
    "TRANS_DATE",
    "TRANS_CODE",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "TRANS_ACQUIRED_DISP_CD",
    "SHRS_OWND_FOLWNG_TRANS",
    "VALU_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
    "SECURITY_TITLE_FN",
    "TRANS_PRICEPERSHARE_FN",
    "NATURE_OF_OWNERSHIP_FN",
    "TRANS_DATE_FN",
    "DEEMED_EXECUTION_DATE_FN",
    "EQUITY_SWAP_TRANS_CD_FN",
    "TRANS_TIMELINESS_FN",
    "TRANS_SHARES_FN",
    "TRANS_ACQUIRED_DISP_CD_FN",
    "SHRS_OWND_FOLWNG_TRANS_FN",
    "VALU_OWND_FOLWNG_TRANS_FN",
    "DIRECT_INDIRECT_OWNERSHIP_FN",
)
DERIVATIVE_COLUMNS = tuple(
    "DERIV_TRANS_SK" if name == "NONDERIV_TRANS_SK" else name
    for name in TRANSACTION_COLUMNS
) + (
    "TRANS_TOTAL_VALUE",
    "CONV_EXERCISE_PRICE",
    "EXCERCISE_DATE",
    "EXPIRATION_DATE",
    "UNDLYNG_SEC_TITLE",
    "UNDLYNG_SEC_SHARES",
    "UNDLYNG_SEC_VALUE",
    "CONV_EXERCISE_PRICE_FN",
    "TRANS_TOTAL_VALUE_FN",
    "EXCERCISE_DATE_FN",
    "EXPIRATION_DATE_FN",
    "UNDLYNG_SEC_TITLE_FN",
    "UNDLYNG_SEC_SHARES_FN",
    "UNDLYNG_SEC_VALUE_FN",
)
FOOTNOTE_COLUMNS = ("ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT")

COLUMNS = {
    "SUBMISSION": SUBMISSION_COLUMNS,
    "REPORTINGOWNER": OWNER_COLUMNS,
    "NONDERIV_TRANS": TRANSACTION_COLUMNS,
    "DERIV_TRANS": DERIVATIVE_COLUMNS,
    "FOOTNOTES": FOOTNOTE_COLUMNS,
}


def submission(accession=ACCESSION, **changes):
    return {
        "ACCESSION_NUMBER": accession,
        "FILING_DATE": "15-JUN-2026",
        "PERIOD_OF_REPORT": "12-JUN-2026",
        "DATE_OF_ORIG_SUB": "",
        "DOCUMENT_TYPE": "4",
        "ISSUERCIK": "1",
        "ISSUERNAME": "Example Corp",
        "ISSUERTRADINGSYMBOL": "EXMP",
        "REMARKS": "Synthetic example",
        **changes,
    }


def owner(accession=ACCESSION, **changes):
    return {
        "ACCESSION_NUMBER": accession,
        "RPTOWNERCIK": "25",
        "RPTOWNERNAME": "Example Person",
        "RPTOWNER_RELATIONSHIP": "OFFICER, DIRECTOR",
        "RPTOWNER_TITLE": "Chief Executive Officer",
        "RPTOWNER_TXT": "",
        **changes,
    }


def transaction(accession=ACCESSION, *, derivative=False, **changes):
    return {
        "ACCESSION_NUMBER": accession,
        "DERIV_TRANS_SK" if derivative else "NONDERIV_TRANS_SK": "10042",
        "SECURITY_TITLE": "Stock Option" if derivative else "Common Stock",
        "TRANS_DATE": "12-JUN-2026",
        "TRANS_CODE": "M" if derivative else "P",
        "TRANS_SHARES": "12.50",
        "TRANS_PRICEPERSHARE": "2.00",
        "TRANS_ACQUIRED_DISP_CD": "A",
        "SHRS_OWND_FOLWNG_TRANS": "100",
        "VALU_OWND_FOLWNG_TRANS": "",
        "DIRECT_INDIRECT_OWNERSHIP": "D",
        "CONV_EXERCISE_PRICE": "1.75",
        "EXCERCISE_DATE": "01-JAN-2026",
        "EXPIRATION_DATE": "01-JAN-2027",
        "UNDLYNG_SEC_TITLE": "Common Stock",
        "UNDLYNG_SEC_SHARES": "12.50",
        "UNDLYNG_SEC_VALUE": "",
        "TRANS_TOTAL_VALUE": "",
        **changes,
    }


def make_dataset(path, *, tables=None, columns=None, extra_members=None):
    data = {
        "SUBMISSION": [submission()],
        "REPORTINGOWNER": [owner()],
        "NONDERIV_TRANS": [transaction()],
        "DERIV_TRANS": [],
        "FOOTNOTES": [],
    }
    if tables is not None:
        data.update(tables)
    schemas = {**COLUMNS, **(columns or {})}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table, rows in data.items():
            if rows is None:
                continue
            text = io.StringIO(newline="")
            writer = csv.DictWriter(
                text, fieldnames=schemas[table], delimiter="\t", extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)
            archive.writestr(f"{table.lower()}.tsv", text.getvalue().encode("utf-8"))
        for name, value in (extra_members or {}).items():
            archive.writestr(name, value)
    return path
