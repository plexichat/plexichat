"""Regression: friendship checks are cached per (user, other), bounded, and
invalidated on every friendship/block mutation.

``_are_friends`` is the primitive consulted from several relationship paths. A
single request can probe the same pair more than once (a block guard followed
by a status read, for example). Without caching that is one authoritative DB
round trip per probe; these tests pin the work to one round trip per distinct
pair while proving a mutation can never leave a stale verdict behind.
"""

import pytest

from src.core.bounded_lru import BoundedLRU
from src.core.relationships.manager.helpers import (
    RelationshipHelpersMixin,
    _FRIEND_CHECK_MAX_ENTRIES,
)

pytestmark = [pytest.mark.relationships]


class CountingDB:
    """Minimal DB double that counts friendship existence probes."""

    def __init__(self, rows=None):
        self.rows = rows or {}
        self.calls = []

    def fetch_one(self, query, params=None):
        if "rel_friends" in query and "user_id = ?" in query:
            self.calls.append(tuple(params))
            return {"ok": 1} if self.rows.get(tuple(params)) else None
        return None


def _checker(rows=None):
    db = CountingDB(rows)
    return RelationshipHelpersMixin(db), db


def test_repeated_checks_hit_db_once(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_cached", lambda _key: None)
    checker, db = _checker()

    # 50 repeated probes of one pair, exactly the per-request fan-out pattern.
    for _ in range(50):
        assert checker._are_friends(1001, 2002) is False

    # Before caching this was 50 identical SELECTs; now it is one.
    assert len(db.calls) == 1
    assert db.calls == [(1001, 2002)]


def test_positive_result_cached_and_pair_scoped(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_cached", lambda _key: None)
    rows = {(1001, 2002): True, (3003, 4004): True}
    checker, db = _checker(rows)

    for _ in range(10):
        assert checker._are_friends(1001, 2002) is True
    assert len(db.calls) == 1  # cached positive, including the False-free path

    # A different pair is a distinct key and a distinct lookup.
    assert checker._are_friends(3003, 4004) is True
    assert len(db.calls) == 2

    # The reversed direction is a distinct key (the check is directional).
    assert checker._are_friends(2002, 1001) is False
    assert len(db.calls) == 3


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_cached", lambda _key: None)
    checker, _db = _checker()

    # Twice the entry ceiling in distinct keys must not grow the cache past it.
    for i in range(_FRIEND_CHECK_MAX_ENTRIES * 2):
        checker._are_friends(1, 10_000 + i)

    stats = checker._friend_check_cache.stats()
    assert isinstance(checker._friend_check_cache, BoundedLRU)
    assert stats["size"] <= _FRIEND_CHECK_MAX_ENTRIES
    assert stats["size"] <= stats["max_entries"]
    assert stats["bytes"] <= stats["max_bytes"]
    assert stats["evictions"] > 0
    assert stats["max_entries"] > 0
    assert stats["max_bytes"] > 0


def test_invalidation_clears_cached_verdict(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_cached", lambda _key: None)
    checker, db = _checker(rows={(1, 2): True})

    assert checker._are_friends(1, 2) is True
    assert len(db.calls) == 1

    checker._invalidate_all_relationships_cache(1, 2)

    # The cached True is gone; the next check re-resolves authoritatively.
    assert checker._are_friends(1, 2) is True
    assert len(db.calls) == 2


def test_remove_friend_invalidates_cached_true(rel_manager, two_users):
    user1, user2 = two_users
    req = rel_manager.send_friend_request(user1.id, user2.id)
    rel_manager.accept_friend_request(user2.id, req.id)

    assert rel_manager._are_friends(user1.id, user2.id) is True
    rel_manager.remove_friend(user1.id, user2.id)

    # Without invalidation this would still serve the cached True.
    assert rel_manager._are_friends(user1.id, user2.id) is False


def test_accept_invalidates_cached_false(rel_manager, two_users):
    user1, user2 = two_users
    # Warm a negative verdict before the friendship exists.
    assert rel_manager._are_friends(user1.id, user2.id) is False

    req = rel_manager.send_friend_request(user1.id, user2.id)
    rel_manager.accept_friend_request(user2.id, req.id)

    # Accept must drop the stale False, not keep denying the new friendship.
    assert rel_manager._are_friends(user1.id, user2.id) is True
