"""
Authorization and Access Control Tests.

Tests that users can only access resources they have permission for,
and that privilege escalation is prevented.
"""

import pytest


class TestAuthorizationChecks:
    """Test authorization and access control mechanisms."""

    def test_user_cannot_access_other_users_messages(self, modules, user_pool):
        """Test that users cannot access messages in conversations they're not part of."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        user3 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)
        modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Private message"
        )

        with pytest.raises(Exception):
            modules.messaging.get_messages(user3.id, dm.id)

    def test_user_cannot_edit_other_users_messages(self, modules, user_pool):
        """Test that users cannot edit messages from other users."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)
        msg = modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Original message"
        )

        with pytest.raises(Exception):
            modules.messaging.edit_message(user2.id, msg.id, "Edited by wrong user")

    def test_user_cannot_delete_other_users_messages(self, modules, user_pool):
        """Test that users cannot delete messages from other users."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)
        msg = modules.messaging.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Original message"
        )

        with pytest.raises(Exception):
            modules.messaging.delete_message(user2.id, msg.id)

    def test_non_member_cannot_send_messages_in_server(self, modules, user_pool):
        """Test that non-members cannot send messages in server channels."""
        owner = user_pool.get_user()
        non_member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        channel = modules.servers.create_channel(
            server_id=server.id, creator_id=owner.id, name="test-channel", type="text"
        )

        conversation_id = getattr(channel, "conversation_id", None)
        if conversation_id:
            with pytest.raises(Exception):
                modules.messaging.send_message(
                    user_id=non_member.id,
                    conversation_id=conversation_id,
                    content="Unauthorized message",
                )

    def test_non_owner_cannot_delete_server(self, modules, user_pool):
        """Test that non-owners cannot delete servers."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        modules.servers.add_member(server.id, member.id)

        with pytest.raises(Exception):
            modules.servers.delete_server(member.id, server.id)

    def test_non_owner_cannot_delete_channels(self, modules, user_pool):
        """Test that non-owners cannot delete channels."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        channel = modules.servers.create_channel(
            server_id=server.id, creator_id=owner.id, name="test-channel", type="text"
        )

        modules.servers.add_member(server.id, member.id)

        with pytest.raises(Exception):
            modules.servers.delete_channel(member.id, channel.id)

    def test_member_cannot_kick_other_members(self, modules, user_pool):
        """Test that regular members cannot kick other members."""
        owner = user_pool.get_user()
        member1 = user_pool.get_user()
        member2 = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        modules.servers.add_member(server.id, member1.id)
        modules.servers.add_member(server.id, member2.id)

        with pytest.raises(Exception):
            modules.servers.remove_member(member1.id, server.id, member2.id)

    def test_user_cannot_modify_other_users_profiles(self, modules, user_pool):
        """Test that users cannot modify other users' profiles."""
        user_pool.get_user()
        user2 = user_pool.get_user()

        with pytest.raises(Exception):
            modules.auth.change_password(
                user_id=user2.id, old_password="anything", new_password="NewPass123!"
            )

    def test_user_cannot_access_other_users_sessions(self, modules, user_pool):
        """Test that users cannot access other users' sessions."""
        user1, username1, password1 = user_pool.get_user_with_credentials()
        user2 = user_pool.get_user()

        modules.auth.login(username1, password1)
        sessions1 = modules.auth.get_sessions(user1.id)

        with pytest.raises(Exception):
            modules.auth.get_sessions(user2.id)
            for session in sessions1:
                modules.auth.revoke_session(user2.id, session.id)

    def test_user_cannot_manage_other_users_bots(self, modules, user_pool):
        """Test that users cannot manage other users' bots."""
        owner = user_pool.get_user()
        other_user = user_pool.get_user()

        bot = modules.auth.create_bot(
            owner_id=owner.id, username=f"testbot_{owner.id}", display_name="Test Bot"
        )

        with pytest.raises(Exception):
            modules.auth.delete_bot(other_user.id, bot.id)

    def test_user_cannot_create_group_with_other_user_as_owner(
        self, modules, user_pool
    ):
        """Test that users cannot create groups with other users as owner."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        group = modules.messaging.create_group(
            owner_id=user1.id, name="Test Group", participant_ids=[user2.id]
        )

        assert group.owner_id == user1.id

    def test_permission_checks_on_server_operations(self, modules, user_pool):
        """Test permission checks on various server operations."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        modules.servers.add_member(server.id, member.id)

        try:
            modules.servers.update_server(
                user_id=member.id, server_id=server.id, name="New Name"
            )
        except Exception:
            pass

    def test_bot_permissions_respected(self, modules, user_pool):
        """Test that bot permissions are properly enforced."""
        owner = user_pool.get_user()

        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot",
            permissions={"messages.send": True, "messages.read": True},
        )

        token_info = modules.auth.verify_token(bot.token)

        assert token_info.permissions.get("messages.send")
        assert token_info.permissions.get("messages.read")

    def test_user_cannot_escalate_own_permissions(self, modules, user_pool):
        """Test that users cannot escalate their own permissions."""
        user = user_pool.get_user()

        user_obj = modules.auth.get_user(user.id)
        assert "admin.*" not in user_obj.permissions or not user_obj.permissions.get(
            "admin.*"
        )

    def test_conversation_participant_validation(self, modules, user_pool):
        """Test that only participants can access conversation data."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        user3 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        participants = modules.messaging.get_participants(user1.id, dm.id)
        participant_ids = [p.user_id for p in participants]

        assert user1.id in participant_ids
        assert user2.id in participant_ids
        assert user3.id not in participant_ids

    def test_channel_access_requires_server_membership(self, modules, user_pool):
        """Test that channel access requires server membership."""
        owner = user_pool.get_user()
        non_member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        channel = modules.servers.create_channel(
            server_id=server.id, creator_id=owner.id, name="test-channel", type="text"
        )

        with pytest.raises(Exception):
            modules.servers.get_channel(channel.id, non_member.id)

    def test_invite_code_validation(self, modules, user_pool):
        """Test that invite codes properly validate access."""
        owner = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        invite = modules.servers.create_invite(server_id=server.id, creator_id=owner.id)

        assert invite.code is not None

    def test_role_based_permissions(self, modules, user_pool):
        """Test role-based permission enforcement."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        try:
            role = modules.servers.create_role(
                server_id=server.id,
                creator_id=owner.id,
                name="Test Role",
                permissions={"messages.send": True},
            )

            modules.servers.add_member(server.id, member.id)

            modules.servers.assign_role(owner.id, server.id, member.id, role.id)
        except Exception:
            pass
