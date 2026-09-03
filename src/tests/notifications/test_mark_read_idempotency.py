"""Regression tests: mark_notification_read is idempotent.

M5 (audit): the original implementation ran an unconditional
``UPDATE ... SET read = 1`` and then always decremented the mention and
unread counters and emitted a NOTIFICATION_UPDATE event. Calling the
endpoint twice therefore double-decremented the counters and produced a
duplicate gateway event for a single state transition.

The fix makes the UPDATE conditional (``AND read = 0``) and gates the
counter/event side effects on its rowcount, so a repeated call is a
no-op that still returns True.
"""

import pytest


def _create_mention(notification_manager, messaging_manager, two_users, content):
    """Create an unread mention notification via the direct fan-out API."""
    user1, user2 = two_users
    dm = messaging_manager.create_dm(user1.id, user2.id)
    msg = messaging_manager.send_message(user1.id, dm.id, content)
    notifs = notification_manager.create_notifications_for_message(
        author_id=user1.id,
        message_id=msg.id,
        conversation_id=dm.id,
        content=msg.content,
    )
    assert notifs, "mention should have produced a notification"
    return user2, notifs


class TestMarkReadIdempotency:
    def test_second_mark_read_does_not_decrement_twice(
        self, notification_manager, messaging_manager, two_users
    ):
        user2, notifs = _create_mention(
            notification_manager,
            messaging_manager,
            two_users,
            f"Hey <@{two_users[1].id}>",
        )
        target = notifs[0]

        feed_before = notification_manager.get_notification_feed(user2.id)
        unread_before = feed_before.unread_count

        assert notification_manager.mark_notification_read(user2.id, target.id) is True
        feed_after_first = notification_manager.get_notification_feed(user2.id)
        assert feed_after_first.unread_count == unread_before - 1

        # Second call for the same transition: idempotent no-op.
        assert notification_manager.mark_notification_read(user2.id, target.id) is True
        feed_after_second = notification_manager.get_notification_feed(user2.id)
        assert feed_after_second.unread_count == unread_before - 1, (
            "repeated mark-read must not decrement the unread counter twice"
        )

    def test_mark_read_repeated_returns_true_and_stays_read(
        self, notification_manager, messaging_manager, two_users
    ):
        user2, notifs = _create_mention(
            notification_manager,
            messaging_manager,
            two_users,
            f"<@{two_users[1].id}> again",
        )
        target = notifs[0]

        assert notification_manager.mark_notification_read(user2.id, target.id) is True
        # Still True on the repeat (nothing transitioned, but the target
        # state holds), and the notification stays read.
        assert notification_manager.mark_notification_read(user2.id, target.id) is True
        stored = notification_manager.get_notification(target.id)
        assert stored.read is True
