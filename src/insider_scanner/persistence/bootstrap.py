"""Database bootstrap and ordered migration execution."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from insider_scanner.persistence.engine import SQLITE_IMMEDIATE_OPTION
from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.migrations import MIGRATIONS, Migration
from insider_scanner.persistence.schema import schema_version


def _validate_migrations(migrations: Sequence[Migration]) -> None:
    """Require unique versions ordered contiguously from version one."""
    versions = [migration.version for migration in migrations]
    seen: set[int] = set()
    for version in versions:
        if version in seen:
            raise PersistenceError(f"Migrations contain duplicate version {version}")
        seen.add(version)

    if any(current < previous for previous, current in zip(versions, versions[1:])):
        raise PersistenceError(
            f"Migration versions must be strictly increasing; got {versions}"
        )

    for expected, actual in enumerate(versions, start=1):
        if actual != expected:
            raise PersistenceError(
                f"Migration sequence is missing version {expected}; got {versions}"
            )


def _validate_schema_history(
    history: Sequence[object],
    migrations: Sequence[Migration],
) -> int:
    """Validate stored versions as an exact supported contiguous prefix."""
    versions = list(history)
    supported_versions = [migration.version for migration in migrations]

    for version in versions:
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PersistenceError(
                f"Stored schema history contains invalid version {version!r}: {versions}"
            )

    seen: set[int] = set()
    for version in versions:
        if version in seen:
            raise PersistenceError(
                f"Stored schema history contains duplicate version {version}: {versions}"
            )
        seen.add(version)

    if versions:
        highest_supported = supported_versions[-1] if supported_versions else 0
        future_versions = [
            version for version in versions if version > highest_supported
        ]
        if future_versions:
            raise PersistenceError(
                "Stored schema history contains unsupported future version "
                f"{min(future_versions)}: {versions}"
            )

        sorted_versions = sorted(versions)
        expected = list(range(1, sorted_versions[-1] + 1))
        if sorted_versions != expected:
            missing = next(
                version for version in expected if version not in sorted_versions
            )
            raise PersistenceError(
                f"Stored schema history is missing version {missing}: {versions}"
            )

    return max(versions, default=0)


def bootstrap_database(
    engine: Engine,
    *,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> None:
    """Create the version table and apply pending migrations atomically."""
    _validate_migrations(migrations)

    try:
        with engine.connect() as raw_connection:
            connection = raw_connection.execution_options(
                **{SQLITE_IMMEDIATE_OPTION: True}
            )
            with connection.begin():
                schema_version.create(connection, checkfirst=True)
                history = list(
                    connection.execute(
                        select(schema_version.c.version).order_by(
                            schema_version.c.version
                        )
                    ).scalars()
                )
                current_version = _validate_schema_history(history, migrations)

                for migration in migrations:
                    if migration.version <= current_version:
                        continue
                    try:
                        migration.upgrade(connection)
                        connection.execute(
                            insert(schema_version).values(version=migration.version)
                        )
                    except Exception as exc:
                        raise PersistenceError(
                            f"Database migration {migration.version} "
                            f"({migration.name}) failed"
                        ) from exc
    except PersistenceError:
        raise
    except Exception as exc:
        raise PersistenceError("Failed to bootstrap database") from exc
