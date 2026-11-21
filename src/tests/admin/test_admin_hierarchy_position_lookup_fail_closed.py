"""A failed role-position lookup must not fabricate a hierarchy grant.

``can_manage_admin`` compares the actor's highest role position with the
target's. The position loader used to swallow DB errors and return 0, so a
transient error while reading the *target* (while the actor's position was
already cached from session validation) made ``current_pos > 0`` true and
granted management authority over an admin the actor cannot legitimately
manage. Failed lookups must deny and must never be cached as 0.
"""

import pytest

from src.core.admin import permission_cache, permissions as perm_core


@pytest.fixture(autouse=True)
def _clear_position_cache():
    with permission_cache._LOCK:
        permission_cache._ENTRIES.clear()
    yield
    with permission_cache._LOCK:
        permission_cache._ENTRIES.clear()


class _SplitDB:
    """Actor's position resolves; the target's lookup raises a DB error."""

    def fetch_one(self, sql, params=()):
        if params and int(params[0]) == 1:
            return {"max_pos": 100}
        raise RuntimeError("db down")


def test_can_manage_admin_denies_when_target_position_lookup_fails():
    db = _SplitDB()

    # A real request warms the actor's position during session validation.
    assert perm_core.get_admin_max_position(db, 1) == 100
    # Before the fix this returned True (100 > 0 from the swallowed error).
    assert perm_core.can_manage_admin(db, 1, 2) is False


class _FlakyDB:
    def __init__(self):
        self.fail = True

    def fetch_one(self, sql, params=()):
        if self.fail:
            raise RuntimeError("db down")
        return {"max_pos": 50}


def test_failed_position_lookup_is_not_cached_as_zero():
    db = _FlakyDB()

    assert perm_core.get_admin_max_position(db, 2) == 0
    db.fail = False
    # Before the fix the error-derived 0 was cached for the whole TTL.
    assert perm_core.get_admin_max_position(db, 2) == 50
