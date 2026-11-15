"""Shared SEC filing fetch, validation, parsing and outcome contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from insider_scanner.core import sec_index
from insider_scanner.core._sec_xml import SecXmlSecurityError
from insider_scanner.core.sec_client import (
    SecBlockedError,
    SecClient,
    SecClientError,
    SecClientSecurityError,
    SecDecodeError,
    SecFilingNotFoundError,
    SecHttpError,
    SecTransportError,
)
from insider_scanner.core.sec_downloader import (
    PendingSecFiling,
    SecDownloadError,
    SecDownloadSecurityError,
    fetch_filing,
    promote_validated_filing,
    quarantine_failed_download,
)
from insider_scanner.core.sec_ownership_document import (
    SecOwnershipDocumentError,
    UnsupportedOwnershipDocumentError,
    extract_ownership_document,
)
from insider_scanner.core.sec_ownership_parser import (
    OwnershipFiling,
    SecOwnershipParseError,
    parse_ownership_document,
)
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)
from insider_scanner.core.sec_trade_mapping import SecTradeMappingError
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.utils.logging import get_logger

_log = get_logger("sec_ingestion")
_COUNTER_KEYS = (
    "discovered",
    "downloaded",
    "parsed",
    "skipped",
    "failed",
    "unsupported",
)
_RECOVERABLE = (
    SecClientError,
    SecDownloadError,
    SecOwnershipDocumentError,
    SecOwnershipParseError,
    SecXmlSecurityError,
)


class SecFilingStatus(str, Enum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SecFilingOutcome:
    """One filing's disposition, with sanitized provenance for retries."""

    accession: str | None
    status: SecFilingStatus
    stage: str = ""
    reason: str = ""

    @property
    def complete(self) -> bool:
        return self.status is not SecFilingStatus.RETRYABLE_FAILURE


def process_filing_row(
    row: sec_index.SecMasterIndexRow,
    *,
    client: SecClient,
    cache_root: Path,
    counters: dict[str, int],
    retain_failed_downloads: bool = False,
    cache: bool = True,
    policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
    on_filing_parsed: Callable[[sec_index.SecMasterIndexRow, OwnershipFiling], None]
    | None = None,
) -> SecFilingOutcome:
    """Process a single filing.

    Transient fetch and sink failures leave the filing retryable. Deterministic
    rejections and unsupported legacy documents are terminal, explicit outcomes.
    Optional validated-cache writes never prevent the persist sink from running.
    """
    accession = _accession_from_path(row.archive_path)
    try:
        pending = fetch_filing(row, client=client, cache_root=cache_root, cache=cache)
    except SecBlockedError as exc:
        _log_recoverable_level(
            "SEC filing failed stage=fetch cik=%s reason=%s",
            row.cik,
            exc,
            accession=accession,
        )
        raise
    except _RECOVERABLE as exc:
        counters["failed"] += 1
        _log_recoverable_level(
            "SEC filing failed stage=fetch cik=%s reason=%s",
            row.cik,
            exc,
            accession=accession,
        )
        return SecFilingOutcome(
            accession, _fetch_failure_status(exc), "fetch", _failure_reason(exc)
        )

    if pending.from_cache:
        counters["skipped"] += 1
    else:
        counters["downloaded"] += 1

    try:
        document = extract_ownership_document(
            pending.content, accession_number=accession, policy=policy
        )
        filing = parse_ownership_document(document, policy=policy)
    except SecBlockedError:
        raise
    except UnsupportedOwnershipDocumentError as exc:
        counters["unsupported"] += 1
        _log.info(
            "SEC filing unsupported accession=%s reason=%s",
            accession,
            type(exc).__name__,
        )
        return SecFilingOutcome(
            accession, SecFilingStatus.UNSUPPORTED, "parse", type(exc).__name__
        )
    except _RECOVERABLE as exc:
        counters["failed"] += 1
        _log_recoverable_level(
            "SEC filing failed stage=parse cik=%s reason=%s",
            row.cik,
            exc,
            accession=accession,
        )
        _maybe_quarantine(
            pending, cache_root=cache_root, enabled=retain_failed_downloads and cache
        )
        return SecFilingOutcome(
            accession, SecFilingStatus.PERMANENT_FAILURE, "parse", type(exc).__name__
        )
    counters["parsed"] += 1

    if on_filing_parsed is not None:
        try:
            on_filing_parsed(row, filing)
        except SecBlockedError:
            raise
        except (*_RECOVERABLE, PersistenceError, SecTradeMappingError) as exc:
            counters["failed"] += 1
            _log_recoverable_level(
                "SEC filing failed stage=persist cik=%s reason=%s",
                row.cik,
                exc,
                accession=accession,
            )
            stage = "mapping" if isinstance(exc, SecTradeMappingError) else "persist"
            return SecFilingOutcome(
                accession, SecFilingStatus.RETRYABLE_FAILURE, stage, type(exc).__name__
            )

    if cache:
        try:
            promote_validated_filing(pending, cache_root=cache_root)
        except SecDownloadError as exc:
            _log.warning(
                "SEC optional cache write failed accession=%s reason=%s",
                accession,
                type(exc).__name__,
            )
    return SecFilingOutcome(accession, SecFilingStatus.SUCCESS)


def _fetch_failure_status(exc: Exception) -> SecFilingStatus:
    if isinstance(
        exc, (SecFilingNotFoundError, SecClientSecurityError, SecDownloadSecurityError)
    ):
        return SecFilingStatus.PERMANENT_FAILURE
    if (
        isinstance(exc, SecHttpError)
        and 400 <= exc.status_code < 500
        and exc.status_code not in {403, 408, 429}
    ):
        return SecFilingStatus.PERMANENT_FAILURE
    return SecFilingStatus.RETRYABLE_FAILURE


def _failure_reason(exc: Exception) -> str:
    """Keep structured request context without arbitrary exception text."""
    parts = [type(exc).__name__]
    if isinstance(exc, SecHttpError):
        parts.append(f"http_status={exc.status_code}")
    if isinstance(
        exc, (SecHttpError, SecTransportError, SecDecodeError, SecClientSecurityError)
    ):
        # The SEC client sanitizes these labels at exception construction.
        parts.append(f"url={exc.url}")
    if isinstance(exc, SecBlockedError):
        parts.append(f"retry_after_seconds={exc.retry_after_seconds:g}")
    return " ".join(parts)


def _log_recoverable_level(
    message: str,
    identifier: str,
    exc: Exception,
    *,
    accession: str | None = None,
) -> None:
    """Log a recoverable failure, escalating security-policy violations to ERROR.

    A :class:`SecClientSecurityError` (blocked host, disallowed redirect,
    content-type or size violation) is not normal for a legitimate SEC archive
    path, so it is surfaced at ERROR even though the batch continues.
    """
    reason = _failure_reason(exc)
    args: tuple[str, ...]
    if accession is not None:
        message += " accession=%s"
        args = (identifier, reason, accession)
    else:
        args = (identifier, reason)
    if isinstance(exc, SecClientSecurityError):
        _log.error(message, *args)
    else:
        _log.warning(message, *args)


def _maybe_quarantine(
    pending: PendingSecFiling, *, cache_root: Path, enabled: bool
) -> None:
    # Only freshly downloaded (non-cache) bytes are worth retaining for debug.
    if not enabled or pending.from_cache:
        return
    try:
        quarantine_failed_download(pending, cache_root=cache_root)
    except SecDownloadError:
        _log.warning("SEC diagnostic quarantine failed cik=%s", pending.row.cik)


def _accession_from_path(archive_path: str) -> str | None:
    stem = archive_path.rsplit("/", 1)[-1]
    for suffix in (".txt", ".xml"):
        if stem.casefold().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem or None


def new_counters() -> dict[str, int]:
    return {key: 0 for key in _COUNTER_KEYS}
