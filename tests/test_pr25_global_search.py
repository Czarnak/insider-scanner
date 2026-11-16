"""Keyboard and request-lifetime regressions for the top-bar search."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from insider_scanner.gui.global_search import GlobalSearchController
from insider_scanner.persistence.feed import EntityHit, EntitySearchResults, FeedMarket


class SearchRepository:
    def __init__(self):
        self.calls = []

    def search_entities(self, text):
        self.calls.append(text)
        return EntitySearchResults(
            companies=(EntityHit("company", "Apple", "AAPL", "", FeedMarket.US),),
            insiders=(EntityHit("insider", "Jane", "", "Jane", FeedMarket.US),),
        )


class SearchPool:
    def __init__(self, *, delayed=False):
        self.delayed = delayed
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)
        if not self.delayed:
            worker.run()


def make_search(qtbot, *, delayed=False):
    window = QWidget()
    layout = QVBoxLayout(window)
    line = QLineEdit()
    other = QPushButton("Other")
    layout.addWidget(line)
    layout.addWidget(other)
    repository = SearchRepository()
    pool = SearchPool(delayed=delayed)
    controller = GlobalSearchController(
        line, repository, thread_pool=pool, debounce_ms=20
    )
    qtbot.addWidget(window)
    qtbot.addWidget(controller.popup)
    window.show()
    QApplication.processEvents()
    window.activateWindow()
    line.setFocus()
    qtbot.waitUntil(line.hasFocus)
    return window, line, other, controller, repository, pool


def test_results_keep_typing_focus(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")
    QApplication.processEvents()

    assert controller.popup.isVisible()
    assert QApplication.focusWidget() is line
    qtbot.keyClicks(QApplication.focusWidget(), "le")
    assert line.text() == "apple"


def test_typing_hides_obsolete_choices_before_debounce(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")
    controller.popup.list_widget.setCurrentRow(2)
    line.setText("msft")

    assert not controller.popup.isVisible()
    company = []
    searches = []
    controller.companyChosen.connect(lambda *args: company.append(args))
    controller.feedSearchRequested.connect(searches.append)
    line.returnPressed.emit()
    assert company == []
    assert searches == ["msft"]


def test_hidden_choices_do_not_override_current_text(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("apple")
    controller.search_now("apple")
    controller.popup.list_widget.setCurrentRow(2)
    controller.popup.hide()
    companies = []
    searches = []
    controller.companyChosen.connect(lambda *args: companies.append(args))
    controller.feedSearchRequested.connect(searches.append)

    qtbot.keyClick(line, Qt.Key.Key_Return)

    assert companies == []
    assert searches == ["apple"]


def test_keyboard_navigation_skips_headers_and_activates(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")
    controller.popup.list_widget.setCurrentRow(0)
    chosen = []
    controller.companyChosen.connect(lambda *args: chosen.append(args))

    qtbot.keyClick(line, Qt.Key.Key_Down)
    assert controller.popup.list_widget.currentRow() == 2
    qtbot.keyClick(line, Qt.Key.Key_Down)
    assert controller.popup.list_widget.currentRow() == 4
    qtbot.keyClick(line, Qt.Key.Key_Up)
    assert controller.popup.list_widget.currentRow() == 2
    qtbot.keyClick(line, Qt.Key.Key_Return)

    assert chosen == [("AAPL", FeedMarket.US)]
    assert not controller.popup.isVisible()
    assert line.hasFocus()


def test_escape_cancels_pending_results(qtbot):
    _window, line, _, controller, _, pool = make_search(qtbot, delayed=True)
    line.setText("app")
    controller.search_now("app")

    qtbot.keyClick(line, Qt.Key.Key_Escape)
    pool.workers[0].run()
    qtbot.wait(30)

    assert not controller.popup.isVisible()
    assert len(pool.workers) == 1


def test_enter_cancels_pending_debounce(qtbot):
    _window, line, _, controller, repository, _ = make_search(qtbot)
    searches = []
    controller.feedSearchRequested.connect(searches.append)
    line.setText("app")

    qtbot.keyClick(line, Qt.Key.Key_Return)
    qtbot.wait(30)

    assert searches == ["app"]
    assert repository.calls == []
    assert not controller.popup.isVisible()


def test_leaving_search_cancels_pending_results(qtbot):
    _window, line, other, controller, _, pool = make_search(qtbot, delayed=True)
    line.setText("app")
    controller.search_now("app")
    other.setFocus()
    qtbot.waitUntil(other.hasFocus)

    pool.workers[0].run()
    qtbot.wait(30)

    assert not controller.popup.isVisible()
    assert len(pool.workers) == 1


def test_request_cancellation_suppresses_results_and_debounce(qtbot):
    _window, line, _, controller, _, pool = make_search(qtbot, delayed=True)
    line.setText("app")
    controller.search_now("app")
    controller.request_cancellation()
    pool.workers[0].run()
    qtbot.wait(30)

    assert not controller.popup.isVisible()
    assert len(pool.workers) == 1


def test_clicking_outside_results_dismisses_without_focus_change(qtbot):
    window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")

    qtbot.mouseClick(window, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))

    assert line.hasFocus()
    assert not controller.popup.isVisible()


def test_mouse_choice_activates_once_without_taking_typing_focus(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")
    chosen = []
    controller.companyChosen.connect(lambda *args: chosen.append(args))
    results = controller.popup.list_widget

    qtbot.mouseClick(
        results.viewport(),
        Qt.MouseButton.LeftButton,
        pos=results.visualItemRect(results.item(2)).center(),
    )

    assert chosen == [("AAPL", FeedMarket.US)]
    assert not controller.popup.isVisible()
    assert line.hasFocus()


def test_enter_suppresses_already_dispatched_search_results(qtbot):
    _window, line, _, controller, _, pool = make_search(qtbot, delayed=True)
    line.setText("app")
    controller.search_now("app")
    searches = []
    controller.feedSearchRequested.connect(searches.append)

    qtbot.keyClick(line, Qt.Key.Key_Return)
    pool.workers[0].run()

    assert searches == ["app"]
    assert not controller.popup.isVisible()


def test_moving_the_window_dismisses_detached_results(qtbot):
    window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")

    window.move(window.pos() + QPoint(20, 20))
    QApplication.processEvents()

    assert not controller.popup.isVisible()


def test_mouse_activation_cannot_reuse_a_hidden_choice(qtbot):
    _window, line, _, controller, _, _ = make_search(qtbot)
    line.setText("app")
    controller.search_now("app")
    item = controller.popup.list_widget.item(2)
    chosen = []
    controller.companyChosen.connect(lambda *args: chosen.append(args))

    controller.popup.list_widget.itemClicked.emit(item)
    controller.popup.list_widget.itemActivated.emit(item)

    assert chosen == [("AAPL", FeedMarket.US)]


def test_direct_new_search_hides_previous_choices_until_result(qtbot):
    _window, _line, _, controller, _, pool = make_search(qtbot, delayed=True)
    controller.search_now("app")
    pool.workers[0].run()
    assert controller.popup.isVisible()

    controller.search_now("msft")

    assert not controller.popup.isVisible()
    pool.workers[1].run()
    assert controller.popup.isVisible()
    assert controller.popup.list_widget.item(0).text() == 'Filter feed for "msft"'


def test_search_anchor_destruction_disconnects_events_and_pending_work(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    line = QLineEdit(window)
    pool = SearchPool(delayed=True)
    controller = GlobalSearchController(
        line, SearchRepository(), parent=window, thread_pool=pool
    )
    line.setText("app")
    controller.search_now("app")

    try:
        with qtbot.captureExceptions() as exceptions:
            line.deleteLater()
            QCoreApplication.sendPostedEvents(line, QEvent.Type.DeferredDelete)
            QCoreApplication.sendEvent(window, QEvent(QEvent.Type.Move))
            pool.workers[0].run()

        assert exceptions == []
    finally:
        if isValid(controller):
            QApplication.instance().removeEventFilter(controller)
            controller.deleteLater()
            QCoreApplication.sendPostedEvents(controller, QEvent.Type.DeferredDelete)
