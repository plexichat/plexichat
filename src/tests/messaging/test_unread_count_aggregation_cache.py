"""Unread-count aggregation cache regression tests.

A client sync burst reads the same user's unread counts several times: the
``/users/@me/unread`` aggregate and one ``/channels/{id}/messages/unread``
call per open channel. Before the bounded aggregate cache each of those reads
issued its own COUNT / GROUP BY over ``msg_participants`` joined with
``msg_messages``. These tests pin the new behaviour:

1. repeated reads in one burst share a single aggregation;
2. a read-state write (mark read) invalidates the user's cached counts;
3. a message write invalidates the aggregate so new messages are counted;
4. the backing in-process tier stays bounded.
"""

import pytest

from src.core.database.cache.base import mem_cache_matching_keys
from src.core.messaging.services import unread_cache


def _count_repo_queries(manager, monkeypatch):
    repo = manager._message_status_svc._repo
    calls = {"all": 0, "one": 0}
    original_all = repo.get_all_unread_counts
    original_one = repo.get_unread_count

    def counted_all(user_id):
        calls["all"] += 1
        return original_all(user_id)

    def counted_one(user_id, conversation_id):
        calls["one"] += 1
        return original_one(user_id, conversation_id)

    monkeypatch.setattr(repo, "get_all_unread_counts", counted_all)
    monkeypatch.setattr(repo, "get_unread_count", counted_one)
    return calls


@pytest.mark.messaging
class TestUnreadCountAggregationCache:
    def test_repeated_burst_reads_share_one_aggregation(
        self, messaging_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "one")
        messaging_manager.send_message(user1.id, dm.id, "two")

        unread_cache.invalidate_all()
        calls = _count_repo_queries(messaging_manager, monkeypatch)

        # First call pays the single GROUP BY (the /users/@me/unread case).
        all_counts = messaging_manager.get_unread_count(user2.id)
        assert all_counts.get(dm.id) == 2
        assert calls["all"] == 1

        # The following per-channel reads in the same burst are served from
        # the aggregate; no further COUNT query is issued.
        for _ in range(5):
            assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 2}
        assert calls["all"] == 1
        assert calls["one"] == 0

    def test_read_invalidates_cached_counts(
        self, messaging_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "one")
        messaging_manager.send_message(user1.id, dm.id, "two")

        unread_cache.invalidate_all()
        calls = _count_repo_queries(messaging_manager, monkeypatch)

        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 2}
        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 2}
        assert calls["one"] == 1  # second read was a cache hit

        # Acknowledging the channel must advance the badge immediately.
        messaging_manager.mark_read(user2.id, dm.id)
        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 0}
        assert calls["one"] == 2  # recomputed after invalidation

    def test_message_write_invalidates_cached_counts(
        self, messaging_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, "one")

        unread_cache.invalidate_all()
        calls = _count_repo_queries(messaging_manager, monkeypatch)

        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 1}

        # A send is a write: the recipient's cached total must be dropped.
        messaging_manager.send_message(user1.id, dm.id, "two")
        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 2}
        assert calls["one"] == 2

        # The aggregate view is invalidated too.
        assert messaging_manager.get_unread_count(user2.id).get(dm.id) == 2
        assert calls["all"] == 1

    def test_bounded_cache_cannot_grow_without_limit(self):
        unread_cache.invalidate_all()
        for i in range(1_200):
            unread_cache.put_one(99_999, 50_000 + i, 1)
        keys = mem_cache_matching_keys("unreadagg:*")
        assert 0 < len(keys) <= 1_000  # shared L1 ceiling, not 1_200 entries
