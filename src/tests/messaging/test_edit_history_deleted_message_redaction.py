"""SECURITY: edit history of a DELETED message must not be readable.

A soft delete blanks ``msg_messages.content`` (the tombstone) but the prior
versions kept in ``msg_edit_history`` survive. ``record_edit`` already refuses
to operate on a deleted message; the read side must mirror that fail-closed
behaviour, otherwise deleting a message to redact sensitive text does not
actually remove it -- the old content is still returned by
``get_edit_history`` to any channel viewer.

Run with: pytest -n 0 -q src/tests/messaging/test_edit_history_deleted_message_redaction.py
"""

import pytest

from src.core.messaging.exceptions import MessageNotFoundError
from src.core.messaging.services.edit_history import EditHistoryService

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

SECRET = "SECRET-PRIOR-CONTENT"


class _WiredDMServers:
    """A wired servers store whose reverse lookup finds no backing channel
    (a genuine DM), so the channel-view gate permits the read."""

    def get_channel_by_conversation(self, conversation_id):
        return None

    def get_channel(self, channel_id, user_id):
        raise AssertionError("DM needs no channel view check")


@pytest.mark.messaging
class TestDeletedMessageEditHistoryRedaction:
    def _seed_history(self, db, message_id, editor_id):
        db.execute(
            """INSERT INTO msg_edit_history
               (id, message_id, editor_id, old_content, old_content_encrypted,
                edit_timestamp, version_number)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (999_000_001, message_id, editor_id, SECRET, 100, 1),
        )

    def _svc(self, db, manager):
        return EditHistoryService(
            db,
            manager._participant_svc,
            encryption_enabled=False,
            servers_mod=_WiredDMServers(),
        )

    def test_get_edit_history_denied_for_deleted_message(
        self, db, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "current-content")
        self._seed_history(db, msg.id, user1.id)

        # The soft delete blanks current content but leaves the history row.
        messaging_manager.delete_message(user1.id, msg.id)
        row = db.fetch_one(
            "SELECT content, deleted FROM msg_messages WHERE id = ?", (msg.id,)
        )
        assert row["deleted"] == 1 and row["content"] == ""
        assert (
            db.fetch_one(
                "SELECT old_content FROM msg_edit_history WHERE message_id = ?",
                (msg.id,),
            )["old_content"]
            == SECRET
        )

        with pytest.raises(MessageNotFoundError):
            self._svc(db, messaging_manager).get_edit_history(user1.id, msg.id)

    def test_get_edit_count_denied_for_deleted_message(
        self, db, messaging_manager, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "current-content")
        self._seed_history(db, msg.id, user1.id)

        messaging_manager.delete_message(user1.id, msg.id)

        with pytest.raises(MessageNotFoundError):
            self._svc(db, messaging_manager).get_edit_count(user1.id, msg.id)
