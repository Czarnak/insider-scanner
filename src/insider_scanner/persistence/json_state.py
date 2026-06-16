"""Shared helpers for resilient, atomic JSON state files.

These back the small UI-state stores (watchlists, alerts) that live alongside
``feed_state.json`` in the user data directory. The write is atomic (temp file +
``os.replace``) and the read never raises — a missing or corrupt file yields
``None`` so callers can fall back to an empty default.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from insider_scanner.utils.logging import get_logger

_log = get_logger("json_state")


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically write *data* to *path* as indented UTF-8 JSON.

    Creates the parent directory if needed and never leaves a partial file:
    the payload is written to a temp file in the same directory, flushed and
    fsynced, then atomically moved into place via ``os.replace``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json_dict(path: Path) -> dict | None:
    """Read *path* as a JSON object; return ``None`` on any failure.

    Returns ``None`` for a missing file, unreadable file, invalid JSON, or a
    top-level value that is not a JSON object. Never raises.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        _log.warning("Could not read JSON state file %s: %s", path, exc)
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.warning("JSON state file %s contains invalid JSON: %s", path, exc)
        return None

    if not isinstance(data, dict):
        _log.warning("JSON state file %s has unexpected top-level type", path)
        return None

    return data
