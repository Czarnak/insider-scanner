"""Shared offline SEC responses, clients and sample indexes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast
from unittest.mock import Mock

from insider_scanner.core.sec_client import SecClient, SecTransport
from insider_scanner.core.sec_fair_access import (
    RateLimiter,
    SecCircuitBreaker,
    SecRetryPolicy,
)

VALID_USER_AGENT = "Insider Scanner ops@insider-scanner.example"

FIXTURES = Path(__file__).parent / "fixtures"

VALID_FILING = (FIXTURES / "sec_form4_primary.xml").read_bytes()

INDEX_TWO_ROWS = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n"
    "-" * 60 + "\n"
    "320193|APPLE INC|4|2026-06-15|edgar/data/320193/0000320193-26-000061.txt\n"
    "789019|MICROSOFT CORP|4|2026-06-15|edgar/data/789019/0000789019-26-000010.txt\n"
)


@dataclass(slots=True)
class StubResponse:
    status_code: int = 200
    chunks: tuple[bytes, ...] = (b"",)
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"Content-Type": "text/plain"}
    )
    close_calls: int = 0

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield from self.chunks

    def close(self) -> None:
        self.close_calls += 1


def make_transport(
    *,
    index_text: str = INDEX_TWO_ROWS,
    filing_bytes: bytes = VALID_FILING,
    index_status: int = 200,
    fetched: list[str] | None = None,
) -> Mock:
    def _get(url: str, **_kwargs: object) -> StubResponse:
        if fetched is not None:
            fetched.append(url)
        if url.endswith(".idx"):
            if index_status != 200:
                return StubResponse(status_code=index_status, chunks=())
            return StubResponse(
                chunks=(index_text.encode("utf-8"),),
                # SEC serves daily-index .idx files as application/octet-stream.
                headers={"Content-Type": "application/octet-stream"},
            )
        return StubResponse(
            chunks=(filing_bytes,), headers={"Content-Type": "application/xml"}
        )

    transport = Mock(spec=SecTransport)
    transport.get.side_effect = _get
    return transport


def make_client(transport: Mock) -> SecClient:
    return SecClient(
        user_agent=VALID_USER_AGENT,
        transport=cast(SecTransport, transport),
        rate_limiter=RateLimiter(min_interval_seconds=0.0),
        circuit_breaker=SecCircuitBreaker(),
        retry_policy=SecRetryPolicy(base_delay_seconds=0.0),
    )


DAY = date(2026, 6, 15)

NEXT_DAY = date(2026, 6, 16)

# Only the Form 4 reaches the ownership sink; the 8-K is filtered out.
INDEX_MIXED = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 60 + "\n"
    "320193|APPLE INC|4|2026-06-15|edgar/data/320193/0000320193-26-000061.txt\n"
    "789019|MICROSOFT CORP|8-K|2026-06-15|edgar/data/789019/0000789019-26-000010.txt\n"
)

# One ownership filing on the first day of a two-day range.
INDEX_ONE_ROW = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 60 + "\n"
    "320193|APPLE INC|4|2026-06-15|edgar/data/320193/0000320193-26-000061.txt\n"
)

# A different ownership filing on the second day.
INDEX_DAY_TWO = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 60 + "\n"
    "789019|MICROSOFT CORP|4|2026-06-16|edgar/data/789019/0000789019-26-000010.txt\n"
)

EMPTY_INDEX = (
    "Description: daily index\n"
    "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 60 + "\n"
)

FIXTURE_ZIP = FIXTURES / "sec_submissions_bulk_small.zip"
