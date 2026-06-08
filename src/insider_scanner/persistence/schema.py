"""SQLAlchemy Core metadata and version-one table definitions."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

from insider_scanner.persistence.types import UTCDateTime


v1_metadata = MetaData()
metadata = v1_metadata

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, primary_key=True),
    Column(
        "applied_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    CheckConstraint("version > 0", name="ck_schema_version_positive"),
)

us_trades = Table(
    "us_trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("canonical_key", String(128), nullable=False),
    Column("ticker", String(32), nullable=False),
    Column("company", String(255), nullable=False, server_default=""),
    Column("insider_name", String(255), nullable=False, server_default=""),
    Column("insider_title", String(255), nullable=False, server_default=""),
    Column("trade_type", String(16), nullable=False),
    Column("trade_date", Date),
    Column("filing_date", Date),
    Column("shares", Float, nullable=False, server_default="0"),
    Column("price", Float, nullable=False, server_default="0"),
    Column("value", Float, nullable=False, server_default="0"),
    Column("shares_owned_after", Float, nullable=False, server_default="0"),
    Column("source", String(32), nullable=False),
    Column(
        "source_provenance_json",
        Text,
        nullable=False,
        server_default="[]",
    ),
    Column("edgar_url", Text, nullable=False, server_default=""),
    Column("is_congress", Boolean, nullable=False, server_default=text("0")),
    Column("congress_member", String(255), nullable=False, server_default=""),
    Column(
        "created_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("canonical_key", name="uq_us_trades_canonical_key"),
    CheckConstraint(
        "trade_type IN ('Buy', 'Sell', 'Exercise', 'Other')",
        name="ck_us_trades_trade_type",
    ),
    CheckConstraint("shares >= 0", name="ck_us_trades_shares_nonnegative"),
    CheckConstraint("price >= 0", name="ck_us_trades_price_nonnegative"),
    CheckConstraint("value >= 0", name="ck_us_trades_value_nonnegative"),
)
Index(
    "ix_us_trades_ticker_filing_date",
    us_trades.c.ticker,
    us_trades.c.filing_date,
)
Index(
    "ix_us_trades_source_filing_date",
    us_trades.c.source,
    us_trades.c.filing_date,
)

congress_trades = Table(
    "congress_trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("canonical_key", String(128), nullable=False),
    Column("official_name", String(255), nullable=False),
    Column("chamber", String(16), nullable=False),
    Column("party", String(32), nullable=False, server_default=""),
    Column("filing_date", Date),
    Column("doc_id", String(128), nullable=False, server_default=""),
    Column("source_url", Text, nullable=False, server_default=""),
    Column("trade_date", Date),
    Column("asset_description", Text, nullable=False, server_default=""),
    Column("ticker", String(32), nullable=False, server_default=""),
    Column("trade_type", String(16), nullable=False),
    Column("owner", String(64), nullable=False, server_default=""),
    Column("amount_range", String(128), nullable=False, server_default=""),
    Column("amount_low", Float, nullable=False, server_default="0"),
    Column("amount_high", Float, nullable=False, server_default="0"),
    Column("comment", Text, nullable=False, server_default=""),
    Column("source", String(16), nullable=False),
    Column(
        "created_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("canonical_key", name="uq_congress_trades_canonical_key"),
    CheckConstraint(
        "chamber IN ('', 'House', 'Senate')",
        name="ck_congress_trades_chamber",
    ),
    CheckConstraint(
        "trade_type IN ('Purchase', 'Sale', 'Exchange', 'Other')",
        name="ck_congress_trades_trade_type",
    ),
    CheckConstraint(
        "source IN ('', 'house', 'senate')",
        name="ck_congress_trades_source",
    ),
    CheckConstraint(
        "amount_low >= 0 AND amount_high >= amount_low",
        name="ck_congress_trades_amount_range",
    ),
)
Index(
    "ix_congress_trades_official_filing_date",
    congress_trades.c.official_name,
    congress_trades.c.filing_date,
)
Index(
    "ix_congress_trades_source_filing_date",
    congress_trades.c.source,
    congress_trades.c.filing_date,
)

european_trades = Table(
    "european_trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("canonical_key", String(128), nullable=False),
    Column("isin", String(12), nullable=False),
    Column("issuer_name", String(255), nullable=False, server_default=""),
    Column("country", String(2), nullable=False),
    Column("regulatory_body", String(16), nullable=False, server_default=""),
    Column("insider_name", String(255), nullable=False, server_default=""),
    Column("position", String(128), nullable=False, server_default=""),
    Column("trade_date", Date),
    Column("filing_date", Date),
    Column("trade_type", String(16), nullable=False),
    Column("instrument_type", String(128), nullable=False, server_default=""),
    Column("volume", Float),
    Column("price", Float),
    Column("currency", String(3), nullable=False, server_default=""),
    Column("total_value", Float),
    Column("source", String(32), nullable=False),
    Column(
        "source_provenance_json",
        Text,
        nullable=False,
        server_default="[]",
    ),
    Column("source_url", Text, nullable=False, server_default=""),
    Column(
        "created_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    Column(
        "updated_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint("canonical_key", name="uq_european_trades_canonical_key"),
    CheckConstraint(
        "country IN ('', 'UK', 'DE', 'FR', 'NL')",
        name="ck_european_trades_country",
    ),
    CheckConstraint(
        "trade_type IN ('Buy', 'Sell', 'Other')",
        name="ck_european_trades_trade_type",
    ),
    CheckConstraint(
        "volume IS NULL OR volume >= 0",
        name="ck_european_trades_volume_nonnegative",
    ),
    CheckConstraint(
        "price IS NULL OR price >= 0",
        name="ck_european_trades_price_nonnegative",
    ),
    CheckConstraint(
        "total_value IS NULL OR total_value >= 0",
        name="ck_european_trades_total_value_nonnegative",
    ),
)
Index(
    "ix_european_trades_isin_trade_date",
    european_trades.c.isin,
    european_trades.c.trade_date,
)
Index(
    "ix_european_trades_source_trade_date",
    european_trades.c.source,
    european_trades.c.trade_date,
)

scan_coverage = Table(
    "scan_coverage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("domain", String(32), nullable=False),
    Column("source", String(32), nullable=False),
    Column("identifier", String(64), nullable=False, server_default=""),
    Column("start_date", Date, nullable=False),
    Column("end_date", Date, nullable=False),
    Column(
        "covered_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint(
        "domain",
        "source",
        "identifier",
        "start_date",
        "end_date",
        name="uq_scan_coverage_interval",
    ),
    CheckConstraint(
        "end_date >= start_date",
        name="ck_scan_coverage_date_order",
    ),
)
Index(
    "ix_scan_coverage_lookup",
    scan_coverage.c.domain,
    scan_coverage.c.source,
    scan_coverage.c.identifier,
    scan_coverage.c.start_date,
    scan_coverage.c.end_date,
)

refresh_state = Table(
    "refresh_state",
    metadata,
    Column("domain", String(32), primary_key=True),
    Column("source", String(32), primary_key=True),
    Column("identifier", String(64), primary_key=True, server_default=""),
    Column("mode", String(32), primary_key=True),
    Column("refreshed_at", UTCDateTime(), nullable=False),
    Column("metadata_json", Text, nullable=False, server_default="{}"),
)
Index("ix_refresh_state_refreshed_at", refresh_state.c.refreshed_at)

price_history = Table(
    "price_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("symbol", String(32), nullable=False),
    Column("price_date", Date, nullable=False),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("adjusted_close", Float),
    Column("volume", BigInteger),
    Column(
        "created_at",
        UTCDateTime(),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
    UniqueConstraint(
        "symbol",
        "price_date",
        name="uq_price_history_symbol_date",
    ),
    CheckConstraint(
        "open >= 0 AND high >= 0 AND low >= 0 AND close >= 0",
        name="ck_price_history_prices_nonnegative",
    ),
    CheckConstraint(
        "high >= low",
        name="ck_price_history_high_low",
    ),
    CheckConstraint(
        "adjusted_close IS NULL OR adjusted_close >= 0",
        name="ck_price_history_adjusted_close_nonnegative",
    ),
    CheckConstraint(
        "volume IS NULL OR volume >= 0",
        name="ck_price_history_volume_nonnegative",
    ),
)
Index(
    "ix_price_history_symbol_date",
    price_history.c.symbol,
    price_history.c.price_date,
)

# Runtime metadata is intentionally separate from the immutable migration-one
# snapshot. Later runtime tables must be introduced by later migrations.
metadata = MetaData()
for _table in v1_metadata.sorted_tables:
    _table.to_metadata(metadata)

schema_version = metadata.tables["schema_version"]
us_trades = metadata.tables["us_trades"]
congress_trades = metadata.tables["congress_trades"]
european_trades = metadata.tables["european_trades"]
scan_coverage = metadata.tables["scan_coverage"]
refresh_state = metadata.tables["refresh_state"]
price_history = metadata.tables["price_history"]
