"""Tests for event types and intents."""

from src.core.events.types import EventType, GatewayIntent


class TestEventType:
    """Tests for EventType enum."""

    def test_message_events_exist(self):
        """Test message event types exist."""
        assert EventType.MESSAGE_CREATE.value == "MESSAGE_CREATE"
        assert EventType.MESSAGE_UPDATE.value == "MESSAGE_UPDATE"
        assert EventType.MESSAGE_DELETE.value == "MESSAGE_DELETE"

    def test_presence_events_exist(self):
        """Test presence event types exist."""
        assert EventType.PRESENCE_UPDATE.value == "PRESENCE_UPDATE"
        assert EventType.TYPING_START.value == "TYPING_START"

    def test_guild_events_exist(self):
        """Test guild event types exist."""
        assert EventType.GUILD_CREATE.value == "GUILD_CREATE"
        assert EventType.GUILD_UPDATE.value == "GUILD_UPDATE"
        assert EventType.GUILD_DELETE.value == "GUILD_DELETE"

    def test_member_events_exist(self):
        """Test member event types exist."""
        assert EventType.GUILD_MEMBER_ADD.value == "GUILD_MEMBER_ADD"
        assert EventType.GUILD_MEMBER_REMOVE.value == "GUILD_MEMBER_REMOVE"
        assert EventType.GUILD_MEMBER_UPDATE.value == "GUILD_MEMBER_UPDATE"

    def test_channel_events_exist(self):
        """Test channel event types exist."""
        assert EventType.CHANNEL_CREATE.value == "CHANNEL_CREATE"
        assert EventType.CHANNEL_UPDATE.value == "CHANNEL_UPDATE"
        assert EventType.CHANNEL_DELETE.value == "CHANNEL_DELETE"

    def test_voice_events_exist(self):
        """Test voice event types exist."""
        assert EventType.VOICE_STATE_UPDATE.value == "VOICE_STATE_UPDATE"

    def test_reaction_events_exist(self):
        """Test reaction event types exist."""
        assert EventType.MESSAGE_REACTION_ADD.value == "MESSAGE_REACTION_ADD"
        assert EventType.MESSAGE_REACTION_REMOVE.value == "MESSAGE_REACTION_REMOVE"

    def test_ready_event_exists(self):
        """Test READY event type exists."""
        assert EventType.READY.value == "READY"
        assert EventType.RESUMED.value == "RESUMED"


class TestGatewayIntent:
    """Tests for GatewayIntent flags."""

    def test_guilds_intent(self):
        """Test GUILDS intent value."""
        assert GatewayIntent.GUILDS == 1 << 0
        assert GatewayIntent.GUILDS == 1

    def test_guild_members_intent(self):
        """Test GUILD_MEMBERS intent value."""
        assert GatewayIntent.GUILD_MEMBERS == 1 << 1
        assert GatewayIntent.GUILD_MEMBERS == 2

    def test_guild_messages_intent(self):
        """Test GUILD_MESSAGES intent value."""
        assert GatewayIntent.GUILD_MESSAGES == 1 << 9
        assert GatewayIntent.GUILD_MESSAGES == 512

    def test_direct_messages_intent(self):
        """Test DIRECT_MESSAGES intent value."""
        assert GatewayIntent.DIRECT_MESSAGES == 1 << 12
        assert GatewayIntent.DIRECT_MESSAGES == 4096

    def test_message_content_intent(self):
        """Test MESSAGE_CONTENT intent value."""
        assert GatewayIntent.MESSAGE_CONTENT == 1 << 15
        assert GatewayIntent.MESSAGE_CONTENT == 32768

    def test_all_intents(self):
        """Test all_intents includes all flags."""
        all_intents = GatewayIntent.all_intents()
        assert all_intents & GatewayIntent.GUILDS
        assert all_intents & GatewayIntent.GUILD_MEMBERS
        assert all_intents & GatewayIntent.GUILD_MESSAGES
        assert all_intents & GatewayIntent.DIRECT_MESSAGES
        assert all_intents & GatewayIntent.MESSAGE_CONTENT

    def test_default_intents(self):
        """Test default_intents excludes privileged."""
        default = GatewayIntent.default_intents()
        assert default & GatewayIntent.GUILDS
        assert default & GatewayIntent.GUILD_MESSAGES
        assert not (default & GatewayIntent.GUILD_MEMBERS)
        assert not (default & GatewayIntent.GUILD_PRESENCES)
        assert not (default & GatewayIntent.MESSAGE_CONTENT)

    def test_privileged_intents(self):
        """Test privileged_intents returns correct flags."""
        privileged = GatewayIntent.privileged_intents()
        assert privileged & GatewayIntent.GUILD_MEMBERS
        assert privileged & GatewayIntent.GUILD_PRESENCES
        assert privileged & GatewayIntent.MESSAGE_CONTENT
        assert not (privileged & GatewayIntent.GUILDS)
        assert not (privileged & GatewayIntent.GUILD_MESSAGES)

    def test_intent_combination(self):
        """Test combining intents with OR."""
        combined = GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES
        assert combined & GatewayIntent.GUILDS
        assert combined & GatewayIntent.GUILD_MESSAGES
        assert not (combined & GatewayIntent.GUILD_MEMBERS)

    def test_intent_check(self):
        """Test checking if intent is set."""
        intents = GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES
        assert bool(intents & GatewayIntent.GUILDS)
        assert bool(intents & GatewayIntent.GUILD_MESSAGES)
        assert not bool(intents & GatewayIntent.GUILD_MEMBERS)
