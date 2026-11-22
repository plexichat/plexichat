"""Regression: a rename that loses a UNIQUE(server_id, name) race fails closed.

``create_custom_emoji`` translates a concurrent UNIQUE constraint violation into
``EmojiNameExistsError`` (the authoritative check for name collisions). The
rename path's ``SELECT ... AND id != ?`` pre-check is followed by a bare UPDATE;
under concurrency two renames can both pass the pre-check, and the loser's
UPDATE raises a raw ``IntegrityError``. It must be translated to the same
``EmojiNameExistsError`` the create path uses, so the failure stays a clean
409-style domain error instead of leaking an unhandled DB exception.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.core.reactions import EmojiNameExistsError
from src.core.reactions.manager import ReactionManager


@pytest.fixture
def manager():
    db = MagicMock()
    db.fetch_one.return_value = None
    db.fetch_all.return_value = []
    servers = MagicMock()
    servers.has_permission.return_value = True
    with patch.object(ReactionManager, "_migrate_emoji_table"):
        return ReactionManager(db=db, servers_module=servers, media_module=None)


def _emoji_row():
    return {
        "id": 123,
        "server_id": 1,
        "name": "old_name",
        "animated": 0,
        "url": "/media/test.png",
        "size": 1000,
        "width": 64,
        "height": 64,
        "created_by": 1,
        "available": 1,
        "created_at": 1704067200000,
    }


def test_rename_concurrent_unique_violation_becomes_domain_error(manager):
    # The duplicate pre-check misses (concurrent rename won the name), then the
    # UPDATE hits the UNIQUE(server_id, name) constraint.
    manager._db.fetch_one.side_effect = [
        _emoji_row(),  # load emoji by id
        None,  # duplicate pre-check: raced, sees no existing row
    ]
    manager._db.execute.side_effect = sqlite3.IntegrityError(
        "UNIQUE constraint failed: react_custom_emoji.server_id, "
        "react_custom_emoji.name"
    )

    with pytest.raises(EmojiNameExistsError):
        manager.update_custom_emoji(user_id=1, emoji_id=123, name="taken")

    # The failed transaction must be rolled back, not left open.
    manager._db.rollback.assert_called_once()


def test_rename_other_db_error_still_propagates(manager):
    """A non-UNIQUE failure must not be mislabelled as a name collision."""
    manager._db.fetch_one.side_effect = [
        _emoji_row(),  # load emoji by id
        None,  # duplicate pre-check
    ]
    manager._db.execute.side_effect = sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError):
        manager.update_custom_emoji(user_id=1, emoji_id=123, name="taken")
