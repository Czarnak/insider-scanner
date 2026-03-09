"""Tests for European models, orchestration, and GUI behavior."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import shutil

from PySide6.QtCore import Qt

from insider_scanner.core.eu_merger import (
    DISPLAY_COLUMNS,
    eu_trades_to_dataframe,
    filter_eu_trades,
    merge_eu_trades,
    save_eu_results,
)
from insider_scanner.core.eu_models import EuropeanInsiderTrade, normalize_position
from insider_scanner.core.eu_scan import scrape_eu_trades_for_isin


def _trade(
    *,
    isin: str = "GB0002875804",
    country: str = "UK",
    body: str = "FCA",
    name: str = "Jane Doe",
    trade_date: date | None = None,
    filing_date: date | None = None,
    trade_type: str = "Buy",
    total_value: float | None = 250000.0,
    source_url: str = "https://example.test/rns",
) -> EuropeanInsiderTrade:
    return EuropeanInsiderTrade(
        isin=isin,
        issuer_name="Example Plc",
        country=country,
        regulatory_body=body,
        insider_name=name,
        position="Executive",
        trade_date=trade_date or date(2025, 1, 2),
        filing_date=filing_date or date(2025, 1, 3),
        trade_type=trade_type,
        instrument_type="Share",
        volume=1000.0,
        price=250.0,
        currency="GBP" if country == "UK" else "EUR",
        total_value=total_value,
        source=body.lower(),
        source_url=source_url,
    )


class TestEuropeanModels:
    def test_normalize_position_handles_localized_roles(self):
        assert normalize_position("Directeur General") == "Executive"
        assert normalize_position("Aufsichtsrat") == "Non-Executive"
        assert normalize_position("Aandeelhouder") == "Major Shareholder"
        assert normalize_position("") == "Other"

    def test_roundtrip_and_total_value(self):
        trade = _trade()
        restored = EuropeanInsiderTrade.from_dict(trade.to_dict())

        assert restored == trade
        assert EuropeanInsiderTrade.compute_total_value(3, 4) == 12
        assert EuropeanInsiderTrade.compute_total_value(None, 4) is None


class TestEuropeanMerger:
    def test_merge_deduplicates_and_sorts(self):
        older = _trade(trade_date=date(2025, 1, 1))
        newer = _trade(
            isin="FR0000131104",
            country="FR",
            body="AMF",
            trade_date=date(2025, 1, 4),
        )
        duplicate = _trade(
            trade_date=date(2025, 1, 1),
            source_url="https://duplicate.example",
        )

        merged = merge_eu_trades([older, newer], [duplicate])

        assert merged == [newer, older]

    def test_filter_and_dataframe_and_save(self):
        trades = [
            _trade(country="UK", body="FCA", trade_type="Buy", total_value=250000.0),
            _trade(
                isin="NL0000009165",
                country="NL",
                body="AFM",
                trade_type="Sell",
                trade_date=date(2025, 1, 5),
                total_value=500.0,
            ),
        ]

        filtered = filter_eu_trades(
            trades,
            country="UK",
            trade_type="Buy",
            min_value=1000.0,
            since=date(2025, 1, 1),
            until=date(2025, 1, 31),
        )
        df = eu_trades_to_dataframe(filtered)
        out_root = Path(".tmp_pytest") / "eu_test_outputs"
        if out_root.exists():
            shutil.rmtree(out_root)
        out_dir = save_eu_results(filtered, label="eu_test", output_dir=out_root)

        assert filtered == [trades[0]]
        assert list(df.columns) == DISPLAY_COLUMNS
        assert out_dir == out_root
        assert (out_root / "eu_test.csv").exists()
        assert (out_root / "eu_test.json").exists()


class TestEuropeanScan:
    def test_country_selection_calls_only_requested_scrapers(self, monkeypatch):
        calls = []

        def fake_amf(isin, date_from=None, date_to=None):
            calls.append((isin, date_from, date_to))
            return [_trade(country="FR", body="AMF", isin=isin)]

        monkeypatch.setattr("insider_scanner.core.amf.scrape_amf_trades", fake_amf)

        trades = scrape_eu_trades_for_isin(
            "FR0000131104",
            "FR",
            date(2025, 1, 1),
            date(2025, 1, 31),
        )

        assert len(trades) == 1
        assert trades[0].country == "FR"
        assert calls == [("FR0000131104", date(2025, 1, 1), date(2025, 1, 31))]


class TestEuropeanTab:
    def test_create_and_toggle_dates(self, qtbot):
        from insider_scanner.gui.european_tab import EuropeanTab

        tab = EuropeanTab()
        qtbot.addWidget(tab)

        assert tab.country_combo.currentText() == "All"
        assert not tab.start_date.isEnabled()
        assert not tab.end_date.isEnabled()

        tab._toggle_dates(Qt.Checked)

        assert tab.start_date.isEnabled()
        assert tab.end_date.isEnabled()

    def test_apply_filters_show_detail_and_save(self, qtbot, monkeypatch):
        from insider_scanner.gui.european_tab import EuropeanTab

        tab = EuropeanTab()
        qtbot.addWidget(tab)
        trade = _trade()
        tab._update_table([trade])
        tab.country_combo.setCurrentText("UK")
        tab.type_combo.setCurrentText("Buy")
        tab.min_value_spin.setValue(1000)

        tab._apply_filters()
        tab._show_detail(tab.trades_table.model().index(0, 0))

        saved: dict[str, object] = {}

        def fake_save(trades, label):
            saved["call"] = (trades, label)
            return "outputs/scans"

        monkeypatch.setattr(
            "insider_scanner.gui.european_tab.save_eu_results",
            fake_save,
        )
        monkeypatch.setattr(
            "insider_scanner.gui.european_tab.QMessageBox.information",
            lambda *args: None,
        )

        tab.isin_edit.setText("gb0002875804")
        tab._save_results()

        assert tab.lbl_count.text() == "1 trade(s) shown (of 1 total)."
        assert "Jane Doe" in tab.detail_text.toPlainText()
        assert tab.btn_open_source.isEnabled()
        assert saved["call"][1] == "GB0002875804_uk_eu_scan"

    def test_watchlist_progress_slot_updates_progress_bar(self, qtbot):
        from insider_scanner.gui.european_tab import EuropeanTab

        tab = EuropeanTab()
        qtbot.addWidget(tab)
        tab.progress.setVisible(True)
        tab.progress.setRange(0, 3)

        tab._on_watchlist_progress(2)

        assert tab.progress.value() == 2
