"""Persistence for application watchlists.

A watchlist is a named set of tracked entities (companies and insiders), stored
as JSON alongside ``feed_state.json``. Legacy US ticker lists are imported once;
the JSON store then supplies both scans and the analysis symbol picker.

All public store operations return **new** immutable ``WatchlistStore`` values;
nothing is mutated in place.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from insider_scanner.persistence.feed import FeedMarket
from insider_scanner.persistence.json_state import (
    atomic_write_json,
    preserve_json_state,
    read_json_dict,
)
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


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise WatchlistError("Watchlist name cannot be blank")
    return name


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
    legacy_watchlist_migrated: bool = False

    # -- queries --------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        return tuple(w.name for w in self.watchlists)

    def get(self, name: str) -> Watchlist | None:
        return next((w for w in self.watchlists if w.name == name), None)

    def us_symbols(self) -> tuple[str, ...]:
        """Return distinct US company identifiers in their watchlist order."""
        return tuple(
            dict.fromkeys(
                e.identifier.strip().upper()
                for w in self.watchlists
                for e in w.entries
                if e.kind == "company"
                and e.market == FeedMarket.US
                and e.identifier.strip()
            )
        )

    # -- immutable operations ------------------------------------------

    def with_list(self, name: str) -> WatchlistStore:
        """Return a copy with an empty list *name* added (no-op if present)."""
        name = _clean_name(name)
        if self.get(name) is not None:
            return self
        if any(w.name.casefold() == name.casefold() for w in self.watchlists):
            raise WatchlistError(f"Watchlist {name!r} already exists")
        if len(self.watchlists) >= _MAX_WATCHLISTS:
            raise WatchlistError(f"Watchlist limit reached ({_MAX_WATCHLISTS})")
        added = (*self.watchlists, Watchlist(name=name))
        return replace(self, watchlists=added)

    def without_list(self, name: str) -> WatchlistStore:
        remaining = tuple(w for w in self.watchlists if w.name != name)
        return replace(self, watchlists=remaining)

    def renamed(self, old: str, new: str) -> WatchlistStore:
        new = _clean_name(new)
        if any(
            w.name != old and w.name.casefold() == new.casefold()
            for w in self.watchlists
        ):
            raise WatchlistError(f"Watchlist {new!r} already exists")
        renamed = tuple(
            replace(w, name=new) if w.name == old else w for w in self.watchlists
        )
        return replace(self, watchlists=renamed)

    def with_entry(self, name: str, entry: WatchEntry) -> WatchlistStore:
        """Return a copy with *entry* appended to list *name* (created if missing)."""
        name = _clean_name(name)
        base = self.with_list(name)
        updated: list[Watchlist] = []
        for w in base.watchlists:
            if w.name != name:
                updated.append(w)
                continue
            if any(e.key() == entry.key() for e in w.entries):
                updated.append(w)  # already present — dedup
                continue
            if len(w.entries) >= _MAX_ENTRIES:
                raise WatchlistError(f"Watchlist entry limit reached ({_MAX_ENTRIES})")
            entries = (*w.entries, entry)
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
        "legacy_watchlist_migrated": s.legacy_watchlist_migrated,
    }


def store_from_dict(d: dict) -> WatchlistStore:
    """Reconstruct a WatchlistStore resiliently.

    Bad entries and bad lists are dropped rather than failing the whole load.
    Existing valid data is retained even above the caps enforced for additions.
    """
    try:
        version = int(d.get("version", SCHEMA_VERSION))
    except (TypeError, ValueError):
        version = SCHEMA_VERSION

    raw_lists = d.get("watchlists", [])
    watchlists: list[Watchlist] = []
    if isinstance(raw_lists, list):
        for raw_w in raw_lists:
            if not isinstance(raw_w, dict):
                continue
            name = str(raw_w.get("name", "")).strip()
            if not name:
                _log.warning("Dropping watchlist with blank name")
                continue
            raw_entries = raw_w.get("entries", [])
            entries: list[WatchEntry] = []
            if isinstance(raw_entries, list):
                for raw_e in raw_entries:
                    try:
                        if not isinstance(raw_e, dict):
                            continue
                        entries.append(entry_from_dict(raw_e))
                    except WatchlistError:
                        _log.warning("Dropping invalid watch entry: %r", raw_e)
            existing = next(
                (
                    i
                    for i, w in enumerate(watchlists)
                    if w.name.casefold() == name.casefold()
                ),
                None,
            )
            if existing is None:
                watchlists.append(Watchlist(name=name, entries=tuple(entries)))
            else:
                original = watchlists[existing]
                keys = {e.key() for e in original.entries}
                merged = list(original.entries)
                for entry in entries:
                    if entry.key() not in keys:
                        merged.append(entry)
                        keys.add(entry.key())
                watchlists[existing] = replace(original, entries=tuple(merged))

    return WatchlistStore(
        version=version,
        watchlists=tuple(watchlists),
        legacy_watchlist_migrated=d.get("legacy_watchlist_migrated") is True,
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def load_watchlists(path: Path, *, legacy_path: Path | None = None) -> WatchlistStore:
    """Load watchlists, optionally importing a legacy ticker file once.

    Damaged JSON is preserved by the shared state helpers. Migration errors
    propagate so callers can report that the legacy import was not saved.
    """
    data = read_json_dict(path)
    if data is None:
        store = WatchlistStore()
    else:
        try:
            store = store_from_dict(data)
        except Exception as exc:  # noqa: BLE001 - never fail a JSON load
            _log.warning("Failed to parse watchlists from %s: %s", path, exc)
            preserve_json_state(path)
            store = WatchlistStore()
    if (
        legacy_path is not None
        and not store.legacy_watchlist_migrated
        and legacy_path.exists()
    ):
        migrated = _import_legacy_tickers(store, legacy_path)
        save_watchlists(path, migrated)
        return migrated
    return store


def _import_legacy_tickers(store: WatchlistStore, legacy_path: Path) -> WatchlistStore:
    """Prepare a complete migration before writing either state or its marker."""
    seen = set(store.us_symbols())
    symbols: list[str] = []
    try:
        legacy_text = legacy_path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise WatchlistError(f"Cannot decode legacy watchlist {legacy_path}") from exc
    for line in legacy_text.splitlines():
        symbol = line.strip().upper()
        if symbol and not symbol.startswith("#") and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)

    result = store
    names = {name.casefold() for name in result.names()}
    suffix = 1
    for offset in range(0, len(symbols), _MAX_ENTRIES):
        name = "Legacy US" if suffix == 1 else f"Legacy US ({suffix})"
        while name.casefold() in names:
            suffix += 1
            name = f"Legacy US ({suffix})"
        names.add(name.casefold())
        result = result.with_list(name)
        for symbol in symbols[offset : offset + _MAX_ENTRIES]:
            result = result.with_entry(
                name,
                WatchEntry(
                    kind="company",
                    market=FeedMarket.US,
                    identifier=symbol,
                    label=symbol,
                ),
            )
        suffix += 1
    return replace(result, legacy_watchlist_migrated=True)


def save_watchlists(path: Path, store: WatchlistStore) -> None:
    """Atomically write *store* to *path* as JSON."""
    atomic_write_json(path, store_to_dict(store), protect_existing=True)


def watchlists_path(data_dir: Path) -> Path:
    """Return the canonical watchlists file path inside *data_dir*."""
    return data_dir / WATCHLISTS_FILENAME
