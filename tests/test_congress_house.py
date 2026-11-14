"""Tests for House financial disclosure scraper (congress_house.py)."""

from __future__ import annotations

import io
import os
import zipfile
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import responses

from insider_scanner.core.congress_house import (
    INDEX_ZIP_URL,
    PTR_PDF_URL,
    _determine_years,
    _extract_ticker,
    _find_header_row,
    _map_columns,
    _normalize_owner,
    _normalize_tx_type,
    _parse_date_flexible,
    _parse_table_row,
    ensure_house_index,
    parse_house_index,
    scrape_house_trades,
    search_filings,
)
from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, FetchStatus
from insider_scanner.core.models import CongressTrade

SAMPLE_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


@pytest.fixture
def fixed_today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2027, 1, 2)

    monkeypatch.setattr("insider_scanner.core.congress_house.date", FixedDate)


# -----------------------------------------------------------------------
# Sample XML fixture (based on real 2026FD.xml structure)
# -----------------------------------------------------------------------

SAMPLE_INDEX_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<FinancialDisclosure>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Allen</Last>
    <First>Richard W.</First>
    <Suffix />
    <FilingType>P</FilingType>
    <StateDst>GA12</StateDst>
    <Year>2026</Year>
    <FilingDate>1/15/2026</FilingDate>
    <DocID>20033751</DocID>
  </Member>
  <Member>
    <Prefix />
    <Last>Amador</Last>
    <First>Jaliel</First>
    <Suffix />
    <FilingType>C</FilingType>
    <StateDst>NY13</StateDst>
    <Year>2026</Year>
    <FilingDate>1/30/2026</FilingDate>
    <DocID>10073252</DocID>
  </Member>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Beyer</Last>
    <First>Donald Sternoff</First>
    <Suffix>Jr</Suffix>
    <FilingType>P</FilingType>
    <StateDst>VA08</StateDst>
    <Year>2026</Year>
    <FilingDate>1/2/2026</FilingDate>
    <DocID>20033714</DocID>
  </Member>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Beyer</Last>
    <First>Donald Sternoff</First>
    <Suffix>Jr</Suffix>
    <FilingType>P</FilingType>
    <StateDst>VA08</StateDst>
    <Year>2026</Year>
    <FilingDate>1/31/2026</FilingDate>
    <DocID>20033903</DocID>
  </Member>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Pelosi</Last>
    <First>Nancy</First>
    <Suffix />
    <FilingType>P</FilingType>
    <StateDst>CA11</StateDst>
    <Year>2026</Year>
    <FilingDate>2/10/2026</FilingDate>
    <DocID>20034001</DocID>
  </Member>
  <Member>
    <Prefix>Hon.</Prefix>
    <Last>Biggs</Last>
    <First>Sheri</First>
    <Suffix />
    <FilingType>A</FilingType>
    <StateDst>SC03</StateDst>
    <Year>2026</Year>
    <FilingDate>1/14/2026</FilingDate>
    <DocID>20033800</DocID>
  </Member>
</FinancialDisclosure>
"""


def _make_sample_zip(xml_content: str = SAMPLE_INDEX_XML, year: int = 2026) -> bytes:
    """Create an in-memory ZIP file containing the XML and TXT index."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{year}FD.xml", xml_content)
        zf.writestr(f"{year}FD.txt", "Prefix\tLast\tFirst\tSuffix\tFilingType\n")
    return buf.getvalue()


# -----------------------------------------------------------------------
# Helper function tests
# -----------------------------------------------------------------------


class TestExtractTicker:
    def test_standard(self):
        assert _extract_ticker("Apple Inc (AAPL) [ST]") == "AAPL"

    def test_with_common_stock(self):
        assert _extract_ticker("NVIDIA Corporation - Common Stock (NVDA)") == "NVDA"

    def test_no_ticker(self):
        assert _extract_ticker("U.S. Treasury Bonds") == ""

    def test_multiple_parens(self):
        # Should find the first valid ticker
        assert _extract_ticker("Alphabet Inc Class A (GOOGL) [ST]") == "GOOGL"

    def test_single_letter(self):
        assert _extract_ticker("Ford Motor Company (F)") == "F"


class TestNormalizeTxType:
    def test_purchase(self):
        assert _normalize_tx_type("P") == "Purchase"
        assert _normalize_tx_type("purchase") == "Purchase"

    def test_sale(self):
        assert _normalize_tx_type("S") == "Sale"
        assert _normalize_tx_type("sale") == "Sale"
        assert _normalize_tx_type("sale (full)") == "Sale"
        assert _normalize_tx_type("sale (partial)") == "Sale"
        assert _normalize_tx_type("S (partial)") == "Sale"
        assert _normalize_tx_type("S (full)") == "Sale"

    def test_exchange(self):
        assert _normalize_tx_type("E") == "Exchange"

    def test_unknown(self):
        assert _normalize_tx_type("X") == "Other"
        assert _normalize_tx_type("") == "Other"


class TestNormalizeOwner:
    def test_self(self):
        assert _normalize_owner("") == "Self"

    def test_spouse(self):
        assert _normalize_owner("SP") == "Spouse"
        assert _normalize_owner("sp") == "Spouse"

    def test_dependent_child(self):
        assert _normalize_owner("DC") == "Dependent Child"

    def test_joint(self):
        assert _normalize_owner("JT") == "Joint"


class TestParseDateFlexible:
    def test_us_format(self):
        assert _parse_date_flexible("01/15/2026") == date(2026, 1, 15)

    def test_us_short_year(self):
        assert _parse_date_flexible("01/15/26") == date(2026, 1, 15)

    def test_iso_format(self):
        assert _parse_date_flexible("2026-01-15") == date(2026, 1, 15)

    def test_empty(self):
        assert _parse_date_flexible("") is None

    def test_dashes(self):
        assert _parse_date_flexible("--") is None

    def test_invalid(self):
        assert _parse_date_flexible("not a date") is None


class TestDetermineYears:
    def test_both_dates_same_year(self):
        assert _determine_years(date(2026, 1, 1), date(2026, 6, 30)) == [2026]

    def test_both_dates_span_years(self):
        assert _determine_years(date(2025, 6, 1), date(2026, 2, 1)) == [2025, 2026]

    def test_from_only(self, fixed_today):
        years = _determine_years(date(2025, 6, 1), None)
        assert years == [2025, 2026, 2027]

    def test_to_only(self):
        assert _determine_years(None, date(2026, 3, 1)) == [2026]

    def test_neither(self, fixed_today):
        assert _determine_years(None, None) == [2027]


# -----------------------------------------------------------------------
# XML index parsing
# -----------------------------------------------------------------------


class TestParseHouseIndex:
    def test_parse_sample(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            filings = parse_house_index(2026)

        assert len(filings) == 6

        # First filing: Allen
        allen = filings[0]
        assert allen["last"] == "Allen"
        assert allen["first"] == "Richard W."
        assert allen["prefix"] == "Hon."
        assert allen["filing_type"] == "P"
        assert allen["state_dst"] == "GA12"
        assert allen["filing_date"] == date(2026, 1, 15)
        assert allen["doc_id"] == "20033751"

    def test_parse_with_bom(self, tmp_path):
        """BOM-prefixed XML (as found in real data) should parse correctly."""
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_bytes(b"\xef\xbb\xbf" + SAMPLE_INDEX_XML.encode("utf-8"))

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            filings = parse_house_index(2026)

        assert len(filings) == 6

    def test_missing_file(self, tmp_path):
        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            filings = parse_house_index(2099)
        assert filings == []


class TestSearchFilings:
    def test_filter_by_type_p(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(2026, filing_type="P")

        # 4 PTR filings: Allen, Beyer x2, Pelosi (Amador is C, Biggs is A)
        assert len(results) == 4
        assert all(r["filing_type"] == "P" for r in results)

    def test_filter_by_name_last_first(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(2026, name="Beyer Donald")

        assert len(results) == 2
        assert all(r["last"] == "Beyer" for r in results)

    def test_filter_by_name_first_last(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(2026, name="Nancy Pelosi")

        assert len(results) == 1
        assert results[0]["last"] == "Pelosi"

    def test_filter_by_date_range(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(
                2026,
                filing_type="P",
                date_from=date(2026, 1, 10),
                date_to=date(2026, 1, 20),
            )

        # Allen (1/15) and Beyer (not 1/2 but not 1/31)
        assert len(results) == 1
        assert results[0]["last"] == "Allen"

    def test_filter_all_officials(self, tmp_path):
        """name=None should return all PTR filings."""
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(2026, name=None, filing_type="P")

        assert len(results) == 4

    def test_no_matches(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            results = search_filings(2026, name="Nonexistent Person")

        assert results == []


# -----------------------------------------------------------------------
# Index download (ensure_house_index)
# -----------------------------------------------------------------------


class TestEnsureHouseIndex:
    @responses.activate
    def test_download_and_extract(self, tmp_path):
        zip_bytes = _make_sample_zip()
        responses.add(
            responses.GET,
            INDEX_ZIP_URL.format(year=2026),
            body=zip_bytes,
            status=200,
        )

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            result = ensure_house_index(2026)

        assert result == tmp_path / "2026FD.xml"
        assert result.exists()
        assert (tmp_path / "2026FD.txt").exists()

    def test_skip_if_present(self, tmp_path):
        # Pre-create the file
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text("<FinancialDisclosure></FinancialDisclosure>")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            result = ensure_house_index(2026)

        assert result == xml_path
        # Should not have made any HTTP requests

    @responses.activate
    def test_force_redownload(self, tmp_path):
        # Pre-create the file
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text("<old/>")

        zip_bytes = _make_sample_zip()
        responses.add(
            responses.GET,
            INDEX_ZIP_URL.format(year=2026),
            body=zip_bytes,
            status=200,
        )

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            ensure_house_index(2026, force=True)

        # File should be replaced with new content
        content = xml_path.read_text()
        assert "Allen" in content

    @responses.activate
    def test_404_raises(self, tmp_path):
        responses.add(
            responses.GET,
            INDEX_ZIP_URL.format(year=2099),
            status=404,
        )

        import requests as req

        with (
            patch(
                "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
            ),
            pytest.raises(req.HTTPError),
        ):
            ensure_house_index(2099)


@pytest.mark.parametrize("year", [2026, 2027])
@responses.activate
def test_recent_house_index_expires_after_ttl(tmp_path, monkeypatch, fixed_today, year):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(house, "time", lambda: 1_000_000)
    cached = tmp_path / f"{year}FD.xml"
    cached.write_text("<FinancialDisclosure />")
    expired = 1_000_000 - house.DEFAULT_CACHE_TTL
    os.utime(cached, (expired, expired))
    responses.add(
        responses.GET,
        INDEX_ZIP_URL.format(year=year),
        body=_make_sample_zip(year=year),
    )

    assert ensure_house_index(year) == cached
    assert "Allen" in cached.read_text()
    assert len(responses.calls) == 1


@pytest.mark.parametrize(
    ("year", "age"), [(2025, 86400 * 365), (2026, 3599), (2027, 3599)]
)
def test_house_index_reuses_archived_or_fresh_cache(
    tmp_path, monkeypatch, fixed_today, year, age
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(house, "time", lambda: 40_000_000)
    cached = tmp_path / f"{year}FD.xml"
    cached.write_text("<FinancialDisclosure />")
    os.utime(cached, (40_000_000 - age, 40_000_000 - age))
    download = MagicMock(side_effect=AssertionError("cache should be reused"))
    monkeypatch.setattr(house, "_download_bytes", download)

    assert ensure_house_index(year) == cached
    download.assert_not_called()


@responses.activate
def test_recent_house_index_with_future_mtime_is_refetched(
    tmp_path, monkeypatch, fixed_today
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(house, "time", lambda: 1_000_000)
    cached = tmp_path / "2027FD.xml"
    cached.write_text("<FinancialDisclosure />")
    os.utime(cached, (1_000_001, 1_000_001))
    responses.add(
        responses.GET,
        INDEX_ZIP_URL.format(year=2027),
        body=_make_sample_zip(year=2027),
    )

    ensure_house_index(2027)
    assert "Allen" in cached.read_text()


# -----------------------------------------------------------------------
# PDF parsing helpers
# -----------------------------------------------------------------------


class TestTableParsing:
    def test_find_header_row(self):
        table = [
            ["", "Filing Information", "", ""],
            ["ID", "Owner", "Asset", "Transaction\nType", "Date", "Amount"],
            ["1", "SP", "Apple Inc (AAPL)", "P", "01/15/2026", "$1,001 - $15,000"],
        ]
        assert _find_header_row(table) == 1

    def test_find_header_row_not_found(self):
        table = [
            ["Name", "Address", "Phone"],
            ["John", "123 Main", "555-1234"],
        ]
        assert _find_header_row(table) is None

    def test_map_columns(self):
        headers = [
            "id",
            "owner",
            "asset",
            "transaction\ntype",
            "date",
            "notification\ndate",
            "amount",
            "cap.\ngains > $200?",
        ]
        col_map = _map_columns(headers)

        assert col_map["id"] == 0
        assert col_map["owner"] == 1
        assert col_map["asset"] == 2
        assert col_map["transaction"] == 3
        assert col_map["date"] == 4
        assert col_map["notification_date"] == 5
        assert col_map["amount"] == 6
        assert col_map["cap_gains"] == 7

    def test_parse_table_row(self):
        col_map = {
            "owner": 0,
            "asset": 1,
            "transaction": 2,
            "date": 3,
            "amount": 4,
        }
        row = ["SP", "Apple Inc (AAPL) [ST]", "P", "01/15/2026", "$1,001 - $15,000"]
        result = _parse_table_row(row, col_map)

        assert result is not None
        assert result["owner"] == "SP"
        assert result["asset"] == "Apple Inc (AAPL) [ST]"
        assert result["tx_type"] == "P"
        assert result["tx_date"] == "01/15/2026"
        assert result["amount"] == "$1,001 - $15,000"

    def test_parse_table_row_empty_asset(self):
        col_map = {"owner": 0, "asset": 1, "transaction": 2}
        row = ["SP", "", "P"]
        result = _parse_table_row(row, col_map)
        assert result is None


# -----------------------------------------------------------------------
# PDF parsing (mock pdfplumber)
# -----------------------------------------------------------------------


class TestParsePtrPdf:
    def test_electronic_pdf(self):
        """Mock pdfplumber to simulate an electronic PTR PDF."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "PERIODIC TRANSACTION REPORT\nFiling Information\nAsset details..."
        )
        mock_page.extract_tables.return_value = [
            [
                [
                    "ID",
                    "Owner",
                    "Asset",
                    "Transaction\nType",
                    "Date",
                    "Notification\nDate",
                    "Amount",
                    "Cap.\nGains > $200?",
                ],
                [
                    "1",
                    "SP",
                    "Apple Inc (AAPL) [ST]",
                    "P",
                    "01/15/2026",
                    "01/16/2026",
                    "$1,001 - $15,000",
                    "",
                ],
                [
                    "2",
                    "",
                    "NVIDIA Corporation (NVDA) [ST]",
                    "S",
                    "01/20/2026",
                    "01/21/2026",
                    "$50,001 - $100,000",
                    "Y",
                ],
                [
                    "3",
                    "JT",
                    "Microsoft Corp (MSFT)",
                    "P",
                    "01/25/2026",
                    "01/26/2026",
                    "$15,001 - $50,000",
                    "",
                ],
            ]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch(
            "insider_scanner.core.congress_house.pdfplumber", create=True
        ) as mock_plumber:
            # Need to patch the import inside the function
            import insider_scanner.core.congress_house as mod

            with patch.dict("sys.modules", {"pdfplumber": mock_plumber}):
                mock_plumber.open.return_value = mock_pdf
                transactions = mod.parse_ptr_pdf(b"fake pdf bytes")

        assert len(transactions) == 3
        assert [tx["source_row_id"] for tx in transactions] == ["1", "2", "3"]
        assert transactions[0]["asset"] == "Apple Inc (AAPL) [ST]"
        assert transactions[0]["owner"] == "SP"
        assert transactions[0]["tx_type"] == "P"
        assert transactions[1]["asset"] == "NVIDIA Corporation (NVDA) [ST]"
        assert transactions[1]["tx_type"] == "S"
        assert transactions[2]["owner"] == "JT"

    def test_scanned_pdf_skipped(self):
        """Scanned PDFs (very little text) should return empty."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""  # No text = scanned
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        import insider_scanner.core.congress_house as mod

        with patch.dict("sys.modules", {"pdfplumber": MagicMock()}) as _:
            import sys

            mock_plumber = sys.modules["pdfplumber"]
            mock_plumber.open.return_value = mock_pdf
            transactions = mod.parse_ptr_pdf(b"fake scanned pdf")

        assert transactions == []


# -----------------------------------------------------------------------
# Full pipeline (scrape_house_trades)
# -----------------------------------------------------------------------


class TestScrapeHouseTrades:
    @responses.activate
    def test_full_pipeline(self, tmp_path):
        """End-to-end: download index → search → fetch PDF → parse → CongressTrade."""
        # Setup: create index
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        # Mock PDF download
        responses.add(
            responses.GET,
            PTR_PDF_URL.format(year=2026, doc_id="20034001"),
            body=SAMPLE_PDF,
            status=200,
        )

        # Mock parse_ptr_pdf to return sample transactions
        mock_transactions = [
            {
                "owner": "",
                "asset": "Apple Inc (AAPL) [ST]",
                "tx_type": "P",
                "tx_date": "01/15/2026",
                "notification_date": "01/16/2026",
                "amount": "$50,001 - $100,000",
                "cap_gains_over_200": "",
                "source_row_id": "17",
            },
            {
                "owner": "SP",
                "asset": "Berkshire Hathaway (BRK.B) [ST]",
                "tx_type": "S (partial)",
                "tx_date": "01/15/2026",
                "amount": "Spouse/DC Over $1,000,000",
            },
        ]

        with (
            patch(
                "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
            ),
            patch(
                "insider_scanner.core.congress_house.parse_ptr_pdf",
                return_value=mock_transactions,
            ),
        ):
            trades = scrape_house_trades(
                official_name="Nancy Pelosi",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
            )

        assert len(trades) == 2
        t = trades[0]
        assert isinstance(t, CongressTrade)
        assert t.official_name == "Hon. Nancy Pelosi"
        assert t.chamber == "House"
        assert t.ticker == "AAPL"
        assert t.trade_type == "Purchase"
        assert t.owner == "Self"
        assert t.amount_range == "$50,001 - $100,000"
        assert t.amount_low == 50001.0
        assert t.amount_high == 100000.0
        assert t.source == "house"
        assert t.doc_id == "20034001"
        assert t.source_row_id == "17"
        assert "20034001" in t.source_url
        sale = trades[1]
        assert sale.ticker == "BRK.B"
        assert sale.trade_type == "Sale"
        assert sale.owner == "Spouse"
        assert sale.amount_range == "Spouse/DC Over $1,000,000"
        assert sale.amount_low == 1000000.0
        assert sale.amount_high == 1000000.0

    def test_no_matching_filings(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            trades = scrape_house_trades(
                official_name="Nobody Here",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
            )

        assert trades == []

    def test_progress_callback(self, tmp_path):
        xml_path = tmp_path / "2026FD.xml"
        xml_path.write_text(SAMPLE_INDEX_XML, encoding="utf-8")

        responses.start()
        responses.add(
            responses.GET,
            PTR_PDF_URL.format(year=2026, doc_id="20034001"),
            body=SAMPLE_PDF,
            status=200,
        )

        progress_calls = []

        with (
            patch(
                "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
            ),
            patch("insider_scanner.core.congress_house.parse_ptr_pdf", return_value=[]),
        ):
            scrape_house_trades(
                official_name="Nancy Pelosi",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
                progress_callback=lambda cur, tot, msg: progress_calls.append(
                    (cur, tot, msg)
                ),
            )

        responses.stop()
        responses.reset()

        # Should have at least the processing call and the "Done" call
        assert len(progress_calls) >= 2
        assert progress_calls[-1][2] == "Done"


# -----------------------------------------------------------------------
# PDF fetch + caching
# -----------------------------------------------------------------------


class TestFetchPtrPdf:
    @responses.activate
    def test_download_and_cache(self, tmp_path):
        responses.add(
            responses.GET,
            PTR_PDF_URL.format(year=2026, doc_id="12345"),
            body=SAMPLE_PDF,
            status=200,
        )

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            from insider_scanner.core.congress_house import fetch_ptr_pdf

            data = fetch_ptr_pdf("12345", 2026)

        assert data == SAMPLE_PDF

    def test_cache_hit(self, tmp_path):
        """Second call should use cache, not HTTP."""
        pdf_dir = tmp_path / "2026" / "pdfs"
        pdf_dir.mkdir(parents=True)
        cached = pdf_dir / "12345.pdf"
        cached.write_bytes(SAMPLE_PDF)

        with patch(
            "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
        ):
            from insider_scanner.core.congress_house import fetch_ptr_pdf

            data = fetch_ptr_pdf("12345", 2026)

        assert data == SAMPLE_PDF


@responses.activate
def test_invalid_cached_index_is_replaced(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
    )
    cached = tmp_path / "2026FD.xml"
    cached.write_bytes(b"<FinancialDisclosure><Member>")
    responses.add(
        responses.GET, INDEX_ZIP_URL.format(year=2026), body=_make_sample_zip()
    )

    assert ensure_house_index(2026) == cached
    assert "Pelosi" in cached.read_text()
    assert len(responses.calls) == 1


@responses.activate
def test_invalid_download_preserves_existing_house_index(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
    )
    cached = tmp_path / "2026FD.xml"
    cached.write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    responses.add(
        responses.GET, INDEX_ZIP_URL.format(year=2026), body=_make_sample_zip("<html/>")
    )

    with pytest.raises(ValueError, match="financial disclosure"):
        ensure_house_index(2026, force=True)

    assert cached.read_text(encoding="utf-8") == SAMPLE_INDEX_XML


@responses.activate
def test_house_zip_publishes_only_expected_members(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
    )
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("2026FD.xml", SAMPLE_INDEX_XML)
        archive.writestr("../outsideFD.xml", SAMPLE_INDEX_XML)
        archive.writestr("2025FD.xml", SAMPLE_INDEX_XML)
    responses.add(
        responses.GET, INDEX_ZIP_URL.format(year=2026), body=content.getvalue()
    )

    ensure_house_index(2026)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["2026FD.xml"]
    assert not (tmp_path.parent / "outsideFD.xml").exists()


@pytest.mark.parametrize(
    "bad_content", [b"<html>Try later</html>", b"%PDF-1.4 truncated"]
)
@responses.activate
def test_corrupt_cached_pdf_is_refetched_once(tmp_path, monkeypatch, bad_content):
    from insider_scanner.core.congress_house import fetch_ptr_pdf

    monkeypatch.setattr(
        "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
    )
    cached = tmp_path / "2026" / "pdfs" / "12345.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(bad_content)
    responses.add(
        responses.GET, PTR_PDF_URL.format(year=2026, doc_id="12345"), body=SAMPLE_PDF
    )

    assert fetch_ptr_pdf("12345", 2026) == SAMPLE_PDF
    assert cached.read_bytes() == SAMPLE_PDF
    assert len(responses.calls) == 1
    # A valid scanned/empty PDF remains a cache hit rather than an endless retry.
    assert fetch_ptr_pdf("12345", 2026) == SAMPLE_PDF
    assert len(responses.calls) == 1


@responses.activate
def test_html_pdf_response_is_not_cached(tmp_path, monkeypatch):
    from insider_scanner.core.congress_house import fetch_ptr_pdf

    monkeypatch.setattr(
        "insider_scanner.core.congress_house.HOUSE_DISCLOSURES_DIR", tmp_path
    )
    responses.add(
        responses.GET,
        PTR_PDF_URL.format(year=2026, doc_id="12345"),
        body=b"<html>blocked</html>",
    )

    with pytest.raises(ValueError, match="not a PDF"):
        fetch_ptr_pdf("12345", 2026)

    assert not (tmp_path / "2026" / "pdfs" / "12345.pdf").exists()


def test_house_scan_reuses_session_for_index_and_pdfs(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    http = MagicMock()
    response_index = MagicMock(content=_make_sample_zip())
    response_pdf = MagicMock(content=SAMPLE_PDF)
    http.get.side_effect = [response_index, response_pdf]
    with patch.object(house, "parse_ptr_pdf", return_value=[]):
        assert (
            house.scrape_house_trades(
                official_name="Nancy Pelosi",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
                session=http,
            )
            == []
        )

    assert http.get.call_count == 2
    http.close.assert_not_called()
    response_index.close.assert_called_once()
    response_pdf.close.assert_called_once()


def test_house_owned_session_closes_when_download_fails(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    http = MagicMock()
    http.__enter__.return_value = http
    http.get.side_effect = OSError("offline")
    with patch.object(house, "_house_session", return_value=http):
        assert (
            house.scrape_house_trades(
                date_from=date(2026, 1, 1), date_to=date(2026, 12, 31)
            )
            == []
        )
    http.__exit__.assert_called_once()


@responses.activate
def test_house_refetches_cached_pdf_once_on_extraction_failure(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    cached = tmp_path / "2026" / "pdfs" / "20034001.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(SAMPLE_PDF)
    responses.add(
        responses.GET, PTR_PDF_URL.format(year=2026, doc_id="20034001"), body=SAMPLE_PDF
    )
    with patch.object(
        house, "parse_ptr_pdf", side_effect=[ValueError("bad stream"), []]
    ) as parser:
        assert (
            house.scrape_house_trades(
                official_name="Nancy Pelosi",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
            )
            == []
        )
    assert parser.call_count == 2
    assert len(responses.calls) == 1


def test_pdf_extraction_error_can_be_distinguished_from_empty_result():
    from insider_scanner.core.congress_house import parse_ptr_pdf

    assert parse_ptr_pdf(b"not a PDF") == []
    with pytest.raises(ValueError, match="transaction extraction failed"):
        parse_ptr_pdf(b"not a PDF", strict=True)
    assert parse_ptr_pdf(SAMPLE_PDF, strict=True) == []


@pytest.mark.parametrize("cached_bytes", [b"not a PDF", b"%PDF-1.4 truncated"])
@responses.activate
def test_house_corrupt_cache_repair_does_not_download_again_on_extraction_error(
    tmp_path, monkeypatch, cached_bytes
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    cached = tmp_path / "2026" / "pdfs" / "20034001.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(cached_bytes)
    responses.add(
        responses.GET, PTR_PDF_URL.format(year=2026, doc_id="20034001"), body=SAMPLE_PDF
    )
    with patch.object(
        house, "parse_ptr_pdf", side_effect=ValueError("bad stream")
    ) as parser:
        assert (
            house.scrape_house_trades(
                official_name="Nancy Pelosi",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 12, 31),
            )
            == []
        )
    assert parser.call_count == 1
    assert len(responses.calls) == 1
    assert cached.read_bytes() == SAMPLE_PDF


def test_house_pdf_preserves_other_pages_when_one_page_fails():
    from insider_scanner.core.congress_house import parse_ptr_pdf

    first_page, damaged_page, last_page = MagicMock(), MagicMock(), MagicMock()
    for page, ticker in [(first_page, "AAPL"), (last_page, "MSFT")]:
        page.extract_text.return_value = "Electronic Periodic Transaction Report " * 3
        page.extract_tables.return_value = [
            [
                ["Asset", "Transaction Type", "Date", "Amount"],
                [f"Example ({ticker})", "P", "01/15/2026", "$1,001 - $15,000"],
            ]
        ]
    damaged_page.extract_text.side_effect = ValueError("damaged text layer")
    damaged_page.extract_tables.side_effect = ValueError("damaged table")
    pdf = MagicMock()
    pdf.pages = [first_page, damaged_page, last_page]
    pdf.__enter__.return_value = pdf
    with patch("pdfplumber.open", return_value=pdf):
        result = parse_ptr_pdf(b"pdf", strict=True, doc_id="12345")

    assert [row["asset"] for row in result] == ["Example (AAPL)", "Example (MSFT)"]
    assert result.status == FetchStatus.PARTIAL
    assert result.issues == (
        FetchIssue("pdf_page_parse_failed", source="house", identifier="12345:page:2"),
    )


def test_house_scanned_pdf_is_explicitly_unsupported():
    from insider_scanner.core.congress_house import parse_ptr_pdf

    result = parse_ptr_pdf(SAMPLE_PDF, doc_id="20034001")

    assert result == []
    assert result.status == FetchStatus.UNSUPPORTED
    assert not result.complete
    assert not result.issues[0].retryable
    assert result.issues[0].identifier == "20034001"


def test_house_electronic_pdf_with_missing_table_is_not_empty_success():
    from insider_scanner.core.congress_house import parse_ptr_pdf

    page = MagicMock()
    page.extract_text.return_value = "Unexpected electronic report content " * 3
    page.extract_tables.return_value = []
    pdf = MagicMock()
    pdf.pages = [page]
    pdf.__enter__.return_value = pdf
    with patch("pdfplumber.open", return_value=pdf):
        result = parse_ptr_pdf(b"pdf", doc_id="12345")

    assert result == []
    assert result.status == FetchStatus.TRANSIENT_FAILURE
    assert result.issues[0].reason == "transaction_table_missing"


def test_house_failed_year_is_not_reported_as_empty_success(monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(
        house, "ensure_house_index", MagicMock(side_effect=OSError("offline"))
    )
    result = house.scrape_house_trades(
        date_from=date(2026, 1, 1), date_to=date(2026, 12, 31), session=MagicMock()
    )

    assert result == []
    assert result.status == FetchStatus.TRANSIENT_FAILURE
    assert result.issues == (
        FetchIssue("index_failed", source="house", identifier="2026"),
    )


def test_house_partial_pdf_rows_reach_scan_result(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    monkeypatch.setattr(house, "_read_cached_pdf", MagicMock(return_value=None))
    monkeypatch.setattr(house, "fetch_ptr_pdf", MagicMock(return_value=SAMPLE_PDF))
    issue = FetchIssue(
        "pdf_page_parse_failed", source="house", identifier="20034001:page:2"
    )
    monkeypatch.setattr(
        house,
        "parse_ptr_pdf",
        MagicMock(
            return_value=FetchResult(
                [
                    {
                        "asset": "Apple (AAPL)",
                        "tx_type": "P",
                        "tx_date": "01/15/2026",
                        "amount": "$1,001 - $15,000",
                    }
                ],
                issues=[issue],
            )
        ),
    )
    result = house.scrape_house_trades(
        official_name="Nancy Pelosi",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        session=MagicMock(),
    )

    assert [trade.ticker for trade in result] == ["AAPL"]
    assert result.status == FetchStatus.PARTIAL
    assert result.issues == (issue,)


@responses.activate
def test_house_pdf_cache_write_is_best_effort(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(
        house, "atomic_write_bytes", MagicMock(side_effect=PermissionError("blocked"))
    )
    responses.add(
        responses.GET, PTR_PDF_URL.format(year=2026, doc_id="12345"), body=SAMPLE_PDF
    )

    assert house.fetch_ptr_pdf("12345", 2026) == SAMPLE_PDF
    assert not (tmp_path / "2026" / "pdfs" / "12345.pdf").exists()


def test_house_missing_index_retains_failure_metadata_through_search(
    tmp_path, monkeypatch
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    result = house.search_filings(2026)

    assert result == []
    assert result.status == FetchStatus.TRANSIENT_FAILURE
    assert result.issues[0].reason == "index_missing"


@pytest.mark.parametrize(
    "reason", ["pdf_page_parse_failed", "transaction_table_missing"]
)
def test_house_repairs_cached_pdf_once_for_retryable_parse_outcome(
    tmp_path, monkeypatch, reason
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    monkeypatch.setattr(house, "_read_cached_pdf", MagicMock(return_value=SAMPLE_PDF))
    fetch = MagicMock(return_value=SAMPLE_PDF)
    monkeypatch.setattr(house, "fetch_ptr_pdf", fetch)
    transaction = {
        "asset": "Apple (AAPL)",
        "tx_type": "P",
        "tx_date": "01/15/2026",
        "amount": "$1,001 - $15,000",
    }
    parser = MagicMock(
        side_effect=[
            FetchResult(
                issues=[FetchIssue(reason, source="house", identifier="20034001")]
            ),
            FetchResult([transaction]),
        ]
    )
    monkeypatch.setattr(house, "parse_ptr_pdf", parser)

    result = house.scrape_house_trades(
        official_name="Nancy Pelosi",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        session=MagicMock(),
    )

    assert [trade.ticker for trade in result] == ["AAPL"]
    assert result.complete
    assert result.issues == ()
    assert parser.call_count == 2
    fetch.assert_called_once()


@pytest.mark.parametrize("repair_stage", ["download", "parse"])
def test_house_failed_cache_repair_retains_partial_transactions(
    tmp_path, monkeypatch, repair_stage
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    monkeypatch.setattr(house, "_read_cached_pdf", MagicMock(return_value=SAMPLE_PDF))
    fetch = MagicMock(return_value=SAMPLE_PDF)
    if repair_stage == "download":
        fetch.side_effect = OSError("offline")
    monkeypatch.setattr(house, "fetch_ptr_pdf", fetch)
    issue = FetchIssue(
        "pdf_page_parse_failed", source="house", identifier="20034001:page:2"
    )
    parser = MagicMock(
        return_value=FetchResult(
            [
                {
                    "asset": "Apple (AAPL)",
                    "tx_type": "P",
                    "tx_date": "01/15/2026",
                    "amount": "$1,001 - $15,000",
                }
            ],
            issues=[issue],
        )
    )
    if repair_stage == "parse":
        parser.side_effect = [parser.return_value, ValueError("invalid replacement")]
    monkeypatch.setattr(house, "parse_ptr_pdf", parser)

    result = house.scrape_house_trades(
        official_name="Nancy Pelosi",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        session=MagicMock(),
    )

    assert [trade.ticker for trade in result] == ["AAPL"]
    assert result.status == FetchStatus.PARTIAL
    assert result.issues == (
        issue,
        FetchIssue("pdf_repair_failed", source="house", identifier="20034001"),
    )
    assert parser.call_count == (1 if repair_stage == "download" else 2)
    fetch.assert_called_once()


def test_house_does_not_repair_deterministically_unsupported_cached_pdf(
    tmp_path, monkeypatch
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    monkeypatch.setattr(house, "_read_cached_pdf", MagicMock(return_value=SAMPLE_PDF))
    fetch = MagicMock()
    monkeypatch.setattr(house, "fetch_ptr_pdf", fetch)

    result = house.scrape_house_trades(
        official_name="Nancy Pelosi",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        session=MagicMock(),
    )

    assert result.status == FetchStatus.UNSUPPORTED
    assert result.issues[0].reason == "scanned_pdf"
    fetch.assert_not_called()


def test_house_no_cache_refreshes_indexes_and_pdfs(tmp_path, monkeypatch):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    (tmp_path / "2026FD.xml").write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    cached_pdf = tmp_path / "2026" / "pdfs" / "20034001.pdf"
    cached_pdf.parent.mkdir(parents=True)
    cached_pdf.write_bytes(SAMPLE_PDF)
    cached_reader = MagicMock(side_effect=AssertionError("cache read during refresh"))
    monkeypatch.setattr(house, "_read_cached_pdf", cached_reader)
    parser = MagicMock(return_value=FetchResult())
    monkeypatch.setattr(house, "parse_ptr_pdf", parser)
    http = MagicMock()
    http.get.side_effect = [
        MagicMock(content=_make_sample_zip()),
        MagicMock(content=SAMPLE_PDF),
    ]

    result = house.scrape_house_trades(
        official_name="Nancy Pelosi",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        use_cache=False,
        session=http,
    )

    assert result.complete
    assert [call.args[0] for call in http.get.call_args_list] == [
        INDEX_ZIP_URL.format(year=2026),
        PTR_PDF_URL.format(year=2026, doc_id="20034001"),
    ]
    cached_reader.assert_not_called()
    parser.assert_called_once_with(SAMPLE_PDF, strict=True, doc_id="20034001")


def test_house_expired_index_refresh_failure_is_incomplete(
    tmp_path, monkeypatch, fixed_today
):
    from insider_scanner.core import congress_house as house

    monkeypatch.setattr(house, "HOUSE_DISCLOSURES_DIR", tmp_path)
    monkeypatch.setattr(house, "time", lambda: 1_000_000)
    cached = tmp_path / "2026FD.xml"
    cached.write_text(SAMPLE_INDEX_XML, encoding="utf-8")
    expired = 1_000_000 - house.DEFAULT_CACHE_TTL
    os.utime(cached, (expired, expired))
    http = MagicMock()
    http.get.side_effect = OSError("offline")

    result = house.scrape_house_trades(
        date_from=date(2026, 1, 1), date_to=date(2026, 12, 31), session=http
    )

    assert not result.complete
    assert result.issues == (
        FetchIssue("index_failed", source="house", identifier="2026"),
    )
    assert cached.read_text(encoding="utf-8") == SAMPLE_INDEX_XML


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Lee", ["Lee"]),
        ("Nancy Pelosi", ["Pelosi"]),
        ('Eric A. "Rick" Crawford', ["Crawford"]),
        ("", []),
    ],
)
def test_house_name_filter_uses_whole_structured_names(query, expected):
    filings = [
        {"first": first, "last": last, "filing_type": "P"}
        for first, last in [
            ("", ""),
            ("Barbara", "Lee"),
            ("Teresa", "Leger"),
            ("John", "Fleeson"),
            ("Nancy", "Pelosi"),
            ("Eric", "Crawford"),
        ]
    ]
    with patch(
        "insider_scanner.core.congress_house.parse_house_index", return_value=filings
    ):
        result = search_filings(2026, name=query)
    assert [filing["last"] for filing in result] == expected
