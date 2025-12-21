"""Tests for event payload creation."""

from src.core import events
from src.core.events.types import EventType


class TestMessagePayloads:
    """Tests for message event payloads."""

    def test_create_message_create(self):
        """Test MESSAGE_CREATE payload creation."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
        )
        assert event.event_type == EventType.MESSAGE_CREATE
        assert event.message_id == 123
        assert event.channel_id == 456
        assert event.content == "Hello!"
        assert event.data["id"] == "123"
        assert event.data["channel_id"] == "456"
        assert event.data["content"] == "Hello!"

    def test_create_message_create_with_server(self):
        """Test MESSAGE_CREATE with server ID."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=111,
        )
        assert event.server_id == 111
        assert event.data["guild_id"] == "111"

    def test_create_message_create_with_attachments(self):
        """Test MESSAGE_CREATE with attachments."""
        attachments = [{"id": "1", "filename": "test.png"}]
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Check this out",
            attachments=attachments,
        )
        assert event.attachments == attachments
        assert event.data["attachments"] == attachments

    def test_create_message_update(self):
        """Test MESSAGE_UPDATE payload creation."""
        event = events.create_message_update(
            message_id=123,
            channel_id=456,
            content="Updated content",
        )
        assert event.event_type == EventType.MESSAGE_UPDATE
        assert event.message_id == 123
        assert event.data["id"] == "123"
        assert event.data["content"] == "Updated content"
        assert "edited_timestamp" in event.data

    def test_create_message_delete(self):
        """Test MESSAGE_DELETE payload creation."""
        event = events.create_message_delete(
            message_id=123,
            channel_id=456,
        )
        assert event.event_type == EventType.MESSAGE_DELETE
        assert event.message_id == 123
        assert event.data["id"] == "123"
        assert event.data["channel_id"] == "456"


class TestPresencePayloads:
    """Tests for presence event payloads."""

    def test_create_presence_update(self):
        """Test PRESENCE_UPDATE payload creation."""
        event = events.create_presence_update(
            user_id=123,
            status="online",
        )
        assert event.event_type == EventType.PRESENCE_UPDATE
        assert event.user_id == 123
        assert event.status == "online"
        assert event.data["status"] == "online"
        assert event.data["user"]["id"] == "123"

    def test_create_presence_update_with_activities(self):
        """Test PRESENCE_UPDATE with activities."""
        activities = [{"type": 0, "name": "Gaming"}]
        event = events.create_presence_update(
            user_id=123,
            status="online",
            activities=activities,
        )
        assert event.activities == activities
        assert event.data["activities"] == activities

    def test_create_typing_start(self):
        """Test TYPING_START payload creation."""
        event = events.create_typing_start(
            user_id=123,
            channel_id=456,
        )
        assert event.event_type == EventType.TYPING_START
        assert event.user_id == 123
        assert event.channel_id == 456
        assert event.data["user_id"] == "123"
        assert event.data["channel_id"] == "456"

    def test_create_typing_start_with_server(self):
        """Test TYPING_START with server ID."""
        event = events.create_typing_start(
            user_id=123,
            channel_id=456,
            server_id=789,
        )
        assert event.server_id == 789
        assert event.data["guild_id"] == "789"


class TestChannelPayloads:
    """Tests for channel event payloads."""

    def test_create_channel_create(self):
        """Test CHANNEL_CREATE payload creation."""
        event = events.create_channel_create(
            channel_id=123,
            channel_type=0,
            name="general",
        )
        assert event.event_type == EventType.CHANNEL_CREATE
        assert event.channel_id == 123
        assert event.name == "general"
        assert event.data["id"] == "123"
        assert event.data["type"] == 0
        assert event.data["name"] == "general"

    def test_create_channel_update(self):
        """Test CHANNEL_UPDATE payload creation."""
        event = events.create_channel_update(
            channel_id=123,
            channel_type=0,
            name="renamed",
            topic="New topic",
        )
        assert event.event_type == EventType.CHANNEL_UPDATE
        assert event.name == "renamed"
        assert event.topic == "New topic"
        assert event.data["topic"] == "New topic"

    def test_create_channel_delete(self):
        """Test CHANNEL_DELETE payload creation."""
        event = events.create_channel_delete(
            channel_id=123,
            channel_type=0,
        )
        assert event.event_type == EventType.CHANNEL_DELETE
        assert event.data["id"] == "123"


class TestGuildPayloads:
    """Tests for guild event payloads."""

    def test_create_guild_create(self):
        """Test GUILD_CREATE payload creation."""
        event = events.create_guild_create(
            server_id=123,
            name="Test Server",
            owner_id=456,
            member_count=10,
        )
        assert event.event_type == EventType.GUILD_CREATE
        assert event.server_id == 123
        assert event.name == "Test Server"
        assert event.owner_id == 456
        assert event.member_count == 10
        assert event.data["id"] == "123"
        assert event.data["name"] == "Test Server"

    def test_create_guild_update(self):
        """Test GUILD_UPDATE payload creation."""
        event = events.create_guild_update(
            server_id=123,
            name="Updated Name",
        )
        assert event.event_type == EventType.GUILD_UPDATE
        assert event.data["id"] == "123"
        assert event.data["name"] == "Updated Name"

    def test_create_guild_delete(self):
        """Test GUILD_DELETE payload creation."""
        event = events.create_guild_delete(server_id=123)
        assert event.event_type == EventType.GUILD_DELETE
        assert event.data["id"] == "123"


class TestMemberPayloads:
    """Tests for guild member event payloads."""

    def test_create_guild_member_add(self):
        """Test GUILD_MEMBER_ADD payload creation."""
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
        )
        assert event.event_type == EventType.GUILD_MEMBER_ADD
        assert event.server_id == 123
        assert event.member_user_id == 456
        assert event.data["guild_id"] == "123"
        assert event.data["user"]["id"] == "456"

    def test_create_guild_member_add_with_nick(self):
        """Test GUILD_MEMBER_ADD with nickname."""
        event = events.create_guild_member_add(
            server_id=123,
            user_id=456,
            nick="TestNick",
        )
        assert event.nick == "TestNick"
        assert event.data["nick"] == "TestNick"

    def test_create_guild_member_remove(self):
        """Test GUILD_MEMBER_REMOVE payload creation."""
        event = events.create_guild_member_remove(
            server_id=123,
            user_id=456,
        )
        assert event.event_type == EventType.GUILD_MEMBER_REMOVE
        assert event.data["guild_id"] == "123"
        assert event.data["user"]["id"] == "456"

    def test_create_guild_member_update(self):
        """Test GUILD_MEMBER_UPDATE payload creation."""
        event = events.create_guild_member_update(
            server_id=123,
            user_id=456,
            nick="NewNick",
            roles=[1, 2, 3],
        )
        assert event.event_type == EventType.GUILD_MEMBER_UPDATE
        assert event.nick == "NewNick"
        assert event.roles == [1, 2, 3]


class TestVoicePayloads:
    """Tests for voice event payloads."""

    def test_create_voice_state_update(self):
        """Test VOICE_STATE_UPDATE payload creation."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=456,
            server_id=789,
        )
        assert event.event_type == EventType.VOICE_STATE_UPDATE
        assert event.user_id == 123
        assert event.voice_channel_id == 456
        assert event.data["user_id"] == "123"
        assert event.data["channel_id"] == "456"

    def test_create_voice_state_update_muted(self):
        """Test VOICE_STATE_UPDATE with mute flags."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=456,
            self_mute=True,
            self_deaf=True,
        )
        assert event.self_mute is True
        assert event.self_deaf is True
        assert event.data["self_mute"] is True
        assert event.data["self_deaf"] is True

    def test_create_voice_state_update_disconnect(self):
        """Test VOICE_STATE_UPDATE for disconnect."""
        event = events.create_voice_state_update(
            user_id=123,
            channel_id=None,
        )
        assert event.voice_channel_id is None
        assert event.data["channel_id"] is None


class TestReactionPayloads:
    """Tests for reaction event payloads."""

    def test_create_reaction_add(self):
        """Test MESSAGE_REACTION_ADD payload creation."""
        emoji = {"name": "thumbsup", "id": None}
        event = events.create_reaction_add(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji=emoji,
        )
        assert event.event_type == EventType.MESSAGE_REACTION_ADD
        assert event.user_id == 123
        assert event.message_id == 456
        assert event.emoji == emoji
        assert event.data["emoji"] == emoji

    def test_create_reaction_remove(self):
        """Test MESSAGE_REACTION_REMOVE payload creation."""
        emoji = {"name": "thumbsup", "id": None}
        event = events.create_reaction_remove(
            user_id=123,
            message_id=456,
            channel_id=789,
            emoji=emoji,
        )
        assert event.event_type == EventType.MESSAGE_REACTION_REMOVE
        assert event.data["user_id"] == "123"


class TestEventToDict:
    """Tests for event serialization."""

    def test_event_to_dict(self, sample_message_event):
        """Test event to_dict method."""
        result = sample_message_event.to_dict()
        assert "t" in result
        assert "d" in result
        assert result["t"] == "MESSAGE_CREATE"
        assert isinstance(result["d"], dict)

    def test_event_data_has_string_ids(self, sample_message_event):
        """Test that IDs in event data are strings."""
        data = sample_message_event.data
        assert isinstance(data["id"], str)
        assert isinstance(data["channel_id"], str)
