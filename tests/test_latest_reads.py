"""Latest reads stay bounded after large historical imports."""

from datetime import date

import pytest
from sqlalchemy import event, update

from insider_scanner.persistence import repositories
from insider_scanner.persistence.repositories import (
    EuropeanTradeRepository,
    UsTradeRepository,
)
from insider_scanner.persistence.schema import european_trades, us_trades
from tests.factories import eu_trade, us_trade


@pytest.mark.parametrize("market", ["us", "eu"])
@pytest.mark.parametrize("sources", [None, ("selected",), "selected"])
def test_latest_materializes_only_requested_count(engine, monkeypatch, market, sources):
    factory = us_trade if market == "us" else eu_trade
    repository = (
        UsTradeRepository(engine) if market == "us" else EuropeanTradeRepository(engine)
    )
    repository.upsert(
        [
            factory(
                insider_name=f"Person {day}",
                filing_date=date(2026, 4, day),
                trade_date=date(2026, 4, day),
                source="selected" if day < 5 else "other",
            )
            for day in range(1, 8)
        ]
    )
    mapper_name = "us_from_row" if market == "us" else "european_from_row"
    mapper = getattr(repositories, mapper_name)
    mapped = []

    def track(row):
        mapped.append(row["id"])
        return mapper(row)

    monkeypatch.setattr(repositories, mapper_name, track)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = repository.query_latest(2, sources=sources)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert [trade.insider_name for trade in result] == (
        ["Person 4", "Person 3"] if sources else ["Person 7", "Person 6"]
    )
    assert len(mapped) == 2
    assert len(statements) == 1
    assert "LIMIT" in statements[0].upper()


@pytest.mark.parametrize("market", ["us", "eu"])
def test_latest_filters_provenance_before_limit_and_tolerates_invalid_json(
    engine, market
):
    factory = us_trade if market == "us" else eu_trade
    table = us_trades if market == "us" else european_trades
    repository = (
        UsTradeRepository(engine) if market == "us" else EuropeanTradeRepository(engine)
    )
    repository.upsert(
        [
            factory(
                insider_name="Merged",
                source="primary",
                trade_date=date(2026, 4, 2),
                filing_date=date(2026, 4, 2),
            ),
            factory(
                insider_name="Primary fallback",
                source="selected",
                trade_date=date(2026, 4, 1),
                filing_date=date(2026, 4, 1),
            ),
            factory(
                insider_name="Unselected malformed",
                source="other",
                trade_date=date(2026, 4, 3),
                filing_date=date(2026, 4, 3),
            ),
        ]
    )
    with engine.begin() as connection:
        connection.execute(update(table).values(source_provenance_json="malformed"))
        connection.execute(
            update(table)
            .where(table.c.insider_name == "Merged")
            .values(source_provenance_json='["primary", "selected"]')
        )

    result = repository.query_latest(1, sources=(value for value in ["selected"]))
    assert [trade.insider_name for trade in result] == ["Merged"]
    result = repository.query_latest(10, sources="selected")
    assert [trade.insider_name for trade in result] == ["Merged", "Primary fallback"]
    assert repository.query_latest(1, sources="missing") == []
