"""
PlexiStash background cleanup job.

Mirrors the ``RetentionCleanupJob`` pattern (daemon thread loop): periodically
purges soft-deleted Stash items past their retention window and expires
one-time download tickets. Both operations are best-effort and isolated so a
failure in one never aborts the cycle.
"""

import threading
import time
from typing import Any, Dict, Optional

import utils.logger as logger


def _stash_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    import utils.config as cfg

    artifacts = config or cfg.get("artifacts", {}) or {}
    return artifacts.get("plexistash", {}) or {}


class StashCleanupJob:
    """Scheduled background worker for Stash item/ticket cleanup."""

    def __init__(self, db: Any, config: Optional[Dict[str, Any]] = None) -> None:
        self._db = db
        self._config = config
        self._is_running = False
        self._thread: Optional[threading.Thread] = None

    def _interval_seconds(self) -> int:
        stash_cfg = _stash_config(self._config)
        minutes = stash_cfg.get("cleanup_interval_minutes", 60)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            minutes = 60
        if minutes <= 0:
            minutes = 60
        return minutes * 60

    def _retention_days(self) -> int:
        stash_cfg = _stash_config(self._config)
        days = stash_cfg.get("retention_days", 30)
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
        return max(0, days)

    def start(self) -> None:
        """Start the background cleanup loop."""
        if self._is_running:
            return
        if self._db is None:
            logger.info("Stash cleanup skipped: no database")
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="StashCleanupJob"
        )
        self._thread.start()
        logger.info("Stash cleanup job started")

    def _run_forever(self) -> None:
        interval = self._interval_seconds()
        while self._is_running:
            try:
                self.run_once()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Stash cleanup cycle failed: {exc}")
            # Sleep in small increments so stop() can shut down promptly.
            slept = 0
            step = 1.0
            while self._is_running and slept < interval:
                time.sleep(min(step, interval - slept))
                slept += step

    def _resolve_manager(self):
        """Return the Stash manager to run a cleanup pass against.

        Prefers the bootstrapped singleton: it already carries the media and
        servers modules, which the retention purge needs to resolve and
        permanently delete referenced media objects. Falls back to a
        directly-constructed manager only when the domain has not been
        initialised, and wires the media module into that fallback when it
        is available. Without media the purge cannot delete referenced
        objects and soft-deleted items would be retained forever, so that
        degraded state is logged loudly instead of being silent.
        """
        from src.core.suite import stash as stash_domain

        try:
            return stash_domain.get_manager()
        except RuntimeError:
            pass

        media = None
        try:
            from src.core import media as media_domain

            media = media_domain._get_manager()
        except Exception:
            media = None
        if media is None:
            logger.warning(
                "Stash cleanup fallback manager has no media module wired: "
                "referenced media objects will NOT be purged and soft-deleted "
                "items with media references will be retained past their "
                "retention window. Wire stash.setup(db, media=...) so the "
                "retention purge can delete media objects."
            )
        from src.core.suite.stash import StashManager

        return StashManager(self._db, media=media)

    def run_once(self) -> Dict[str, int]:
        """Run one cleanup cycle; returns counts of removed rows."""
        purged = 0
        expired = 0
        try:
            manager = self._resolve_manager()
            purged = manager.purge_deleted_items(self._retention_days())
        except Exception as exc:
            logger.error(f"Stash deleted-item purge failed: {exc}")
        try:
            manager = self._resolve_manager()
            expired = manager.cleanup_expired_tickets()
        except Exception as exc:
            logger.error(f"Stash ticket cleanup failed: {exc}")
        if purged or expired:
            logger.info(
                f"Stash cleanup: purged {purged} deleted items, "
                f"expired {expired} tickets"
            )
        return {"purged": purged, "expired": expired}

    def stop(self) -> None:
        """Stop the background cleanup loop."""
        self._is_running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
