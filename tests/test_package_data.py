"""Tests for packaged runtime seed resources."""

from importlib.resources import files


PACKAGED_SEED_NAMES = (
    "congress_members.json",
    "tickers_watchlist.txt",
    "eu_watchlist.txt",
)


def test_packaged_seed_files_are_readable():
    seed_root = files("insider_scanner.resources.seeds")

    for seed_name in PACKAGED_SEED_NAMES:
        seed = seed_root.joinpath(seed_name)

        assert seed.is_file()
        content = seed.read_text(encoding="utf-8").strip()
        assert content

        if seed_name == "congress_members.json":
            assert content.startswith(("{", "["))
