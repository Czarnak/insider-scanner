"""Unit tests for persistence error classification."""

from __future__ import annotations

import errno as errno_mod

from insider_scanner.persistence.errors import PersistenceError, is_access_denied


class FakeOperationalError(Exception):
    """Stand-in for sqlite3 / SQLAlchemy OperationalError (matched by name)."""


def _oserror(*, errno_value=None, winerror=None, message="boom") -> OSError:
    """Build an OSError with arbitrary errno/winerror set cross-platform."""
    exc = OSError(message)
    if errno_value is not None:
        exc.errno = errno_value
    if winerror is not None:
        # winerror is a Windows-only attribute; set it directly so the test is
        # platform-independent.
        exc.winerror = winerror
    return exc


# ---------------------------------------------------------------------------
# Positive cases
# ---------------------------------------------------------------------------


def test_permission_error_is_access_denied():
    assert is_access_denied(PermissionError("denied")) is True


def test_oserror_eacces_is_access_denied():
    assert is_access_denied(_oserror(errno_value=errno_mod.EACCES)) is True


def test_oserror_eperm_is_access_denied():
    assert is_access_denied(_oserror(errno_value=errno_mod.EPERM)) is True


def test_oserror_windows_access_denied_winerror_is_access_denied():
    assert is_access_denied(_oserror(winerror=5)) is True


def test_oserror_windows_sharing_violation_winerror_is_access_denied():
    assert is_access_denied(_oserror(winerror=32)) is True


def test_operational_error_unable_to_open_is_access_denied():
    err = FakeOperationalError("unable to open database file")
    err.__class__.__name__ = "OperationalError"
    assert is_access_denied(err) is True


def test_operational_error_readonly_is_access_denied():
    class OperationalError(Exception):
        pass

    assert (
        is_access_denied(OperationalError("attempt to write a readonly database"))
        is True
    )


def test_operational_error_database_locked_is_access_denied():
    class OperationalError(Exception):
        pass

    assert is_access_denied(OperationalError("database is locked")) is True


def test_operational_error_permission_denied_message_is_access_denied():
    class OperationalError(Exception):
        pass

    assert is_access_denied(OperationalError("Permission denied")) is True


def test_chained_cause_permission_error_is_access_denied():
    wrapped = PersistenceError("Failed to query unified transaction feed")
    wrapped.__cause__ = PermissionError("denied")
    assert is_access_denied(wrapped) is True


def test_chained_context_permission_error_is_access_denied():
    wrapped = PersistenceError("save failed")
    wrapped.__context__ = _oserror(errno_value=errno_mod.EACCES)
    assert is_access_denied(wrapped) is True


# ---------------------------------------------------------------------------
# Negative cases
# ---------------------------------------------------------------------------


def test_none_is_not_access_denied():
    assert is_access_denied(None) is False


def test_generic_runtime_error_is_not_access_denied():
    assert is_access_denied(RuntimeError("database offline")) is False


def test_value_error_is_not_access_denied():
    assert is_access_denied(ValueError("bad input")) is False


def test_oserror_file_not_found_errno_is_not_access_denied():
    assert is_access_denied(_oserror(errno_value=errno_mod.ENOENT)) is False


def test_operational_error_unrelated_message_is_not_access_denied():
    class OperationalError(Exception):
        pass

    assert is_access_denied(OperationalError("near 'SELECT': syntax error")) is False


def test_non_operational_exception_with_locked_message_is_not_access_denied():
    # Message matching only applies to OperationalError-named exceptions.
    assert is_access_denied(RuntimeError("database is locked")) is False


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_cyclic_cause_chain_terminates():
    a = PersistenceError("a")
    b = PersistenceError("b")
    a.__cause__ = b
    b.__cause__ = a  # cycle
    # Must terminate and return False rather than recursing forever.
    assert is_access_denied(a) is False
