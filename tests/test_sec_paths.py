"""Behavior tests for the shared SEC path-containment predicate."""

from __future__ import annotations

from pathlib import Path

from insider_scanner.core._sec_paths import resolves_within


def test_direct_child_is_within(tmp_path: Path) -> None:
    child = tmp_path / "validated-v1" / "abc.filing"
    assert resolves_within(child, tmp_path) is True


def test_root_itself_is_within(tmp_path: Path) -> None:
    assert resolves_within(tmp_path, tmp_path) is True


def test_deeply_nested_child_is_within(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "d.json"
    assert resolves_within(nested, tmp_path) is True


def test_sibling_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    sibling = tmp_path / "other" / "file.filing"
    assert resolves_within(sibling, root) is False


def test_traversal_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    escape = root / ".." / "outside" / "file.filing"
    assert resolves_within(escape, root) is False


def test_nonexistent_paths_resolve_lexically(tmp_path: Path) -> None:
    """Containment holds for not-yet-created paths (resolve(strict=False))."""
    root = tmp_path / "cache"
    inside = root / "validated-v1" / "deadbeef.filing"
    assert resolves_within(inside, root) is True
