"""Offline end-to-end integration tests for the SEC daily-ingestion pipeline.

Wires together: SecMasterIndexRow / parse_master_index → download_filing
(stub transport, no network) → extract_ownership_document →
parse_ownership_document and asserts the normalised OwnershipFiling.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from insider_scanner.core._sec_xml import SecXmlSecurityError
from insider_scanner.core.sec_client import SecClient, SecClientSecurityError, SecTransport
from insider_scanner.core.sec_downloader import (
    DownloadedSecFiling,
    fetch_filing,
    promote_validated_filing,
)
from insider_scanner.core.sec_index import SecMasterIndexRow, parse_master_index
from insider_scanner.core.sec_ownership_document import (
    SecOwnershipDocumentError,
    extract_ownership_document,
)
from insider_scanner.core.sec_ownership_parser import OwnershipFiling, parse_ownership_document
from insider_scanner.core.sec_security import DEFAULT_SEC_SECURITY_POLICY, SecSecurityPolicy, SecSecurityReason

FIXTURE_DIR = Path(__file__).parent / "fixtures"
VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StubResponse:
    """Minimal HTTP response stub — mirrors the _SecResponse Protocol."""

    status_code: int
    chunks: tuple[bytes, ...]
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "application/octet-stream"}
    )
    close_calls: int = 0

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.close_calls += 1


def _make_apple_row() -> SecMasterIndexRow:
    return SecMasterIndexRow(
        cik="0000320193",
        company_name="APPLE INC",
        form_type="4",
        filing_date=date(2026, 6, 15),
        archive_path="edgar/data/320193/0000320193-26-000061.txt",
    )


def _make_stub_client(content: bytes) -> tuple[SecClient, Mock]:
    """Return a SecClient backed by a Mock transport that returns *content*."""
    transport = Mock(spec=SecTransport)
    transport.get.return_value = StubResponse(status_code=200, chunks=(content,))
    client = SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
    )
    return client, transport


def _fixture_bytes() -> bytes:
    return (FIXTURE_DIR / "sec_form4_submission.txt").read_bytes()


def _fetch_parse_promote(
    row: SecMasterIndexRow,
    *,
    client: SecClient,
    cache_root: Path,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
) -> tuple[DownloadedSecFiling, OwnershipFiling]:
    """Run the real fetch → parse → promote boundary in that order."""
    pending = fetch_filing(row, client=client, cache_root=cache_root)
    document = extract_ownership_document(pending.content, policy=policy)
    filing = parse_ownership_document(document, policy=policy)
    downloaded = promote_validated_filing(pending, cache_root=cache_root)
    return downloaded, filing


def _validated_cache_files(cache_root: Path) -> list[Path]:
    """Return list of files under cache_root/validated-v1, or empty if absent.

    Read-only: does not mutate the cache.
    """
    namespace = cache_root / "validated-v1"
    if not namespace.exists():
        return []
    return list(namespace.iterdir())


def _run_full_chain(
    row: SecMasterIndexRow,
    client: SecClient,
    tmp_path: Path,
) -> OwnershipFiling:
    """Return the normalized result produced before cache promotion."""
    _, filing = _fetch_parse_promote(row, client=client, cache_root=tmp_path)
    return filing


# ---------------------------------------------------------------------------
# Test A — full chain from an explicit SecMasterIndexRow
# ---------------------------------------------------------------------------


class TestFullChainFromExplicitRow:
    """Exercise download → extract → parse with a manually constructed row."""

    def test_download_calls_transport_with_exact_archive_url(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, transport = _make_stub_client(_fixture_bytes())

        downloaded, _ = _fetch_parse_promote(
            row, client=client, cache_root=tmp_path
        )

        expected_url = (
            "https://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000061.txt"
        )
        assert downloaded.archive_url == expected_url
        transport.get.assert_called_once_with(
            expected_url,
            headers={"User-Agent": VALID_USER_AGENT},
            timeout=(15.0, 15.0),
            allow_redirects=False,
            stream=True,
        )

    def test_download_is_not_from_cache_on_first_call(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        downloaded, _ = _fetch_parse_promote(
            row, client=client, cache_root=tmp_path
        )

        assert downloaded.from_cache is False
        assert downloaded.content_path.exists()

    def test_parser_rejection_leaves_no_validated_cache(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(b"not an ownership filing")

        with pytest.raises(SecOwnershipDocumentError):
            _fetch_parse_promote(row, client=client, cache_root=tmp_path)

        assert not (tmp_path / "validated-v1").exists()

    def test_ownership_document_extracts_correct_accession_and_type(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        downloaded, _ = _fetch_parse_promote(
            row, client=client, cache_root=tmp_path
        )
        content_bytes = downloaded.content_path.read_bytes()
        doc = extract_ownership_document(content_bytes)

        # Accession is read from the SEC-HEADER inside the submission, not from row.
        assert doc.accession_number == "0000320193-26-000061"
        assert doc.document_type == "4"

    def test_issuer_fields_normalised_correctly(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        assert filing.issuer.name == "Apple Inc."
        assert filing.issuer.trading_symbol == "AAPL"
        assert filing.issuer.cik == "0000320193"

    def test_reporting_owner_fields_normalised_correctly(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        owner = filing.reporting_owner
        assert owner.name == "COOK TIMOTHY D"
        assert owner.is_officer is True
        assert owner.officer_title == "Chief Executive Officer"

    def test_exactly_one_non_derivative_transaction(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        assert len(filing.non_derivative_transactions) == 1

    def test_non_derivative_transaction_classified_as_sale(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)
        txn = filing.non_derivative_transactions[0]

        assert txn.category == "sale"
        assert txn.transaction_code == "S"

    def test_non_derivative_transaction_amounts_are_correct(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)
        txn = filing.non_derivative_transactions[0]

        assert txn.shares == Decimal("100000")
        assert txn.price_per_share == Decimal("195.50")
        assert txn.acquired_disposed == "D"

    def test_non_derivative_transaction_has_footnote_f1(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)
        txn = filing.non_derivative_transactions[0]

        assert txn.footnote_ids == ("F1",)

    def test_non_derivative_transaction_row_id_uses_accession(
        self, tmp_path: Path
    ) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)
        txn = filing.non_derivative_transactions[0]

        assert txn.row_id == "0000320193-26-000061:nonDerivative:0"

    def test_decoy_ex24_block_did_not_leak_into_filing(self, tmp_path: Path) -> None:
        """The EX-24 power-of-attorney is the FIRST document; confirm it is skipped."""
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        # No derivative transactions means the EX-24 was not misinterpreted.
        assert filing.derivative_transactions == ()
        # Issuer and owner must come from the real Form 4 block, not the decoy.
        assert filing.issuer.name == "Apple Inc."
        assert filing.reporting_owner.name == "COOK TIMOTHY D"


# ---------------------------------------------------------------------------
# Test B — parse_master_index feeds the same chain
# ---------------------------------------------------------------------------

# Minimal master.idx: four header lines, column header, dashes, one data row.
_MINIMAL_MASTER_INDEX = """\
Description:           Master Index of EDGAR Dissemination Feed
Last Data Received:    June 15, 2026
Comments:              webmaster@sec.gov
Anonymous FTP:         ftp://ftp.sec.gov/edgar/

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
320193|APPLE INC|4|2026-06-15|edgar/data/320193/0000320193-26-000061.txt
"""


class TestIndexParserFeedsChain:
    """parse_master_index row → download → extract → parse."""

    def test_parse_master_index_yields_exactly_one_row(self) -> None:
        rows = parse_master_index(_MINIMAL_MASTER_INDEX)

        assert len(rows) == 1

    def test_parsed_row_has_correct_fields(self) -> None:
        rows = parse_master_index(_MINIMAL_MASTER_INDEX)
        row = rows[0]

        assert row.cik == "0000320193"  # normalised to 10 digits
        assert row.company_name == "APPLE INC"
        assert row.form_type == "4"
        assert row.filing_date == date(2026, 6, 15)
        assert row.archive_path == "edgar/data/320193/0000320193-26-000061.txt"

    def test_index_row_produces_apple_filing(self, tmp_path: Path) -> None:
        rows = parse_master_index(_MINIMAL_MASTER_INDEX)
        row = rows[0]
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        assert filing.issuer.name == "Apple Inc."

    def test_index_row_produces_single_sale_transaction(self, tmp_path: Path) -> None:
        rows = parse_master_index(_MINIMAL_MASTER_INDEX)
        row = rows[0]
        client, _ = _make_stub_client(_fixture_bytes())

        filing = _run_full_chain(row, client, tmp_path)

        assert len(filing.non_derivative_transactions) == 1
        txn = filing.non_derivative_transactions[0]
        assert txn.category == "sale"
        assert txn.shares == Decimal("100000")


# ---------------------------------------------------------------------------
# Test C — cache reuse across the pipeline
# ---------------------------------------------------------------------------


class TestCacheReuseAcrossPipeline:
    """Parser-approved cache entries are reused without another request."""

    def test_second_call_returns_from_cache(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, transport = _make_stub_client(_fixture_bytes())

        # First call — cache miss.
        first, _ = _fetch_parse_promote(
            row, client=client, cache_root=tmp_path
        )
        assert first.from_cache is False

        # Second call — cache hit (same transport mock, same client).
        second, _ = _fetch_parse_promote(
            row, client=client, cache_root=tmp_path
        )
        assert second.from_cache is True

    def test_transport_called_only_once(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, transport = _make_stub_client(_fixture_bytes())

        _fetch_parse_promote(row, client=client, cache_root=tmp_path)
        _fetch_parse_promote(row, client=client, cache_root=tmp_path)

        transport.get.assert_called_once()

    def test_parsed_filing_is_identical_on_cache_hit(self, tmp_path: Path) -> None:
        row = _make_apple_row()
        client, _ = _make_stub_client(_fixture_bytes())

        filing_first = _run_full_chain(row, client, tmp_path)

        # Re-use the same client — transport is only hit once (already cached).
        client2, transport2 = _make_stub_client(_fixture_bytes())
        # Pre-seed: first download to populate cache.
        _fetch_parse_promote(row, client=client2, cache_root=tmp_path)
        # Now second call is a true cache hit.
        filing_second = _run_full_chain(row, client2, tmp_path)

        assert filing_first.accession_number == filing_second.accession_number
        assert filing_first.issuer == filing_second.issuer
        assert filing_first.reporting_owner == filing_second.reporting_owner
        assert (
            filing_first.non_derivative_transactions
            == filing_second.non_derivative_transactions
        )


# ---------------------------------------------------------------------------
# Test D — HTTP redirect rejection (Task 3.3)
# ---------------------------------------------------------------------------


class TestHttpRedirectRejection:
    """A redirect to an unapproved host must be blocked before a second request."""

    def test_http_redirect_rejection_leaves_no_validated_cache_and_stops_after_one_request(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        row = _make_apple_row()

        # Transport returns a 301 redirect to an unapproved external host.
        redirect_response = StubResponse(
            status_code=301,
            chunks=(b"",),
            headers={
                "Content-Type": "application/octet-stream",
                "Location": "https://evil.example.com/stolen-data",
            },
        )
        transport = Mock(spec=SecTransport)
        transport.get.return_value = redirect_response
        client = SecClient(
            user_agent=VALID_USER_AGENT,
            transport=cast(SecTransport, transport),
        )

        with caplog.at_level("DEBUG"), pytest.raises(SecClientSecurityError) as exc_info:
            _fetch_parse_promote(row, client=client, cache_root=tmp_path)

        # Security boundary fires before a second network request is made.
        transport.get.assert_called_once()
        # Promotion never ran — no validated cache artifact.
        assert _validated_cache_files(tmp_path) == []
        # The rejected redirect target must never appear in diagnostics or logs.
        assert "evil.example.com" not in str(exc_info.value)
        assert "evil.example.com" not in caplog.text


# ---------------------------------------------------------------------------
# Test E — extraction-stage XML security rejections (Task 3.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param("sec_form4_xxe_dtd.xml", id="xxe_dtd_pre_parse_guard"),
        pytest.param("sec_form4_deep.xml", id="deep_tree_depth_guard"),
    ],
)
class TestExtractionStageXmlSecurity:
    """XXE/DTD and excessive-depth fixtures must be rejected during extraction.

    The XXE/DTD fixture is rejected by the pre-parse DTD/entity regex guard
    *before* lxml parses; the deep fixture is rejected by the post-parse tree
    depth guard. Both occur inside ``extract_ownership_document`` (before
    parse/promote) and both raise ``SecXmlSecurityError``.
    """

    def test_xml_security_rejected_during_extraction_leaves_no_cache(
        self, fixture_name: str, tmp_path: Path
    ) -> None:
        content = (FIXTURE_DIR / fixture_name).read_bytes()
        row = _make_apple_row()
        client, _ = _make_stub_client(content)

        with pytest.raises(SecXmlSecurityError) as exc_info:
            _fetch_parse_promote(row, client=client, cache_root=tmp_path)

        assert _validated_cache_files(tmp_path) == []
        assert exc_info.value.reason == SecSecurityReason.XML


# ---------------------------------------------------------------------------
# Test F — parser-stage long-text rejection (Task 3.5)
# ---------------------------------------------------------------------------

# Fixture footnote flattens to "TASK6_FOOTNOTE_SENTINEL rule 10b5-1 plan adopted".
# Set the limit well below that length so only this field trips it; Test G proves
# the SAME fixture parses cleanly under the default policy, so this is not vacuous.
_TIGHT_LONG_TEXT_POLICY = dataclasses.replace(
    DEFAULT_SEC_SECURITY_POLICY, xml_max_long_text_chars=24
)


class TestParserStageSecurityRejection:
    """Long-text limit violation during parse must leave no cache or log leakage."""

    def test_parser_security_rejection_leaves_no_validated_cache_or_diagnostics(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        content = (FIXTURE_DIR / "sec_form4_markup_footnote.xml").read_bytes()
        row = _make_apple_row()
        client, _ = _make_stub_client(content)

        with caplog.at_level("DEBUG"):
            with pytest.raises(SecXmlSecurityError):
                _fetch_parse_promote(
                    row,
                    client=client,
                    cache_root=tmp_path,
                    policy=_TIGHT_LONG_TEXT_POLICY,
                )

        # Promotion never ran.
        assert _validated_cache_files(tmp_path) == []
        # Sentinel must not appear in any captured log record.
        assert "TASK6_FOOTNOTE_SENTINEL" not in caplog.text


# ---------------------------------------------------------------------------
# Test G — markup footnote pipeline under default policy (Task 3.6)
# ---------------------------------------------------------------------------


class TestMarkupFootnotePipeline:
    """Under the default policy, markup footnotes are flattened to plain text."""

    def test_markup_footnote_pipeline_flattens_text_without_log_leakage(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        content = (FIXTURE_DIR / "sec_form4_markup_footnote.xml").read_bytes()
        row = _make_apple_row()
        client, _ = _make_stub_client(content)

        with caplog.at_level("DEBUG"):
            _, filing = _fetch_parse_promote(
                row, client=client, cache_root=tmp_path
            )

        assert len(filing.footnotes) == 1
        footnote = filing.footnotes[0]

        # Footnote text must be whitespace-normalised plain text — no XML markup.
        assert "<" not in footnote.text
        assert ">" not in footnote.text

        # Sentinel IS the footnote content, so it must appear in the parsed value.
        assert "TASK6_FOOTNOTE_SENTINEL" in footnote.text

        # But it must not leak into logs.
        assert "TASK6_FOOTNOTE_SENTINEL" not in caplog.text
