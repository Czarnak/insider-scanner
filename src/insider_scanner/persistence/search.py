"""Transaction-local trigram indexes for literal Unicode substring searches."""

from __future__ import annotations

from sqlalchemy import Integer, Table, column, select, table
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

_FIELDS = (
    "market",
    "transaction_type",
    "issuer",
    "identifier",
    "person",
    "role",
    "source",
)
_SOURCES = {
    "us_trades": (
        "US",
        ("trade_type", "company", "ticker", "insider_name", "insider_title", "source"),
    ),
    "congress_trades": (
        "Congress",
        (
            "trade_type",
            "asset_description",
            "ticker",
            "official_name",
            "chamber",
            "source",
        ),
    ),
    "european_trades": (
        "Europe",
        ("trade_type", "issuer_name", "isin", "insider_name", "position", "source"),
    ),
}


def install_search_indexes(connection: Connection) -> None:
    """Backfill and maintain normalized search data in the caller's transaction.

    Every identifier comes from the fixed schema map above. Triggers execute
    inside the trade write transaction, so committed search hits and source
    records agree after inserts, corrections, deletions, and rollbacks.
    """
    fields = ", ".join(_FIELDS)
    for source_name, (market, source_fields) in _SOURCES.items():
        index_name = f"{source_name}_search"
        connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {index_name} "
            f"USING fts5({fields}, tokenize='trigram case_sensitive 1')"
        )
        old_values = ", ".join(
            [f"casefold('{market}')"] + [f"casefold({name})" for name in source_fields]
        )
        new_values = ", ".join(
            [f"casefold('{market}')"]
            + [f"casefold(new.{name})" for name in source_fields]
        )
        connection.exec_driver_sql(
            f"INSERT INTO {index_name} (rowid, {fields}) "
            f"SELECT id, {old_values} FROM {source_name} "
            f"WHERE id NOT IN (SELECT rowid FROM {index_name})"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {index_name}_insert "
            f"AFTER INSERT ON {source_name} BEGIN "
            f"INSERT INTO {index_name} (rowid, {fields}) "
            f"VALUES (new.id, {new_values}); END"
        )
        updated_fields = ", ".join(("id", *source_fields))
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {index_name}_update "
            f"AFTER UPDATE OF {updated_fields} ON {source_name} BEGIN "
            f"DELETE FROM {index_name} WHERE rowid = old.id; "
            f"INSERT INTO {index_name} (rowid, {fields}) "
            f"VALUES (new.id, {new_values}); END"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {index_name}_delete "
            f"AFTER DELETE ON {source_name} BEGIN "
            f"DELETE FROM {index_name} WHERE rowid = old.id; END"
        )


def search_index_condition(
    source: Table,
    search: str,
    *,
    fields: tuple[str, ...] = (),
) -> ColumnElement[bool] | None:
    """Return indexed candidate IDs, or None for a search shorter than a trigram.

    MATCH receives one quoted literal, with embedded quotes escaped. Operators,
    wildcard characters, and punctuation remain ordinary substring text. Field
    restrictions are a checked subset of the normalized feed column names.
    """
    if source.name not in _SOURCES:
        raise ValueError("Unsupported search source")
    if any(field not in _FIELDS for field in fields):
        raise ValueError("Unsupported search field")
    normalized = search.casefold()
    if len(normalized) < 3:
        return None
    phrase = '"' + normalized.replace('"', '""') + '"'
    if fields:
        phrase = "{" + " ".join(fields) + "} : " + phrase
    index_name = f"{source.name}_search"
    search_table = table(index_name, column("rowid", Integer))
    candidate_ids = select(search_table.c.rowid).where(column(index_name).match(phrase))
    return source.c.id.in_(candidate_ids)
