"""Member identities remain stable across seed, display and disclosure names."""

import json

import pytest

from insider_scanner.core.congress_identity import (
    MemberIndex,
    get_member_index,
    identity_for,
    name_matches,
    name_tokens,
    resolve_member,
)

BEGICH = {
    "name": "Begich Nicholas",
    "first_name": "Nicholas",
    "last_name": "Begich",
    "official_name": "Nicholas J. Begich III",
    "bioguide_id": "B001323",
    "chamber": "House",
}


@pytest.mark.parametrize(
    "name",
    [
        "Nicholas J. Begich III",
        "Hon. Nicholas Begich III",
        "begich nicholas",
        "Begich",
        "BegiCH, Nicholas",
        "N. Begich",
        "bioguide:B001323",
    ],
)
def test_resolves_display_and_disclosure_aliases_to_same_member(name):
    member = resolve_member(name, [BEGICH])
    assert member is not None
    assert member.key == "bioguide:B001323"
    assert (member.first_name, member.last_name) == ("Nicholas", "Begich")


def test_ambiguous_surnames_and_initials_do_not_choose_first_member():
    members = [
        {"first_name": first, "last_name": "Smith"} for first in ("John", "James")
    ]
    assert resolve_member("Smith", members) is None
    assert resolve_member("J. Smith", members, allow_surname=False) is None
    assert resolve_member("John Smith", members).first_name == "John"


@pytest.mark.parametrize(
    "name", ["", "N", "Nicholas", "Begi", "Foo Begich", "Begicholas Nicholas"]
)
def test_flag_matching_requires_both_names_and_whole_tokens(name):
    assert resolve_member(name, [BEGICH], allow_surname=False) is None


def test_suffixes_are_removed_only_as_trailing_whole_words():
    assert name_tokens("Hon. John Srinivasan, Jr.") == ("john", "srinivasan")
    assert name_tokens("Bill Srivastava") == ("bill", "srivastava")


@pytest.mark.parametrize(
    "query",
    [
        'Eric A. "Rick" Crawford',
        "Crawford Eric",
        "E. Crawford",
        "Rick Crawford",
        "Crawford",
    ],
)
def test_matching_uses_structured_compound_surname_and_nickname_aliases(query):
    member = {
        "first_name": "Eric",
        "last_name": "Crawford",
        "official_name": 'Eric A. "Rick" Crawford',
    }
    assert name_matches(member, query)
    assert name_matches(query, member)
    assert not name_matches(member, "Eric Crawfordson")


def test_compound_last_names_and_unknown_name_fallback():
    member = identity_for({"first_name": "Marie", "last_name": "Gluesenkamp Perez"})
    assert name_matches(member, "Hon. Marie Gluesenkamp Perez")
    assert not name_matches(member, "Marie Perez")
    assert (
        identity_for("Hon. John Q. Example, Jr.").key
        == identity_for("john example").key
    )


def test_member_index_caches_reads_and_invalidates_after_replacement(
    tmp_path, monkeypatch
):
    path = tmp_path / "members.json"
    path.write_text(json.dumps([BEGICH]), encoding="utf-8")
    first = get_member_index(path)
    original_read = type(path).read_text
    reads = []

    def read(self, *args, **kwargs):
        reads.append(self)
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", read)
    assert get_member_index(path) is first
    assert not reads
    replacement = path.with_suffix(".new")
    replacement.write_text(
        '[{"first_name":"Nancy","last_name":"Pelosi"}]', encoding="utf-8"
    )
    replacement.replace(path)
    second = get_member_index(path)
    assert second is not first
    assert second.resolve("Nancy Pelosi") is not None
    assert second.resolve("Nicholas Begich") is None
    assert reads == [path]


def test_legacy_seed_order_is_explicit():
    member = MemberIndex([{"name": "Pelosi Nancy"}]).resolve("Nancy Pelosi")
    assert member is not None
    assert (member.first_name, member.last_name) == ("Nancy", "Pelosi")
