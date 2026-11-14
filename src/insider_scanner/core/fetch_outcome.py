"""Explicit completeness metadata for fetched and persisted source records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

RecordT = TypeVar("RecordT")


class FetchStatus(str, Enum):
    COMPLETE = "complete"
    EMPTY = "empty"
    PARTIAL = "partial"
    TRANSIENT_FAILURE = "transient_failure"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class FetchIssue:
    """A safe diagnostic; never include response bodies or credentials."""

    reason: str
    source: str = ""
    identifier: str = ""
    retryable: bool = True


class FetchResult(list[RecordT], Generic[RecordT]):
    """List-compatible records with explicit source completeness.

    Only COMPLETE and EMPTY prove coverage. Partial records remain usable but
    must not suppress a retry. UNSUPPORTED describes a deterministic limitation,
    rather than a successful empty response.
    """

    def __init__(
        self,
        records: Iterable[RecordT] = (),
        *,
        status: FetchStatus | None = None,
        issues: Iterable[FetchIssue] = (),
    ) -> None:
        super().__init__(records)
        self.issues = tuple(issues)
        if status is None:
            if self.issues:
                status = FetchStatus.PARTIAL if self else FetchStatus.TRANSIENT_FAILURE
            else:
                status = FetchStatus.COMPLETE if self else FetchStatus.EMPTY
        if self.issues and status in {FetchStatus.COMPLETE, FetchStatus.EMPTY}:
            raise ValueError("a complete fetch must not contain unresolved issues")
        self.status = status

    @property
    def complete(self) -> bool:
        return self.status in {FetchStatus.COMPLETE, FetchStatus.EMPTY}


def as_fetch_result(records: list[RecordT]) -> FetchResult[RecordT]:
    """Adapt legacy injected list adapters without discarding existing metadata."""
    return records if isinstance(records, FetchResult) else FetchResult(records)


def describe_incomplete(records: list[RecordT]) -> str:
    """Describe incomplete results without hiding usable records from callers."""
    if not isinstance(records, FetchResult) or records.complete:
        return ""
    details = []
    for issue in records.issues[:3]:
        label = " ".join(part for part in (issue.source, issue.identifier) if part)
        reason = issue.reason.replace("_", " ")
        details.append(f"{label}: {reason}" if label else reason)
    if len(records.issues) > 3:
        count = len(records.issues) - 3
        details.append(f"{count} more issue{'s' if count != 1 else ''}")
    detail = "; ".join(details) or records.status.value.replace("_", " ")
    return f"Incomplete results ({detail})"
