"""Tests for messaging DM functionality."""

from unittest.mock import patch


class TestDMCreateRaceRecovery:
    """M13: concurrent DM creation must not depend on exception-message
    sniffing ("UNIQUE"/"integrity") to recover the winner's conversation.

    When two users race to create the same DM, the loser's lookup INSERT
    fails. Recovery must re-query the lookup — regardless of how the
    driver phrases the error — and return the winner's conversation.
    """

    def test_lookup_race_recovers_without_message_sniffing(
        self, db, auth_manager, messaging_manager
    ):
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("m13u1", "m13u1@example.com", "TestPass123!")
            user2 = auth_manager.register("m13u2", "m13u2@example.com", "TestPass123!")

        # The winner's DM already exists.
        winner = messaging_manager.create_dm(user1.id, user2.id)
        assert winner is not None

        svc = messaging_manager._conversation_svc
        real_lookup = svc._repo.get_dm_lookup

        state = {"pre_check_done": False}

        def miss_once_then_find(u1, u2):
            # First read (create_dm's pre-check) misses so the code proceeds
            # to create; the recovery re-query finds the winner's row.
            if not state["pre_check_done"]:
                state["pre_check_done"] = True
                return None
            return real_lookup(u1, u2)

        def explode(*args, **kwargs):
            # Driver-agnostic failure text: no "unique"/"integrity" substrings.
            raise RuntimeError("storage exploded")

        with (
            patch.object(svc._repo, "get_dm_lookup", side_effect=miss_once_then_find),
            patch.object(svc._repo, "create_dm_lookup", side_effect=explode),
        ):
            recovered = messaging_manager.create_dm(user1.id, user2.id)

        assert recovered is not None
        assert recovered.id == winner.id


class TestDM:
    """Test direct message functionality."""

    def test_create_dm(self, db, auth_manager, messaging_manager):
        """Test creating a DM conversation."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM between users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        assert dm is not None
        assert dm.id is not None
        # DM conversations have conversation_type attribute
        assert dm.conversation_type.value == "dm"

    def test_send_dm_message(self, db, auth_manager, messaging_manager):
        """Test sending a message in DM."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Send message
        message = messaging_manager.send_message(user1.id, dm.id, "Hello, user2!")

        assert message is not None
        assert message.id is not None
        assert message.content == "Hello, user2!"
        assert message.author_id == user1.id
        assert message.conversation_id == dm.id

    def test_get_dm_messages(self, db, auth_manager, messaging_manager):
        """Test retrieving messages from DM."""
        from src.utils import encryption

        # Create two users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register("user1", "user1@example.com", "TestPass123!")
            user2 = auth_manager.register("user2", "user2@example.com", "TestPass123!")

        # Create DM
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Send multiple messages
        msg1 = messaging_manager.send_message(user1.id, dm.id, "First message")
        msg2 = messaging_manager.send_message(user2.id, dm.id, "Second message")
        msg3 = messaging_manager.send_message(user1.id, dm.id, "Third message")

        # Retrieve messages
        messages = messaging_manager.get_messages(user1.id, dm.id, limit=10)

        assert messages is not None
        assert len(messages) >= 3
        message_ids = [m.id for m in messages]
        assert msg1.id in message_ids
        assert msg2.id in message_ids
        assert msg3.id in message_ids
