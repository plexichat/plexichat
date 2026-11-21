"""Regression: scoped mark-read must use ONE grouped read, not COUNT + GROUP BY.

``mark_channel_read`` / ``mark_server_read`` / ``mark_thread_read`` used to run
two reads over the very same ``notif_notifications`` rows before flipping them:

  1. ``SELECT COUNT(*) ... WHERE user_id = ? AND <scope> AND read = 0``  (fetch_one)
  2. ``SELECT conversation_id, COUNT(*) ... GROUP BY conversation_id``  (fetch_all)

The returned count is exactly ``sum(total for each grouped row)``, so query (1)
was a redundant full scan of the same filtered rows. It is removed here: the
grouped read is the single source of truth for both the return value and the
counter decrements.

These tests pin the behaviour: one owner-scoped grouped read and no standalone
COUNT probe, correct counts/decrements, and a foreign user's counters untouched.
"""

import pytest

pytestmark = [pytest.mark.notifications, pytest.mark.unit]


class _QuerySpy:
    """Delegate to the real DB while recording every statement kind."""

    def __init__(self, real):
        self._real = real
        self.fetch_one_calls = []
        self.fetch_all_calls = []
        self.execute_calls = []
        self.execute_many_calls = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def fetch_one(self, query, params=None):
        self.fetch_one_calls.append(" ".join(query.split()))
        return self._real.fetch_one(query, params)

    def fetch_all(self, query, params=None):
        self.fetch_all_calls.append(" ".join(query.split()))
        return self._real.fetch_all(query, params)

    def execute(self, query, params=None):
        self.execute_calls.append(" ".join(query.split()))
        return self._real.execute(query, params)

    def execute_many(self, query, params_list):
        self.execute_many_calls.append((" ".join(query.split()), len(params_list)))
        return self._real.execute_many(query, params_list)


def _seed(users_with_server, messaging_manager, notification_manager, scope, scope_id):
    """Seed 8 unread mentions for member2, one per conversation, in ``scope``."""
    owner, member1, member2, server, channel, servers, messaging, notifications = (
        users_with_server
    )
    notification_manager._servers = servers  # permission-checked fan-out
    scope_kwargs = {"server_id": server.id, "channel_id": channel.id}
    scope_kwargs[scope] = scope_id
    for index in range(8):
        dm = messaging_manager.create_dm(member1.id, member2.id)
        content = f"<@{member2.id}> grouped {index}"
        msg = messaging_manager.send_message(member1.id, dm.id, content)
        notification_manager.create_notifications_for_message(
            author_id=member1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
            **scope_kwargs,
        )
    return member2


@pytest.mark.parametrize(
    "scope,scope_id,method",
    [
        ("channel_id", "channel", "mark_channel_read"),
        ("server_id", "server", "mark_server_read"),
        ("thread_id", 777_777, "mark_thread_read"),
    ],
)
def test_mark_read_uses_single_grouped_query(
    users_with_server,
    messaging_manager,
    notification_manager,
    scope,
    scope_id,
    method,
):
    """A scoped mark-read issues no standalone COUNT over notif_notifications."""
    owner, member1, member2, server, channel, servers, messaging, notifications = (
        users_with_server
    )
    if scope_id == "channel":
        scope_id = channel.id
    elif scope_id == "server":
        scope_id = server.id

    recipient = _seed(
        users_with_server,
        messaging_manager,
        notification_manager,
        scope,
        scope_id,
    )

    before = notification_manager.get_unread_totals(recipient.id)
    assert before.unread_count == 8
    assert before.mention_count == 8

    real_db = notification_manager._db
    spy = _QuerySpy(real_db)
    notification_manager._db = spy
    try:
        marked = getattr(notification_manager, method)(recipient.id, scope_id)
    finally:
        notification_manager._db = real_db

    # Return value still equals the number of rows flipped.
    assert marked == 8

    # THE regression: no redundant ``SELECT COUNT(*) ... notif_notifications``
    # probe. Before the fix this was one fetch_one per mark-read call.
    count_probes = [
        q for q in spy.fetch_one_calls if "COUNT" in q and "notif_notifications" in q
    ]
    assert count_probes == [], count_probes

    # The surviving grouped read is owner-scoped and groups by conversation.
    grouped = [
        q for q in spy.fetch_all_calls if "notif_notifications" in q and "GROUP BY" in q
    ]
    assert len(grouped) == 1, spy.fetch_all_calls
    assert "user_id = ?" in grouped[0]
    assert "GROUP BY conversation_id" in grouped[0]

    # The write stays owner-scoped too.
    writes = [q for q in spy.execute_calls if "read = 1" in q]
    assert len(writes) == 1, spy.execute_calls
    assert "user_id = ?" in writes[0]

    after = notification_manager.get_unread_totals(recipient.id)
    assert after.unread_count == 0
    assert after.mention_count == 0


def test_mark_channel_read_never_touches_foreign_counters(
    users_with_server, messaging_manager, notification_manager
):
    """No cross-user leak: a foreign row on the same conversation survives."""
    owner, member1, member2, server, channel, servers, messaging, notifications = (
        users_with_server
    )
    recipient = _seed(
        users_with_server,
        messaging_manager,
        notification_manager,
        "channel_id",
        channel.id,
    )

    # Give the owner (a different user) its own counter on one of the same
    # conversations. This must not be decremented by the recipient's mark-read.
    shared_conv = notification_manager.get_notifications(recipient.id, limit=1)[0]
    notification_manager._update_unread_count(
        owner.id, shared_conv.conversation_id, server.id, channel.id, is_mention=True
    )
    foreign_before = notification_manager.get_unread_totals(owner.id)
    assert foreign_before.unread_count == 1
    assert foreign_before.mention_count == 1

    notification_manager.mark_channel_read(recipient.id, channel.id)

    foreign_after = notification_manager.get_unread_totals(owner.id)
    assert foreign_after.unread_count == 1
    assert foreign_after.mention_count == 1

    recipient_after = notification_manager.get_unread_totals(recipient.id)
    assert recipient_after.unread_count == 0
    assert recipient_after.mention_count == 0
