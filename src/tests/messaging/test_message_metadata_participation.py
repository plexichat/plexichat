"""Regression tests: message metadata updates re-check conversation
participation at write time.

``update_message_metadata`` authorised on authorship alone. A user who had
authored a message but was later removed from the conversation (kick/ban/
group leave) could keep mutating that message's metadata in a conversation
they can no longer view. Every sibling write path (edit/pin/forward/...)
re-checks membership; this one failed open. The non-participant path mirrors
``edit_message``: a 404-equivalent ``MessageNotFoundError`` so message
existence cannot be probed.

Run with: pytest src/tests/messaging/test_message_metadata_participation.py -q
"""

import pytest

from src.core.messaging.exceptions import MessageNotFoundError


@pytest.mark.messaging
class TestMessageMetadataParticipation:
    def test_removed_author_cannot_update_own_message_metadata(
        self, db, messaging_manager, group_conversation
    ):
        group, owner, member, _member2, _messaging = group_conversation

        # The member authors a message while still in the group.
        msg = messaging_manager.send_message(member.id, group.id, "my message")
        assert msg is not None

        # Owner removes the author from the conversation.
        assert messaging_manager.remove_participant(owner.id, group.id, member.id)

        # Authorship alone must not grant a post-removal write. The oracle
        # matches edit_message: a non-participant sees "not found".
        with pytest.raises(MessageNotFoundError):
            messaging_manager.update_message_metadata(
                member.id, msg.id, {"injected": True}
            )

    def test_current_participant_can_still_update_own_metadata(
        self, db, messaging_manager, group_conversation
    ):
        """The new gate must not weaken the legitimate author path."""
        group, owner, _member, _member2, _messaging = group_conversation

        msg = messaging_manager.send_message(owner.id, group.id, "my message")
        updated = messaging_manager.update_message_metadata(
            owner.id, msg.id, {"poll_id": 123}
        )
        assert updated is not None
