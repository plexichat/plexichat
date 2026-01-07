"""
Tests for @user mention parsing and notifications.
"""

from src.core.notifications import MentionType


class TestUserMentionParsing:
    """Tests for parsing @user mentions."""

    def test_parse_single_user_mention(self, db_and_modules):
        """Test parsing a single user mention."""
        db, auth, messaging, servers, relationships, presence, notifications = (
            db_and_modules
        )

        content = "Hello <@123456789>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.USER
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<@123456789>"

    def test_parse_multiple_user_mentions(self, db_and_modules):
        """Test parsing multiple user mentions."""
        db, auth, messaging, servers, relationships, presence, notifications = (
            db_and_modules
        )

        content = "Hey <@111> and <@222> check this out"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_user_mention_positions(self, db_and_modules):
        """Test mention positions are correct."""
        db, auth, messaging, servers, relationships, presence, notifications = (
            db_and_modules
        )

        content = "Hello <@123>"
        mentions = notifications.parse_mentions(content)

        assert mentions[0].start_pos == 6
        assert mentions[0].end_pos == 12

    def test_parse_no_mentions(self, db_and_modules):
        """Test parsing content with no mentions."""
        db, auth, messaging, servers, relationships, presence, notifications = (
            db_and_modules
        )

        content = "Hello world, no mentions here"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0

    def test_parse_empty_content(self, db_and_modules):
        """Test parsing empty content."""
        db, auth, messaging, servers, relationships, presence, notifications = (
            db_and_modules
        )

        mentions = notifications.parse_mentions("")
        assert len(mentions) == 0

        mentions = notifications.parse_mentions(None)
        assert len(mentions) == 0


class TestUserMentionValidation:
    """Tests for validating @user mentions."""

    def test_validate_existing_user(self, fresh_users):
        """Test validating mention of existing user."""
        (
            user1,
            user2,
            auth,
            messaging,
            servers,
            relationships,
            presence,
            notifications,
        ) = fresh_users

        content = f"Hey <@{user2.id}>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_validate_nonexistent_user(self, fresh_users):
        """Test validating mention of nonexistent user."""
        (
            user1,
            user2,
            auth,
            messaging,
            servers,
            relationships,
            presence,
            notifications,
        ) = fresh_users

        content = "<@999999999999>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "not found" in validated[0].error.lower()


class TestUserMentionNotifications:
    """Tests for creating notifications from @user mentions."""

    def test_create_notification_for_user_mention(self, users_with_dm):
        """Test notification is created for mentioned user."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"Hey <@{user2.id}> check this out"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert notifs[0].user_id == user2.id
        assert notifs[0].author_id == user1.id
        assert notifs[0].mention_type == MentionType.USER

    def test_no_notification_for_self_mention(self, users_with_dm):
        """Test no notification when user mentions themselves."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"I am <@{user1.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 0

    def test_notification_content_preview(self, users_with_dm):
        """Test notification includes content preview."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"Hey <@{user2.id}> this is a test message"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert "test message" in notifs[0].content_preview

    def test_multiple_mentions_same_user(self, users_with_dm):
        """Test only one notification for multiple mentions of same user."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}> hey <@{user2.id}> hello <@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert notifs[0].user_id == user2.id

    def test_mention_multiple_users(self, group_conversation):
        """Test notifications for multiple mentioned users."""
        owner, member1, member2, group, messaging, notifications, relationships = (
            group_conversation
        )

        content = f"Hey <@{member1.id}> and <@{member2.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
        )

        assert len(notifs) == 2
        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        assert member2.id in notified_users
