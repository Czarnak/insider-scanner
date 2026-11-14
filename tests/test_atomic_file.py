"""Failure and interruption regressions for atomic publication."""

import os
from pathlib import Path

import pytest

from insider_scanner.utils import atomic_file


def test_atomic_write_creates_parents_and_replaces_bytes(tmp_path):
    path = tmp_path / "nested" / "state"
    atomic_file.atomic_write_bytes(path, b"first")
    atomic_file.atomic_write_bytes(path, b"second")
    assert path.read_bytes() == b"second"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failure_keeps_original_and_cleans_temp(
    tmp_path, monkeypatch, failure, operation
):
    path = tmp_path / "state"
    path.write_bytes(b"original")

    def fail(*args):
        raise failure("injected")

    monkeypatch.setattr(atomic_file.os, operation, fail)
    with pytest.raises(failure, match="injected"):
        atomic_file.atomic_write_bytes(path, b"replacement")
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_fdopen_failure_closes_unclaimed_descriptor(tmp_path, monkeypatch):
    descriptors = []

    def fail(descriptor, *args):
        descriptors.append(descriptor)
        raise OSError("fdopen failed")

    monkeypatch.setattr(atomic_file.os, "fdopen", fail)
    with pytest.raises(OSError, match="fdopen failed"):
        atomic_file.atomic_write_bytes(tmp_path / "state", b"data")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert list(tmp_path.iterdir()) == []


def test_replace_failure_does_not_close_reused_descriptor(tmp_path, monkeypatch):
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"keep open")
    opened = []

    def fail(*args):
        # The temporary stream is closed before replacement. Opening another
        # file here permits the OS to reuse that exact descriptor number.
        opened.append(unrelated.open("rb"))
        raise PermissionError("target locked")

    monkeypatch.setattr(atomic_file.os, "replace", fail)
    try:
        with pytest.raises(PermissionError, match="target locked"):
            atomic_file.atomic_write_bytes(tmp_path / "state", b"data")
        assert opened[0].read() == b"keep open"
    finally:
        for stream in opened:
            stream.close()
    assert list(tmp_path.iterdir()) == [unrelated]


def test_cleanup_failure_does_not_mask_write_failure(tmp_path, monkeypatch):
    def fail_sync(*args):
        raise OSError("write failed")

    def fail_cleanup(*args, **kwargs):
        raise PermissionError("cleanup locked")

    monkeypatch.setattr(atomic_file.os, "fsync", fail_sync)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(OSError, match="write failed"):
            atomic_file.atomic_write_bytes(tmp_path / "state", b"data")
    for path in tmp_path.iterdir():
        path.unlink()
