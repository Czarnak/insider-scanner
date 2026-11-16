"""Regressions for selection ownership and stable investigation widgets."""

from dataclasses import replace

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from insider_scanner.gui.feed_filters import FeedFilterChips
from insider_scanner.gui.feed_page import FeedPageWidget
from insider_scanner.gui.investigation_drawer import InvestigationDrawer
from insider_scanner.gui.theme import DARK, LIGHT, build_stylesheet, get_theme_manager
from insider_scanner.persistence.feed import FeedCriteria, InvestigationBundle
from tests.test_feed_page import FakeFeedRepository, _page
from tests.test_investigation_drawer import FakeRepository, _detail, _record


class CapturingPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


def _text(drawer):
    return " ".join(label.text() for label in drawer.findChildren(QLabel))


def test_rapid_selection_renders_summary_and_only_loads_final_record(qtbot):
    pool = CapturingPool()
    repo = FakeRepository()
    drawer = InvestigationDrawer(repo, thread_pool=pool)
    qtbot.addWidget(drawer)
    for i in range(4):
        drawer.show_record(_record(key=f"us:{i}", issuer=f"Company {i}"))
    assert "Company 3" in _text(drawer)
    assert pool.workers == []
    qtbot.waitUntil(lambda: len(pool.workers) == 1)
    pool.workers[0].run()
    assert repo.detail_calls == ["us:3"]


def test_superseding_before_debounce_expires_invalidates_old_result(qtbot):
    pool = CapturingPool()
    repo = FakeRepository(detail_return=_detail(footnotes="Old secret footnote"))
    drawer = InvestigationDrawer(repo, thread_pool=pool)
    qtbot.addWidget(drawer)
    drawer.show_record(_record())
    qtbot.waitUntil(lambda: len(pool.workers) == 1)
    drawer.show_record(_record(key="us:2", issuer="New company"))
    pool.workers[0].run()
    assert "Old secret footnote" not in _text(drawer)
    assert "New company" in _text(drawer)
    assert not drawer._state_label.isHidden()


def test_palette_during_pending_load_never_restores_previous_bundle(qtbot):
    pool = CapturingPool()
    repo = FakeRepository(detail_return=_detail(footnotes="Previous footnote"))
    drawer = InvestigationDrawer(repo, thread_pool=pool)
    qtbot.addWidget(drawer)
    drawer.show_record(_record())
    qtbot.waitUntil(lambda: len(pool.workers) == 1)
    pool.workers[0].run()
    drawer.show_record(_record(key="us:2", issuer="Current company"))
    drawer._on_palette_changed(get_theme_manager().palette())
    assert "Previous footnote" not in _text(drawer)
    assert "Current company" in _text(drawer)
    assert "Loading" in drawer._state_label.text()
    assert not drawer._state_label.isHidden()
    qtbot.waitUntil(lambda: len(pool.workers) == 2)
    repo._detail_error = RuntimeError("unavailable")
    pool.workers[1].run()
    drawer._on_palette_changed(get_theme_manager().palette())
    assert "Could not load" in drawer._state_label.text()
    assert not drawer._state_label.isHidden()
    assert "Previous footnote" not in _text(drawer)


def test_record_and_theme_updates_reuse_all_content_widgets(qtbot):
    drawer = InvestigationDrawer(None)
    qtbot.addWidget(drawer)
    first = _record()
    drawer.show_record(first)
    widgets = tuple(drawer._content.findChildren(QLabel))
    drawer.show_record(replace(first, key="us:2", issuer="Second issuer"))
    drawer._render(
        drawer._record,
        InvestigationBundle(
            detail=_detail(metadata=(("Party", "Independent"),)),
            by_person=(first,),
            by_issuer=(first,),
        ),
    )
    drawer._on_palette_changed(get_theme_manager().palette())
    assert tuple(drawer._content.findChildren(QLabel)) == widgets
    assert "Second issuer" in _text(drawer)
    drawer.show_record(replace(first, key="us:3", issuer="Third issuer"))
    assert "Independent" not in _text(drawer)
    assert "Second issuer" not in _text(drawer)


def test_semantic_colors_survive_global_styles_and_reused_direction(qtbot, qapp):
    original = qapp.styleSheet()
    drawer = InvestigationDrawer(None)
    qtbot.addWidget(drawer)
    try:
        for palette in (LIGHT, DARK):
            qapp.setStyleSheet(build_stylesheet(palette))
            drawer._on_palette_changed(palette)
            for transaction, color in (
                ("Buy", palette.purchase),
                ("Sell", palette.sale),
            ):
                drawer.show_record(_record(transaction_type=transaction))
                label = next(
                    l
                    for l in drawer.findChildren(QLabel)
                    if l.accessibleName() == "Transaction type"
                )
                label.ensurePolished()
                assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(
                    color
                )
    finally:
        qapp.setStyleSheet(original)


def test_hiding_drawer_cancels_pending_and_submitted_loads(qtbot):
    pool = CapturingPool()
    drawer = InvestigationDrawer(FakeRepository(), thread_pool=pool)
    qtbot.addWidget(drawer)
    drawer.show()
    drawer.show_record(_record())
    drawer.hide()
    qtbot.wait(220)
    assert pool.workers == []
    drawer.show()
    drawer.show_record(_record())
    qtbot.waitUntil(lambda: len(pool.workers) == 1)
    drawer.hide()
    pool.workers[0].run()
    assert drawer._bundle is None


def test_returning_to_parent_resumes_cancelled_detail_load(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    pool = CapturingPool()
    drawer = InvestigationDrawer(FakeRepository(), parent, thread_pool=pool)
    QVBoxLayout(parent).addWidget(drawer)
    parent.show()
    drawer.show_record(_record())
    qtbot.waitUntil(lambda: len(pool.workers) == 1)
    parent.hide()
    pool.workers[0].run()
    assert drawer._bundle is None
    parent.show()
    qtbot.waitUntil(lambda: len(pool.workers) == 2, timeout=600)
    pool.workers[1].run()
    assert drawer._state_label.isHidden()


def test_reused_actions_follow_current_record(qtbot, monkeypatch):
    drawer = InvestigationDrawer(None)
    qtbot.addWidget(drawer)
    companies, watches, urls = [], [], []
    drawer.openCompanyRequested.connect(lambda *args: companies.append(args))
    drawer.watchRequested.connect(watches.append)
    monkeypatch.setattr(
        "insider_scanner.gui.investigation_drawer.webbrowser.open", urls.append
    )
    drawer.show_record(_record())
    current = _record(key="us:2", identifier="MSFT", source_url="https://current.test")
    drawer.show_record(current)
    for name in ("Open company", "Watch", "Open filing"):
        next(b for b in drawer.findChildren(QPushButton) if b.text() == name).click()
    assert companies == [("MSFT", current.market)]
    assert watches == [current]
    assert urls == ["https://current.test"]


def test_feed_model_reset_closes_drawer_and_same_row_can_open_new_record(qtbot):
    pool = CapturingPool()
    page = FeedPageWidget(FakeFeedRepository([_page([_record()])]), thread_pool=pool)
    qtbot.addWidget(page)
    pool.workers[0].run()
    page.table.selectRow(0)
    assert not page.drawer.isHidden()
    replacement = _record(key="us:2", identifier="MSFT")
    page.model.replace_records((replacement,))
    assert page.drawer.isHidden()
    assert page._drawer_origin_row == -1
    page.table.selectRow(0)
    assert page.drawer._record == replacement


def test_chip_click_keeps_sender_alive_until_deferred_deletion(qtbot):
    chips = FeedFilterChips()
    qtbot.addWidget(chips)
    chips.set_criteria(FeedCriteria(search="first"))
    chip = chips._chips["search"]
    observed = []

    def rebuild(_key):
        observed.append((isValid(chip), chip.parent(), chip.isHidden()))
        chips.set_criteria(FeedCriteria(search="second"))

    chips.chipRemoved.connect(rebuild)
    chip.btn_remove.click()
    assert observed == [(True, chips, True)]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(chip)


def test_rebuilding_chips_defers_disposal(qtbot):
    chips = FeedFilterChips()
    qtbot.addWidget(chips)
    chips.set_criteria(FeedCriteria(search="first"))
    chip = chips._chips["search"]
    chips.set_criteria(FeedCriteria())
    assert chip.parent() is chips
    assert chip.isHidden()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(chip)
