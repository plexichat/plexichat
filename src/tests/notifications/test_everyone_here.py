"""
Tests for @everyone and @here mentions with permissions.
"""

from src.core.notifications import MentionType


class TestEveryoneParsing:
    """Tests for parsing @everyone mentions."""

    def test_parse_everyone_mention(self, db_and_modules):
        """Test parsing @everyone mention."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey @everyone check this out"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.EVERYONE
        assert mentions[0].target_id is None
        assert mentions[0].raw_text == "@everyone"

    def test_parse_everyone_position(self, db_and_modules):
        """Test @everyone position is correct."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey @everyone"
        mentions = notifications.parse_mentions(content)

        assert mentions[0].start_pos == 4
        assert mentions[0].end_pos == 13


class TestHereParsing:
    """Tests for parsing @here mentions."""

    def test_parse_here_mention(self, db_and_modules):
        """Test parsing @here mention."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey @here check this out"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.HERE
        assert mentions[0].target_id is None
        assert mentions[0].raw_text == "@here"

    def test_parse_both_everyone_and_here(self, db_and_modules):
        """Test parsing both @everyone and @here."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "@everyone and @here"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 2
        types = {m.mention_type for m in mentions}
        assert MentionType.EVERYONE in types
        assert MentionType.HERE in types


class TestEveryoneValidation:
    """Tests for validating @everyone mentions."""

    def test_everyone_invalid_in_dm(self, users_with_dm):
        """Test @everyone is invalid in DMs."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = "@everyone"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "dm" in validated[0].error.lower()

    def test_everyone_valid_with_permission(self, users_with_server):
        """Test @everyone is valid with permission in server."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        content = "@everyone"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server.id, channel.id)

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_here_invalid_in_dm(self, users_with_dm):
        """Test @here is invalid in DMs."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = "@here"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False


class TestEveryoneNotifications:
    """Tests for @everyone notifications."""

    def test_everyone_notifies_all_members(self, users_with_server):
        """Test @everyone notifies all server members."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@everyone check this out"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        assert member2.id in notified_users
        assert owner.id not in notified_users

        for notif in notifs:
            assert notif.mention_type == MentionType.EVERYONE

    def test_everyone_does_not_notify_sender(self, users_with_server):
        """Test @everyone doesn't notify the sender."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@everyone"
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


class TestHereNotifications:
    """Tests for @here notifications."""

    def test_here_notifies_online_members(self, users_with_server):
        """Test @here notifies online members."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@here check this out"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        for notif in notifs:
            assert notif.mention_type == MentionType.HERE

    def test_here_does_not_notify_sender(self, users_with_server):
        """Test @here doesn't notify the sender."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = "@here"
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
