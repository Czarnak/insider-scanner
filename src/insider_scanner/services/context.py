"""Application persistence context for scan services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from insider_scanner.persistence import bootstrap_database, create_sqlite_engine
from insider_scanner.persistence.coverage import CoverageRepository
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.refresh import RefreshStateRepository
from insider_scanner.persistence.repositories import (
    CongressTradeRepository,
    EuropeanTradeRepository,
    UsTradeRepository,
)
from insider_scanner.utils.config import DATABASE_FILE


@dataclass(frozen=True)
class PersistenceContext:
    engine: Engine
    us_trades: UsTradeRepository
    congress_trades: CongressTradeRepository
    european_trades: EuropeanTradeRepository
    coverage: CoverageRepository
    refresh: RefreshStateRepository

    def close(self) -> None:
        self.engine.dispose()


def open_persistence(
    database_file: Path | None = None,
) -> PersistenceContext:
    """Open and bootstrap persistence, never returning a partial context."""
    engine: Engine | None = None
    try:
        engine = create_sqlite_engine(database_file or DATABASE_FILE)
        bootstrap_database(engine)
        return PersistenceContext(
            engine=engine,
            us_trades=UsTradeRepository(engine),
            congress_trades=CongressTradeRepository(engine),
            european_trades=EuropeanTradeRepository(engine),
            coverage=CoverageRepository(engine),
            refresh=RefreshStateRepository(engine),
        )
    except PersistenceError:
        if engine is not None:
            engine.dispose()
        raise
    except Exception as error:
        if engine is not None:
            engine.dispose()
        raise PersistenceError("Failed to open application persistence") from error
