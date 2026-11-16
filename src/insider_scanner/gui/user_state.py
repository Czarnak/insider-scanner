"""Own the window's user-state snapshots and recoverable persistence policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from insider_scanner.persistence.alerts import (
    AlertStore,
    alerts_path,
    load_alerts,
    save_alerts,
)
from insider_scanner.persistence.errors import is_access_denied
from insider_scanner.persistence.feed import FeedCriteria
from insider_scanner.persistence.feed_state import (
    SCHEMA_VERSION,
    FeedState,
    SavedScreen,
    load_feed_state,
    save_feed_state,
)
from insider_scanner.persistence.watchlist_rename import (
    WatchlistRenamePendingError,
    recover_watchlist_rename,
    save_watchlist_rename,
)
from insider_scanner.persistence.watchlists import (
    WatchlistError,
    WatchlistStore,
    load_watchlists,
    save_watchlists,
    watchlists_path,
)
from insider_scanner.utils.logging import get_logger

log = get_logger("gui.user_state")


class UserStateInitializationError(RuntimeError):
    """A pending coordinated update cannot safely be recovered at startup."""


class UserStateStore:
    """Keep current user edits in memory even when their files cannot be saved.

    Watchlist renames have a stronger contract: reject an unjournaled rename,
    but accept its new snapshots once the recovery journal is durable. The
    optional notifier receives safe status text and its display timeout.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        notify: Callable[[str, int], None] | None = None,
    ) -> None:
        self.state_path = state_path
        self.data_dir = state_path.parent
        self.watchlists_path = watchlists_path(self.data_dir)
        self.alerts_path = alerts_path(self.data_dir)
        self._notify = notify
        self.feed_state = load_feed_state(state_path)
        try:
            recover_watchlist_rename(self.data_dir)
        except (OSError, WatchlistError) as exc:
            log.exception("Watchlist rename recovery failed")
            raise UserStateInitializationError(
                "Could not recover a pending watchlist rename. "
                "Check data folder access and restart."
            ) from exc
        self.startup_notice: str | None = None
        try:
            self.watchlists = load_watchlists(
                self.watchlists_path,
                legacy_path=self.data_dir / "tickers_watchlist.txt",
            )
        except (OSError, WatchlistError):
            log.exception("Legacy watchlist migration failed")
            self.startup_notice = (
                "Could not import legacy watchlist. Existing watchlists are available; "
                "the import will retry on next startup."
            )
            self.watchlists = load_watchlists(self.watchlists_path)
        self.alerts = load_alerts(self.alerts_path)

    def _notice(self, message: str, timeout_ms: int) -> None:
        if self._notify is not None:
            self._notify(message, timeout_ms)

    def _save_failure(self, what: str, exc: BaseException) -> None:
        if is_access_denied(exc):
            message = f"Couldn't save {what} — the data folder isn't writable."
        else:
            message = f"Couldn't save {what}."
        self._notice(message, 6_000)

    def save_feed(self, criteria: FeedCriteria, column_layout: str | None) -> bool:
        """Capture the live feed without discarding saved screens on failure."""
        self.feed_state = FeedState(
            version=SCHEMA_VERSION,
            criteria=criteria,
            column_layout=column_layout,
            screens=self.feed_state.screens,
        )
        try:
            save_feed_state(self.state_path, self.feed_state)
        except Exception as exc:
            log.exception("Failed to persist feed state")
            self._save_failure("filters", exc)
            return False
        return True

    def set_screen(self, name: str, criteria: FeedCriteria) -> None:
        """Replace a named screen and cap the pending snapshot at 200 screens."""
        screens = tuple(s for s in self.feed_state.screens if s.name != name)
        self.feed_state = replace(
            self.feed_state, screens=(*screens, SavedScreen(name, criteria))[-200:]
        )

    def delete_screen(self, name: str) -> None:
        """Remove a named screen from the pending snapshot."""
        self.feed_state = replace(
            self.feed_state,
            screens=tuple(s for s in self.feed_state.screens if s.name != name),
        )

    def save_watchlists(self, store: WatchlistStore | None = None) -> bool:
        """Retain edits, then recover any earlier rename before writing."""
        if store is not None:
            self.watchlists = store
        try:
            recover_watchlist_rename(self.data_dir)
            save_watchlists(self.watchlists_path, self.watchlists)
        except Exception as exc:
            log.exception("Failed to persist watchlists")
            self._save_failure("watchlists", exc)
            return False
        return True

    def save_alerts(self, store: AlertStore | None = None) -> bool:
        """Retain alert edits and watermarks without overwriting a pending rename."""
        if store is not None:
            self.alerts = store
        try:
            recover_watchlist_rename(self.data_dir)
            save_alerts(self.alerts_path, self.alerts)
        except Exception as exc:
            log.exception("Failed to persist alerts")
            self._save_failure("alerts", exc)
            return False
        return True

    def rename_watchlist(
        self,
        watchlists: WatchlistStore,
        old: str,
        new: str,
        *,
        alerts: AlertStore | None = None,
    ) -> bool:
        """Publish a rename together with current alert references and watermarks."""
        renamed_alerts = (self.alerts if alerts is None else alerts).renamed_watchlist(
            old, new
        )
        try:
            save_watchlist_rename(self.data_dir, watchlists, renamed_alerts)
        except WatchlistRenamePendingError:
            log.exception("Watchlist rename is pending recovery")
            self._notice(
                "Watchlist rename saved for recovery; some files could not be updated.",
                10_000,
            )
        except (OSError, WatchlistError) as exc:
            log.exception("Could not save watchlist rename")
            self._save_failure("watchlist rename", exc)
            return False
        self.watchlists = watchlists
        self.alerts = renamed_alerts
        return True
