"""
Tests for @role mention parsing and notifications.
"""

from src.core.notifications import MentionType


class TestRoleMentionParsing:
    """Tests for parsing @role mentions."""

    def test_parse_single_role_mention(self, db_and_modules):
        """Test parsing a single role mention."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey <@&123456789>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.ROLE
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<@&123456789>"

    def test_parse_multiple_role_mentions(self, db_and_modules):
        """Test parsing multiple role mentions."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Attention <@&111> and <@&222>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_mixed_user_and_role_mentions(self, db_and_modules):
        """Test parsing both user and role mentions."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey <@111> and <@&222>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].mention_type == MentionType.USER
        assert mentions[1].mention_type == MentionType.ROLE


class TestRoleMentionValidation:
    """Tests for validating @role mentions."""

    def test_validate_existing_role(self, users_with_role):
        """Test validating mention of existing role."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        content = f"Hey <@&{role.id}>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server.id)

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_validate_nonexistent_role(self, users_with_server):
        """Test validating mention of nonexistent role."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        content = "<@&999999999999>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server.id)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "not found" in validated[0].error.lower()

    def test_validate_role_wrong_server(self, db_and_modules):
        """Test validating role mention from different server."""
        db, auth, messaging, servers_mod, relationships, presence, notifications = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner = auth.register(
            username=f"owner_{unique_id}",
            email=f"owner_{unique_id}@example.com",
            password="TestPass123!"
        )

        server1 = servers_mod.create_server(owner.id, f"Server1 {unique_id}")
        server2 = servers_mod.create_server(owner.id, f"Server2 {unique_id}")

        role = servers_mod.create_role(
            user_id=owner.id,
            server_id=server1.id,
            name="TestRole",
            permissions={},
            mentionable=True
        )

        content = f"<@&{role.id}>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server2.id)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "not in this server" in validated[0].error.lower()


class TestRoleMentionNotifications:
    """Tests for creating notifications from @role mentions."""

    def test_create_notification_for_role_members(self, users_with_role):
        """Test notifications are created for role members."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"Attention <@&{role.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        assert len(notifs) >= 1
        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        for notif in notifs:
            assert notif.mention_type == MentionType.ROLE

    def test_no_notification_for_sender_in_role(self, users_with_role):
        """Test sender doesn't get notification even if in role."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        servers.assign_role(owner.id, server.id, owner.id, role.id)

        group = messaging.create_group(owner.id, "Server Group", [member1.id])

        content = f"<@&{role.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert owner.id not in notified_users

    def test_role_mention_with_user_mention(self, users_with_role):
        """Test user mention takes priority over role mention."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}> and <@&{role.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        member1_notifs = [n for n in notifs if n.user_id == member1.id]
        assert len(member1_notifs) == 1
        assert member1_notifs[0].mention_type == MentionType.USER
