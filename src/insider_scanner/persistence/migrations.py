"""Ordered Python migrations for the local SQLite schema."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from insider_scanner.persistence.schema import v1_metadata


@dataclass(frozen=True)
class Migration:
    """One immutable, ordered schema migration."""

    version: int
    name: str
    upgrade: Callable[[Connection], None]


def _create_initial_schema(connection: Connection) -> None:
    v1_metadata.create_all(connection)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="create_initial_schema",
        upgrade=_create_initial_schema,
    ),
)
