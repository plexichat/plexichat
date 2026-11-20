"""Regression: mark-read counter decrements must not be an N+1 per conversation.

``mark_channel_read`` / ``mark_server_read`` / ``mark_thread_read`` group the
affected notifications by conversation, then used to call
``_decrement_mention_count`` and ``_decrement_unread_count`` for every group.
Each helper did its own SELECT + UPDATE, so fanning a single "mark read" out
over N conversations cost 4N statements (plus the grouped read and the
``read = 1`` UPDATE). Both counters live on the same ``notif_unread`` row, so
the work is now one owner-scoped SELECT + one batched UPDATE per bounded chunk.

These tests assert the statement count is independent of N, that the counters
still clamp/decrement correctly, and that a foreign user's row sharing a
conversation id is never touched (authz fail-closed, no cross-user leak).
"""

import pytest

from src.core.notifications.manager.unread import _BULK_DECREMENT_CHUNK_SIZE


pytestmark = [pytest.mark.notifications, pytest.mark.unit]


class _CountingDB:
    """Delegate to the real DB while recording emitted statements."""

    def __init__(self, real):
        self._real = real
        self.fetch_all_calls = []
        self.execute_calls = []
        self.execute_many_calls = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def fetch_all(self, query, params=None):
        self.fetch_all_calls.append(" ".join(query.split()))
        return self._real.fetch_all(query, params)

    def execute(self, query, params=None):
        self.execute_calls.append(" ".join(query.split()))
        return self._real.execute(query, params)

    def execute_many(self, query, params_list):
        self.execute_many_calls.append((" ".join(query.split()), len(params_list)))
        return self._real.execute_many(query, params_list)


def _seed_channel_notifications(
    users_with_server, messaging_manager, notification_manager, count
):
    """Create ``count`` notifications in one channel, each in its own DM."""
    owner, member1, member2, server, channel, servers, messaging, notifications = (
        users_with_server
    )
    # Fan-out is permission-checked (fail-closed without a servers module).
    notification_manager._servers = servers
    channel_id = channel.id
    server_id = server.id if hasattr(server, "id") else server

    conv_ids = []
    for index in range(count):
        dm = messaging_manager.create_dm(member1.id, member2.id)
        conv_ids.append(dm.id)
        content = f"<@{member2.id}> batched {index}"
        msg = messaging_manager.send_message(member1.id, dm.id, content)
        notification_manager.create_notifications_for_message(
            author_id=member1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
            server_id=server_id,
            channel_id=channel_id,
        )
    return owner, member2, channel_id, conv_ids


def test_mark_channel_read_batches_counter_decrements(
    users_with_server, messaging_manager, notification_manager
):
    """12 conversations cost a constant number of statements, not 4 * 12."""
    owner, recipient, channel_id, _ = _seed_channel_notifications(
        users_with_server, messaging_manager, notification_manager, count=12
    )

    before = notification_manager.get_unread_totals(recipient.id)
    assert before.unread_count == 12
    assert before.mention_count == 12

    real_db = notification_manager._db
    spy = _CountingDB(real_db)
    notification_manager._db = spy
    try:
        marked = notification_manager.mark_channel_read(recipient.id, channel_id)
    finally:
        notification_manager._db = real_db

    assert marked == 12

    # Old path: 1 grouped SELECT + 1 UPDATE + 4 per conversation = 50.
    # New path: 1 grouped SELECT + 1 UPDATE + 1 scoped SELECT + 1 executemany.
    total = (
        len(spy.fetch_all_calls) + len(spy.execute_calls) + len(spy.execute_many_calls)
    )
    assert total <= 6, (
        total,
        spy.fetch_all_calls,
        spy.execute_calls,
        spy.execute_many_calls,
    )
    assert len(spy.execute_many_calls) == 1
    assert len(spy.execute_calls) == 1  # only the `read = 1` UPDATE

    unread_selects = [q for q in spy.fetch_all_calls if "FROM notif_unread" in q]
    assert len(unread_selects) == 1
    # The batched counter read must be owner-scoped.
    assert "user_id = ?" in unread_selects[0]
    # ...and so must the batched write.
    write_sql = spy.execute_many_calls[0][0]
    assert "user_id = ?" in write_sql

    after = notification_manager.get_unread_totals(recipient.id)
    assert after.unread_count == 0
    assert after.mention_count == 0


def test_bulk_decrement_is_owner_scoped_and_clamps(
    users_with_server, messaging_manager, notification_manager
):
    """A foreign row sharing a conversation id is untouched; amounts clamp at 0."""
    owner, recipient, channel_id, conv_ids = _seed_channel_notifications(
        users_with_server, messaging_manager, notification_manager, count=3
    )

    # Simulate the other participant of one of these conversations having its
    # own unread counters for the same conversation id.
    shared_conv = conv_ids[0]
    notification_manager._update_unread_count(
        owner.id, shared_conv, None, None, is_mention=True
    )
    foreign_before = notification_manager.get_unread_totals(owner.id)
    assert foreign_before.unread_count == 1
    assert foreign_before.mention_count == 1

    notification_manager.mark_channel_read(recipient.id, channel_id)

    foreign_after = notification_manager.get_unread_totals(owner.id)
    assert foreign_after.unread_count == 1
    assert foreign_after.mention_count == 1

    recipient_after = notification_manager.get_unread_totals(recipient.id)
    assert recipient_after.unread_count == 0
    assert recipient_after.mention_count == 0

    # Chunking/clamp contract is visible at the helper boundary too.
    assert _BULK_DECREMENT_CHUNK_SIZE > 0
