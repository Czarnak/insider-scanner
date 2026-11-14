"""Filing-local row identities survive repeated lots and partial reparses."""

from unittest.mock import MagicMock, patch

import pytest

from insider_scanner.core.congress_house import parse_ptr_pdf
from insider_scanner.core.congress_senate import parse_ptr_page
from insider_scanner.core.fetch_outcome import FetchStatus
from insider_scanner.core.models import CongressTrade


def _house_page(*rows):
    page = MagicMock()
    page.extract_text.return_value = "Electronic Periodic Transaction Report " * 3
    page.extract_tables.return_value = [
        [["Asset", "Transaction Type", "Date", "Amount"], *rows]
    ]
    return page


def test_house_repeated_lots_without_ids_use_distinct_physical_positions():
    row = ["Example (AAPL)", "P", "01/15/2026", "$1,001 - $15,000"]
    first_page = _house_page(row, row)
    failed_page = _house_page(row)
    failed_page.extract_tables.side_effect = ValueError("damaged page")
    last_page = _house_page(row)
    # A second transaction table on the same page must also remain distinct.
    last_page.extract_tables.return_value.append(
        [["Asset", "Transaction Type", "Date", "Amount"], row]
    )
    pdf = MagicMock()
    pdf.pages = [first_page, failed_page, last_page]
    pdf.__enter__.return_value = pdf

    with patch("pdfplumber.open", return_value=pdf):
        partial = parse_ptr_pdf(b"pdf", strict=True)
        failed_page.extract_tables.side_effect = None
        complete = parse_ptr_pdf(b"pdf", strict=True)

    assert partial.status is FetchStatus.PARTIAL
    assert complete.complete
    assert [tx["source_row_id"] for tx in partial] == [
        "page:1:table:1:row:2",
        "page:1:table:1:row:3",
        "page:3:table:1:row:2",
        "page:3:table:2:row:2",
    ]
    assert [tx["source_row_id"] for tx in complete] == [
        "page:1:table:1:row:2",
        "page:1:table:1:row:3",
        "page:2:table:1:row:2",
        "page:3:table:1:row:2",
        "page:3:table:2:row:2",
    ]


@pytest.mark.parametrize("include_id", [False, True])
def test_senate_repeated_lots_have_stable_ids_when_an_earlier_row_is_repaired(
    include_id,
):
    headers = "<th>#</th>" if include_id else ""
    headers += (
        "<th>Transaction Date</th><th>Asset Name</th><th>Type</th><th>Amount</th>"
    )

    def row(asset, ordinal):
        source_id = f"<td>{ordinal}</td>" if include_id else ""
        return (
            f"<tr>{source_id}<td>01/15/2026</td><td>{asset}</td>"
            "<td>Purchase</td><td>$1,001 - $15,000</td></tr>"
        )

    def document(first_asset):
        return (
            f"<table><thead><tr>{headers}</tr></thead><tbody>"
            f"{row(first_asset, 7)}{row('Example (AAPL)', 8)}"
            f"{row('Example (AAPL)', 9)}</tbody></table>"
        )

    partial = parse_ptr_page(document(""))
    complete = parse_ptr_page(document("Example (AAPL)"))

    assert partial.status is FetchStatus.PARTIAL
    assert complete.complete
    assert [tx["source_row_id"] for tx in partial] == (
        ["8", "9"] if include_id else ["row:2", "row:3"]
    )
    assert [tx["source_row_id"] for tx in complete][1:] == [
        tx["source_row_id"] for tx in partial
    ]
    assert len({tx["source_row_id"] for tx in complete}) == 3


def test_congress_row_id_roundtrips_and_legacy_exports_keep_empty_identity():
    trade = CongressTrade(doc_id="filing", source_row_id="row:7", source="senate")

    assert CongressTrade.from_dict(trade.to_dict()) == trade
    payload = trade.to_dict()
    del payload["source_row_id"]
    assert CongressTrade.from_dict(payload).source_row_id == ""
