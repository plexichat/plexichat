"""Regression tests: bookmark/scheduled listing ``limit`` clamped from below.

``BookmarkService.get_bookmarks`` and
``ScheduledMessageService.list_scheduled_messages`` passed ``limit`` straight
into the SQL ``LIMIT ?`` with no clamp from below. SQLite treats ``LIMIT -1``
as *unlimited*, so ``limit=-1`` bypassed the page cap and dumped the whole
bookmark / scheduled-message list instead of an empty page (same bug class as
``src/tests/messaging/test_negative_limit_clamp.py``).

Run with: pytest src/tests/messaging/test_bookmark_scheduled_limit_clamp.py -q
"""

import time

import pytest

from src.core.messaging.services.bookmarks import BookmarkService
from src.core.messaging.services.scheduled import ScheduledMessageService


def _future_ms() -> int:
    return int(time.time() * 1000) + 600000


@pytest.mark.messaging
class TestBookmarkNegativeLimitClamp:
    def test_get_bookmarks_negative_limit_returns_empty(
        self, db, messaging_manager, two_users
    ):
        """A negative bookmark limit must not dump the whole bookmark list."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "remember this")
        svc = BookmarkService(db, messaging_manager)
        svc.add_bookmark(user1.id, msg.id, dm.id, label="mine")
        assert svc.get_bookmarks(user1.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert svc.get_bookmarks(user1.id, limit=0) == []
        # Positive limits still work.
        assert len(svc.get_bookmarks(user1.id, limit=10)) == 1


@pytest.mark.messaging
class TestScheduledNegativeLimitClamp:
    def test_list_scheduled_negative_limit_returns_empty(
        self, db, messaging_manager, two_users
    ):
        """A negative scheduled-list limit must not dump the whole list."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        svc = ScheduledMessageService(db)
        svc.create_scheduled_message(user1.id, dm.id, "later", _future_ms())
        assert svc.list_scheduled_messages(user1.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert svc.list_scheduled_messages(user1.id, limit=0) == []
        # Positive limits still work.
        assert len(svc.list_scheduled_messages(user1.id, limit=10)) == 1
