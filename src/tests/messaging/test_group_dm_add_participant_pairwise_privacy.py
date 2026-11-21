"""Regression: adding a participant to a group DM must honour the DM privacy
settings of every existing member, not only those of the newcomer.

``create_group`` was fixed to check block/DM-settings across all participant
pairs in both directions. ``add_participant`` still only called
``enforce_group_dm_privacy(existing_id, participant_id)``, which checks the
NEWCOMER's settings but never the existing members'. An owner/admin could
therefore insert a user into a group whose existing member opted out of DMs
(``allow_dms_from='none'`` or ``'friends'`` with a non-friend), even though
creating that group with the same pair is rejected.

Each rejection test is non-vacuous: it fails on the one-directional check.
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
class TestAddParticipantHonoursExistingMemberDMSettings:
    def test_add_participant_rejects_existing_member_with_none_dms(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        # The group is created while all settings are permissive; the member
        # then opts out of DMs. Adding the newcomer pairs them together.
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        messaging_manager._user_settings_svc.update_message_settings(
            member.id, allow_dms_from="none"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant(owner.id, group.id, newcomer.id)

    def test_add_participant_rejects_friends_only_existing_member_vs_non_friend(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])
        messaging_manager._user_settings_svc.update_message_settings(
            member.id, allow_dms_from="friends"
        )
        # newcomer is not a friend of member, so the pair must be rejected.

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant(owner.id, group.id, newcomer.id)

    def test_add_participant_allows_unrestricted_existing_member(
        self, db, messaging_manager, wired_relationships, three_users
    ):
        owner, member, newcomer = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id])

        participant = messaging_manager.add_participant(owner.id, group.id, newcomer.id)
        assert participant is not None
