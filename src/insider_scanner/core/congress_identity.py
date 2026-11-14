"""Structured Congress identities and whole-name matching for legacy records."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from insider_scanner.utils import config

_TITLES = {
    "hon",
    "honorable",
    "sen",
    "senator",
    "rep",
    "representative",
    "mr",
    "mrs",
    "ms",
    "dr",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_WORDS = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
_NICKNAMES = re.compile(r'"[^"]*"|“[^”]*”')


def _name_words(name: str) -> list[str]:
    tokens = _WORDS.findall(_NICKNAMES.sub("", name))
    while tokens and tokens[0].casefold() in _TITLES:
        tokens.pop(0)
    while tokens and tokens[-1].casefold() in _SUFFIXES:
        tokens.pop()
    return tokens


def name_tokens(name: str) -> tuple[str, ...]:
    """Remove only whole honorific/suffix tokens, retaining surname spelling."""
    tokens = [word.casefold() for word in _name_words(name)]
    return tuple(tokens)


@dataclass(frozen=True)
class MemberIdentity:
    key: str
    first_name: str
    last_name: str
    official_name: str
    name: str
    chamber: str = ""
    first_aliases: tuple[str, ...] = ()
    name_aliases: tuple[tuple[str, ...], ...] = ()


def _parse_name(name: str) -> tuple[str, str]:
    # A trailing ", Jr." is a suffix, not a last-name-first delimiter.
    if "," in name:
        last, first = name.split(",", 1)
        if name_tokens(first):
            first_tokens, last_tokens = _name_words(first), _name_words(last)
            return " ".join(first_tokens), " ".join(last_tokens)
    tokens = _name_words(name)
    if len(tokens) < 2:
        return "", " ".join(tokens)
    return tokens[0], tokens[-1]


def _from_mapping(value: Mapping) -> MemberIdentity:
    first, last = (
        str(value.get("first_name") or "").strip(),
        str(value.get("last_name") or "").strip(),
    )
    official = str(value.get("official_name") or "").strip()
    display = str(value.get("name") or "").strip()
    if not first or not last:
        if official:
            first, last = _parse_name(official)
        elif display:
            # The original member file used a documented "Last First" schema.
            tokens = _name_words(display)
            last, first = (
                (tokens[0], " ".join(tokens[1:]))
                if len(tokens) > 1
                else (" ".join(tokens), "")
            )
    official = official or f"{first} {last}".strip()
    display = display or f"{last} {first}".strip()
    bioguide = str(value.get("bioguide_id") or "").strip()
    openstates = str(value.get("openstates_id") or "").strip()
    key = (
        f"bioguide:{bioguide.upper()}"
        if bioguide
        else f"openstates:{openstates}"
        if openstates
        else "name:"
        + "|".join((" ".join(name_tokens(last)), " ".join(name_tokens(first))))
    )
    official_first, _ = _parse_name(official)
    aliases = {
        words[0] for alias in (first, official_first) if (words := name_tokens(alias))
    }
    for nickname in re.findall(r'"([^"]+)"|“([^”]+)”', official):
        aliases.update(words[0] for part in nickname if (words := name_tokens(part)))
    return MemberIdentity(
        key,
        first,
        last,
        official,
        display,
        str(value.get("chamber") or ""),
        tuple(sorted(aliases)),
        tuple(
            sorted(
                {name_tokens(alias) for alias in (official, display, f"{first} {last}")}
            )
        ),
    )


def _matches_identity(
    identity: MemberIdentity, candidate: str, *, allow_surname: bool
) -> bool:
    tokens = name_tokens(candidate)
    surname = name_tokens(identity.last_name)
    first = name_tokens(identity.first_name)
    if not tokens or not surname:
        return False
    if tokens in identity.name_aliases and first:
        return True
    if tokens == surname:
        return allow_surname
    if not first or len(tokens) <= len(surname):
        return False
    if tokens[-len(surname) :] == surname:
        given = tokens[: -len(surname)]
    elif tokens[: len(surname)] == surname:
        given = tokens[len(surname) :]
    else:
        return False
    # Additional middle names and initials need not match the disclosure spelling.
    aliases = identity.first_aliases or (first[0],)
    if len(given[0]) == 1 and any(len(token) > 1 for token in given[1:]):
        # "Craig A. Goldman" must not become reversed "A. ... Craig".
        return False
    return any(
        given[0] == alias or (len(given[0]) == 1 and given[0] == alias[0])
        for alias in aliases
    )


class MemberIndex:
    """Index members by surname tokens, checking only plausible candidates."""

    def __init__(self, members: Sequence[Mapping]):
        self.members = tuple(
            dict(member) for member in members if isinstance(member, Mapping)
        )
        self.by_key: dict[str, MemberIdentity] = {}
        self.by_token: dict[str, list[MemberIdentity]] = defaultdict(list)
        self.by_alias: dict[tuple[str, ...], set[MemberIdentity]] = defaultdict(set)
        for member in self.members:
            identity = _from_mapping(member)
            if not identity.first_name or not identity.last_name:
                continue
            self.by_key[identity.key] = identity
        for identity in self.by_key.values():
            for alias in identity.name_aliases:
                self.by_alias[alias].add(identity)
            for token in set(name_tokens(identity.last_name)):
                self.by_token[token].append(identity)

    def resolve(
        self, name: str, *, allow_surname: bool = True
    ) -> MemberIdentity | None:
        if name in self.by_key:
            return self.by_key[name]
        exact = self.by_alias.get(name_tokens(name), set())
        if exact:
            return next(iter(exact)) if len(exact) == 1 else None
        candidates = {
            identity
            for token in name_tokens(name)
            for identity in self.by_token.get(token, ())
        }
        matches = [
            identity
            for identity in candidates
            if _matches_identity(identity, name, allow_surname=allow_surname)
        ]
        return matches[0] if len(matches) == 1 else None


@lru_cache(maxsize=8)
def _read_index(path: Path, stamp: tuple[int, int, int]) -> MemberIndex:
    del stamp  # The stat fingerprint invalidates the cache after atomic seed updates.
    try:
        members = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        members = []
    return MemberIndex(members if isinstance(members, list) else [])


def get_member_index(path: Path | None = None) -> MemberIndex:
    path = Path(path or config.CONGRESS_FILE).resolve()
    try:
        stat = path.stat()
    except OSError:
        return MemberIndex([])
    return _read_index(path, (stat.st_mtime_ns, stat.st_size, stat.st_ino))


def resolve_member(
    value: str | Mapping | MemberIdentity,
    members: Sequence[Mapping] | None = None,
    *,
    allow_surname: bool = True,
) -> MemberIdentity | None:
    """Resolve a unique known member; surname-only searches are optional."""
    if isinstance(value, MemberIdentity):
        return value
    if isinstance(value, Mapping):
        return _from_mapping(value)
    index = get_member_index() if members is None else MemberIndex(members)
    return index.resolve(value, allow_surname=allow_surname)


def identity_for(value: str | Mapping | MemberIdentity) -> MemberIdentity:
    """Use a stable known-member key, falling back to a normalized full name."""
    identity = resolve_member(value)
    if identity is not None:
        return identity
    first, last = _parse_name(str(value))
    return _from_mapping(
        {"first_name": first, "last_name": last, "official_name": str(value)}
    )


def name_matches(
    query: str | Mapping | MemberIdentity,
    candidate: str | Mapping | MemberIdentity,
    *,
    allow_surname: bool = True,
) -> bool:
    """Match surname and first name/initial without substring containment."""
    identity = identity_for(candidate) if not isinstance(candidate, str) else None
    candidate_name = (
        candidate
        if isinstance(candidate, str)
        else identity_for(candidate).official_name
    )
    if isinstance(query, str):
        known = resolve_member(query)
        if known is None and identity is not None:
            return _matches_identity(identity, query, allow_surname=allow_surname)
        query_identity = known or identity_for(query)
    else:
        query_identity = identity_for(query)
    if identity is not None:
        if query_identity.key == identity.key:
            return True
        candidate_name = identity.official_name
    if not query_identity.first_name and allow_surname:
        surname = name_tokens(query_identity.last_name)
        tokens = name_tokens(candidate_name)
        return bool(surname) and (
            tokens[-len(surname) :] == surname or tokens[: len(surname)] == surname
        )
    return _matches_identity(
        query_identity, candidate_name, allow_surname=allow_surname
    )
