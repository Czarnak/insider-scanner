"""Persistence for GUI-managed watchlists.

A watchlist is a named set of tracked entities (companies and insiders), stored
as JSON alongside ``feed_state.json``. This is a richer, GUI-only concept that is
deliberately independent of the plain-text scan watchlist files
(``tickers_watchlist.txt`` / ``eu_watchlist.txt``).

All public store operations return **new** immutable ``WatchlistStore`` values;
nothing is mutated in place.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.json_state import atomic_write_json, read_json_dict
from insider_scanner.utils.logging import get_logger

_log = get_logger("watchlists")

SCHEMA_VERSION = 1
WATCHLISTS_FILENAME = "watchlists.json"
_MAX_WATCHLISTS = 200
_MAX_ENTRIES = 1000

EntryKind = Literal["company", "insider"]
_VALID_KINDS: frozenset[str] = frozenset({"company", "insider"})

# A stable tuple identifying an entry for dedup/removal.
EntryKey = tuple[str, str, str, str]


class WatchlistError(ValueError):
    """Raised when watchlist data cannot be parsed or coerced."""


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WatchEntry:
    """One tracked entity within a watchlist."""

    kind: EntryKind
    market: FeedMarket
    identifier: str = ""
    person: str = ""
    label: str = ""
    note: str = ""

    def key(self) -> EntryKey:
        """Return a stable identity tuple for dedup and removal."""
        return (
            self.kind,
            self.market.value,
            self.identifier.casefold(),
            self.person.casefold(),
        )


@dataclass(frozen=True)
class Watchlist:
    """A named, ordered collection of watch entries."""

    name: str
    entries: tuple[WatchEntry, ...] = ()


@dataclass(frozen=True)
class WatchlistStore:
    """Serialisable snapshot of all user watchlists."""

    version: int = SCHEMA_VERSION
    watchlists: tuple[Watchlist, ...] = ()

    # -- queries --------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        return tuple(w.name for w in self.watchlists)

    def get(self, name: str) -> Watchlist | None:
        return next((w for w in self.watchlists if w.name == name), None)

    # -- immutable operations ------------------------------------------

    def with_list(self, name: str) -> WatchlistStore:
        """Return a copy with an empty list *name* added (no-op if present)."""
        if self.get(name) is not None:
            return self
        added = (*self.watchlists, Watchlist(name=name))
        return replace(self, watchlists=added[:_MAX_WATCHLISTS])

    def without_list(self, name: str) -> WatchlistStore:
        remaining = tuple(w for w in self.watchlists if w.name != name)
        return replace(self, watchlists=remaining)

    def renamed(self, old: str, new: str) -> WatchlistStore:
        renamed = tuple(
            replace(w, name=new) if w.name == old else w for w in self.watchlists
        )
        return replace(self, watchlists=renamed)

    def with_entry(self, name: str, entry: WatchEntry) -> WatchlistStore:
        """Return a copy with *entry* appended to list *name* (created if missing)."""
        base = self.with_list(name)
        updated: list[Watchlist] = []
        for w in base.watchlists:
            if w.name != name:
                updated.append(w)
                continue
            if any(e.key() == entry.key() for e in w.entries):
                updated.append(w)  # already present — dedup
                continue
            entries = (*w.entries, entry)[:_MAX_ENTRIES]
            updated.append(replace(w, entries=entries))
        return replace(base, watchlists=tuple(updated))

    def without_entry(self, name: str, key: EntryKey) -> WatchlistStore:
        updated: list[Watchlist] = []
        for w in self.watchlists:
            if w.name != name:
                updated.append(w)
                continue
            entries = tuple(e for e in w.entries if e.key() != key)
            updated.append(replace(w, entries=entries))
        return replace(self, watchlists=tuple(updated))


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def entry_to_dict(e: WatchEntry) -> dict:
    return {
        "kind": e.kind,
        "market": e.market.value,
        "identifier": e.identifier,
        "person": e.person,
        "label": e.label,
        "note": e.note,
    }


def entry_from_dict(d: dict) -> WatchEntry:
    """Reconstruct a WatchEntry; raises WatchlistError on invalid data."""
    try:
        kind = str(d["kind"])
        if kind not in _VALID_KINDS:
            raise WatchlistError(f"invalid entry kind: {kind!r}")
        market = FeedMarket(d["market"])
        return WatchEntry(
            kind=kind,  # type: ignore[arg-type]
            market=market,
            identifier=str(d.get("identifier", "")),
            person=str(d.get("person", "")),
            label=str(d.get("label", "")),
            note=str(d.get("note", "")),
        )
    except WatchlistError:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise WatchlistError(f"Cannot parse WatchEntry from dict: {exc}") from exc


def watchlist_to_dict(w: Watchlist) -> dict:
    return {"name": w.name, "entries": [entry_to_dict(e) for e in w.entries]}


def store_to_dict(s: WatchlistStore) -> dict:
    return {
        "version": s.version,
        "watchlists": [watchlist_to_dict(w) for w in s.watchlists],
    }


def store_from_dict(d: dict) -> WatchlistStore:
    """Reconstruct a WatchlistStore resiliently.

    Bad entries and bad lists are dropped rather than failing the whole load.
    """
    try:
        version = int(d.get("version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        version = SCHEMA_VERSION

    raw_lists = d.get("watchlists", [])
    watchlists: list[Watchlist] = []
    if isinstance(raw_lists, list):
        for raw_w in raw_lists[:_MAX_WATCHLISTS]:
            if not isinstance(raw_w, dict):
                continue
            name = str(raw_w.get("name", ""))
            raw_entries = raw_w.get("entries", [])
            entries: list[WatchEntry] = []
            if isinstance(raw_entries, list):
                for raw_e in raw_entries[:_MAX_ENTRIES]:
                    try:
                        if not isinstance(raw_e, dict):
                            continue
                        entries.append(entry_from_dict(raw_e))
                    except WatchlistError:
                        _log.warning("Dropping invalid watch entry: %r", raw_e)
            watchlists.append(Watchlist(name=name, entries=tuple(entries)))

    return WatchlistStore(version=version, watchlists=tuple(watchlists))


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def load_watchlists(path: Path) -> WatchlistStore:
    """Load a WatchlistStore from *path*; returns an empty store on any error."""
    data = read_json_dict(path)
    if data is None:
        return WatchlistStore()
    try:
        return store_from_dict(data)
    except Exception as exc:  # noqa: BLE001 - never fail a load
        _log.warning("Failed to parse watchlists from %s: %s", path, exc)
        return WatchlistStore()


def save_watchlists(path: Path, store: WatchlistStore) -> None:
    """Atomically write *store* to *path* as JSON."""
    atomic_write_json(path, store_to_dict(store))


def watchlists_path(data_dir: Path) -> Path:
    """Return the canonical watchlists file path inside *data_dir*."""
    return data_dir / WATCHLISTS_FILENAME
