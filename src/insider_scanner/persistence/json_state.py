"""Shared helpers for resilient, atomic JSON state files.

Missing or damaged files yield ``None`` so callers can load defaults. Damaged
files are preserved under unique ``.corrupt-*`` names. If preservation fails,
subsequent writes refuse to overwrite the original until it can be preserved.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from insider_scanner.utils.atomic_file import atomic_write_bytes
from insider_scanner.utils.logging import get_logger

_log = get_logger("json_state")
_state_lock = threading.RLock()
# A successful reread must not clear this: callers may still hold defaults
# returned by an earlier failed load. Only preservation or absence clears it.
_pending_preservation: set[Path] = set()


def atomic_write_json(
    path: Path, data: dict, *, protect_existing: bool = False
) -> None:
    """Atomically write *data* to *path* as indented UTF-8 JSON.

    UI stores use ``protect_existing`` to check for damage even when saving
    without loading first. Checkpoints avoid rereading their growing payload.
    A previously failed preservation is always retried before writing; failure
    raises ``OSError`` and leaves the original untouched.
    """
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    with _state_lock:
        if protect_existing:
            read_json_dict(path)
        key = path.absolute()
        if key in _pending_preservation:
            _preserve_original(path)
            _pending_preservation.discard(key)
        atomic_write_bytes(path, payload)


def _preserve_original(path: Path) -> None:
    if path.is_dir():
        raise IsADirectoryError(f"JSON state path is a directory: {path}")
    backup = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex}")
    try:
        path.rename(backup)
    except FileNotFoundError:
        return
    _log.warning("Preserved unreadable or corrupt JSON state %s at %s", path, backup)


def preserve_json_state(path: Path) -> None:
    """Preserve a failed load, deferring preservation if the file is locked."""
    with _state_lock:
        key = path.absolute()
        _pending_preservation.add(key)
        try:
            _preserve_original(path)
        except OSError as exc:
            _log.warning(
                "Could not preserve JSON state %s; saving is blocked until "
                "preservation succeeds: %s",
                path,
                exc,
            )
        else:
            _pending_preservation.discard(key)


def read_json_dict(path: Path) -> dict | None:
    """Read *path* as a JSON object; return ``None`` on any failure.

    Files that cannot be read, decoded, or parsed as objects are preserved
    before returning defaults. Ordinary filesystem and decoding failures do
    not prevent application startup.
    """
    with _state_lock:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError) as exc:
            _log.warning("Could not load JSON state file %s: %s", path, exc)
            preserve_json_state(path)
            return None

        if not isinstance(data, dict):
            _log.warning("JSON state file %s has unexpected top-level type", path)
            preserve_json_state(path)
            return None

        return data
