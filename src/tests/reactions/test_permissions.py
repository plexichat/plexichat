"""
Tests for permission checks in server channels.
"""

import pytest
from src.core.reactions import PermissionDeniedError
from src.core.servers import ChannelType


class TestServerPermissions:
    """Tests for server channel permission checks."""

    def test_owner_can_add_reaction(self, users_with_server):
        """Test server owner can add reactions."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reaction = reactions.add_reaction(owner.id, msg.id, "owner_react")

        assert reaction is not None

    def test_member_can_add_reaction(self, users_with_server):
        """Test member with permission can add reactions."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reaction = reactions.add_reaction(member.id, msg.id, "member_react")

        assert reaction is not None

    def test_member_without_permission_cannot_add(self, db_and_modules):
        """Test member without add_reactions permission cannot add."""
        db, auth, messaging, servers_mod, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"perm_owner_{unique_id}",
            email=f"perm_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        member = auth.register(
            username=f"perm_member_{unique_id}",
            email=f"perm_member_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers_mod.create_server(owner.id, f"Perm Server {unique_id}")
        servers_mod.add_member(server.id, member.id)

        channel = servers_mod.get_channels(owner.id, server.id)[0]

        everyone_role = None
        roles = servers_mod.get_roles(owner.id, server.id)
        for role in roles:
            if role.name == "@everyone":
                everyone_role = role
                break

        if everyone_role:
            servers_mod.update_role(
                owner.id,
                everyone_role.id,
                permissions={"messages.add_reactions": False}
            )

        msg = servers_mod.send_channel_message(owner.id, channel.id, "Test message")

        with pytest.raises(PermissionDeniedError) as exc_info:
            reactions.add_reaction(member.id, msg.id, "denied")

        assert exc_info.value.permission == "messages.add_reactions"


class TestModeratorActions:
    """Tests for moderator-only actions."""

    def test_owner_can_remove_all_reactions(self, users_with_server):
        """Test server owner can remove all reactions."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reactions.add_reaction(owner.id, msg.id, "mod_test1")
        reactions.add_reaction(member.id, msg.id, "mod_test1")
        reactions.add_reaction(member.id, msg.id, "mod_test2")

        count = reactions.remove_all_reactions(owner.id, msg.id)

        assert count == 3

        msg_reactions = reactions.get_reactions(owner.id, msg.id)
        assert msg_reactions.total_count == 0

    def test_owner_can_remove_emoji_reactions(self, users_with_server):
        """Test server owner can remove all reactions of specific emoji."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reactions.add_reaction(owner.id, msg.id, "emoji_mod1")
        reactions.add_reaction(member.id, msg.id, "emoji_mod1")
        reactions.add_reaction(owner.id, msg.id, "emoji_mod2")

        count = reactions.remove_all_reactions_for_emoji(owner.id, msg.id, "emoji_mod1")

        assert count == 2

        msg_reactions = reactions.get_reactions(owner.id, msg.id)
        emojis = [r.emoji for r in msg_reactions.reactions]
        assert "emoji_mod1" not in emojis
        assert "emoji_mod2" in emojis

    def test_member_cannot_remove_all_reactions(self, users_with_server):
        """Test regular member cannot remove all reactions."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reactions.add_reaction(owner.id, msg.id, "no_mod")

        with pytest.raises(PermissionDeniedError):
            reactions.remove_all_reactions(member.id, msg.id)

    def test_member_cannot_remove_emoji_reactions(self, users_with_server):
        """Test regular member cannot remove all emoji reactions."""
        owner, member, server, channel, msg, servers, reactions = users_with_server

        reactions.add_reaction(owner.id, msg.id, "no_emoji_mod")

        with pytest.raises(PermissionDeniedError):
            reactions.remove_all_reactions_for_emoji(member.id, msg.id, "no_emoji_mod")

    def test_admin_can_remove_all_reactions(self, db_and_modules):
        """Test admin with messages.manage can remove all reactions."""
        db, auth, messaging, servers_mod, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"admin_owner_{unique_id}",
            email=f"admin_owner_{unique_id}@example.com",
            password="TestPass123!"
        )
        admin = auth.register(
            username=f"admin_user_{unique_id}",
            email=f"admin_user_{unique_id}@example.com",
            password="TestPass123!"
        )

        server = servers_mod.create_server(owner.id, f"Admin Server {unique_id}")
        servers_mod.add_member(server.id, admin.id)

        admin_role = servers_mod.create_role(
            owner.id,
            server.id,
            f"Admin_{unique_id}",
            permissions={"messages.manage": True}
        )
        servers_mod.assign_role(owner.id, server.id, admin.id, admin_role.id)

        channel = servers_mod.get_channels(owner.id, server.id)[0]
        msg = servers_mod.send_channel_message(owner.id, channel.id, "Admin test")

        reactions.add_reaction(owner.id, msg.id, "admin_remove")

        count = reactions.remove_all_reactions(admin.id, msg.id)

        assert count == 1


class TestGroupConversationPermissions:
    """Tests for group conversation moderator actions."""

    def test_group_owner_can_remove_all_reactions(self, group_with_message):
        """Test group owner can remove all reactions."""
        owner, member1, member2, group, msg, messaging, reactions = group_with_message

        reactions.add_reaction(member1.id, msg.id, "grp_mod1")
        reactions.add_reaction(member2.id, msg.id, "grp_mod1")

        count = reactions.remove_all_reactions(owner.id, msg.id)

        assert count == 2

    def test_group_member_cannot_remove_all_reactions(self, group_with_message):
        """Test group member cannot remove all reactions."""
        owner, member1, member2, group, msg, messaging, reactions = group_with_message

        reactions.add_reaction(owner.id, msg.id, "grp_no_mod")

        with pytest.raises(PermissionDeniedError):
            reactions.remove_all_reactions(member1.id, msg.id)

    def test_group_admin_can_remove_all_reactions(self, group_with_message):
        """Test group admin can remove all reactions."""
        owner, member1, member2, group, msg, messaging, reactions = group_with_message

        messaging.update_participant_role(
            owner.id,
            group.id,
            member1.id,
            messaging.ParticipantRole.ADMIN
        )

        reactions.add_reaction(member2.id, msg.id, "grp_admin_mod")

        count = reactions.remove_all_reactions(member1.id, msg.id)

        assert count == 1
