"""Tests for the shared JSON-state helpers — pure, no Qt."""

from __future__ import annotations

import json
from pathlib import Path

from insider_scanner.persistence.json_state import (
    atomic_write_json,
    read_json_dict,
)


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
