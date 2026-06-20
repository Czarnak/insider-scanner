"""Offline end-to-end integration tests for the SEC daily-ingestion pipeline.

Wires together: SecMasterIndexRow / parse_master_index → download_filing
(stub transport, no network) → extract_ownership_document →
parse_ownership_document and asserts the normalised OwnershipFiling.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from insider_scanner.core.sec_client import SecClient, SecTransport
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
) -> tuple[DownloadedSecFiling, OwnershipFiling]:
    """Run the real fetch → parse → promote boundary in that order."""
    pending = fetch_filing(row, client=client, cache_root=cache_root)
    document = extract_ownership_document(pending.content)
    filing = parse_ownership_document(document)
    downloaded = promote_validated_filing(pending, cache_root=cache_root)
    return downloaded, filing


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
