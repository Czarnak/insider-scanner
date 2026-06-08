"""Application-level ownership of persistence-backed scan services."""

from __future__ import annotations

from dataclasses import dataclass

from insider_scanner.services.congress import CongressScanService
from insider_scanner.services.context import PersistenceContext, open_persistence
from insider_scanner.services.european import EuropeanScanService
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


def open_application_services() -> ApplicationServices:
    """Bootstrap persistence and construct all scan services."""
    persistence = open_persistence()
    return ApplicationServices(
        persistence=persistence,
        us=UsScanService(persistence),
        congress=CongressScanService(persistence),
        european=EuropeanScanService(persistence),
    )
