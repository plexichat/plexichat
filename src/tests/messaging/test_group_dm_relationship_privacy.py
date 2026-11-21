"""Regression tests: group-DM participant creation/addition must not bypass
the relationship privacy controls that ``create_dm`` enforces.

A block (either direction), ``allow_dms_from='none'`` or a friends-only
recipient must not be silently draggable into a group conversation: group
sends are intentionally outside the DM-only send gate, so the creation-time
gate is the only place that can stop a blocked party from re-establishing
contact. These tests are non-vacuous -- they FAIL on the unfixed code, which
added every supplied participant without any relationship/DM-setting check.
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
class TestGroupDMRelationshipPrivacy:
    def test_create_group_rejects_participant_who_blocked_owner(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, victim = two_users
        wired_relationships.block_user(victim.id, owner.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [victim.id])

    def test_create_group_rejects_participant_owner_blocked(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, victim = two_users
        wired_relationships.block_user(owner.id, victim.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [victim.id])

    def test_create_group_rejects_none_dms_participant(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, victim = two_users
        messaging_manager._user_settings_svc.update_message_settings(
            victim.id, allow_dms_from="none"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [victim.id])

    def test_create_group_rejects_friends_only_non_friend(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, victim = two_users
        messaging_manager._user_settings_svc.update_message_settings(
            victim.id, allow_dms_from="friends"
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_group(owner.id, "Group", [victim.id])

    def test_add_participant_rejects_user_who_blocked_owner(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        owner, victim = two_users
        group = messaging_manager.create_group(owner.id, "Group")
        wired_relationships.block_user(victim.id, owner.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.add_participant(owner.id, group.id, victim.id)

    def test_legitimate_group_creation_still_works(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        """The gate must not block ordinary group creation."""
        owner, friend = two_users
        group = messaging_manager.create_group(owner.id, "Group", [friend.id])
        assert group is not None
