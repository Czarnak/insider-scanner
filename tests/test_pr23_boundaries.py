"""The application owns feed access and SEC lookup crosses a service boundary."""

import ast
from pathlib import Path
from unittest.mock import Mock

import pytest

from insider_scanner.services.application import ApplicationServices
from insider_scanner.services.us import UsScanService


def test_application_constructs_feed_with_shared_engine():
    persistence = Mock()
    services = ApplicationServices(persistence, Mock(), Mock(), Mock())
    assert services.feed._engine is persistence.engine


def test_gui_information_does_not_import_cli():
    path = Path(__file__).parents[1] / "src/insider_scanner/gui/sec_backfill_info.py"
    imports = [
        node.module
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom)
    ]
    assert "insider_scanner.cli" not in imports


def test_shared_sec_submissions_url_preserves_cli_compatibility():
    from insider_scanner.cli import SEC_BULK_SUBMISSIONS_URL as cli_url
    from insider_scanner.core.sec_bulk import SEC_BULK_SUBMISSIONS_URL

    assert cli_url == SEC_BULK_SUBMISSIONS_URL
    assert SEC_BULK_SUBMISSIONS_URL.endswith("/bulkdata/submissions.zip")


@pytest.mark.parametrize("cik", ["0000000320", None])
def test_us_service_resolves_cik_and_builds_filings_url(monkeypatch, cik):
    resolver = Mock(return_value=cik)
    filing_url = Mock(return_value="https://example.test/filings")
    monkeypatch.setattr("insider_scanner.core.edgar.resolve_cik", resolver)
    monkeypatch.setattr("insider_scanner.core.edgar.get_filing_url", filing_url)
    service = UsScanService(Mock(), adapters={}, latest_adapters={})

    result = service.resolve_cik(" aapl ")

    resolver.assert_called_once_with("AAPL")
    if cik:
        assert result == (cik, "https://example.test/filings")
        filing_url.assert_called_once_with(cik)
    else:
        assert result is None
        filing_url.assert_not_called()
