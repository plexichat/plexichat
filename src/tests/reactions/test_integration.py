"""
Tests for integration with messaging and servers modules.
"""

import pytest
from src.core.reactions import MessageNotFoundError


class TestMessagingIntegration:
    """Tests for integration with messaging module."""

    def test_react_to_dm_message(self, users_with_dm):
        """Test reacting to DM message."""
        user1, user2, dm, msg, reactions = users_with_dm

        reaction = reactions.add_reaction(user1.id, msg.id, "dm_react")

        assert reaction is not None
        assert reaction.message_id == msg.id

    def test_react_to_group_message(self, group_with_message):
        """Test reacting to group message."""
        owner, member1, member2, group, msg, messaging, reactions = group_with_message

        r1 = reactions.add_reaction(owner.id, msg.id, "group_react")
        r2 = reactions.add_reaction(member1.id, msg.id, "group_react")
        r3 = reactions.add_reaction(member2.id, msg.id, "group_react")

        msg_reactions = reactions.get_reactions(owner.id, msg.id)
        group_react = next((r for r in msg_reactions.reactions if r.emoji == "group_react"), None)

        assert group_react is not None
        assert group_react.count == 3

    def test_react_to_deleted_message_fails(self, db_and_modules):
        """Test cannot react to deleted message."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"del1_{unique_id}",
            email=f"del1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"del2_{unique_id}",
            email=f"del2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "To be deleted")

        messaging.delete_message(user1.id, msg.id)

        with pytest.raises(MessageNotFoundError):
            reactions.add_reaction(user1.id, msg.id, "deleted")

    def test_reactions_persist_after_message_edit(self, fresh_users_with_dm):
        """Test reactions persist after message is edited."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "persist")

        from src.core import messaging
        messaging.edit_message(user1.id, msg.id, "Edited content")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)
        persist = next((r for r in msg_reactions.reactions if r.emoji == "persist"), None)

        assert persist is not None
        assert persist.count == 1


class TestServerIntegration:
    """Tests for integration with servers module."""

    def test_react_to_channel_message(self, users_with_server):
        """Test reacting to server channel message."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reaction = reactions.add_reaction(owner.id, msg.id, "channel_react")

        assert reaction is not None

    def test_member_can_react_in_channel(self, users_with_server):
        """Test server member can react in channel."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reaction = reactions.add_reaction(member.id, msg.id, "member_channel")

        assert reaction is not None

    def test_non_member_cannot_react(self, db_and_modules):
        """Test non-server member cannot react to channel message."""
        db, auth, messaging, servers_mod, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"srv_owner_{unique_id}",
            email=f"srv_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        outsider = auth.register(
            username=f"outsider_{unique_id}",
            email=f"outsider_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers_mod.create_server(owner.id, f"Private Server {unique_id}")
        channel = servers_mod.get_channels(owner.id, server.id)[0]
        msg = servers_mod.send_channel_message(owner.id, channel.id, "Private message")

        with pytest.raises(MessageNotFoundError):
            reactions.add_reaction(outsider.id, msg.id, "outsider")

    def test_custom_emoji_server_validation(self, users_with_server):
        """Test custom emoji must belong to message's server."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "server_emoji")
        custom_str = f"<:server_emoji:{emoji.id}>"

        reaction = reactions.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.is_custom is True
        assert reaction.custom_emoji_id == emoji.id


class TestRelationshipsIntegration:
    """Tests for integration with relationships module."""

    def test_blocked_user_reactions_hidden(self, db_and_modules):
        """Test blocked user's reactions are hidden from blocker."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"block1_{unique_id}",
            email=f"block1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"block2_{unique_id}",
            email=f"block2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"block3_{unique_id}",
            email=f"block3_{unique_id}@example.com",
            password="TestPass123!"
        )

        group = messaging.create_group(user1.id, f"Block Test {unique_id}", [user2.id, user3.id])
        msg = messaging.send_message(user1.id, group.id, "Block test message")

        reactions.add_reaction(user1.id, msg.id, "block_test")
        reactions.add_reaction(user2.id, msg.id, "block_test")
        reactions.add_reaction(user3.id, msg.id, "block_test")

        relationships.block_user(user1.id, user2.id)

        msg_reactions = reactions.get_reactions(user1.id, msg.id)
        block_test = next((r for r in msg_reactions.reactions if r.emoji == "block_test"), None)

        assert block_test is not None
        assert block_test.count == 2

    def test_blocked_by_user_reactions_hidden(self, db_and_modules):
        """Test reactions from user who blocked you are hidden."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"blockedby1_{unique_id}",
            email=f"blockedby1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"blockedby2_{unique_id}",
            email=f"blockedby2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"blockedby3_{unique_id}",
            email=f"blockedby3_{unique_id}@example.com",
            password="TestPass123!"
        )

        group = messaging.create_group(user1.id, f"Blocked By Test {unique_id}", [user2.id, user3.id])
        msg = messaging.send_message(user1.id, group.id, "Blocked by test")

        reactions.add_reaction(user1.id, msg.id, "blockedby_test")
        reactions.add_reaction(user2.id, msg.id, "blockedby_test")
        reactions.add_reaction(user3.id, msg.id, "blockedby_test")

        relationships.block_user(user2.id, user1.id)

        msg_reactions = reactions.get_reactions(user1.id, msg.id)
        blockedby_test = next((r for r in msg_reactions.reactions if r.emoji == "blockedby_test"), None)

        assert blockedby_test is not None
        assert blockedby_test.count == 2

    def test_blocked_users_hidden_from_user_list(self, db_and_modules):
        """Test blocked users are hidden from reaction user list."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"list1_{unique_id}",
            email=f"list1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"list2_{unique_id}",
            email=f"list2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"list3_{unique_id}",
            email=f"list3_{unique_id}@example.com",
            password="TestPass123!"
        )

        group = messaging.create_group(user1.id, f"List Test {unique_id}", [user2.id, user3.id])
        msg = messaging.send_message(user1.id, group.id, "List test")

        reactions.add_reaction(user1.id, msg.id, "list_test")
        reactions.add_reaction(user2.id, msg.id, "list_test")
        reactions.add_reaction(user3.id, msg.id, "list_test")

        relationships.block_user(user1.id, user2.id)

        users = reactions.get_reaction_users(user1.id, msg.id, "list_test")
        user_ids = [u.user_id for u in users]

        assert user2.id not in user_ids
        assert user1.id in user_ids
        assert user3.id in user_ids

    def test_no_block_shows_all_reactions(self, fresh_users_with_dm):
        """Test all reactions visible when no blocks exist."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "no_block")
        reactions.add_reaction(user2.id, msg.id, "no_block")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)
        no_block = next((r for r in msg_reactions.reactions if r.emoji == "no_block"), None)

        assert no_block is not None
        assert no_block.count == 2
