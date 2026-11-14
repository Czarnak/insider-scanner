"""SQLAlchemy Core persistence foundation."""

from insider_scanner.persistence.bootstrap import bootstrap_database
from insider_scanner.persistence.coverage import DateInterval
from insider_scanner.persistence.engine import create_sqlite_engine
from insider_scanner.persistence.errors import PersistenceError

__all__ = [
    "DateInterval",
    "PersistenceError",
    "bootstrap_database",
    "create_sqlite_engine",
]
