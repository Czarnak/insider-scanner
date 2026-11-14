"""Tests for the shared JSON-state helpers — pure, no Qt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from insider_scanner.persistence.alerts import load_alerts, save_alerts
from insider_scanner.persistence.feed_state import load_feed_state, save_feed_state
from insider_scanner.persistence.json_state import (
    atomic_write_json,
    read_json_dict,
)
from insider_scanner.persistence.watchlists import load_watchlists, save_watchlists


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        atomic_write_json(p, {"a": 1, "b": ["x", "y"]})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": ["x", "y"]}

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        p = tmp_path / "nested" / "deep" / "data.json"
        atomic_write_json(p, {})
        assert p.exists()

    def test_no_leftover_temp_files(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        atomic_write_json(p, {"k": "v"})
        leftovers = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert leftovers == []

    def test_second_write_replaces_atomically(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        atomic_write_json(p, {"v": 1})
        atomic_write_json(p, {"v": 2})
        assert read_json_dict(p) == {"v": 2}

    def test_unicode_is_preserved(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        atomic_write_json(p, {"name": "Łukasz €"})
        assert read_json_dict(p) == {"name": "Łukasz €"}


class TestReadJsonDict:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert read_json_dict(tmp_path / "nope.json") is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text("{ not valid }", encoding="utf-8")
        assert read_json_dict(p) is None

    def test_non_dict_top_level_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert read_json_dict(p) is None

    def test_valid_dict_returned(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text('{"a": 1}', encoding="utf-8")
        assert read_json_dict(p) == {"a": 1}

    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        payload = {"version": 1, "items": [{"name": "A"}, {"name": "B"}]}
        atomic_write_json(p, payload)
        assert read_json_dict(p) == payload


@pytest.mark.parametrize("payload", [b"\xff\xfe", b"{broken", b"[]", b"null"])
def test_corrupt_original_survives_defaults_save(tmp_path, payload):
    path = tmp_path / "state.json"
    path.write_bytes(payload)
    assert read_json_dict(path) is None
    atomic_write_json(path, {"default": True})
    assert read_json_dict(path) == {"default": True}
    backups = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == payload


def test_successive_corrupt_files_have_distinct_backups(tmp_path):
    path = tmp_path / "state.json"
    for content in (b"first broken", b"second broken"):
        path.write_bytes(content)
        assert read_json_dict(path) is None
    assert {p.read_bytes() for p in tmp_path.glob("*.corrupt-*")} == {
        b"first broken",
        b"second broken",
    }


def test_unreadable_file_is_preserved_before_defaults(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original = b'{"watchlists": [{"name": "valuable"}]}'
    path.write_bytes(original)
    read_text = Path.read_text

    def locked_read(self, *args, **kwargs):
        if self == path:
            raise PermissionError("locked")
        return read_text(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", locked_read)
        assert read_json_dict(path) is None
    atomic_write_json(path, {})
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == original


def test_failed_preservation_blocks_save_until_original_is_safe(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_bytes(b"broken")
    rename = Path.rename

    def locked_rename(self, *args, **kwargs):
        if self == path:
            raise PermissionError("cannot preserve")
        return rename(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", locked_rename)
        assert read_json_dict(path) is None
        with pytest.raises(PermissionError, match="cannot preserve"):
            atomic_write_json(path, {"default": True})
        assert path.read_bytes() == b"broken"
        assert list(tmp_path.iterdir()) == [path]
    atomic_write_json(path, {"default": True})
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == b"broken"


def test_successful_reread_keeps_protection_from_earlier_failed_load(
    tmp_path, monkeypatch
):
    path = tmp_path / "state.json"
    original = b'{"important": "original"}'
    path.write_bytes(original)

    def locked(*args, **kwargs):
        raise PermissionError("temporarily locked")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", locked)
        patch.setattr(Path, "rename", locked)
        assert read_json_dict(path) is None
    assert read_json_dict(path) == {"important": "original"}
    atomic_write_json(path, {"default": True})
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == original


@pytest.mark.parametrize(
    ("load", "save"),
    [
        (load_feed_state, save_feed_state),
        (load_watchlists, save_watchlists),
        (load_alerts, save_alerts),
    ],
)
@pytest.mark.parametrize("payload", [b"\xffinvalid UTF-8", b"not JSON", b"[]"])
def test_ui_stores_preserve_original_on_load_and_save(tmp_path, load, save, payload):
    path = tmp_path / "state.json"
    path.write_bytes(payload)
    state = load(path)
    save(path, state)
    assert load(path) == state
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == payload


@pytest.mark.parametrize(
    ("load", "save"),
    [
        (load_feed_state, save_feed_state),
        (load_watchlists, save_watchlists),
        (load_alerts, save_alerts),
    ],
)
def test_ui_save_preserves_file_corrupted_after_load(tmp_path, load, save):
    path = tmp_path / "state.json"
    state = load(path)
    path.write_bytes(b"damaged after load")
    save(path, state)
    assert load(path) == state
    assert next(tmp_path.glob("*.corrupt-*")).read_bytes() == b"damaged after load"


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
def test_json_write_interruption_keeps_original(tmp_path, monkeypatch, failure):
    from insider_scanner.utils import atomic_file

    path = tmp_path / "state.json"
    atomic_write_json(path, {"original": True})

    def interrupted(*args):
        raise failure()

    monkeypatch.setattr(atomic_file.os, "replace", interrupted)
    with pytest.raises(failure):
        atomic_write_json(path, {"replacement": True})
    assert read_json_dict(path) == {"original": True}
    assert list(tmp_path.iterdir()) == [path]
