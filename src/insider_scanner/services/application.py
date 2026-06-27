"""Application-level ownership of persistence-backed scan services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from insider_scanner.core.sec_client import SecClient
from insider_scanner.core.sec_security import (
    DEFAULT_SEC_SECURITY_POLICY,
    SecSecurityPolicy,
)
from insider_scanner.services.congress import CongressScanService
from insider_scanner.services.context import PersistenceContext, open_persistence
from insider_scanner.services.european import EuropeanScanService
from insider_scanner.services.sec_backfill import SecBackfillService
from insider_scanner.services.sec_comparison import SecComparisonService
from insider_scanner.services.sec_daily import SecDailyIngestionService
from insider_scanner.services.us import UsScanService


@dataclass(frozen=True)
class ApplicationServices:
    """Shared services for one CLI invocation or GUI process."""

    persistence: PersistenceContext
    us: UsScanService
    congress: CongressScanService
    european: EuropeanScanService

    def close(self) -> None:
        self.persistence.close()

    def make_sec_daily(
        self,
        *,
        client: SecClient,
        cache_root: Path,
        policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
        cleanup: bool = True,
        continue_on_error: bool = True,
        checkpoint_path: Path | None = None,
    ) -> SecDailyIngestionService:
        """Lazily construct a daily SEC ingestion service.

        The ``SecClient`` is supplied by the caller (CLI/GUI boundary) rather
        than eagerly built in :func:`open_application_services`, because it needs
        a user-agent and network policy that most processes never use.
        """
        return SecDailyIngestionService(
            self.persistence,
            client=client,
            cache_root=cache_root,
            policy=policy,
            cleanup=cleanup,
            continue_on_error=continue_on_error,
            checkpoint_path=checkpoint_path,
        )

    def make_sec_comparison(self) -> SecComparisonService:
        """Construct a local DB comparison service for SEC validation reports."""
        return SecComparisonService(self.persistence)
    def make_sec_backfill(
        self,
        *,
        client: SecClient,
        cache_root: Path,
        policy: SecSecurityPolicy = DEFAULT_SEC_SECURITY_POLICY,
        cleanup: bool = True,
        checkpoint_path: Path | None = None,
    ) -> SecBackfillService:
        """Lazily construct a full bulk backfill service (CLI/GUI supplies the client)."""
        return SecBackfillService(
            self.persistence,
            client=client,
            cache_root=cache_root,
            policy=policy,
            cleanup=cleanup,
            checkpoint_path=checkpoint_path,
        )


def open_application_services() -> ApplicationServices:
    """Bootstrap persistence and construct all scan services."""
    persistence = open_persistence()
    return ApplicationServices(
        persistence=persistence,
        us=UsScanService(persistence),
        congress=CongressScanService(persistence),
        european=EuropeanScanService(persistence),
    )
