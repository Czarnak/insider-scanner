"""Behavior tests for the SEC filing downloader."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import date
import hashlib
import math
import os
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from insider_scanner.core.sec_index import SecMasterIndexRow
from insider_scanner.core.sec_client import SecClient, SecTransport


VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"


@dataclass(frozen=True, slots=True)
class StubResponse:
    status_code: int
    content: bytes


def make_row(
    archive_path: str = "edgar/data/320193/0000320193-26-000061.TXT",
) -> SecMasterIndexRow:
    return SecMasterIndexRow(
        cik="0000320193",
        company_name="APPLE INC",
        form_type="4",
        filing_date=date(2026, 6, 15),
        archive_path=archive_path,
    )


def make_client(content: bytes = b"filing payload") -> tuple[SecClient, Mock]:
    transport = Mock(spec=SecTransport)
    transport.get.return_value = StubResponse(status_code=200, content=content)
    client = SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
    )
    return client, transport


def expected_cache_path(row: SecMasterIndexRow, cache_dir: Path) -> Path:
    digest = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}{Path(row.archive_path).suffix.lower()}"


def test_downloaded_sec_filing_is_frozen_and_slotted(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import DownloadedSecFiling

    row = make_row()
    downloaded = DownloadedSecFiling(
        row=row,
        archive_url="https://www.sec.gov/Archives/example.txt",
        content_path=tmp_path / "example.txt",
        from_cache=False,
    )

    assert downloaded.__slots__ == (
        "row",
        "archive_url",
        "content_path",
        "from_cache",
    )
    assert not hasattr(downloaded, "__dict__")
    with pytest.raises(FrozenInstanceError):
        downloaded.from_cache = True  # type: ignore[misc]


def test_download_fetches_exact_archive_url_to_stable_injected_cache_path(
    tmp_path: Path,
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    row = make_row()
    client, transport = make_client(content=b"private filing bytes")
    expected_url = (
        "https://www.sec.gov/Archives/"
        "edgar/data/320193/0000320193-26-000061.TXT"
    )
    expected_name = hashlib.sha256(row.archive_path.encode("utf-8")).hexdigest() + ".txt"

    result = download_filing(row, client=client, cache_dir=tmp_path)

    assert result.row is row
    assert result.archive_url == expected_url
    assert result.content_path == tmp_path / expected_name
    assert result.content_path.read_bytes() == b"private filing bytes"
    assert result.from_cache is False
    assert tuple(tmp_path.iterdir()) == (result.content_path,)
    transport.get.assert_called_once_with(
        expected_url,
        headers={"User-Agent": VALID_USER_AGENT},
        timeout=15.0,
    )


class DerivedSecMasterIndexRow(SecMasterIndexRow):
    pass


class DerivedSecClient(SecClient):
    pass


@pytest.mark.parametrize("invalid_row", [None, object(), "row"])
def test_invalid_row_type_fails_before_client_or_filesystem(
    tmp_path: Path, invalid_row: object
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    client, transport = make_client()
    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="row must be exactly SecMasterIndexRow"):
        download_filing(
            cast(SecMasterIndexRow, invalid_row), client=client, cache_dir=cache_dir
        )

    transport.get.assert_not_called()
    assert not cache_dir.exists()


def test_row_subclass_is_rejected_before_io(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    base_row = make_row()
    row = DerivedSecMasterIndexRow(
        base_row.cik,
        base_row.company_name,
        base_row.form_type,
        base_row.filing_date,
        base_row.archive_path,
    )
    client, transport = make_client()
    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="row must be exactly SecMasterIndexRow"):
        download_filing(row, client=client, cache_dir=cache_dir)

    transport.get.assert_not_called()
    assert not cache_dir.exists()


@pytest.mark.parametrize("invalid_client", [None, object(), "client"])
def test_invalid_client_type_fails_before_filesystem(
    tmp_path: Path, invalid_client: object
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="client must be exactly SecClient"):
        download_filing(
            make_row(), client=cast(SecClient, invalid_client), cache_dir=cache_dir
        )

    assert not cache_dir.exists()


def test_client_subclass_is_rejected_before_io(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    client = DerivedSecClient(user_agent=VALID_USER_AGENT)
    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="client must be exactly SecClient"):
        download_filing(make_row(), client=client, cache_dir=cache_dir)

    assert not cache_dir.exists()


@pytest.mark.parametrize("invalid_cache_dir", [None, "cache", object()])
def test_cache_dir_must_be_a_path_before_client_call(
    invalid_cache_dir: object,
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    client, transport = make_client()

    with pytest.raises(TypeError, match="cache_dir must be a pathlib.Path"):
        download_filing(
            make_row(), client=client, cache_dir=cast(Path, invalid_cache_dir)
        )

    transport.get.assert_not_called()


@pytest.mark.parametrize("invalid_ttl", [True, "1", object()])
def test_invalid_ttl_type_fails_before_client_or_filesystem(
    tmp_path: Path, invalid_ttl: object
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    client, transport = make_client()
    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(TypeError, match="max_age_seconds"):
        download_filing(
            make_row(),
            client=client,
            cache_dir=cache_dir,
            max_age_seconds=cast(float, invalid_ttl),
        )

    transport.get.assert_not_called()
    assert not cache_dir.exists()


@pytest.mark.parametrize(
    "invalid_ttl", [0.0, -1.0, math.inf, -math.inf, math.nan]
)
def test_invalid_ttl_value_fails_before_client_or_filesystem(
    tmp_path: Path, invalid_ttl: float
) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    client, transport = make_client()
    cache_dir = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="max_age_seconds"):
        download_filing(
            make_row(),
            client=client,
            cache_dir=cache_dir,
            max_age_seconds=invalid_ttl,
        )

    transport.get.assert_not_called()
    assert not cache_dir.exists()


def test_positive_ttl_reuses_fresh_regular_cache_file(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.write_bytes(b"cached filing")
    client, transport = make_client(content=b"network filing")

    result = download_filing(
        row, client=client, cache_dir=tmp_path, max_age_seconds=60.0
    )

    assert result.content_path == cache_path
    assert result.content_path.read_bytes() == b"cached filing"
    assert result.from_cache is True
    transport.get.assert_not_called()


def test_positive_ttl_refreshes_stale_cache_file(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.write_bytes(b"stale filing")
    os.utime(cache_path, (100.0, 100.0))
    client, transport = make_client(content=b"fresh filing")

    result = download_filing(
        row, client=client, cache_dir=tmp_path, max_age_seconds=10.0
    )

    assert result.content_path.read_bytes() == b"fresh filing"
    assert result.from_cache is False
    transport.get.assert_called_once()


def test_cache_age_equal_to_ttl_is_fresh(tmp_path: Path) -> None:
    from insider_scanner.core.sec_downloader import download_filing

    row = make_row()
    cache_path = expected_cache_path(row, tmp_path)
    cache_path.write_bytes(b"boundary filing")
    os.utime(cache_path, (990.0, 990.0))
    client, transport = make_client(content=b"network filing")

    with patch(
        "insider_scanner.core.sec_downloader.time.time", return_value=1000.0
    ):
        result = download_filing(
            row, client=client, cache_dir=tmp_path, max_age_seconds=10.0
        )

    assert result.from_cache is True
    assert cache_path.read_bytes() == b"boundary filing"
    transport.get.assert_not_called()
