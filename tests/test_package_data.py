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
        with seed.open("rb") as seed_file:
            seed_file.read(1)
