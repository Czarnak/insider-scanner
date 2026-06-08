"""Persistence-specific errors."""


class PersistenceError(RuntimeError):
    """Raised when database initialization or migration fails."""
