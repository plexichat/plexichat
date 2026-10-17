"""Regression tests: notification listing ``limit`` must be clamped.

``FeedMixin.get_notifications`` / ``get_notification_feed`` capped ``limit``
from above (``min(limit, max_per_page)``) but never from below. A negative
``limit`` reached the SQL ``LIMIT ?`` verbatim, and SQLite treats
``LIMIT -1`` as *unlimited* — so ``GET /users/@me/notifications?limit=-1``
(which accepts any ``int``) dumped the caller's whole notification table
instead of an empty page.
"""

from unittest.mock import patch

from src.utils import encryption


def _make_notified_user(auth_manager, messaging_manager, notification_manager):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user1 = auth_manager.register(
            username="limittest1", email="limit1@example.com", password="TestPass123!"
        )
        user2 = auth_manager.register(
            username="limittest2", email="limit2@example.com", password="TestPass123!"
        )
    dm = messaging_manager.create_dm(user1.id, user2.id)
    content = f"<@{user2.id}> hello"
    msg = messaging_manager.send_message(user1.id, dm.id, content)
    notification_manager.create_notifications_for_message(
        author_id=user1.id,
        message_id=msg.id,
        conversation_id=dm.id,
        content=content,
    )
    assert len(notification_manager.get_notifications(user2.id)) == 1
    return user2


class TestNegativeLimitClamp:
    def test_get_notifications_negative_limit_returns_empty(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """A negative limit must not bypass the page cap (LIMIT -1 = all)."""
        user2 = _make_notified_user(
            auth_manager, messaging_manager, notification_manager
        )
        assert notification_manager.get_notifications(user2.id, limit=-1) == []

    def test_get_notifications_zero_limit_returns_empty(
        self, auth_manager, messaging_manager, notification_manager
    ):
        user2 = _make_notified_user(
            auth_manager, messaging_manager, notification_manager
        )
        assert notification_manager.get_notifications(user2.id, limit=0) == []

    def test_feed_negative_limit_returns_empty_page_with_totals(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """The feed page is empty but user-scoped totals are unaffected."""
        user2 = _make_notified_user(
            auth_manager, messaging_manager, notification_manager
        )
        feed = notification_manager.get_notification_feed(user2.id, limit=-1)
        assert feed.notifications == []
        assert feed.total_count == 1
        assert feed.unread_count == 1
        # A clamped negative limit behaves exactly like limit=0.
        zero = notification_manager.get_notification_feed(user2.id, limit=0)
        assert feed.has_more == zero.has_more
        assert len(zero.notifications) == 0

    def test_positive_limit_still_honoured(
        self, auth_manager, messaging_manager, notification_manager
    ):
        user2 = _make_notified_user(
            auth_manager, messaging_manager, notification_manager
        )
        assert len(notification_manager.get_notifications(user2.id, limit=50)) == 1
        feed = notification_manager.get_notification_feed(user2.id, limit=50)
        assert len(feed.notifications) == 1
        assert feed.has_more is False
