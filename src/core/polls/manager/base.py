"""Shared state and member contract for the poll manager mixins.

``PollManager`` is composed from one mixin per responsibility. This module
owns the manager's state, the validation-free helpers, and the cross-mixin
method contract, so no mixin imports another one.
"""

from typing import Any, Dict, Optional

import utils.config as config
import utils.logger as logger
from ...base import BaseManager, SnowflakeID

from ..exceptions import PollConfigurationError
from ..limits import resolve_field_limits
from ..models import Poll, PollResults
from ..read_cache import PollReadCache


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True if ``exc`` is a UNIQUE-violation error from the driver.

    Both ``sqlite3.IntegrityError`` (covers SQLite's
    ``UNIQUE constraint failed`` text) and pg8000's ``IntegrityError``
    (postgres) qualify. pg8000 maps every integrity failure — UNIQUE, NOT
    NULL, CHECK, FOREIGN KEY — onto the single DB-API ``IntegrityError``
    class, so the postgres branch still inspects the message rather than
    returning True outright. Falls back to textual sniffing
    (``"unique" / "duplicate"``) so drivers that wrap the underlying error
    still match.
    """
    try:
        import sqlite3

        if isinstance(exc, sqlite3.IntegrityError):
            txt = str(exc).lower()
            return "unique" in txt or "duplicate" in txt
    except Exception as inspect_error:
        # Try the next driver-specific shape of the error.
        logger.debug(f"poll unique check probe failed: {inspect_error}")
    try:
        import pg8000.dbapi

        if isinstance(exc, pg8000.dbapi.IntegrityError):
            txt = str(exc).lower()
            return "unique" in txt or "duplicate" in txt or "already exists" in txt
    except Exception as inspect_error:
        logger.debug(f"poll duplicate check probe failed: {inspect_error}")
    txt = str(exc).lower()
    return "unique" in txt or "duplicate" in txt


class PollManagerBase(BaseManager):
    """Manager state plus the cross-mixin method contract."""

    def __init__(self, db, auth_module=None, messaging_module=None):
        """
        Initialize the poll manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for message access
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._config = self._load_config()
        self._encrypt_polls = config.get("encryption.encrypt_polls", False)
        # Bounded, short-TTL accelerator for the hot hydrated reads (detail
        # and results). The authz gate always runs before this is consulted,
        # and every mutation clears it; see reading.py / voting.py.
        self._poll_read_cache = PollReadCache()

        logger.info("Poll module initialized")

    def _poll_read_key(self, kind: str, poll_id, user_id):
        """Build a per-credential key for a cached poll read.

        ``kind`` separates the detail and results namespaces so a results
        entry can never be mistaken for a plain poll body. Numeric coercion
        keeps int/str representations of the same snowflake from creating
        duplicate entries (the API layer passes ids as strings).
        """
        try:
            pid = int(poll_id)
        except (TypeError, ValueError):
            pid = poll_id
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            uid = user_id
        return (kind, pid, uid)

    def _invalidate_poll_read_cache(self) -> None:
        """Drop all cached poll detail/results bodies after a mutation.

        Vote totals and the voter roster are visible to other viewers, so a
        write to any poll invalidates every viewer's cached body. ``getattr``
        keeps this a no-op for lightweight callers/managers that never wired a
        cache (e.g. unit-test stubs borrowing mixin methods).
        """
        cache = getattr(self, "_poll_read_cache", None)
        if cache is not None:
            cache.clear()

    def _require_messaging(self):
        """Fail closed when the messaging module is not wired.

        Ownership and channel-access checks are enforced through the
        messaging layer; running without it would silently skip both,
        so a missing dependency is a configuration error instead of a
        permissive fallback. Returns the messaging module so callers
        keep a narrowed, non-Optional reference.
        """
        if not self._messaging:
            raise PollConfigurationError(
                "Polls module requires the messaging module to be wired "
                "for message-existence and authorization checks; call "
                "polls.setup(db, messaging_module=...) first"
            )
        return self._messaging

    def _load_config(self) -> Dict[str, Any]:
        """Load poll configuration."""
        defaults = {
            "min_options": 2,
            "max_options": 10,
            "min_duration_hours": 1,
            "max_duration_hours": 168,
            "max_question_length": 300,
            "max_option_length": 100,
        }

        poll_config = config.get("polls", {})
        if not isinstance(poll_config, dict):
            poll_config = {}
        merged = {**defaults, **poll_config}
        limits = resolve_field_limits(merged)
        # Every consumer reads ``self._config``; sanitising it here guarantees
        # no other call site can observe an out-of-envelope value.
        merged.update(
            min_options=limits.min_options,
            max_options=limits.max_options,
            max_question_length=limits.max_question_length,
            max_option_length=limits.max_option_length,
            min_duration_hours=limits.min_duration_hours,
            max_duration_hours=limits.max_duration_hours,
        )
        return merged

    # -- Implemented by the behaviour mixins ------------------------------
    def get_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Poll]:
        raise NotImplementedError

    def _has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        raise NotImplementedError

    def _voter_count(self, poll_id: SnowflakeID) -> int:
        raise NotImplementedError

    def _voter_stats(
        self, user_id: SnowflakeID, poll_id: SnowflakeID
    ) -> tuple[int, bool]:
        """Return ``(voter count, viewer voted)`` from one aggregate query."""
        raise NotImplementedError

    def _can_view_results(self, poll: Poll, user_voted: bool) -> bool:
        raise NotImplementedError

    def _row_to_poll(self, row) -> Poll:
        raise NotImplementedError

    def get_results(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> PollResults:
        raise NotImplementedError

    def _end_poll(self, poll_id: SnowflakeID):
        raise NotImplementedError

    def _emit_poll_update(
        self,
        poll: Poll,
        actor_id: SnowflakeID,
        action: str,
    ) -> None:
        raise NotImplementedError

    def _has_admin_poll_voters_perm(self, user_id: SnowflakeID) -> bool:
        raise NotImplementedError
