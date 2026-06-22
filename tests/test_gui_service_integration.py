from __future__ import annotations

import importlib
import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QRunnable

from insider_scanner.core.eu_models import EuropeanInsiderTrade
from insider_scanner.core.models import CongressTrade, InsiderTrade
from insider_scanner.services.congress import CongressScanService
from insider_scanner.services.context import open_persistence
from insider_scanner.services.european import EuropeanScanService
from insider_scanner.services.us import UsScanService


class InlinePool:
    def start(self, worker):
        worker.run()


class FakeService:
    def __init__(self, result=None, error=None):
        self.result = [] if result is None else result
        self.error = error
        self.calls = []

    def scan(self, identifier, **kwargs):
        self.calls.append(("scan", identifier, kwargs))
        if self.error:
            raise self.error
        return self.result

    def latest(self, **kwargs):
        self.calls.append(("latest", kwargs))
        if self.error:
            raise self.error
        return self.result


def test_scan_tab_worker_uses_injected_service(monkeypatch, qtbot):
    from insider_scanner.gui import scan_tab

    service = FakeService([InsiderTrade(ticker="AAPL", filing_date=date(2025, 1, 2))])
    monkeypatch.setattr(scan_tab.QThreadPool, "globalInstance", lambda: InlinePool())
    tab = scan_tab.ScanTab(service)
    qtbot.addWidget(tab)
    tab.ticker_edit.setText("AAPL")

    tab._run_scan()

    assert service.calls[0][0:2] == ("scan", "AAPL")
    assert service.calls[0][2]["sources"] == ("secform4", "openinsider")
    assert callable(service.calls[0][2]["cancelled"])
    assert len(tab._trades) == 1


def test_scan_tab_watchlist_stops_between_identifiers(monkeypatch, qtbot):
    from insider_scanner.gui import scan_tab

    class CancellingService(FakeService):
        def scan(self, identifier, **kwargs):
            result = super().scan(identifier, **kwargs)
            tab._cancel_event.set()
            return result

    service = CancellingService()
    monkeypatch.setattr(scan_tab.QThreadPool, "globalInstance", lambda: InlinePool())
    monkeypatch.setattr(
        "insider_scanner.utils.config.load_watchlist", lambda: ["AAPL", "MSFT"]
    )
    tab = scan_tab.ScanTab(service)
    qtbot.addWidget(tab)

    tab._run_watchlist()

    assert [call[1] for call in service.calls] == ["AAPL"]
    assert "cancelled" in tab.status_label.text()


def test_scan_tab_latest_uses_injected_service(monkeypatch, qtbot):
    from insider_scanner.gui import scan_tab

    service = FakeService([InsiderTrade(ticker="MSFT")])
    monkeypatch.setattr(scan_tab.QThreadPool, "globalInstance", lambda: InlinePool())
    tab = scan_tab.ScanTab(service)
    qtbot.addWidget(tab)
    tab.latest_count_spin.setValue(25)

    tab._run_latest()

    assert service.calls == [
        (
            "latest",
            {
                "count": 25,
                "sources": ("openinsider",),
                "start_date": None,
                "end_date": None,
                "use_cache": True,
                "cancelled": tab._cancel_event.is_set,
            },
        )
    ]
    assert len(tab._trades) == 1


def test_scan_tab_service_error_is_concise(monkeypatch, qtbot):
    from insider_scanner.gui import scan_tab

    messages = []
    logs = []
    service = FakeService(error=RuntimeError("database internals"))
    monkeypatch.setattr(scan_tab.QThreadPool, "globalInstance", lambda: InlinePool())
    monkeypatch.setattr(
        scan_tab.QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        scan_tab.log,
        "error",
        lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    tab = scan_tab.ScanTab(service)
    qtbot.addWidget(tab)
    tab.ticker_edit.setText("AAPL")

    tab._run_scan()

    assert logs
    assert messages == [("Scan Error", "Scan failed. See logs for details.")]
    assert tab.btn_scan.isEnabled()


def test_congress_tab_worker_uses_injected_service(monkeypatch, qtbot):
    from insider_scanner.gui import congress_tab

    service = FakeService([CongressTrade(official_name="Doe Jane", source="house")])
    monkeypatch.setattr(
        congress_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    tab = congress_tab.CongressTab(service)
    qtbot.addWidget(tab)

    tab._run_scan()

    assert service.calls[0][0:2] == ("scan", "All")
    assert service.calls[0][2]["sources"] == ("house", "senate")


def test_gui_default_scans_work_without_enabled_dates(monkeypatch, qtbot, tmp_path):
    from insider_scanner.gui import congress_tab, european_tab, scan_tab

    monkeypatch.setattr(scan_tab.QThreadPool, "globalInstance", lambda: InlinePool())
    monkeypatch.setattr(
        congress_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    monkeypatch.setattr(
        european_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    persistence = open_persistence(tmp_path / "gui-default.sqlite3")
    us = UsScanService(
        persistence,
        adapters={"secform4": lambda *_: [], "openinsider": lambda *_: []},
        latest_adapters={
            "openinsider": lambda *_: [
                InsiderTrade(
                    ticker="AAPL",
                    insider_name="Jane",
                    trade_type="Buy",
                    filing_date=date(2025, 1, 3),
                    source="openinsider",
                )
            ]
        },
    )
    congress = CongressScanService(
        persistence,
        adapters={"house": lambda *_: [], "senate": lambda *_: []},
        today=lambda: date(2026, 6, 8),
    )
    european = EuropeanScanService(
        persistence,
        adapters={source: lambda *_: [] for source in ("rns", "bafin", "amf", "afm")},
        latest_adapters={
            "rns": lambda *_: [
                EuropeanInsiderTrade(
                    isin="GB0002875804",
                    country="UK",
                    regulatory_body="FCA",
                    insider_name="Jane",
                    trade_type="Buy",
                    trade_date=date(2025, 1, 2),
                    source="rns",
                )
            ]
        },
    )
    try:
        us_tab = scan_tab.ScanTab(us)
        congress_widget = congress_tab.CongressTab(congress)
        eu_tab = european_tab.EuropeanTab(european)
        for widget in (us_tab, congress_widget, eu_tab):
            qtbot.addWidget(widget)

        us_tab.ticker_edit.setText("AAPL")
        eu_tab.isin_edit.setText("GB0002875804")
        us_tab._run_scan()
        congress_widget._run_scan()
        eu_tab._run_scan()

        assert len(us_tab._trades) == 1
        assert congress_widget._trades == []
        assert len(eu_tab._trades) == 1
    finally:
        persistence.close()


def test_congress_tab_service_error_is_concise(monkeypatch, qtbot):
    from insider_scanner.gui import congress_tab

    messages = []
    service = FakeService(error=RuntimeError("database internals"))
    monkeypatch.setattr(
        congress_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    monkeypatch.setattr(
        congress_tab.QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(congress_tab.log, "error", lambda *args, **kwargs: None)
    tab = congress_tab.CongressTab(service)
    qtbot.addWidget(tab)

    tab._run_scan()

    assert messages == [("Scan Error", "Scan failed. See logs for details.")]
    assert tab.btn_scan.isEnabled()


def test_service_error_is_logged_and_shown_concisely(monkeypatch, qtbot):
    from insider_scanner.gui import european_tab

    service = FakeService(error=RuntimeError("database path and internals"))
    messages = []
    logs = []
    monkeypatch.setattr(
        european_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    monkeypatch.setattr(
        european_tab.QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        european_tab.log,
        "error",
        lambda *args, **kwargs: logs.append((args, kwargs)),
    )
    tab = european_tab.EuropeanTab(service)
    qtbot.addWidget(tab)
    tab.isin_edit.setText("GB0002875804")

    tab._run_scan()

    assert logs
    assert messages == [("Scan Error", "Scan failed. See logs for details.")]


def test_european_watchlist_and_latest_use_injected_service(monkeypatch, qtbot):
    from insider_scanner.gui import european_tab

    trade = EuropeanInsiderTrade(
        isin="GB0002875804",
        country="UK",
        trade_type="Buy",
        source="rns",
    )
    service = FakeService([trade])
    monkeypatch.setattr(
        european_tab.QThreadPool, "globalInstance", lambda: InlinePool()
    )
    monkeypatch.setattr(
        european_tab,
        "load_eu_watchlist",
        lambda: ["GB0002875804", "GB0000000001"],
    )
    tab = european_tab.EuropeanTab(service)
    qtbot.addWidget(tab)
    tab.country_combo.setCurrentText("UK")

    tab._run_watchlist()
    tab.latest_count_spin.setValue(20)
    tab._run_latest()

    assert [call[1] for call in service.calls[:2]] == [
        "GB0002875804",
        "GB0000000001",
    ]
    assert service.calls[2] == (
        "latest",
        {
            "count": 20,
            "country": "UK",
            "start_date": None,
            "end_date": None,
            "use_cache": True,
            "cancelled": tab._cancel_event.is_set,
        },
    )
    assert tab.progress.isHidden()


def test_main_window_shares_injected_services(qtbot):
    from insider_scanner.gui.main_window import MainWindow

    services = SimpleNamespace(
        us=FakeService(), congress=FakeService(), european=FakeService()
    )
    win = MainWindow(services)
    qtbot.addWidget(win)

    assert win.scan_tab._service is services.us
    assert win.congress_tab._service is services.congress
    assert win.european_tab._service is services.european
    assert win.scan_tab._thread_pool is win._thread_pool
    assert win.congress_tab._thread_pool is win._thread_pool
    assert win.european_tab._thread_pool is win._thread_pool


def test_main_window_close_cancels_and_drains_real_worker_before_service_close(qtbot):
    from insider_scanner.gui.main_window import MainWindow

    started = threading.Event()
    finished = threading.Event()
    events = []

    class BlockingRunnable(QRunnable):
        def run(self):
            started.set()
            while not win.scan_tab._cancel_event.is_set():
                time.sleep(0.005)
            events.append("worker-finished")
            finished.set()

    class Services:
        us = FakeService()
        congress = FakeService()
        european = FakeService()

        def close(self):
            assert finished.is_set()
            events.append("services-closed")

    services = Services()
    win = MainWindow(services, shutdown_timeout_ms=1_000)
    qtbot.addWidget(win)
    win.show()
    win._thread_pool.start(BlockingRunnable())
    assert started.wait(timeout=1)

    assert win.close()
    services.close()

    assert events == ["worker-finished", "services-closed"]
    assert win._thread_pool.activeThreadCount() == 0
    assert win.scan_tab._cancel_event.is_set()
    assert win.congress_tab._cancel_event.is_set()
    assert win.european_tab._cancel_event.is_set()


def test_main_window_close_timeout_is_logged_and_ignored(qtbot, caplog):
    from insider_scanner.gui.main_window import MainWindow

    release = threading.Event()
    started = threading.Event()

    class NonCooperativeRunnable(QRunnable):
        def run(self):
            started.set()
            release.wait(timeout=2)

    win = MainWindow(
        SimpleNamespace(
            us=FakeService(),
            congress=FakeService(),
            european=FakeService(),
        ),
        shutdown_timeout_ms=25,
    )
    qtbot.addWidget(win)
    win.show()
    win._thread_pool.start(NonCooperativeRunnable())
    assert started.wait(timeout=1)

    with caplog.at_level("WARNING"):
        assert not win.close()

    assert win.isVisible()
    assert "Timed out waiting for GUI workers to stop" in caplog.text
    release.set()
    assert win._thread_pool.waitForDone(1_000)
    assert win.close()


@pytest.mark.parametrize(
    ("module_name", "class_name", "tab_name"),
    [
        ("insider_scanner.gui.scan_tab", "ScanTab", "Scan"),
        ("insider_scanner.gui.congress_tab", "CongressTab", "Congress"),
        ("insider_scanner.gui.european_tab", "EuropeanTab", "European"),
    ],
)
def test_main_window_tab_failure_is_logged_and_propagated_without_secret(
    module_name, class_name, tab_name, monkeypatch, caplog, qtbot
):
    from insider_scanner.gui import main_window

    secret = "api-token=super-secret"

    def fail_tab(service, *, thread_pool=None):
        raise RuntimeError(secret)

    tab_module = importlib.import_module(module_name)
    monkeypatch.setattr(tab_module, class_name, fail_tab)

    with (
        caplog.at_level("ERROR"),
        pytest.raises(main_window.MainWindowInitializationError) as exc_info,
    ):
        main_window.MainWindow(
            SimpleNamespace(
                us=FakeService(),
                congress=FakeService(),
                european=FakeService(),
            )
        )

    assert str(exc_info.value) == "Could not initialize application window."
    assert secret not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert secret in caplog.text
    assert f"{tab_name} tab initialization failed" in caplog.text


def test_sec_ingest_range_dispatch_emits_progress_cancels_and_reads_concurrently(
    tmp_path, qtbot
):
    """A background SEC ingest job emits progress, honours cancellation between
    dates, and lets a second connection read committed rows mid-run."""
    from unittest.mock import Mock

    from insider_scanner.core.sec_client import SecTransport
    from insider_scanner.services.sec_daily import SecDailyIngestionService
    from insider_scanner.utils.threading import dispatch
    from tests.test_sec_daily_service import (
        DAY,
        EMPTY_INDEX,
        INDEX_DAY_TWO,
        INDEX_ONE_ROW,
        NEXT_DAY,
    )
    from tests.test_sec_downloads import StubResponse, VALID_FILING, make_client

    db_path = tmp_path / "sec-bg.sqlite3"
    persistence = open_persistence(db_path)

    def _get(url: str, **_kwargs: object) -> StubResponse:
        if url.endswith(".idx"):
            if "20260615" in url:
                return StubResponse(chunks=(INDEX_ONE_ROW.encode("utf-8"),))
            if "20260616" in url:
                return StubResponse(chunks=(INDEX_DAY_TWO.encode("utf-8"),))
            return StubResponse(chunks=(EMPTY_INDEX.encode("utf-8"),))
        return StubResponse(
            chunks=(VALID_FILING,), headers={"Content-Type": "application/xml"}
        )

    transport = Mock(spec=SecTransport)
    transport.get.side_effect = _get

    cancel_event = threading.Event()
    progress: list[object] = []
    results: list[object] = []
    concurrent_read: dict[str, object] = {}

    def on_progress(snapshot: object) -> None:
        progress.append(snapshot)

        # A separate connection must read the just-committed rows mid-run.
        def _read() -> None:
            reader = open_persistence(db_path)
            try:
                concurrent_read["rows"] = list(
                    reader.us_trades.query_latest(100, sources="sec_edgar")
                )
            finally:
                reader.close()

        thread = threading.Thread(target=_read)
        thread.start()
        thread.join(timeout=5)

        # Cancel after the first date completes; the second date must be skipped.
        cancel_event.set()

    def work(progress_callback):
        service = SecDailyIngestionService(
            persistence,
            client=make_client(transport),
            cache_root=tmp_path / "cache",
        )
        return service.ingest_range(
            DAY,
            NEXT_DAY,
            cancelled=cancel_event.is_set,
            on_progress=progress_callback,
        )

    try:
        dispatch(
            InlinePool(),
            work,
            on_result=results.append,
            on_progress=on_progress,
        )
    finally:
        persistence.close()

    # Progress emitted for the first date only (cancelled before the second).
    assert [s.current_date for s in progress] == [DAY]  # type: ignore[attr-defined]
    assert results and results[0].dates_completed == 1  # type: ignore[attr-defined]
    # The concurrent reader saw the first date's row committed during the run.
    assert len(concurrent_read["rows"]) == 1  # type: ignore[arg-type]
