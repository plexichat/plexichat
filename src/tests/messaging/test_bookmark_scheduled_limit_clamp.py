"""Regression tests: bookmark/scheduled listing ``limit`` clamped on both sides.

``BookmarkService.get_bookmarks`` and
``ScheduledMessageService.list_scheduled_messages`` passed ``limit`` straight
into the SQL ``LIMIT ?`` with no clamp. SQLite treats ``LIMIT -1`` as
*unlimited*, so ``limit=-1`` dumped the whole list; an arbitrarily large
positive limit (e.g. ``10**12``) bypassed the page cap just as effectively.
For bookmarks every returned row also triggers a message-fetch/enrichment
query, making an unbounded read a cheap denial-of-service primitive.

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


@pytest.mark.messaging
class TestListingUpperLimitClamp:
    def test_bookmarks_huge_limit_is_capped(
        self, db, messaging_manager, two_users
    ):
        """A huge positive bookmark limit must not dump the whole list."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "remember this")
        svc = BookmarkService(db, messaging_manager)
        svc.add_bookmark(user1.id, msg.id, dm.id, label="mine")

        rows = svc.get_bookmarks(user1.id, limit=10**12)
        assert len(rows) <= BookmarkService.MAX_LIST_LIMIT

    def test_scheduled_huge_limit_is_capped(
        self, db, messaging_manager, two_users
    ):
        """A huge positive scheduled limit must not dump the whole history."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        svc = ScheduledMessageService(db)
        svc.create_scheduled_message(user1.id, dm.id, "later", _future_ms())

        rows = svc.list_scheduled_messages(user1.id, limit=10**12)
        assert len(rows) <= ScheduledMessageService.MAX_LIST_LIMIT
