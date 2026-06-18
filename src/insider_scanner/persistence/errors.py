"""Persistence-specific errors and error classification."""

from __future__ import annotations

import errno as _errno

#: POSIX errno values that indicate the OS denied access to a resource.
_ACCESS_ERRNOS: frozenset[int] = frozenset({_errno.EACCES, _errno.EPERM})

#: Windows ``winerror`` codes: 5 = ERROR_ACCESS_DENIED, 32 = ERROR_SHARING_VIOLATION
#: (the latter is what surfaces when the SQLite file is locked by another process).
_ACCESS_WINERRORS: frozenset[int] = frozenset({5, 32})

#: Substrings (lowercase) in a SQLite/SQLAlchemy ``OperationalError`` message that
#: indicate an access/permission/lock problem rather than a query bug.
_ACCESS_MESSAGE_FRAGMENTS: tuple[str, ...] = (
    "unable to open database file",
    "readonly database",
    "database is locked",
    "permission denied",
)

#: Bound on how far the ``__cause__`` / ``__context__`` chain is walked, so a
#: cyclic chain can never spin forever.
_MAX_CHAIN_DEPTH = 20


class PersistenceError(RuntimeError):
    """Raised when database initialization or migration fails."""


def _is_access_link(exc: BaseException) -> bool:
    """Return True if *exc* itself (ignoring its chain) denotes denied access."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in _ACCESS_ERRNOS:
            return True
        if getattr(exc, "winerror", None) in _ACCESS_WINERRORS:
            return True
    # SQLite / SQLAlchemy raise OperationalError for access, lock and read-only
    # problems. Match by class name so this module needs no DB-driver imports.
    if type(exc).__name__ == "OperationalError":
        message = str(exc).casefold()
        if any(fragment in message for fragment in _ACCESS_MESSAGE_FRAGMENTS):
            return True
    return False


def is_access_denied(exc: BaseException | None) -> bool:
    """Return True if *exc* (or anything in its cause/context chain) is an
    OS-level access, permission, read-only or file-lock failure.

    Pure and dependency-light: the underlying DB driver need not be importable.
    The chain walk is depth-bounded and cycle-safe.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_MAX_CHAIN_DEPTH):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if _is_access_link(current):
            return True
        current = current.__cause__ or current.__context__
    return False
