"""Unit tests for ``src.core.base`` shared helpers and :class:`BaseManager`."""

from src.core.base import BaseManager, generate_id, timestamp_ms


class _FakeDB:
    """Minimal DB double exposing only what ``BaseManager._user_exists`` uses."""

    def __init__(self, user_ids=()):
        self._user_ids = set(user_ids)

    def fetch_one(self, query, params):
        assert "auth_users" in query
        return (1,) if params[0] in self._user_ids else None


class _FakeAuth:
    def __init__(self, user_ids=()):
        self._user_ids = set(user_ids)

    def get_user(self, user_id):
        return {"id": user_id} if user_id in self._user_ids else None


def test_timestamp_ms_returns_int():
    assert isinstance(timestamp_ms(), int)


def test_timestamp_ms_is_monotonic_non_decreasing():
    a = timestamp_ms()
    b = timestamp_ms()
    assert b >= a


def test_generate_id_returns_unique_positive_ints():
    ids = {generate_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(isinstance(i, int) and i > 0 for i in ids)


def test_base_manager_timestamp_and_id_helpers():
    manager = BaseManager(db=_FakeDB())
    assert isinstance(manager._get_timestamp(), int)
    assert manager._generate_id() > 0
    assert manager.db is manager._db


def test_user_exists_via_auth_module():
    manager = BaseManager(db=_FakeDB(), auth_module=_FakeAuth(user_ids={42}))
    assert manager._user_exists(42) is True
    assert manager._user_exists(43) is False


def test_user_exists_falls_back_to_db_when_no_auth():
    manager = BaseManager(db=_FakeDB(user_ids={7}))
    assert manager._user_exists(7) is True
    assert manager._user_exists(8) is False


def test_user_exists_falls_back_to_db_when_auth_raises():
    class _BoomAuth:
        def get_user(self, user_id):
            raise RuntimeError("boom")

    manager = BaseManager(db=_FakeDB(user_ids={7}), auth_module=_BoomAuth())
    assert manager._user_exists(7) is True
