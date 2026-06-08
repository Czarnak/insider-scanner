"""Tests for Senate EFD financial disclosure scraper (congress_senate.py)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import requests
import responses

from insider_scanner.core.congress_senate import (
    BASE_URL,
    EFDSession,
    REPORT_DATA,
    SEARCH_HOME,
    SEARCH_LANDING,
    _extract_ticker,
    _map_senate_columns,
    _normalize_tx_type,
    _parse_date,
    _split_name,
    parse_ptr_page,
    parse_search_results,
    scrape_senate_trades,
    search_senate_filings,
)
from insider_scanner.core.models import CongressTrade


# -----------------------------------------------------------------------
# Sample HTML fixtures
# -----------------------------------------------------------------------

LANDING_HTML = """
<html>
<body>
<form method="post">
<input type="hidden" name="csrfmiddlewaretoken" value="test-csrf-token-abc123">
<input type="checkbox" name="prohibition_agreement" value="1">
<button type="submit">I understand</button>
</form>
</body>
</html>
"""

SEARCH_FORM_HTML = """
<html><body>
<form>
<input name="first_name">
<input name="last_name">
<input name="filer_type">
<input name="filer_type">
<input name="filer_type">
<input name="report_type">
<input name="submitted_start_date">
<input name="submitted_end_date">
<input name="candidate_state">
<input name="senator_state">
<input name="csrfmiddlewaretoken">
<input name="report_types">
<input name="filter_types">
<input name="action">
</form>
</body></html>
"""

SEARCH_RESPONSE_JSON = {
    "result": "ok",
    "recordsTotal": 3,
    "recordsFiltered": 3,
    "data": [
        [
            "Tommy",
            "Tuberville",
            "Senator",
            '<a href="/search/view/ptr/7718a62c-639e-4cf9-bcb8-00eb421ac444/" target="_blank">Periodic Transaction Report for 09/01/2022</a>',
            "09/15/2022",
        ],
        [
            "Tommy",
            "Tuberville",
            "Senator",
            '<a href="/search/view/ptr/abc12345-def6-7890-abcd-ef1234567890/" target="_blank">Periodic Transaction Report for 06/01/2022</a>',
            "06/15/2022",
        ],
        [
            "Tommy",
            "Tuberville",
            "Senator",
            '<a href="/search/view/paper/aaaa1111-bbbb-2222-cccc-333344445555/" target="_blank">Periodic Transaction Report (Paper) for 01/01/2022</a>',
            "01/20/2022",
        ],
    ],
}

PTR_PAGE_HTML = """
<html>
<body>
<h1>Periodic Transaction Report</h1>
<div class="table-responsive">
<table class="table">
<thead>
<tr>
<th>#</th>
<th>Transaction Date</th>
<th>Owner</th>
<th>Ticker</th>
<th>Asset Name</th>
<th>Asset Type</th>
<th>Type</th>
<th>Amount</th>
<th>Comment</th>
</tr>
</thead>
<tbody>
<tr>
<td>1</td>
<td>09/01/2022</td>
<td>Self</td>
<td>AAPL</td>
<td>Apple Inc - Common Stock</td>
<td>Stock</td>
<td>Purchase</td>
<td>$15,001 - $50,000</td>
<td></td>
</tr>
<tr>
<td>2</td>
<td>09/02/2022</td>
<td>Spouse</td>
<td>MSFT</td>
<td>Microsoft Corp - Common Stock</td>
<td>Stock</td>
<td>Sale (Full)</td>
<td>$50,001 - $100,000</td>
<td>Sold to rebalance portfolio</td>
</tr>
<tr>
<td>3</td>
<td>09/05/2022</td>
<td>Self</td>
<td>--</td>
<td>Vanguard Total Bond Market ETF (BND)</td>
<td>Other Securities</td>
<td>Purchase</td>
<td>$1,001 - $15,000</td>
<td></td>
</tr>
</tbody>
</table>
</div>
</body>
</html>
"""

PTR_PAGE_NO_TABLE_HTML = """
<html><body><h1>Report</h1><p>No transactions to display.</p></body></html>
"""


# -----------------------------------------------------------------------
# Helper function tests
# -----------------------------------------------------------------------


class TestSplitName:
    def test_first_last(self):
        assert _split_name("Nancy Pelosi") == ("Nancy", "Pelosi")

    def test_comma_format(self):
        assert _split_name("Booker, Cory") == ("Cory", "Booker")

    def test_middle_name(self):
        first, last = _split_name("John D. Booker")
        assert first == "John"
        assert last == "D. Booker"

    def test_single_name(self):
        assert _split_name("Pelosi") == ("", "Pelosi")

    def test_whitespace(self):
        assert _split_name("  Nancy   Pelosi  ") == ("Nancy", "Pelosi")


class TestNormalizeTxType:
    def test_purchase(self):
        assert _normalize_tx_type("Purchase") == "Purchase"
        assert _normalize_tx_type("purchase") == "Purchase"

    def test_sale(self):
        assert _normalize_tx_type("Sale") == "Sale"
        assert _normalize_tx_type("Sale (Full)") == "Sale"
        assert _normalize_tx_type("Sale (Partial)") == "Sale"

    def test_exchange(self):
        assert _normalize_tx_type("Exchange") == "Exchange"

    def test_unknown(self):
        assert _normalize_tx_type("Unknown") == "Other"
        assert _normalize_tx_type("") == "Other"


class TestExtractTicker:
    def test_standard(self):
        assert _extract_ticker("Vanguard Total Bond Market ETF (BND)") == "BND"

    def test_no_ticker(self):
        assert _extract_ticker("U.S. Treasury Bonds") == ""


class TestParseDate:
    def test_us_format(self):
        assert _parse_date("09/15/2022") == date(2022, 9, 15)

    def test_iso_format(self):
        assert _parse_date("2022-09-15") == date(2022, 9, 15)

    def test_empty(self):
        assert _parse_date("") is None

    def test_dashes(self):
        assert _parse_date("--") is None


# -----------------------------------------------------------------------
# Search result parsing
# -----------------------------------------------------------------------


class TestParseSearchResults:
    def test_parse_standard(self):
        results = parse_search_results(SEARCH_RESPONSE_JSON)
        assert len(results) == 3

        # First result: electronic PTR
        r = results[0]
        assert r["first_name"] == "Tommy"
        assert r["last_name"] == "Tuberville"
        assert r["filer_type"] == "Senator"
        assert r["report_uuid"] == "7718a62c-639e-4cf9-bcb8-00eb421ac444"
        assert r["filing_date"] == date(2022, 9, 15)
        assert r["is_paper"] is False

    def test_paper_filing_detected(self):
        results = parse_search_results(SEARCH_RESPONSE_JSON)
        # Third result is a paper filing
        assert results[2]["is_paper"] is True
        assert results[2]["report_uuid"] == ""

    def test_empty_response(self):
        results = parse_search_results({"result": "ok", "data": []})
        assert results == []

    def test_missing_data_key(self):
        results = parse_search_results({"result": "ok"})
        assert results == []

    def test_short_row_skipped(self):
        data = {"data": [["too", "short"]]}
        results = parse_search_results(data)
        assert results == []


# -----------------------------------------------------------------------
# PTR page parsing
# -----------------------------------------------------------------------


class TestParsePtrPage:
    def test_parse_standard_table(self):
        transactions = parse_ptr_page(PTR_PAGE_HTML)
        assert len(transactions) == 3

        # First: AAPL purchase
        tx1 = transactions[0]
        assert tx1["ticker"] == "AAPL"
        assert tx1["asset_name"] == "Apple Inc - Common Stock"
        assert tx1["tx_type"] == "Purchase"
        assert tx1["owner"] == "Self"
        assert tx1["amount"] == "$15,001 - $50,000"
        assert tx1["tx_date"] == "09/01/2022"

        # Second: MSFT sale by spouse
        tx2 = transactions[1]
        assert tx2["ticker"] == "MSFT"
        assert tx2["tx_type"] == "Sale (Full)"
        assert tx2["owner"] == "Spouse"
        assert tx2["comment"] == "Sold to rebalance portfolio"

        # Third: BND with -- ticker
        tx3 = transactions[2]
        assert tx3["ticker"] == "--"
        assert tx3["asset_name"] == "Vanguard Total Bond Market ETF (BND)"

    def test_no_table(self):
        transactions = parse_ptr_page(PTR_PAGE_NO_TABLE_HTML)
        assert transactions == []

    def test_empty_html(self):
        transactions = parse_ptr_page("")
        assert transactions == []


class TestMapSenateColumns:
    def test_standard_headers(self):
        headers = [
            "#",
            "transaction date",
            "owner",
            "ticker",
            "asset name",
            "asset type",
            "type",
            "amount",
            "comment",
        ]
        col_map = _map_senate_columns(headers)
        assert col_map["id"] == 0
        assert col_map["tx_date"] == 1
        assert col_map["owner"] == 2
        assert col_map["ticker"] == 3
        assert col_map["asset_name"] == 4
        assert col_map["asset_type"] == 5
        assert col_map["type"] == 6
        assert col_map["amount"] == 7
        assert col_map["comment"] == 8


# -----------------------------------------------------------------------
# EFD Session
# -----------------------------------------------------------------------


class TestEFDSession:
    @responses.activate
    def test_authenticate(self):
        responses.add(
            responses.GET,
            SEARCH_LANDING,
            body=LANDING_HTML,
            status=200,
        )
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=new-csrf-token-xyz; Path=/"},
        )

        session = EFDSession()
        assert not session.is_authenticated

        session.authenticate()
        assert session.is_authenticated

    @responses.activate
    @patch("insider_scanner.core.congress_senate._create_browser_session")
    def test_authenticate_retries_403_with_browser_session(
        self, create_browser_session
    ):
        responses.add(
            responses.GET,
            SEARCH_LANDING,
            body="Access Denied",
            status=403,
        )

        landing_response = MagicMock(status_code=200, text=LANDING_HTML)
        agreement_response = MagicMock(status_code=200, text=SEARCH_FORM_HTML)
        browser_session = MagicMock()
        browser_session.get.return_value = landing_response
        browser_session.post.return_value = agreement_response
        browser_session.cookies.get_dict.return_value = {
            "csrftoken": "browser-csrf-token"
        }
        create_browser_session.return_value = browser_session

        session = EFDSession()
        session.authenticate()

        create_browser_session.assert_called_once()
        browser_session.get.assert_called_once_with(SEARCH_LANDING, timeout=15)
        assert session.is_authenticated

    @responses.activate
    def test_authenticate_no_csrf(self):
        responses.add(
            responses.GET,
            SEARCH_LANDING,
            body="<html><body>No form here</body></html>",
            status=200,
        )

        session = EFDSession()
        try:
            session.authenticate()
            assert False, "Should have raised"
        except ConnectionError as e:
            assert "CSRF" in str(e)

    @responses.activate
    def test_search(self):
        # Pre-authenticate
        responses.add(responses.GET, SEARCH_LANDING, body=LANDING_HTML, status=200)
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=tok; Path=/"},
        )
        responses.add(
            responses.POST,
            REPORT_DATA,
            json=SEARCH_RESPONSE_JSON,
            status=200,
        )

        session = EFDSession()
        session.authenticate()

        data = session.search(last_name="Tuberville")
        assert data["result"] == "ok"
        assert data["recordsTotal"] == 3

    def test_search_not_authenticated(self):
        session = EFDSession()
        try:
            session.search(last_name="test")
            assert False, "Should have raised"
        except ConnectionError as e:
            assert "not authenticated" in str(e)

    @responses.activate
    def test_fetch_page(self):
        responses.add(responses.GET, SEARCH_LANDING, body=LANDING_HTML, status=200)
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=tok; Path=/"},
        )
        responses.add(
            responses.GET,
            BASE_URL + "/search/view/ptr/abc123/",
            body=PTR_PAGE_HTML,
            status=200,
        )

        session = EFDSession()
        session.authenticate()

        html = session.fetch_page("/search/view/ptr/abc123/")
        assert "Apple Inc" in html


# -----------------------------------------------------------------------
# Integrated search function
# -----------------------------------------------------------------------


class TestSearchSenateFilings:
    @responses.activate
    def test_filters_paper_filings(self):
        # Authenticate
        responses.add(responses.GET, SEARCH_LANDING, body=LANDING_HTML, status=200)
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=tok; Path=/"},
        )
        responses.add(
            responses.POST,
            REPORT_DATA,
            json=SEARCH_RESPONSE_JSON,
            status=200,
        )

        session = EFDSession()
        session.authenticate()

        results = search_senate_filings(
            session,
            last_name="Tuberville",
        )

        # 3 results total, 1 paper → 2 electronic
        assert len(results) == 2
        assert all(not r["is_paper"] for r in results)

    @responses.activate
    def test_empty_results(self):
        responses.add(responses.GET, SEARCH_LANDING, body=LANDING_HTML, status=200)
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=tok; Path=/"},
        )
        responses.add(
            responses.POST,
            REPORT_DATA,
            json={"result": "ok", "recordsTotal": 0, "recordsFiltered": 0, "data": []},
            status=200,
        )

        session = EFDSession()
        session.authenticate()

        results = search_senate_filings(session, last_name="Nobody")
        assert results == []


# -----------------------------------------------------------------------
# Full pipeline
# -----------------------------------------------------------------------


class TestScrapeSentateTrades:
    def test_full_pipeline(self):
        """Test scrape_senate_trades with fully mocked session."""
        mock_session = MagicMock(spec=EFDSession)
        mock_session.is_authenticated = True
        mock_session._authenticated = True

        # Mock search
        mock_session.search.return_value = SEARCH_RESPONSE_JSON

        # Mock PTR page fetch
        mock_session.fetch_page.return_value = PTR_PAGE_HTML

        with patch(
            "insider_scanner.core.congress_senate.search_senate_filings"
        ) as mock_search:
            mock_search.return_value = [
                {
                    "first_name": "Tommy",
                    "last_name": "Tuberville",
                    "filer_type": "Senator",
                    "report_title": "PTR for 09/01/2022",
                    "report_url": "/search/view/ptr/7718a62c-639e-4cf9-bcb8-00eb421ac444/",
                    "report_uuid": "7718a62c-639e-4cf9-bcb8-00eb421ac444",
                    "filing_date": date(2022, 9, 15),
                    "is_paper": False,
                },
            ]

            trades = scrape_senate_trades(
                official_name="Tommy Tuberville",
                session=mock_session,
            )

        assert len(trades) == 3

        t1 = trades[0]
        assert isinstance(t1, CongressTrade)
        assert t1.official_name == "Tommy Tuberville"
        assert t1.chamber == "Senate"
        assert t1.ticker == "AAPL"
        assert t1.trade_type == "Purchase"
        assert t1.amount_range == "$15,001 - $50,000"
        assert t1.amount_low == 15001.0
        assert t1.amount_high == 50000.0
        assert t1.source == "senate"

        t2 = trades[1]
        assert t2.ticker == "MSFT"
        assert t2.trade_type == "Sale"
        assert t2.owner == "Spouse"

        # Third: ticker was "--", should extract BND from asset_name
        t3 = trades[2]
        assert t3.ticker == "BND"

    def test_no_filings(self):
        mock_session = MagicMock(spec=EFDSession)
        mock_session.is_authenticated = True
        mock_session._authenticated = True

        with patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
            return_value=[],
        ):
            trades = scrape_senate_trades(
                official_name="Nobody Here",
                session=mock_session,
            )

        assert trades == []

    def test_progress_callback(self):
        mock_session = MagicMock(spec=EFDSession)
        mock_session.is_authenticated = True
        mock_session._authenticated = True
        mock_session.fetch_page.return_value = PTR_PAGE_HTML

        progress_calls = []

        with patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
        ) as mock_search:
            mock_search.return_value = [
                {
                    "first_name": "Tommy",
                    "last_name": "Tuberville",
                    "filer_type": "Senator",
                    "report_title": "PTR",
                    "report_url": "/search/view/ptr/abc/",
                    "report_uuid": "abc",
                    "filing_date": date(2022, 9, 15),
                    "is_paper": False,
                },
            ]

            scrape_senate_trades(
                official_name="Tommy Tuberville",
                session=mock_session,
                progress_callback=lambda c, t, m: progress_calls.append((c, t, m)),
            )

        assert len(progress_calls) >= 2
        assert progress_calls[-1][2] == "Done"

    def test_name_splitting_for_search(self):
        """Verify official_name gets split correctly for the search."""
        mock_session = MagicMock(spec=EFDSession)
        mock_session.is_authenticated = True
        mock_session._authenticated = True

        with patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
            return_value=[],
        ) as mock_search:
            scrape_senate_trades(
                official_name="Tommy Tuberville",
                session=mock_session,
            )

        # Should have called search with split name
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args
        assert call_kwargs.kwargs["first_name"] == "Tommy"
        assert call_kwargs.kwargs["last_name"] == "Tuberville"

    def test_fetch_error_skipped(self):
        """PTR page fetch failures should be skipped gracefully."""
        mock_session = MagicMock(spec=EFDSession)
        mock_session.is_authenticated = True
        mock_session._authenticated = True
        mock_session.fetch_page.side_effect = requests.ConnectionError("timeout")

        with patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
        ) as mock_search:
            mock_search.return_value = [
                {
                    "first_name": "Tommy",
                    "last_name": "Tuberville",
                    "filer_type": "Senator",
                    "report_title": "PTR",
                    "report_url": "/search/view/ptr/abc/",
                    "report_uuid": "abc",
                    "filing_date": date(2022, 9, 15),
                    "is_paper": False,
                },
            ]

            trades = scrape_senate_trades(
                official_name="Tommy Tuberville",
                session=mock_session,
            )

        # Should have no trades (error was skipped)
        assert trades == []
