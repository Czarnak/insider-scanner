"""Tests for Senate EFD financial disclosure scraper (congress_senate.py)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import pytest
import requests
import responses

from insider_scanner.core.congress_senate import (
    BASE_URL,
    REPORT_DATA,
    SEARCH_HOME,
    SEARCH_LANDING,
    EFDSession,
    _extract_ticker,
    _map_senate_columns,
    _normalize_tx_type,
    _parse_date,
    _split_name,
    create_efd_session,
    parse_ptr_page,
    parse_search_results,
    scrape_senate_trades,
    search_senate_filings,
)
from insider_scanner.core.fetch_outcome import FetchIssue, FetchResult, FetchStatus
from insider_scanner.core.models import CongressTrade
from insider_scanner.core.sec_fair_access import RateLimiter


@pytest.fixture(autouse=True)
def senate_clock(monkeypatch):
    """Exercise the limiter with virtual time rather than sleeping offline."""
    clock = MagicMock()
    clock.now = 0.0
    clock.monotonic.side_effect = lambda: clock.now

    def advance(seconds):
        clock.now += seconds

    clock.sleep.side_effect = advance
    limiter = RateLimiter(1.0, monotonic=clock.monotonic, sleep=clock.sleep)
    monkeypatch.setattr(
        "insider_scanner.core.congress_senate._SENATE_RATE_LIMITER", limiter
    )
    return clock


@pytest.fixture
def register_efd_auth():
    """Register the shared landing, CSRF and agreement exchange."""

    def register():
        responses.add(responses.GET, SEARCH_LANDING, body=LANDING_HTML, status=200)
        responses.add(
            responses.POST,
            SEARCH_HOME,
            body=SEARCH_FORM_HTML,
            status=200,
            headers={"Set-Cookie": "csrftoken=tok; Path=/"},
        )

    return register


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
        assert last == "Booker"

    def test_single_name(self):
        assert _split_name("Unknownsurname") == ("", "Unknownsurname")

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
        assert _normalize_tx_type("S (partial)") == "Sale"

    def test_purchase_code(self):
        assert _normalize_tx_type("P") == "Purchase"

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
        assert [tx["source_row_id"] for tx in transactions] == ["1", "2", "3"]

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
        assert transactions.status is FetchStatus.TRANSIENT_FAILURE

    @pytest.mark.parametrize(
        "html",
        [
            "<html><h1>Accept the prohibition agreement</h1></html>",
            '<div class="table-responsive"><table><tr><th>Asset name</th><th>Amount</th></tr></table></div>',
        ],
    )
    def test_unrecognized_ptr_page_is_not_empty_success(self, html):
        result = parse_ptr_page(html)
        assert result == []
        assert not result.complete
        assert result.issues


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
        self, create_browser_session, senate_clock
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
        assert senate_clock.sleep.call_count == 2

    @responses.activate
    def test_authenticate_no_csrf(self):
        responses.add(
            responses.GET,
            SEARCH_LANDING,
            body="<html><body>No form here</body></html>",
            status=200,
        )

        session = EFDSession()
        with pytest.raises(ConnectionError, match="CSRF"):
            session.authenticate()

    @responses.activate
    def test_search(self, register_efd_auth):
        # Pre-authenticate
        register_efd_auth()
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
        with pytest.raises(ConnectionError, match="not authenticated"):
            session.search(last_name="test")

    @responses.activate
    def test_search_submits_inclusive_filing_date_bounds(self, register_efd_auth):
        register_efd_auth()
        responses.add(responses.POST, REPORT_DATA, json=SEARCH_RESPONSE_JSON)

        with EFDSession() as session:
            session.authenticate()
            session.search(date_from=date(2026, 11, 13), date_to=date(2026, 11, 14))

        submitted = parse_qs(responses.calls[-1].request.body)
        assert submitted["submitted_start_date"] == ["11/13/2026 00:00:00"]
        assert submitted["submitted_end_date"] == ["11/14/2026 23:59:59"]

    @responses.activate
    def test_fetch_page(self, register_efd_auth):
        register_efd_auth()
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
    def test_filters_paper_filings(self, register_efd_auth):
        # Authenticate
        register_efd_auth()
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
    def test_empty_results(self, register_efd_auth):
        register_efd_auth()
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
        assert results.status is FetchStatus.EMPTY

    def test_search_failure_retains_completed_pages(self):
        session = MagicMock(spec=EFDSession)
        first_page = {**SEARCH_RESPONSE_JSON, "recordsFiltered": 150}
        session.search.side_effect = [first_page, ConnectionError("offline")]

        result = search_senate_filings(session)

        assert len(result) == 2
        assert result.status is FetchStatus.PARTIAL
        assert any(issue.reason == "search_failed" for issue in result.issues)
        assert session.search.call_count == 2

    def test_result_limit_is_explicit(self):
        session = MagicMock(spec=EFDSession)
        row = SEARCH_RESPONSE_JSON["data"][0]
        session.search.return_value = {
            "result": "ok",
            "recordsFiltered": 1500,
            "data": [row] * 100,
        }

        result = search_senate_filings(session)

        assert len(result) == 1000
        assert result.status is FetchStatus.PARTIAL
        assert result.issues == (
            FetchIssue("result_limit", source="senate", identifier="1000"),
        )
        assert session.search.call_count == 10

    @pytest.mark.parametrize(
        "response",
        [
            {"result": "failed"},
            {"result": "ok", "recordsFiltered": 1, "data": []},
            {"result": "ok", "recordsFiltered": 1, "data": [["bad"]]},
        ],
    )
    def test_failed_or_incomplete_search_is_not_empty_success(self, response):
        session = MagicMock(spec=EFDSession)
        session.search.return_value = response

        result = search_senate_filings(session)

        assert result == []
        assert not result.complete
        assert result.issues

    def test_paper_only_search_is_unsupported(self):
        session = MagicMock(spec=EFDSession)
        session.search.return_value = {
            "result": "ok",
            "recordsFiltered": 1,
            "data": [
                next(row for row in SEARCH_RESPONSE_JSON["data"] if "/paper/" in row[3])
            ],
        }

        result = search_senate_filings(session)

        assert result.status is FetchStatus.UNSUPPORTED
        assert not result.issues[0].retryable


# -----------------------------------------------------------------------
# Full pipeline
# -----------------------------------------------------------------------


class TestScrapeSentateTrades:
    def test_normalizes_shared_disclosure_fields(self):
        session = MagicMock(spec=EFDSession)
        session.is_authenticated = True
        filing = {
            "first_name": "Test",
            "last_name": "Senator",
            "report_url": "/search/view/ptr/test/",
            "report_uuid": "test",
            "filing_date": date(2026, 1, 20),
            "is_paper": False,
        }
        transaction = {
            "ticker": "--",
            "asset_name": "Brown-Forman Class B (BF-B)",
            "tx_type": "S (partial)",
            "owner": "Spouse",
            "amount": "Spouse/DC Over $1,000,000",
        }
        with (
            patch(
                "insider_scanner.core.congress_senate.search_senate_filings",
                return_value=[filing],
            ),
            patch(
                "insider_scanner.core.congress_senate.parse_ptr_page",
                return_value=[transaction],
            ),
        ):
            trades = scrape_senate_trades(official_name="Test Senator", session=session)

        assert len(trades) == 1
        trade = trades[0]
        assert trade.ticker == "BF-B"
        assert trade.trade_type == "Sale"
        assert trade.owner == "Spouse"
        assert trade.amount_range == "Spouse/DC Over $1,000,000"
        assert trade.amount_low == trade.amount_high == 1000000.0

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
        assert [trade.source_row_id for trade in trades] == ["1", "2", "3"]

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
        assert call_kwargs.kwargs["first_name"] == ""
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


@responses.activate
def test_injected_limiter_controls_all_requests(register_efd_auth):
    register_efd_auth()
    responses.add(responses.GET, BASE_URL + "/search/view/ptr/test/", body="page")
    limiter = MagicMock(spec=RateLimiter)
    with EFDSession(rate_limiter=limiter) as session:
        session.authenticate()
        assert session.fetch_page("/search/view/ptr/test/") == "page"
    assert limiter.acquire.call_count == 3
    assert not session.is_authenticated


@pytest.mark.parametrize("operation", ["search", "fetch_page"])
@responses.activate
def test_agreement_response_reauthenticates_and_retries_once(
    operation, register_efd_auth
):
    register_efd_auth()
    method = responses.POST if operation == "search" else responses.GET
    url = REPORT_DATA if operation == "search" else BASE_URL + "/search/view/ptr/test/"
    responses.add(method, url, body=LANDING_HTML)
    if operation == "search":
        responses.add(method, url, json=SEARCH_RESPONSE_JSON)
    else:
        responses.add(method, url, body=PTR_PAGE_HTML)

    with EFDSession() as session:
        session.authenticate()
        with patch.object(session, "authenticate", wraps=session.authenticate) as renew:
            result = (
                session.search() if operation == "search" else session.fetch_page(url)
            )
        renew.assert_called_once()
        assert session.is_authenticated

    assert result == (SEARCH_RESPONSE_JSON if operation == "search" else PTR_PAGE_HTML)
    assert sum(call.request.url == url for call in responses.calls) == 2


@responses.activate
def test_ptr_redirect_to_agreement_url_reauthenticates(register_efd_auth):
    register_efd_auth()
    url = BASE_URL + "/search/view/ptr/test/"
    responses.add(responses.GET, url, status=302, headers={"Location": SEARCH_HOME})
    # A redirect alone identifies the return to the agreement, even if the
    # destination page's markup changes or contains no agreement checkbox.
    responses.add(responses.GET, SEARCH_HOME, body="<html>Agreement</html>")
    responses.add(responses.GET, url, body=PTR_PAGE_HTML)

    with EFDSession() as session:
        session.authenticate()
        with patch.object(session, "authenticate", wraps=session.authenticate) as renew:
            assert session.fetch_page(url) == PTR_PAGE_HTML
        renew.assert_called_once()


@pytest.mark.parametrize("operation", ["search", "fetch_page"])
@responses.activate
def test_repeated_agreement_response_fails_after_one_renewal(
    operation, register_efd_auth
):
    register_efd_auth()
    method = responses.POST if operation == "search" else responses.GET
    url = REPORT_DATA if operation == "search" else BASE_URL + "/search/view/ptr/test/"
    responses.add(method, url, body=LANDING_HTML)

    with EFDSession() as session:
        session.authenticate()
        with (
            patch.object(session, "authenticate", wraps=session.authenticate) as renew,
            pytest.raises(ConnectionError, match="after re-authentication"),
        ):
            session.search() if operation == "search" else session.fetch_page(url)
        renew.assert_called_once()
        assert not session.is_authenticated

    assert sum(call.request.url == url for call in responses.calls) == 2


@responses.activate
def test_repeated_search_agreement_stays_retryable_incomplete(register_efd_auth):
    register_efd_auth()
    responses.add(responses.POST, REPORT_DATA, body=LANDING_HTML)
    with EFDSession() as session:
        session.authenticate()
        result = search_senate_filings(session)
    assert not result.complete
    assert result.status == FetchStatus.TRANSIENT_FAILURE
    assert result.issues[0].reason == "search_failed"
    assert result.issues[0].retryable


@responses.activate
def test_unrelated_html_search_failure_does_not_renew_agreement(register_efd_auth):
    register_efd_auth()
    responses.add(responses.POST, REPORT_DATA, body="<html>Unavailable</html>")
    with EFDSession() as session:
        session.authenticate()
        with patch.object(session, "authenticate", wraps=session.authenticate) as renew:
            result = search_senate_filings(session)
        renew.assert_not_called()
    assert not result.complete
    assert result.issues[0].reason == "search_failed"


def test_efd_context_closes_transport_on_error():
    session = EFDSession()
    with (
        patch.object(session.session, "close") as close,
        pytest.raises(RuntimeError),
        session,
    ):
        raise RuntimeError("stop")
    close.assert_called_once()


def test_create_session_closes_failed_authentication():
    transport = MagicMock()
    with (
        patch(
            "insider_scanner.core.congress_senate.EFDSession", return_value=transport
        ),
        pytest.raises(ConnectionError),
    ):
        transport.authenticate.side_effect = ConnectionError("no CSRF")
        create_efd_session()
    transport.close.assert_called_once()


@pytest.mark.parametrize("has_filings", [False, True])
def test_scrape_closes_owned_session(has_filings):
    session = MagicMock(spec=EFDSession)
    session.fetch_page.return_value = PTR_PAGE_HTML
    filings = parse_search_results(SEARCH_RESPONSE_JSON)[:1] if has_filings else []
    with (
        patch(
            "insider_scanner.core.congress_senate.create_efd_session",
            return_value=session,
        ),
        patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
            return_value=filings,
        ),
    ):
        scrape_senate_trades()
    session.close.assert_called_once()


def test_scrape_closes_owned_session_on_search_error():
    session = MagicMock(spec=EFDSession)
    with (
        patch(
            "insider_scanner.core.congress_senate.create_efd_session",
            return_value=session,
        ),
        patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
            side_effect=RuntimeError("failed"),
        ),
        pytest.raises(RuntimeError),
    ):
        scrape_senate_trades()
    session.close.assert_called_once()


def test_scrape_keeps_injected_session_open():
    session = MagicMock(spec=EFDSession)
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings", return_value=[]
    ):
        assert scrape_senate_trades(session=session) == []
    session.close.assert_not_called()


def test_scrape_retains_successful_ptr_when_another_fails():
    session = MagicMock(spec=EFDSession)
    filings = parse_search_results(SEARCH_RESPONSE_JSON)
    filings = [filing for filing in filings if not filing["is_paper"]]
    session.fetch_page.side_effect = [PTR_PAGE_HTML, ConnectionError("offline")]
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings",
        return_value=filings,
    ):
        result = scrape_senate_trades(session=session)
    assert len(result) == 3
    assert result.status is FetchStatus.PARTIAL
    assert result.issues == (
        FetchIssue("ptr_failed", source="senate", identifier=filings[1]["report_uuid"]),
    )


def test_scrape_propagates_search_cap_with_retained_trades():
    session = MagicMock(spec=EFDSession)
    session.fetch_page.return_value = PTR_PAGE_HTML
    filing = parse_search_results(SEARCH_RESPONSE_JSON)[0]
    issue = FetchIssue("result_limit", source="senate", identifier="1000")
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings",
        return_value=FetchResult([filing], issues=[issue]),
    ):
        result = scrape_senate_trades(session=session)
    assert len(result) == 3
    assert result.status is FetchStatus.PARTIAL
    assert result.issues == (issue,)


def test_scrape_retains_failed_ptr_identifier_for_unrecognized_page():
    session = MagicMock(spec=EFDSession)
    session.fetch_page.return_value = "<html>blocked</html>"
    filing = parse_search_results(SEARCH_RESPONSE_JSON)[0]
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings",
        return_value=[filing],
    ):
        result = scrape_senate_trades(session=session)
    assert result == []
    assert result.status is FetchStatus.TRANSIENT_FAILURE
    assert result.issues[0].identifier == filing["report_uuid"]


@pytest.mark.parametrize("has_electronic_filing", [False, True])
@pytest.mark.parametrize("has_retryable_issue", [False, True])
def test_scrape_classifies_unsupported_search_issues(
    has_electronic_filing, has_retryable_issue
):
    session = MagicMock(spec=EFDSession)
    session.fetch_page.return_value = PTR_PAGE_NO_TABLE_HTML
    filings = (
        [parse_search_results(SEARCH_RESPONSE_JSON)[0]] if has_electronic_filing else []
    )
    issues = [FetchIssue("paper_filing", source="senate", retryable=False)]
    if has_retryable_issue:
        issues.append(FetchIssue("search_failed", source="senate"))
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings",
        return_value=FetchResult(filings, issues=issues),
    ):
        result = scrape_senate_trades(session=session)
    assert result == []
    assert result.issues == tuple(issues)
    expected = (
        FetchStatus.TRANSIENT_FAILURE
        if has_retryable_issue
        else FetchStatus.UNSUPPORTED
    )
    assert result.status is expected


def test_scrape_preserves_trades_with_unsupported_search_issues():
    session = MagicMock(spec=EFDSession)
    session.fetch_page.return_value = PTR_PAGE_HTML
    filing = parse_search_results(SEARCH_RESPONSE_JSON)[0]
    issue = FetchIssue("paper_filing", source="senate", retryable=False)
    with patch(
        "insider_scanner.core.congress_senate.search_senate_filings",
        return_value=FetchResult([filing], issues=[issue]),
    ):
        result = scrape_senate_trades(session=session)
    assert len(result) == 3
    assert result.status is FetchStatus.PARTIAL
    assert result.issues == (issue,)


@responses.activate
def test_default_sessions_share_the_senate_request_budget(
    register_efd_auth, senate_clock
):
    register_efd_auth()
    with EFDSession() as first, EFDSession() as second:
        first.authenticate()
        second.authenticate()
    assert senate_clock.now == pytest.approx(3.0)
    assert senate_clock.sleep.call_count == 3


@pytest.mark.parametrize(
    "full_name,expected",
    [
        ("Katie Boyd Britt", ("Katie", "Britt")),
        ("Nicholas J. Begich III", ("Nicholas", "Begich")),
        ("Gilbert Ray Cisneros, Jr.", ("Gilbert", "Cisneros")),
        ("Angus S. King, Jr.", ("Angus", "King")),
    ],
)
def test_split_name_ignores_middle_names_and_trailing_suffixes(full_name, expected):
    assert _split_name(full_name) == expected


def test_structured_senate_member_uses_surname_search_and_local_first_name_filter():
    session = MagicMock(spec=EFDSession)
    filings = [
        {
            "first_name": "Katie",
            "last_name": "Britt",
            "report_uuid": "correct",
            "report_url": "/correct",
        },
        {
            "first_name": "Other",
            "last_name": "Britt",
            "report_uuid": "wrong",
            "report_url": "/wrong",
        },
    ]
    with (
        patch(
            "insider_scanner.core.congress_senate.search_senate_filings",
            return_value=filings,
        ) as search,
        patch("insider_scanner.core.congress_senate.parse_ptr_page", return_value=[]),
    ):
        scrape_senate_trades(
            member={
                "first_name": "Katie",
                "last_name": "Britt",
                "official_name": "Katie Boyd Britt",
            },
            session=session,
        )
    assert search.call_args.kwargs["first_name"] == ""
    assert search.call_args.kwargs["last_name"] == "Britt"
    session.fetch_page.assert_called_once_with("/correct")
