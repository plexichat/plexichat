"""Regression: a profile rename that loses a UNIQUE race fails closed.

``register()`` translates a concurrent UNIQUE constraint violation on
``auth_users.username`` / ``auth_users.email_index`` into ``UserExistsError``
(the 409-style domain error the profile route already catches). The sibling
``update_user()`` path builds a raw ``UPDATE auth_users SET ...`` with only a
format/blacklist check and no uniqueness backstop, so a concurrent rename that
collides on the UNIQUE column leaks a raw ``sqlite3.IntegrityError`` (HTTP 500)
instead of the domain error the create path and the route both expect.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.core.auth.exceptions import UserExistsError
from src.core.auth.manager import AuthManager


def _auth_db():
    return AuthManager.get_instance()._db


def _fail_on_update(db, exc, column):
    """Patch execute to raise ``exc`` on the profile UPDATE touching ``column``."""
    orig_exec = db.execute

    def fake_execute(sql, params=None, **kwargs):
        if "UPDATE auth_users" in str(sql) and column in str(sql):
            raise exc
        return orig_exec(sql, params, **kwargs)

    return patch.object(db, "execute", side_effect=fake_execute)


def test_update_user_username_unique_violation_becomes_domain_error(
    auth_manager, test_user
):
    """A UNIQUE race on rename must surface as UserExistsError, not IntegrityError."""
    db = _auth_db()
    with (
        _fail_on_update(
            db,
            sqlite3.IntegrityError("UNIQUE constraint failed: auth_users.username"),
            "username",
        ),
        patch.object(db, "rollback", new=MagicMock()) as rollback,
    ):
        with pytest.raises(UserExistsError):
            auth_manager.update_user(test_user.id, username="taken_name")

    # The failed statement must be rolled back, not left inside an open txn.
    rollback.assert_called_once()


def test_update_user_unique_violation_also_covers_email_index(auth_manager, test_user):
    """The blind email index is UNIQUE too; its race maps to the same 409."""
    db = _auth_db()
    with (
        _fail_on_update(
            db,
            sqlite3.IntegrityError("UNIQUE constraint failed: auth_users.email_index"),
            "email_index",
        ),
        patch.object(db, "rollback", new=MagicMock()) as rollback,
    ):
        with pytest.raises(UserExistsError):
            auth_manager.update_user(test_user.id, email="taken@example.com")

    rollback.assert_called_once()


def test_update_user_other_db_error_still_propagates(auth_manager, test_user):
    """A non-UNIQUE failure must not be mislabelled as a name collision."""
    db = _auth_db()
    with _fail_on_update(db, sqlite3.OperationalError("disk I/O error"), "username"):
        with pytest.raises(sqlite3.OperationalError):
            auth_manager.update_user(test_user.id, username="some_name")
