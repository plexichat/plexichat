"""Tests for gateway intents filtering."""

from src.api.websocket.intents import (
    validate_intents,
    has_privileged_intents,
    get_privileged_intents_requested,
    filter_event_by_intents,
    should_include_message_content,
    DEFAULT_INTENTS,
    ALL_INTENTS,
    PRIVILEGED_INTENTS,
)
from src.core.events.types import GatewayIntent
from src.core import events


class TestIntentValidation:
    """Tests for intent validation."""

    def test_validate_intents_valid(self):
        """Test valid intents pass validation."""
        assert validate_intents(0) is True
        assert validate_intents(1) is True
        assert validate_intents(513) is True
        assert validate_intents(ALL_INTENTS) is True

    def test_validate_intents_negative(self):
        """Test negative intents fail validation."""
        assert validate_intents(-1) is False
        assert validate_intents(-100) is False

    def test_validate_intents_too_large(self):
        """Test intents larger than all_intents fail."""
        assert validate_intents(ALL_INTENTS + 1) is False
        assert validate_intents(ALL_INTENTS * 2) is False


class TestPrivilegedIntents:
    """Tests for privileged intent detection."""

    def test_has_privileged_intents_true(self):
        """Test detecting privileged intents."""
        assert has_privileged_intents(GatewayIntent.GUILD_MEMBERS) is True
        assert has_privileged_intents(GatewayIntent.GUILD_PRESENCES) is True
        assert has_privileged_intents(GatewayIntent.MESSAGE_CONTENT) is True

    def test_has_privileged_intents_false(self):
        """Test non-privileged intents."""
        assert has_privileged_intents(GatewayIntent.GUILDS) is False
        assert has_privileged_intents(GatewayIntent.GUILD_MESSAGES) is False
        assert has_privileged_intents(DEFAULT_INTENTS) is False

    def test_has_privileged_intents_combined(self):
        """Test combined intents with privileged."""
        combined = GatewayIntent.GUILDS | GatewayIntent.GUILD_MEMBERS
        assert has_privileged_intents(combined) is True

    def test_get_privileged_intents_requested(self):
        """Test getting list of privileged intents."""
        intents = GatewayIntent.GUILD_MEMBERS | GatewayIntent.MESSAGE_CONTENT
        requested = get_privileged_intents_requested(intents)
        assert "GUILD_MEMBERS" in requested
        assert "MESSAGE_CONTENT" in requested
        assert "GUILD_PRESENCES" not in requested

    def test_get_privileged_intents_none(self):
        """Test getting privileged intents when none requested."""
        requested = get_privileged_intents_requested(GatewayIntent.GUILDS)
        assert requested == []


class TestEventFiltering:
    """Tests for event filtering by intents."""

    def test_filter_guild_event_with_guilds_intent(self):
        """Test guild event passes with GUILDS intent."""
        event = events.create_guild_create(server_id=1, name="Test", owner_id=2)
        assert filter_event_by_intents(event, GatewayIntent.GUILDS) is True

    def test_filter_guild_event_without_guilds_intent(self):
        """Test guild event fails without GUILDS intent."""
        event = events.create_guild_create(server_id=1, name="Test", owner_id=2)
        assert filter_event_by_intents(event, 0) is False

    def test_filter_message_event_with_guild_messages_intent(self):
        """Test message event passes with GUILD_MESSAGES intent."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )
        assert filter_event_by_intents(event, GatewayIntent.GUILD_MESSAGES) is True

    def test_filter_message_event_without_guild_messages_intent(self):
        """Test message event fails without GUILD_MESSAGES intent."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test", server_id=4
        )
        assert filter_event_by_intents(event, GatewayIntent.GUILDS) is False

    def test_filter_dm_event_with_direct_messages_intent(self):
        """Test DM event passes with DIRECT_MESSAGES intent."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        assert filter_event_by_intents(event, GatewayIntent.DIRECT_MESSAGES) is True

    def test_filter_dm_event_without_direct_messages_intent(self):
        """Test DM event fails without DIRECT_MESSAGES intent."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        assert filter_event_by_intents(event, GatewayIntent.GUILD_MESSAGES) is False

    def test_filter_member_event_with_guild_members_intent(self):
        """Test member event passes with GUILD_MEMBERS intent."""
        event = events.create_guild_member_add(server_id=1, user_id=2)
        assert filter_event_by_intents(event, GatewayIntent.GUILD_MEMBERS) is True

    def test_filter_member_event_without_guild_members_intent(self):
        """Test member event fails without GUILD_MEMBERS intent."""
        event = events.create_guild_member_add(server_id=1, user_id=2)
        assert filter_event_by_intents(event, GatewayIntent.GUILDS) is False

    def test_filter_presence_event_with_presences_intent(self):
        """Test presence event passes with GUILD_PRESENCES intent."""
        event = events.create_presence_update(user_id=1, status="online", server_id=2)
        assert filter_event_by_intents(event, GatewayIntent.GUILD_PRESENCES) is True

    def test_filter_presence_event_without_presences_intent(self):
        """Test presence event fails without GUILD_PRESENCES intent."""
        event = events.create_presence_update(user_id=1, status="online", server_id=2)
        assert filter_event_by_intents(event, GatewayIntent.GUILDS) is False

    def test_filter_typing_event_with_typing_intent(self):
        """Test typing event passes with GUILD_MESSAGE_TYPING intent."""
        event = events.create_typing_start(user_id=1, channel_id=2, server_id=3)
        assert filter_event_by_intents(event, GatewayIntent.GUILD_MESSAGE_TYPING) is True

    def test_filter_typing_event_without_typing_intent(self):
        """Test typing event fails without GUILD_MESSAGE_TYPING intent."""
        event = events.create_typing_start(user_id=1, channel_id=2, server_id=3)
        assert filter_event_by_intents(event, GatewayIntent.GUILDS) is False


class TestMessageContentIntent:
    """Tests for MESSAGE_CONTENT intent."""

    def test_should_include_content_dm(self):
        """Test DM message content with DIRECT_MESSAGES intent."""
        assert should_include_message_content(GatewayIntent.DIRECT_MESSAGES, is_dm=True) is True

    def test_should_not_include_content_dm_without_intent(self):
        """Test DM message content without DIRECT_MESSAGES intent."""
        assert should_include_message_content(GatewayIntent.GUILDS, is_dm=True) is False

    def test_should_include_content_guild(self):
        """Test guild message content with MESSAGE_CONTENT intent."""
        assert should_include_message_content(GatewayIntent.MESSAGE_CONTENT, is_dm=False) is True

    def test_should_not_include_content_guild_without_intent(self):
        """Test guild message content without MESSAGE_CONTENT intent."""
        assert should_include_message_content(GatewayIntent.GUILD_MESSAGES, is_dm=False) is False


class TestDefaultIntents:
    """Tests for default intent values."""

    def test_default_intents_value(self):
        """Test DEFAULT_INTENTS has expected value."""
        assert DEFAULT_INTENTS == GatewayIntent.default_intents()

    def test_all_intents_value(self):
        """Test ALL_INTENTS has expected value."""
        assert ALL_INTENTS == GatewayIntent.all_intents()

    def test_privileged_intents_value(self):
        """Test PRIVILEGED_INTENTS has expected value."""
        assert PRIVILEGED_INTENTS == GatewayIntent.privileged_intents()

    def test_default_intents_excludes_privileged(self):
        """Test default intents exclude privileged."""
        assert not (DEFAULT_INTENTS & PRIVILEGED_INTENTS)
