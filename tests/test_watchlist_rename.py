"""Crash recovery for the two stores affected by a watchlist rename."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime

import pytest

from insider_scanner.persistence.alerts import (
    AlertRule,
    AlertStore,
    alerts_path,
    load_alerts,
    save_alerts,
)
from insider_scanner.persistence.alerts import (
    store_to_dict as alerts_to_dict,
)
from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.watchlists import (
    WatchlistError,
    WatchlistStore,
    load_watchlists,
    save_watchlists,
    watchlists_path,
)
from insider_scanner.persistence.watchlists import (
    store_to_dict as watchlists_to_dict,
)


def _rename_module():
    try:
        return importlib.import_module("insider_scanner.persistence.watchlist_rename")
    except ModuleNotFoundError:
        pytest.fail("Watchlist rename save/recovery is not implemented")


@pytest.fixture
def stores(tmp_path):
    before_lists = WatchlistStore().with_list("Old")
    before_alerts = AlertStore().with_rule(AlertRule("Rule", watchlist="Old"))
    save_watchlists(watchlists_path(tmp_path), before_lists)
    save_alerts(alerts_path(tmp_path), before_alerts)
    return before_lists.renamed("Old", "New"), before_alerts.renamed_watchlist(
        "Old", "New"
    )


def _journal(watchlists, alerts):
    return {
        "version": 1,
        "watchlists": watchlists_to_dict(watchlists),
        "alerts": alerts_to_dict(alerts),
    }


def test_save_publishes_both_stores_and_removes_journal(tmp_path, stores):
    rename = _rename_module()
    watchlists, alerts = stores
    rename.save_watchlist_rename(tmp_path, watchlists, alerts)
    assert load_watchlists(watchlists_path(tmp_path)) == watchlists
    assert load_alerts(alerts_path(tmp_path)) == alerts
    assert not (tmp_path / rename.WATCHLIST_RENAME_FILENAME).exists()


@pytest.mark.parametrize("failed_write", ["save_watchlists", "save_alerts"])
def test_partial_save_retains_durable_intent_and_recovers_idempotently(
    tmp_path, stores, monkeypatch, failed_write
):
    rename = _rename_module()
    watchlists, alerts = stores

    def fail(*args):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(rename, failed_write, fail)
        with pytest.raises(rename.WatchlistRenamePendingError, match="pending"):
            rename.save_watchlist_rename(tmp_path, watchlists, alerts)
    journal = tmp_path / rename.WATCHLIST_RENAME_FILENAME
    assert json.loads(journal.read_text(encoding="utf-8")) == _journal(
        watchlists, alerts
    )
    assert load_alerts(alerts_path(tmp_path)).get("Rule").watchlist == "Old"
    rename.recover_watchlist_rename(tmp_path)
    assert load_watchlists(watchlists_path(tmp_path)) == watchlists
    assert load_alerts(alerts_path(tmp_path)) == alerts
    assert not journal.exists()
    rename.recover_watchlist_rename(tmp_path)
    assert load_alerts(alerts_path(tmp_path)) == alerts


def test_journal_creation_failure_does_not_accept_or_write_rename(
    tmp_path, stores, monkeypatch
):
    rename = _rename_module()
    watchlists, alerts = stores

    def fail(*args, **kwargs):
        raise OSError("journal unavailable")

    monkeypatch.setattr(rename, "atomic_write_json", fail)
    with pytest.raises(OSError, match="journal unavailable") as error:
        rename.save_watchlist_rename(tmp_path, watchlists, alerts)
    assert not isinstance(error.value, rename.WatchlistRenamePendingError)
    assert load_watchlists(watchlists_path(tmp_path)).names() == ("Old",)
    assert load_alerts(alerts_path(tmp_path)).get("Rule").watchlist == "Old"
    assert not (tmp_path / rename.WATCHLIST_RENAME_FILENAME).exists()


def test_cleanup_failure_keeps_replayable_journal(tmp_path, stores, monkeypatch):
    rename = _rename_module()
    watchlists, alerts = stores
    journal = tmp_path / rename.WATCHLIST_RENAME_FILENAME
    original_unlink = type(journal).unlink

    def locked_unlink(path, *args, **kwargs):
        if path == journal:
            raise PermissionError("journal locked")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(journal), "unlink", locked_unlink)
        with pytest.raises(rename.WatchlistRenamePendingError, match="pending"):
            rename.save_watchlist_rename(tmp_path, watchlists, alerts)
    assert journal.exists()
    assert load_watchlists(watchlists_path(tmp_path)) == watchlists
    assert load_alerts(alerts_path(tmp_path)) == alerts
    rename.recover_watchlist_rename(tmp_path)
    assert not journal.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "not_json",
        "not_object",
        "missing_watchlists",
        "missing_alerts",
        "unknown_version",
        "boolean_version",
        "bad_lists",
        "bad_entry",
        "bad_rules",
        "missing_rule_name",
        "bad_criteria",
        "bad_timestamp",
        "coerced_boolean",
        "extra_paths",
    ],
)
def test_malformed_journal_is_retained_and_never_replaces_user_stores(
    tmp_path, stores, mutation
):
    rename = _rename_module()
    watchlists, alerts = stores
    data = _journal(watchlists, alerts)
    if mutation == "not_json":
        raw = b"{broken"
    elif mutation == "not_object":
        raw = b"[]"
    else:
        if mutation == "missing_watchlists":
            data.pop("watchlists")
        elif mutation == "missing_alerts":
            data.pop("alerts")
        elif mutation == "unknown_version":
            data["version"] = 2
        elif mutation == "boolean_version":
            data["version"] = True
        elif mutation == "bad_lists":
            data["watchlists"]["watchlists"] = "not a list"
        elif mutation == "bad_entry":
            data["watchlists"]["watchlists"][0]["entries"] = [{"kind": "robot"}]
        elif mutation == "bad_rules":
            data["alerts"]["rules"] = "not a list"
        elif mutation == "missing_rule_name":
            data["alerts"]["rules"][0].pop("name")
        elif mutation == "bad_criteria":
            data["alerts"]["rules"][0]["criteria"]["markets"] = ["unknown"]
        elif mutation == "bad_timestamp":
            data["alerts"]["rules"][0]["last_seen_at"] = "yesterday"
        elif mutation == "coerced_boolean":
            data["alerts"]["rules"][0]["enabled"] = 1
        elif mutation == "extra_paths":
            data["alerts_path"] = "../unrelated.json"
        raw = json.dumps(data).encode("utf-8")
    journal = tmp_path / rename.WATCHLIST_RENAME_FILENAME
    journal.write_bytes(raw)
    before_lists = watchlists_path(tmp_path).read_bytes()
    before_alerts = alerts_path(tmp_path).read_bytes()
    with pytest.raises(WatchlistError, match="journal"):
        rename.recover_watchlist_rename(tmp_path)
    assert journal.read_bytes() == raw
    assert watchlists_path(tmp_path).read_bytes() == before_lists
    assert alerts_path(tmp_path).read_bytes() == before_alerts


def test_failed_recovery_keeps_old_intent_and_refuses_to_start_new_rename(
    tmp_path, stores, monkeypatch
):
    rename = _rename_module()
    watchlists, alerts = stores
    journal = tmp_path / rename.WATCHLIST_RENAME_FILENAME
    data = _journal(watchlists, alerts)
    journal.write_text(json.dumps(data), encoding="utf-8")

    def fail(*args):
        raise OSError("still unavailable")

    monkeypatch.setattr(rename, "save_alerts", fail)
    with pytest.raises(OSError) as error:
        rename.save_watchlist_rename(
            tmp_path,
            watchlists.renamed("New", "Third"),
            alerts.renamed_watchlist("New", "Third"),
        )
    assert not isinstance(error.value, rename.WatchlistRenamePendingError)
    assert json.loads(journal.read_text(encoding="utf-8")) == data


def test_disabled_invalid_rule_error_and_utc_watermark_survive_replay(tmp_path, stores):
    rename = _rename_module()
    watchlists, alerts = stores
    alerts = alerts.with_rule(
        AlertRule(
            "Broken",
            criteria=FeedCriteria(value_min=1),
            enabled=False,
            error="Invalid criteria: historical failure",
            last_seen_at=datetime(2026, 11, 15, tzinfo=UTC),
        )
    )
    journal = tmp_path / rename.WATCHLIST_RENAME_FILENAME
    journal.write_text(json.dumps(_journal(watchlists, alerts)), encoding="utf-8")
    rename.recover_watchlist_rename(tmp_path)
    assert load_alerts(alerts_path(tmp_path)) == alerts
    assert not journal.exists()
