"""Regression: deleting a message must invalidate unread-count caches.

The unread aggregate is a COUNT over ``msg_messages`` with ``deleted = 0``.
A soft/hard delete therefore *changes* the authoritative count, but the
delete paths never dropped the bounded process-local aggregate cache. A
client that read the badge before the delete kept seeing the pre-delete
count until the short safety-net TTL expired.

This module pins the fail-closed invariant: a message write that can change
unread counts (send already did; delete did not) invalidates the namespace so
the next read recomputes from rows.
"""

import pytest

from src.core.messaging.services import unread_cache


@pytest.mark.messaging
class TestUnreadCountDeleteInvalidation:
    def test_delete_invalidates_cached_unread_count(self, messaging_manager, two_users):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "unread me")

        unread_cache.invalidate_all()

        # Populate the cache with the pre-delete count.
        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 1}
        # And the aggregate view too.
        assert messaging_manager.get_unread_count(user2.id).get(dm.id) == 1

        # The author deletes the message. The backing COUNT excludes the
        # tombstone, so the authoritative count is now zero.
        assert messaging_manager.delete_message(user1.id, msg.id) is True

        # Both views must immediately reflect the delete rather than serve a
        # stale cached count until the 2s safety-net TTL lapses.
        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 0}
        assert messaging_manager.get_unread_count(user2.id).get(dm.id, 0) == 0

    def test_bulk_delete_invalidates_cached_unread_count(
        self, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        first = messaging_manager.send_message(user1.id, dm.id, "one")
        second = messaging_manager.send_message(user1.id, dm.id, "two")

        unread_cache.invalidate_all()

        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 2}

        deleted = messaging_manager.delete_messages_bulk(
            user1.id, dm.id, [first.id, second.id]
        )
        assert set(deleted) == {first.id, second.id}

        assert messaging_manager.get_unread_count(user2.id, dm.id) == {dm.id: 0}
