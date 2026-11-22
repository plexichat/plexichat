"""Security regressions for bounded bulk operations and edit-history reads.

- Bulk archive previously accepted an unbounded list of message ids, unlike
  the bulk-delete path which caps requests at 100 ids.
- Edit history fetches previously had no SQL LIMIT, so a single read could
  pull an unlimited number of (possibly encrypted) rows into memory.
"""

import pytest

from src.core.messaging.exceptions import InvalidContentError
from src.core.messaging.repositories.edit_history import MAX_EDIT_HISTORY_ROWS
from src.core.messaging.services.edit_history import EditHistoryService
from src.core.messaging.services.message.edit_delete import BULK_ARCHIVE_MAX_IDS


class _WiredDMServers:
    """A wired servers store whose reverse lookup finds no backing channel
    (a genuine DM), so the channel-view gate permits the read. The fail-closed
    gate still denies reads when this module is genuinely unwired."""

    def get_channel_by_conversation(self, conversation_id):
        return None

    def get_channel(self, channel_id, user_id):
        raise AssertionError("DM needs no channel view check")


@pytest.mark.messaging
class TestBulkOperationBounds:
    def test_archive_bulk_rejects_oversized_batch(self, messaging_manager, two_users):
        user1, _user2 = two_users
        oversized = [1_000_000 + i for i in range(BULK_ARCHIVE_MAX_IDS + 1)]

        with pytest.raises(InvalidContentError):
            messaging_manager._message_svc.archive_messages_bulk(user1.id, oversized)

    def test_archive_bulk_within_cap_still_succeeds(self, group_conversation):
        group, owner = group_conversation[0], group_conversation[1]
        messaging = group_conversation[4]
        msg = messaging.send_message(owner.id, group.id, "archive me")

        result = messaging._message_svc.archive_messages_bulk(owner.id, [msg.id])

        assert result["success_count"] == 1
        assert result["failed_count"] == 0


@pytest.mark.messaging
class TestEditHistoryFetchBounds:
    def _seed_history(self, db, message_id, user_id, count):
        for version in range(1, count + 1):
            db.execute(
                """INSERT INTO msg_edit_history
                   (id, message_id, editor_id, old_content, old_content_encrypted,
                    edit_timestamp, version_number)
                   VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                (
                    900_000_000 + version,
                    message_id,
                    user_id,
                    "old content",
                    version,
                    version,
                ),
            )

    def test_get_edit_history_is_bounded(self, db, messaging_manager, two_users):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "history")

        total = MAX_EDIT_HISTORY_ROWS + 25
        self._seed_history(db, msg.id, user1.id, total)

        svc = EditHistoryService(
            db,
            messaging_manager._participant_svc,
            encryption_enabled=False,
            servers_mod=_WiredDMServers(),
        )
        history = svc.get_edit_history(user1.id, msg.id)

        assert len(history) == MAX_EDIT_HISTORY_ROWS
        # Newest versions are retained, returned in ascending order.
        assert history[0]["version_number"] == total - MAX_EDIT_HISTORY_ROWS + 1
        assert history[-1]["version_number"] == total

    def test_get_edit_history_oversized_limit_is_clamped(
        self, db, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "history")
        self._seed_history(db, msg.id, user1.id, MAX_EDIT_HISTORY_ROWS + 5)

        svc = EditHistoryService(
            db,
            messaging_manager._participant_svc,
            encryption_enabled=False,
            servers_mod=_WiredDMServers(),
        )
        history = svc.get_edit_history(user1.id, msg.id, limit=1_000_000)

        assert len(history) <= MAX_EDIT_HISTORY_ROWS
