"""Regression: the batch participant-add path must not bypass group-DM privacy.

``create_group`` vets every participant pair and ``add_participant`` vets the
newcomer against every current member. ``add_participant_to_conversations``
(the batch add used for server joins) did neither: it inserted the user into
whatever conversation ids it was handed, with no DM-privacy gate at all. Since
the module-level function is public, a group DM id passed to it re-admits a
block / ``none`` / friends-only-excluded user that both guarded paths reject.

The rejection tests are non-vacuous: they FAIL on the unfixed batch-add path.
The positive controls keep the guard from overreaching onto server channel
conversations, which are GROUP rows backed by a ``server_id`` and are not DMs.
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.models import ParticipantRole


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
class TestGroupDMBatchAddPrivacy:
    def test_batch_add_rejects_user_blocked_by_existing_member(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        # Owner does not block the newcomer, so an owner-only check would pass.
        wired_relationships.block_user(member.id, newcomer.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])

    def test_batch_add_rejects_user_who_blocked_existing_member(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        wired_relationships.block_user(newcomer.id, member.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])

    def test_batch_add_rejects_none_dms_existing_member(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        messaging_manager._user_settings_svc.update_message_settings(
            member.id, allow_dms_from="none"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])

    def test_batch_add_rejects_friends_only_existing_member_vs_non_friend(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        messaging_manager._user_settings_svc.update_message_settings(
            member.id, allow_dms_from="friends"
        )
        # newcomer is not a friend of member, so the pair must be rejected.

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])

    def test_batch_add_rejects_newcomer_none_dms_setting(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, newcomer = two_users
        group = messaging_manager.create_group(owner.id, "Group")
        messaging_manager._user_settings_svc.update_message_settings(
            newcomer.id, allow_dms_from="none"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])

    def test_batch_add_allows_unrestricted_group(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        """The guard must not block ordinary batch adds into group DMs."""
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])

        messaging_manager.add_participant_to_conversations(newcomer.id, [group.id])
        assert newcomer.id in messaging_manager.get_participant_ids(group.id)

    def test_batch_add_still_allows_server_channel_conversation(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        """Server channels are GROUP rows but are backed by a server_id.

        Their membership comes from server membership, not DM privacy, so the
        new group-DM guard must not reject a blocked pair there.
        """
        owner, newcomer = two_users
        conv = messaging_manager.create_server_channel_conversation(1000, 2000)
        messaging_manager._participant_svc.add_participant(
            conv.id, owner.id, ParticipantRole.OWNER
        )
        wired_relationships.block_user(owner.id, newcomer.id)

        messaging_manager.add_participant_to_conversations(newcomer.id, [conv.id])
        assert newcomer.id in messaging_manager.get_participant_ids(conv.id)
