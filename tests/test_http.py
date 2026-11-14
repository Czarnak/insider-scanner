"""Connection ownership, bounded retries and legacy SEC access regressions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, PropertyMock

import pytest
import requests
import responses

from insider_scanner.core.sec_client import (
    SecClientSecurityError,
    SecConfigurationError,
)
from insider_scanner.utils import http
from insider_scanner.utils.config import get_sec_user_agent

URL = "https://example.com/page"
SEC_URL = "https://www.sec.gov/files/company_tickers.json"


@pytest.fixture(autouse=True)
def clean_pools():
    http.close_http_sessions()
    yield
    http.close_http_sessions()


@responses.activate
def test_generic_reuses_connection_pool_and_recreates_after_shutdown(monkeypatch):
    sessions = []
    factory = requests.Session

    def make_session():
        session = factory()
        session.close = Mock(wraps=session.close)
        sessions.append(session)
        return session

    monkeypatch.setattr(http.requests, "Session", make_session)
    responses.get(URL, body="response")
    assert http.fetch_url(URL) == http.fetch_url(URL) == "response"
    assert len(sessions) == 1
    http.close_http_sessions()
    sessions[0].close.assert_called_once()
    assert http.fetch_url(URL) == "response"
    assert len(sessions) == 2


@responses.activate
@pytest.mark.parametrize(
    "failure", [429, 500, 502, 503, 504, requests.Timeout(), requests.ConnectionError()]
)
def test_generic_retries_transient_failures_with_one_attempt_budget(failure):
    waits = []
    if isinstance(failure, int):
        responses.get(URL, status=failure)
    else:
        responses.get(URL, body=failure)
    responses.get(URL, body="success")
    with http.HttpSession(sleep=waits.append) as session:
        assert session.get(URL).text == "success"
    assert len(responses.calls) == 2
    assert waits == [1]


@responses.activate
def test_retries_are_bounded_and_close_responses():
    responses.get(URL, status=503, headers={"Retry-After": "600"})
    waits = []
    with (
        http.HttpSession(sleep=waits.append) as session,
        pytest.raises(requests.HTTPError),
    ):
        session.get(URL)
    assert len(responses.calls) == 4
    assert waits == [30, 30, 30]


@responses.activate
def test_permanent_http_failure_is_not_retried():
    responses.get(URL, status=404)
    with (
        http.HttpSession(sleep=lambda _: pytest.fail("unexpected retry")) as session,
        pytest.raises(requests.HTTPError),
    ):
        session.get(URL)
    assert len(responses.calls) == 1


def test_body_failure_retries_and_releases_both_responses():
    failed = Mock(spec=requests.Response)
    failed.headers = {}
    type(failed).content = PropertyMock(
        side_effect=requests.exceptions.ChunkedEncodingError()
    )
    success = Mock(spec=requests.Response)
    success.headers = {}
    success.content = b"complete"
    raw = Mock(spec=requests.Session)
    raw.headers = {}
    raw.get.side_effect = [failed, success]
    with http.HttpSession(session=raw, sleep=lambda _: None) as session:
        assert session.get(URL) is success
    assert raw.get.call_count == 2
    failed.close.assert_called_once()
    success.close.assert_called_once()
    raw.close.assert_not_called()


def test_each_thread_reuses_its_own_pool():
    barrier = Barrier(2)

    def worker():
        first = http._http_session()
        barrier.wait(timeout=5)
        assert http._http_session() is first
        return first

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: worker(), range(2)))
    assert first is not second


@responses.activate
def test_sec_host_uses_runtime_validated_identity_even_without_legacy_flag(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Scanner first@scanner.example")
    responses.get(SEC_URL, json={"0": {"ticker": "AAPL", "cik_str": 320193}})
    http.fetch_url(SEC_URL, headers={"user-agent": "invalid override"})
    assert (
        responses.calls[-1].request.headers["User-Agent"]
        == "Scanner first@scanner.example"
    )
    monkeypatch.setenv("SEC_USER_AGENT", "Scanner second@scanner.example")
    http.fetch_url(SEC_URL)
    assert (
        responses.calls[-1].request.headers["User-Agent"]
        == "Scanner second@scanner.example"
    )
    assert get_sec_user_agent() == "Scanner second@scanner.example"


def test_sec_rejects_missing_contact_before_network(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(SecConfigurationError):
        http.fetch_url(SEC_URL)


@responses.activate
def test_legacy_sec_redirect_still_obeys_host_boundary(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Scanner ops@scanner.example")
    responses.get(
        SEC_URL, status=302, headers={"Location": "https://untrusted.example/file"}
    )
    with pytest.raises(SecClientSecurityError):
        http.fetch_url(SEC_URL, use_sec_agent=True)
    assert len(responses.calls) == 1


@responses.activate
def test_legacy_browse_accepts_bounded_html(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Scanner ops@scanner.example")
    url = "https://www.sec.gov/cgi-bin/browse-edgar?CIK=AAPL"
    responses.get(url, body="<html>CIK=320193</html>", content_type="text/html")
    assert http.fetch_url(url, use_sec_agent=True) == "<html>CIK=320193</html>"
