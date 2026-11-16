"""Own alert evaluation requests independently of the window shell."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from insider_scanner.persistence.alerts import AlertStore
from insider_scanner.persistence.watchlists import WatchlistStore
from insider_scanner.services.alerts import AlertHit, evaluate_alerts
from insider_scanner.utils.logging import get_logger
from insider_scanner.utils.threading import LatestRequest

log = get_logger("gui.alert_controller")


class AlertController(QObject):
    """Publish only current alert hits and coordinate the notification count.

    Rule/watchlist changes, local-data updates and explicit checks all submit
    immutable snapshots through ``check``. Refreshing clears previous results
    immediately; a superseded evaluation cannot restore them. Cancellation
    ends delivery and prevents new work while the owning window drains its
    shared thread pool.
    """

    hitsChanged = Signal(object)  # tuple[AlertHit, ...]
    badgeChanged = Signal(int)

    def __init__(
        self,
        repository: object,
        *,
        thread_pool: QThreadPool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._requests = LatestRequest(self, thread_pool)
        self._stopped = False
        self._match_count = 0

    def check(self, store: AlertStore, watchlists: WatchlistStore) -> None:
        """Evaluate the supplied current snapshots off the GUI thread."""
        if self._stopped:
            return
        self.invalidate()
        if self._repository is None or not any(rule.enabled for rule in store.rules):
            return
        self._requests.start(
            evaluate_alerts,
            args=(self._repository, store, watchlists),
            on_result=self._on_evaluated,
            on_error=self._on_error,
        )

    def invalidate(self) -> None:
        """Discard pending callbacks and clear obsolete presented matches."""
        self._requests.invalidate()
        if not self._stopped:
            self._on_evaluated(())

    @Slot(int)
    def update_match_count(self, count: int) -> None:
        """Follow page acknowledgements and avoid duplicate badge updates."""
        if not self._stopped and count != self._match_count:
            self._match_count = count
            self.badgeChanged.emit(count)

    def request_cancellation(self) -> None:
        """Suppress in-flight callbacks and permanently stop dispatch."""
        self._stopped = True
        self._requests.invalidate()

    def _on_evaluated(self, hits: tuple[AlertHit, ...]) -> None:
        self.hitsChanged.emit(hits)
        self.update_match_count(sum(hit.match_count for hit in hits))

    @staticmethod
    def _on_error(error_info: tuple) -> None:
        log.error("Alert evaluation failed: %s", error_info[1])
