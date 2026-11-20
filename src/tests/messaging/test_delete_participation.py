"""Regression tests: message deletes re-check conversation participation.

``delete_message`` and ``delete_messages_bulk`` authorised the message author
on authorship alone. A user who authored a message but was later removed from
the conversation (kick/ban/group leave) could keep deleting -- including hard
deleting -- messages in a conversation they can no longer view. Every sibling
write path (edit_message / update_message_metadata / pin / forward) re-checks
membership at write time; the author branch of delete failed open.

The non-participant path mirrors edit_message: a 404-equivalent
``MessageNotFoundError`` so message existence cannot be probed. Legitimate
authors who are still participants keep full delete access.

Run with: pytest src/tests/messaging/test_delete_participation.py -q
"""

import pytest

from src.core.messaging.exceptions import MessageNotFoundError


@pytest.mark.messaging
class TestDeleteParticipation:
    def test_removed_author_cannot_delete_own_message(
        self, db, messaging_manager, group_conversation
    ):
        group, owner, member, _member2, _messaging = group_conversation

        msg = messaging_manager.send_message(member.id, group.id, "my message")
        assert msg is not None

        # The group owner removes the author after the message was posted.
        assert messaging_manager.remove_participant(owner.id, group.id, member.id)

        # Authorship is not a standing write grant. Mirror edit_message's
        # oracle: a non-participant sees "not found" and cannot probe existence.
        with pytest.raises(MessageNotFoundError):
            messaging_manager.delete_message(member.id, msg.id)

        # The failed delete must not have touched the stored message.
        assert messaging_manager.get_message(owner.id, msg.id) is not None

    def test_removed_author_cannot_hard_delete_own_message(
        self, db, messaging_manager, group_conversation
    ):
        group, owner, member, _member2, _messaging = group_conversation

        msg = messaging_manager.send_message(member.id, group.id, "persist me")
        assert messaging_manager.remove_participant(owner.id, group.id, member.id)

        with pytest.raises(MessageNotFoundError):
            messaging_manager.delete_message(member.id, msg.id, hard_delete=True)

        # Hard delete is the destructive path; the row must still be present.
        assert messaging_manager.get_message(owner.id, msg.id) is not None

    def test_removed_author_cannot_bulk_delete_own_message(
        self, db, messaging_manager, group_conversation
    ):
        group, owner, member, _member2, _messaging = group_conversation

        msg = messaging_manager.send_message(member.id, group.id, "bulk target")
        assert messaging_manager.remove_participant(owner.id, group.id, member.id)

        deleted = messaging_manager.delete_messages_bulk(member.id, group.id, [msg.id])
        assert deleted == []
        assert messaging_manager.get_message(owner.id, msg.id) is not None

    def test_current_author_can_still_delete_own_message(
        self, db, messaging_manager, group_conversation
    ):
        """The new gate must not weaken the legitimate author path."""
        group, _owner, member, _member2, _messaging = group_conversation

        msg = messaging_manager.send_message(member.id, group.id, "my message")
        assert messaging_manager.delete_message(member.id, msg.id) is True
