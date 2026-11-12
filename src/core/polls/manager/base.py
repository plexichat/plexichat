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
from ..models import Poll, PollResults


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
    except Exception:
        pass
    try:
        import pg8000.dbapi

        if isinstance(exc, pg8000.dbapi.IntegrityError):
            txt = str(exc).lower()
            return "unique" in txt or "duplicate" in txt or "already exists" in txt
    except Exception:
        pass
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

        logger.info("Poll module initialized")

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
        return {**defaults, **poll_config}

    # -- Implemented by the behaviour mixins ------------------------------
    def get_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Poll]:
        raise NotImplementedError

    def _has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        raise NotImplementedError

    def _voter_count(self, poll_id: SnowflakeID) -> int:
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
