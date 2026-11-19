"""Regression tests for DM privacy checks that must fail closed.

``create_dm`` used to skip both the block check and the friends-only check
whenever the optional relationships module was unavailable. A partial outage
therefore reopened DMs to a blocked pair and let strangers message
friends-only recipients.

Run with: pytest src/tests/messaging/test_dm_privacy_fail_closed.py -q
"""

import pytest

import src.core.messaging.services.conversation as conversation_service
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
class TestCreateDmBlockFailClosed:
    def test_block_is_enforced_when_relationships_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        user1, user2 = two_users
        wired_relationships.block_user(user2.id, user1.id)

        # Simulate the optional module failing to initialise. The block row is
        # still authoritative in the database.
        monkeypatch.setattr(
            conversation_service, "_get_relationships_module", lambda: None
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)

    def test_friends_only_denies_when_relationships_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        user1, user2 = two_users
        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(user2.id, allow_dms_from="friends")

        monkeypatch.setattr(
            conversation_service, "_get_relationships_module", lambda: None
        )

        # Cannot prove friendship without the relationship source: deny.
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)

    def test_friends_only_still_allows_a_real_friend(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        """The fail-closed gate must not weaken the legitimate friend path."""
        user1, user2 = two_users
        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(user2.id, allow_dms_from="friends")

        request = wired_relationships.send_friend_request(user1.id, user2.id)
        wired_relationships.accept_friend_request(user2.id, request.id)

        dm = messaging_manager.create_dm(user1.id, user2.id)
        assert dm is not None
