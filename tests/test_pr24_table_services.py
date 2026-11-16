"""Regression coverage for the services used by Scan/Congress tables."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.services.trade_tables import (
    filter_congress_trades,
    load_congress_members,
    save_congress_results,
    save_table_results,
    table_trades_to_dataframe,
)


def test_invalid_member_seed_is_reported(tmp_path, monkeypatch, caplog):
    seed = tmp_path / "members.json"
    seed.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr("insider_scanner.utils.config.CONGRESS_FILE", seed)

    assert load_congress_members().names == ["All"]
    assert "Congress member" in caplog.text


def test_empty_member_mapping_still_applies_sector_filter():
    trade = CongressTrade(official_name="Unknown Member")

    assert filter_congress_trades([trade], sector="Defense", member_sectors={}) == []
    assert filter_congress_trades([trade], sector="Other", member_sectors={}) == [trade]


def test_member_names_and_sectors_come_from_one_read(tmp_path, monkeypatch):
    seed = tmp_path / "members.json"
    seed.write_text(
        json.dumps(
            [
                {"name": "Zed Member", "sector": "Defense"},
                {"official_name": "Amy Member", "sector": ["Finance", "Technology"]},
            ]
        ),
        encoding="utf-8",
    )
    reads = []
    read_text = Path.read_text

    def record_read(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", record_read)

    members = load_congress_members(seed)

    assert reads == [seed]
    assert members.names == ["All", "Amy Member", "Zed Member"]
    assert members.sectors == {
        "Amy Member": ["Finance", "Technology"],
        "Zed Member": ["Defense"],
    }


def test_member_loader_retains_valid_rows_around_malformed_entries(tmp_path, caplog):
    seed = tmp_path / "members.json"
    seed.write_text(
        json.dumps(
            [
                None,
                {"official_name": 42},
                {"name": "  "},
                {"name": " Valid Member ", "sector": [" Energy ", "Energy", ""]},
                {"name": "Missing Sectors", "sector": None},
                {"name": "Bad Sectors", "sector": [5]},
                {"name": "Empty Sectors", "sector": []},
            ]
        ),
        encoding="utf-8",
    )

    members = load_congress_members(seed)

    assert members.names == [
        "All",
        "Bad Sectors",
        "Empty Sectors",
        "Missing Sectors",
        "Valid Member",
    ]
    assert members.sectors == {
        "Valid Member": ["Energy"],
        "Missing Sectors": ["Other"],
        "Bad Sectors": ["Other"],
        "Empty Sectors": ["Other"],
    }
    assert "Skipping malformed Congress member entry" in caplog.text
    assert "Invalid Congress member sectors" in caplog.text


@pytest.mark.parametrize("contents", [b"\xff\xfe\x80", b'{"name": "Wrong root"}'])
def test_invalid_member_file_is_recoverable(tmp_path, contents, caplog):
    seed = tmp_path / "members.json"
    seed.write_bytes(contents)

    members = load_congress_members(seed)

    assert members.names == ["All"]
    assert members.sectors == {}
    assert "Congress member" in caplog.text


def test_member_file_read_error_is_reported(tmp_path, monkeypatch, caplog):
    def fail_read(*args, **kwargs):
        raise PermissionError("Access denied")

    monkeypatch.setattr(Path, "read_text", fail_read)

    members = load_congress_members(tmp_path / "members.json")

    assert members.names == ["All"]
    assert members.sectors == {}
    assert "could not be read" in caplog.text


def test_missing_member_file_has_empty_metadata(tmp_path):
    members = load_congress_members(tmp_path / "missing.json")

    assert members.names == ["All"]
    assert members.sectors == {}


def test_congress_filters_combine_without_changing_domain_records():
    matching = CongressTrade(
        official_name="Member One",
        trade_type="Purchase",
        amount_low=50_001,
        filing_date=date(2026, 11, 1),
    )
    trades = [
        CongressTrade(
            official_name="Member One", trade_type="Sale", amount_low=100_001
        ),
        matching,
        CongressTrade(
            official_name="Member One",
            trade_type="Purchase",
            amount_low=1_001,
            filing_date=date(2026, 11, 1),
        ),
        CongressTrade(
            official_name="Member One", trade_type="Purchase", amount_low=50_001
        ),
    ]

    result = filter_congress_trades(
        trades,
        trade_type="Purchase",
        min_value=50_000,
        since=date(2026, 11, 1),
        until=date(2026, 11, 1),
        sector="Finance",
        member_sectors={"Member One": ["Finance"]},
    )

    assert result == [matching]
    assert result[0] is matching
    assert len(trades) == 4


def test_dataframe_keeps_metadata_and_raw_values():
    frame = table_trades_to_dataframe(
        [InsiderTrade("AAA", value=-1500, value_missing=True, is_congress=True)]
    )

    assert frame.loc[0, "value"] == -1500
    assert bool(frame.loc[0, "value_missing"])
    assert bool(frame.loc[0, "is_congress"])


def test_common_export_keeps_domain_values_and_utf8(tmp_path):
    trades = [
        InsiderTrade(
            "AAA",
            insider_name="Łukasz Owner",
            shares=1500,
            price=0,
            price_missing=True,
            value=-15_000,
            filing_date=date(2026, 11, 1),
        )
    ]

    out = save_table_results(trades, "filtered", tmp_path)

    assert out == tmp_path
    records = json.loads((tmp_path / "filtered.json").read_text(encoding="utf-8"))
    assert records[0]["insider_name"] == "Łukasz Owner"
    assert records[0]["value"] == -15_000
    assert records[0]["price_missing"] is True
    assert records[0]["filing_date"] == "2026-11-01"
    with (tmp_path / "filtered.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["shares"] == "1500"
    assert rows[0]["value"] == "-15000"
    assert rows[0]["price_missing"] == "True"
    assert rows[0]["insider_name"] == "Łukasz Owner"


def test_congress_export_keeps_amount_bounds_and_default_label(tmp_path):
    trade = CongressTrade(amount_range="$1,001 - $15,000", amount_low=1001)

    assert save_congress_results([trade], output_dir=tmp_path) == tmp_path

    record = json.loads((tmp_path / "congress_scan.json").read_text(encoding="utf-8"))[
        0
    ]
    assert record["amount_range"] == "$1,001 - $15,000"
    assert record["amount_low"] == 1001
    assert (tmp_path / "congress_scan.csv").exists()


def test_export_keeps_filename_labels_inside_output_directory(tmp_path):
    output = tmp_path / "exports"
    save_table_results([InsiderTrade("AAA")], "../outside", output)
    assert not (tmp_path / "outside.csv").exists()
    assert not (tmp_path / "outside.json").exists()
    assert len(list(output.glob("*.csv"))) == 1
    assert len(list(output.glob("*.json"))) == 1
