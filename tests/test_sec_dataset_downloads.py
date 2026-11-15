"""Quarterly downloads use SEC discovery and the shared bounded HTTP lifecycle."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from insider_scanner.core.sec_client import (
    SecClientSecurityError,
    SecHttpError,
    SecTransportError,
)
from insider_scanner.core.sec_fair_access import SecRetryPolicy
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecResourceLimits,
    SecResourceProfile,
    SecSecurityReason,
)
from tests.test_sec_client import StubResponse, configured_transport, make_client

INDEX = (
    "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
)
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2006q1_form345.zip"


def _module():
    name = "insider_scanner.core.sec_dataset_downloads"
    assert importlib.util.find_spec(name) is not None, "dataset discovery is missing"
    return importlib.import_module(name)


def _profile():
    profile = getattr(SecResourceProfile, "DATASET_ZIP", None)
    assert profile is not None, "bounded dataset ZIP profile is missing"
    return profile


def _download(client, path, *, url=URL, cancelled=None):
    download = getattr(client, "fetch_to_file", None)
    assert callable(download), "SEC streaming file download is missing"
    return download(url, path, profile=_profile(), cancelled=cancelled)


def _zip_response(*chunks, status=200, headers=None):
    return StubResponse(
        status_code=status,
        chunks=chunks,
        headers=headers or {"Content-Type": "application/zip"},
    )


def test_discovery_uses_actual_links_across_sec_prefixes_and_deduplicates():
    html = f'''<a href="/files/datastandardsinnovation/data/insider-transactions-data-sets/2026q3_form345.zip">new</a>
    <a href="{URL}">old</a><a href="{URL}">duplicate</a>
    <a href="https://evil.example/2026q4_form345.zip">external</a>
    <a href="https://user:secret@www.sec.gov/files/2026q4_form345.zip">credentials</a>
    <a href="//www.sec.gov.evil.example/files/2026q4_form345.zip">lookalike</a>
    <a href="http://www.sec.gov/files/2026q4_form345.zip">insecure</a>
    <a href="/files/2005q4_form345.zip">too old</a>
    <a href="/files/2026q5_form345.zip">bad quarter</a>
    <a href="/files/2026q4_form345.zip?token=secret">query</a>
    <a href="https://[bad-host/files/2026q4_form345.zip">malformed</a>
    <a href="/files/../2026q4_form345.zip">traversal</a>'''
    response = StubResponse(
        chunks=(html.encode(),), headers={"Content-Type": "text/html; charset=utf-8"}
    )
    transport = configured_transport(response)
    module = _module()
    quarters = module.discover_dataset_quarters(make_client(transport))
    assert [(item.year, item.quarter) for item in quarters] == [(2006, 1), (2026, 3)]
    assert quarters[0].url == URL
    assert transport.get.call_count == 1
    assert transport.get.call_args.args == (INDEX,)
    assert response.close_calls == 1


def test_discovery_never_guesses_when_no_archives_are_published():
    response = StubResponse(
        chunks=(b"<html>Temporarily unavailable</html>",),
        headers={"Content-Type": "text/html"},
    )
    module = _module()
    with pytest.raises(module.SecDatasetDiscoveryError, match="published quarterly"):
        module.discover_dataset_quarters(make_client(configured_transport(response)))


@pytest.mark.parametrize("status", [404, 503])
def test_discovery_preserves_typed_http_failure(status):
    module = _module()
    with pytest.raises(SecHttpError) as error:
        module.discover_dataset_quarters(
            make_client(configured_transport(StubResponse(status_code=status)))
        )
    assert error.value.status_code == status


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/files/2006q1_form345.zip",
        "https://www.sec.gov/files/2006q2_form345.zip",
    ],
)
def test_quarter_rejects_untrusted_or_mismatched_download_identity(url):
    with pytest.raises(ValueError):
        _module().DatasetQuarter(2006, 1, url)


def test_quarter_rejects_boolean_quarter():
    with pytest.raises(ValueError):
        _module().DatasetQuarter(2006, True, URL)


def test_download_publishes_complete_file_without_fetch_bytes(tmp_path, monkeypatch):
    response = _zip_response(b"PK", b"\x03\x04", b"body")
    transport = configured_transport(response)
    client = make_client(transport)
    monkeypatch.setattr(
        type(client), "fetch_bytes", Mock(side_effect=AssertionError("must stream"))
    )
    target = tmp_path / "nested" / "quarter.zip"
    result = _download(client, target)
    assert result == target
    assert target.read_bytes() == b"PK\x03\x04body"
    assert list(target.parent.iterdir()) == [target]
    assert response.close_calls == 1
    assert response.iter_chunk_sizes == [
        DEFAULT_SEC_SECURITY_POLICY.response_chunk_bytes
    ]


def test_streaming_body_retry_discards_partial_first_attempt(tmp_path):
    class BrokenBody(StubResponse):
        def iter_content(self, chunk_size):
            yield b"partial-first-attempt"
            raise requests.exceptions.ChunkedEncodingError("private error")

    first = BrokenBody(headers={"Content-Type": "application/zip"})
    second = _zip_response(b"complete")
    transport = configured_transport(first, second)
    client = replace(
        make_client(transport),
        retry_policy=SecRetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    target = tmp_path / "quarter.zip"
    _download(client, target)
    assert target.read_bytes() == b"complete"
    assert first.close_calls == second.close_calls == 1
    assert transport.get.call_count == 2


def test_transport_failure_preserves_existing_file_and_cleans_temp(tmp_path):
    class BrokenBody(StubResponse):
        def iter_content(self, chunk_size):
            yield b"partial"
            raise requests.ConnectionError("private error")

    response = BrokenBody(headers={"Content-Type": "application/zip"})
    target = tmp_path / "quarter.zip"
    target.write_bytes(b"existing")
    with pytest.raises(SecTransportError):
        _download(make_client(configured_transport(response)), target)
    assert target.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [target]
    assert response.close_calls == 1


@pytest.mark.parametrize(
    "headers,chunks,reason",
    [
        ({"Content-Type": "text/html"}, (b"blocked",), SecSecurityReason.CONTENT_TYPE),
        (
            {"Content-Type": "application/zip", "Content-Length": "9"},
            (b"small",),
            SecSecurityReason.RESPONSE_SIZE,
        ),
        (
            {"Content-Type": "application/zip"},
            (b"1234", b"5678"),
            SecSecurityReason.RESPONSE_SIZE,
        ),
    ],
)
def test_download_reuses_content_type_and_byte_bounds(
    tmp_path, headers, chunks, reason
):
    profile = _profile()
    limits = dict(DEFAULT_SEC_SECURITY_POLICY.resource_limits)
    limits[profile] = SecResourceLimits(frozenset({"application/zip"}), 6)
    response = _zip_response(*chunks, headers=headers)
    client = make_client(
        configured_transport(response),
        policy=replace(DEFAULT_SEC_SECURITY_POLICY, resource_limits=limits),
    )
    target = tmp_path / "quarter.zip"
    with pytest.raises(SecClientSecurityError) as error:
        _download(client, target)
    assert error.value.reason is reason
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
    assert response.close_calls == 1


def test_download_rejects_external_redirect_and_preserves_destination(tmp_path):
    response = _zip_response(
        status=302, headers={"Location": "https://evil.example/private.zip"}
    )
    target = tmp_path / "quarter.zip"
    target.write_bytes(b"existing")
    with pytest.raises(SecClientSecurityError) as error:
        _download(make_client(configured_transport(response)), target)
    assert error.value.reason is SecSecurityReason.REDIRECT
    assert target.read_bytes() == b"existing"
    assert response.close_calls == 1


def test_cancelled_download_closes_response_and_does_not_publish(tmp_path):
    state = {"cancelled": False}

    class CancellingResponse(StubResponse):
        def iter_content(self, chunk_size):
            yield b"partial"
            state["cancelled"] = True
            yield b"remaining"

    response = CancellingResponse(headers={"Content-Type": "application/zip"})
    target = tmp_path / "quarter.zip"
    target.write_bytes(b"existing")
    from insider_scanner.core import sec_client

    cancelled_error = getattr(sec_client, "SecDownloadCancelled", None)
    assert cancelled_error is not None, "streaming cancellation outcome is missing"
    with pytest.raises(cancelled_error):
        _download(
            make_client(configured_transport(response)),
            target,
            cancelled=lambda: state["cancelled"],
        )
    assert target.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [target]
    assert response.close_calls == 1


def test_already_cancelled_download_never_requests(tmp_path):
    from insider_scanner.core import sec_client

    cancelled_error = getattr(sec_client, "SecDownloadCancelled", None)
    assert cancelled_error is not None, "streaming cancellation outcome is missing"
    transport = configured_transport()
    with pytest.raises(cancelled_error):
        _download(
            make_client(transport), tmp_path / "quarter.zip", cancelled=lambda: True
        )
    transport.get.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_quarter_helper_uses_only_one_quarterly_download(tmp_path):
    module = _module()
    response = _zip_response(b"quarter archive")
    transport = configured_transport(response)
    target = tmp_path / "quarter.zip"
    result = module.download_dataset_quarter(
        make_client(transport), module.DatasetQuarter(2006, 1, URL), target
    )
    assert result == target
    assert target.read_bytes() == b"quarter archive"
    assert transport.get.call_count == 1
    assert transport.get.call_args.args == (URL,)
