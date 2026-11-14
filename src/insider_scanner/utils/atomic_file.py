"""Atomic file publication with explicit descriptor ownership."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Flush bytes to a sibling temporary file and atomically replace *path*.

    The stream owns the descriptor as soon as ``fdopen`` succeeds. Cleanup runs
    for interrupts too, and cleanup errors never hide the original failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    owned_descriptor: int | None = descriptor
    try:
        stream = os.fdopen(descriptor, "wb")
        owned_descriptor = None
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if owned_descriptor is not None:
            try:
                os.close(owned_descriptor)
            except OSError:
                pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
