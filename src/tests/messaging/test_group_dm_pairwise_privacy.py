"""Regression: group creation must not admit a blocked/privacy-restricted pair.

``create_group`` used to check only owner<->participant. Two supplied
participants who block each other (or whose DM settings exclude each other)
were therefore admitted together, even though adding the same user with
``add_participant`` after creation is rejected. Group sends are outside the
DM-only send gate, so the creation-time gate is the only defence here.

Each test is non-vacuous: it fails on code that only checks owner<->participant.
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError


@pytest.fixture
def wired_relationships(db):
    """Expose a real relationships manager, then restore global state."""
    from src.core import relationships

    previous_manager = relationships._manager
    previous_setup = relationships._setup_complete
    relationships.setup(db)
    try:
        yield relationships
    finally:
        relationships._manager = previous_manager
        relationships._setup_complete = previous_setup


@pytest.mark.messaging
class TestGroupDMPairwisePrivacy:
    def test_create_group_rejects_two_participants_who_block_each_other(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, alice, bob = three_users
        # Neither party blocks the owner, so an owner-only gate passes both.
        wired_relationships.block_user(alice.id, bob.id)
        wired_relationships.block_user(bob.id, alice.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [alice.id, bob.id])

    def test_create_group_rejects_block_when_only_one_side_blocks(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, blocker, victim = three_users
        wired_relationships.block_user(blocker.id, victim.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [blocker.id, victim.id])

    def test_create_group_rejects_none_dms_participant_pair(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, open_member, private_member = three_users
        # private_member opts out of DMs; the owner is allowed through the
        # owner-only gate in the buggy version, which never checks this pair.
        messaging_manager._user_settings_svc.update_message_settings(
            private_member.id, allow_dms_from="none"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(
                owner.id, "Group", [open_member.id, private_member.id]
            )

    def test_create_group_rejects_friends_only_participant_vs_non_friend(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, friend, stranger = three_users
        # Make the owner a friend so the owner-only gate passes, while the
        # other participant is not a friend and must still be rejected.
        request = wired_relationships.send_friend_request(owner.id, friend.id)
        wired_relationships.accept_friend_request(friend.id, request.id)
        messaging_manager._user_settings_svc.update_message_settings(
            friend.id, allow_dms_from="friends"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [friend.id, stranger.id])

    def test_create_group_allows_unrestricted_participant_pair(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, alice, bob = three_users
        conv = messaging_manager.create_group(owner.id, "Group", [alice.id, bob.id])
        assert conv is not None
