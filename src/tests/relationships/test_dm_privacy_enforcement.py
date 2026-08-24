"""
Regression tests for DM privacy enforcement (Agent 22 W2-A fixes).

Covers:
- H1: blocked users cannot create/open DMs (create_dm block check)
- H4: allow_dms_from="friends" only lets friends open DMs
- H2: blocked parties cannot send messages into pre-existing DM channels
- H3: DM event routing excludes blocked participants
"""

import pytest

from src.core.events.models import Event
from src.core.events.router import EventRouter
from src.core.events.types import EventType
from src.core.messaging.exceptions import ConversationAccessDeniedError


@pytest.fixture
def wired_relationships(db):
    """Point the global relationships module at the test database."""
    from src.core import relationships

    previous_manager = relationships._manager
    previous_setup = relationships._setup_complete
    relationships.setup(db)
    yield relationships._manager
    relationships._manager = previous_manager
    relationships._setup_complete = previous_setup


class TestCreateDmBlockEnforcement:
    """H1: either direction of a block prevents DM creation."""

    def test_blocked_by_recipient_cannot_create_dm(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        wired_relationships.block_user(user2.id, user1.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)

    def test_blocker_cannot_create_dm_either(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        wired_relationships.block_user(user1.id, user2.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)


class TestCreateDmFriendsOnly:
    """H4: allow_dms_from='friends' requires an existing friendship."""

    def test_non_friend_cannot_dm_friends_only_recipient(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(user2.id, allow_dms_from="friends")

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)

    def test_friend_can_dm_friends_only_recipient(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        settings_svc = messaging_manager._user_settings_svc
        settings_svc.update_message_settings(user2.id, allow_dms_from="friends")

        request = wired_relationships.send_friend_request(user1.id, user2.id)
        wired_relationships.accept_friend_request(user2.id, request.id)

        dm = messaging_manager.create_dm(user1.id, user2.id)
        assert dm is not None


class TestDmSendBlockEnforcement:
    """H2: legacy DM channels become unwritable after a block."""

    def test_blocked_party_cannot_send_into_existing_dm(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        from src.core.messaging.models import MessageType

        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Works before the block...
        msg = messaging_manager.send_message(
            user_id=user1.id,
            conversation_id=dm.id,
            content="hello",
            message_type=MessageType.TEXT,
        )
        assert msg is not None

        wired_relationships.block_user(user2.id, user1.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content="still there?",
                message_type=MessageType.TEXT,
            )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(
                user_id=user2.id,
                conversation_id=dm.id,
                content="or me",
                message_type=MessageType.TEXT,
            )


class StubMessaging:
    def __init__(self, participant_ids):
        self._participant_ids = participant_ids

    def get_participant_ids(self, conversation_id):
        return list(self._participant_ids)


class TestDmEventRoutingBlockFilter:
    """H3: DM events never fan out to blocked participants."""

    def _event(self, acting_user_id):
        return Event(
            event_type=EventType.MESSAGE_CREATE,
            data={},
            channel_id=1000,
            user_id=acting_user_id,
        )

    def test_no_blocks_all_participants_receive(self, wired_relationships, two_users):
        user1, user2 = two_users
        router = EventRouter(
            relationships_module=wired_relationships,
            messaging_module=StubMessaging([user1.id, user2.id]),
        )
        recipients = router.get_recipients(self._event(user1.id))
        assert set(recipients) == {user1.id, user2.id}

    def test_blocked_participant_excluded_from_dm_events(
        self, wired_relationships, two_users
    ):
        user1, user2 = two_users
        wired_relationships.block_user(user1.id, user2.id)

        router = EventRouter(
            relationships_module=wired_relationships,
            messaging_module=StubMessaging([user1.id, user2.id]),
        )
        recipients = router.get_recipients(self._event(user1.id))
        assert user2.id not in recipients

    def test_reverse_blocked_actor_excluded_from_other_side(
        self, wired_relationships, two_users
    ):
        user1, user2 = two_users
        wired_relationships.block_user(user2.id, user1.id)

        router = EventRouter(
            relationships_module=wired_relationships,
            messaging_module=StubMessaging([user1.id, user2.id]),
        )
        recipients = router.get_recipients(self._event(user2.id))
        assert user1.id not in recipients
