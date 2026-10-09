"""Tests for notification unread count tracking."""

import pathlib
import re

import pytest

from src.core.notifications.manager import unread as unread_module
from src.core.notifications.models import UnreadCount


@pytest.mark.notifications
class TestUnreadCounts:
    """Tests for unread count tracking and management."""

    def test_get_unread_count_no_unread(self, notification_manager, test_user):
        """Test getting unread count when there are no unread notifications."""
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread is not None
        assert isinstance(unread.unread_count, int)
        assert isinstance(unread.mention_count, int)

    def test_get_unread_counts_dict(self, notification_manager, test_user):
        """Test getting per-conversation unread counts."""
        counts = notification_manager.get_unread_counts(test_user.id)
        assert isinstance(counts, dict)

    def test_get_mention_count_zero(self, notification_manager, test_user):
        """Test getting mention count with no mentions."""
        count = notification_manager.get_mention_count(test_user.id)
        assert isinstance(count, int)
        assert count >= 0

    def test_mark_all_read_clears_counts(self, notification_manager, test_user):
        """Test that marking all read clears unread counts."""
        notification_manager.mark_all_read(test_user.id)
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread.mention_count == 0

    def test_unread_count_dataclass(self):
        """Test UnreadCount dataclass fields."""
        uc = UnreadCount(user_id=1, conversation_id=2)
        assert uc.unread_count == 0
        assert uc.mention_count == 0

    def test_unread_count_with_server_filter(self, notification_manager, test_user):
        """Test getting unread count filtered by server."""
        unread = notification_manager.get_unread_count(test_user.id, server_id=999)
        assert unread is not None
        assert unread.unread_count == 0
        assert unread.mention_count == 0

    def test_mark_server_read(self, notification_manager, test_user):
        """Test marking all notifications in a server as read."""
        count = notification_manager.mark_server_read(test_user.id, 999)
        assert isinstance(count, int)

    def test_mark_channel_read(self, notification_manager, test_user):
        """Test marking all notifications in a channel as read."""
        count = notification_manager.mark_channel_read(test_user.id, 999)
        assert isinstance(count, int)


def test_mark_read_invalidates_unread_count_cache(
    monkeypatch, messaging_manager, two_users
):
    """Acking a channel must drop the ``GET /users/@me/unread`` cache entry.

    The route is decorated ``@cached(ttl=10, prefix="unread_counts_api")``
    with key ``unread_counts_api:current_user:TokenInfo:<user_id>``. Before
    the fix nothing ever invalidated that prefix, so a user who read a
    channel kept getting the pre-ack count back for up to 10 seconds.
    """
    user1, user2 = two_users
    conv = messaging_manager.create_group(
        owner_id=user1.id,
        name="unread cache invalidation",
        participant_ids=[user2.id],
    )
    messaging_manager.send_message(user1.id, conv.id, "hello")

    recorded = []

    def _record(pattern: str) -> int:
        recorded.append(pattern)
        return 0

    # The service imports ``invalidate_pattern`` lazily from this module, so
    # patching the attribute here intercepts the call.
    monkeypatch.setattr("src.core.database.invalidate_pattern", _record)

    messaging_manager.mark_read(user2.id, conv.id)

    assert any(
        p.startswith("unread_counts_api") and str(user2.id) in p for p in recorded
    ), f"mark_read must invalidate the unread-count cache (saw: {recorded})"


def test_mark_unread_invalidates_unread_count_cache(
    monkeypatch, messaging_manager, two_users
):
    """Rolling a channel back to unread must drop the same cache entry."""
    user1, user2 = two_users
    conv = messaging_manager.create_group(
        owner_id=user1.id,
        name="unread cache invalidation (rollback)",
        participant_ids=[user2.id],
    )
    messaging_manager.send_message(user1.id, conv.id, "hello")
    messaging_manager.mark_read(user2.id, conv.id)

    recorded = []

    def _record(pattern: str) -> int:
        recorded.append(pattern)
        return 0

    monkeypatch.setattr("src.core.database.invalidate_pattern", _record)

    messaging_manager.mark_unread(user2.id, conv.id)

    assert any(
        p.startswith("unread_counts_api") and str(user2.id) in p for p in recorded
    ), f"mark_unread must invalidate the unread-count cache (saw: {recorded})"


@pytest.mark.notifications
class TestUnreadDecrementClamp:
    """The counter clamp must be engine-portable.

    ``_decrement_*`` used SQLite's two-argument scalar ``MAX(0, x - ?)``,
    which PostgreSQL parses as the one-argument aggregate and rejects
    (``function max(integer, integer) does not exist``). ``feed`` commits the
    read/delete first, so on Postgres the counters were never decremented.
    """

    def test_decrement_clamps_at_zero(self, notification_manager, test_user):
        conv_id = 424242
        notification_manager._update_unread_count(
            test_user.id, conv_id, None, None, is_mention=True
        )
        row = notification_manager._db.fetch_one(
            "SELECT unread_count, mention_count FROM notif_unread "
            "WHERE user_id = ? AND conversation_id = ?",
            (test_user.id, conv_id),
        )
        assert row["unread_count"] == 1
        assert row["mention_count"] == 1

        # Decrement by more than is present: both counters clamp at zero.
        notification_manager._decrement_unread_count(test_user.id, conv_id, amount=5)
        notification_manager._decrement_mention_count(test_user.id, conv_id, amount=5)

        clamped = notification_manager._db.fetch_one(
            "SELECT unread_count, mention_count FROM notif_unread "
            "WHERE user_id = ? AND conversation_id = ?",
            (test_user.id, conv_id),
        )
        assert clamped["unread_count"] == 0
        assert clamped["mention_count"] == 0

        # The manager totals agree (no negative leak into the public API).
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread.unread_count >= 0
        assert unread.mention_count >= 0


def test_unread_sql_does_not_use_two_argument_max():
    """Guard: SQL-level ``MAX(0, …)`` is SQLite-only and must not return.

    Matched case-sensitively on the uppercase SQL spelling so the portable
    Python clamp (``max(0, value - amount)``) in the helpers is not flagged.
    """
    source = pathlib.Path(unread_module.__file__).read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"\bMAX\(\s*0\s*,", executable), (
        "unread.py must clamp in Python, not with SQLite's two-argument MAX()"
    )
