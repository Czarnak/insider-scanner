"""UTC refresh-state storage and deterministic freshness checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from insider_scanner.persistence.errors import PersistenceError
from insider_scanner.persistence.schema import refresh_state


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def is_fresh(
    refreshed_at: datetime | None,
    *,
    now: datetime,
    ttl: timedelta,
) -> bool:
    if ttl < timedelta(0):
        raise ValueError("ttl must not be negative")
    current = _aware_utc(now)
    if refreshed_at is None:
        return False
    refreshed = _aware_utc(refreshed_at)
    # Clamp effective age at zero by treating future clock-skew values as fresh.
    return current - refreshed < ttl


class RefreshStateRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _conditions(domain: str, identifier: str, source: str, mode: str):
        return and_(
            refresh_state.c.domain == domain,
            refresh_state.c.identifier == identifier,
            refresh_state.c.source == source,
            refresh_state.c.mode == mode,
        )

    def get(
        self, domain: str, identifier: str, source: str, mode: str
    ) -> datetime | None:
        try:
            with self._engine.begin() as connection:
                return connection.execute(
                    select(refresh_state.c.refreshed_at).where(
                        self._conditions(domain, identifier, source, mode)
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PersistenceError("refresh state query failed") from error

    def set(
        self,
        domain: str,
        identifier: str,
        source: str,
        mode: str,
        refreshed_at: datetime,
    ) -> None:
        value = _aware_utc(refreshed_at)
        values = {
            "domain": domain,
            "identifier": identifier,
            "source": source,
            "mode": mode,
            "refreshed_at": value,
        }
        try:
            with self._engine.begin() as connection:
                statement = insert(refresh_state).values(values)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            refresh_state.c.domain,
                            refresh_state.c.identifier,
                            refresh_state.c.source,
                            refresh_state.c.mode,
                        ],
                        set_={"refreshed_at": value},
                    )
                )
        except SQLAlchemyError as error:
            raise PersistenceError("refresh state update failed") from error
