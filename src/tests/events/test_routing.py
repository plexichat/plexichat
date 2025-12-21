"""Tests for event routing logic."""

from src.core import events
from src.core.events.types import EventType, GatewayIntent
from src.core.events.router import (
    get_required_intent,
    get_dm_intent,
    filter_by_intents,
)


class TestIntentMapping:
    """Tests for event to intent mapping."""

    def test_guild_events_require_guilds_intent(self):
        """Test guild events require GUILDS intent."""
        assert get_required_intent(EventType.GUILD_CREATE) == GatewayIntent.GUILDS
        assert get_required_intent(EventType.GUILD_UPDATE) == GatewayIntent.GUILDS
        assert get_required_intent(EventType.GUILD_DELETE) == GatewayIntent.GUILDS

    def test_channel_events_require_guilds_intent(self):
        """Test channel events require GUILDS intent."""
        assert get_required_intent(EventType.CHANNEL_CREATE) == GatewayIntent.GUILDS
        assert get_required_intent(EventType.CHANNEL_UPDATE) == GatewayIntent.GUILDS
        assert get_required_intent(EventType.CHANNEL_DELETE) == GatewayIntent.GUILDS

    def test_member_events_require_guild_members_intent(self):
        """Test member events require GUILD_MEMBERS intent."""
        assert get_required_intent(EventType.GUILD_MEMBER_ADD) == GatewayIntent.GUILD_MEMBERS
        assert get_required_intent(EventType.GUILD_MEMBER_REMOVE) == GatewayIntent.GUILD_MEMBERS
        assert get_required_intent(EventType.GUILD_MEMBER_UPDATE) == GatewayIntent.GUILD_MEMBERS

    def test_presence_events_require_guild_presences_intent(self):
        """Test presence events require GUILD_PRESENCES intent."""
        assert get_required_intent(EventType.PRESENCE_UPDATE) == GatewayIntent.GUILD_PRESENCES

    def test_voice_events_require_voice_states_intent(self):
        """Test voice events require GUILD_VOICE_STATES intent."""
        assert get_required_intent(EventType.VOICE_STATE_UPDATE) == GatewayIntent.GUILD_VOICE_STATES

    def test_reaction_events_require_reactions_intent(self):
        """Test reaction events require GUILD_MESSAGE_REACTIONS intent."""
        assert get_required_intent(EventType.MESSAGE_REACTION_ADD) == GatewayIntent.GUILD_MESSAGE_REACTIONS
        assert get_required_intent(EventType.MESSAGE_REACTION_REMOVE) == GatewayIntent.GUILD_MESSAGE_REACTIONS

    def test_typing_events_require_typing_intent(self):
        """Test typing events require GUILD_MESSAGE_TYPING intent."""
        assert get_required_intent(EventType.TYPING_START) == GatewayIntent.GUILD_MESSAGE_TYPING

    def test_dm_message_events_require_dm_intent(self):
        """Test DM message events require DIRECT_MESSAGES intent."""
        assert get_dm_intent(EventType.MESSAGE_CREATE) == GatewayIntent.DIRECT_MESSAGES
        assert get_dm_intent(EventType.MESSAGE_UPDATE) == GatewayIntent.DIRECT_MESSAGES
        assert get_dm_intent(EventType.MESSAGE_DELETE) == GatewayIntent.DIRECT_MESSAGES

    def test_dm_typing_requires_dm_typing_intent(self):
        """Test DM typing requires DIRECT_MESSAGE_TYPING intent."""
        assert get_dm_intent(EventType.TYPING_START) == GatewayIntent.DIRECT_MESSAGE_TYPING

    def test_ready_event_no_intent_required(self):
        """Test READY event has no intent requirement."""
        assert get_required_intent(EventType.READY) is None


class TestIntentFiltering:
    """Tests for intent-based event filtering."""

    def test_guild_event_passes_with_guilds_intent(self, sample_guild_event, all_intents):
        """Test guild event passes with GUILDS intent."""
        assert filter_by_intents(sample_guild_event, all_intents) is True

    def test_guild_event_fails_without_guilds_intent(self, sample_guild_event, no_intents):
        """Test guild event fails without GUILDS intent."""
        assert filter_by_intents(sample_guild_event, no_intents) is False

    def test_message_event_passes_with_guild_messages_intent(self, sample_message_event):
        """Test message event passes with GUILD_MESSAGES intent."""
        intents = GatewayIntent.GUILD_MESSAGES
        assert filter_by_intents(sample_message_event, intents) is True

    def test_message_event_fails_without_guild_messages_intent(self, sample_message_event):
        """Test message event fails without GUILD_MESSAGES intent."""
        intents = GatewayIntent.GUILDS
        assert filter_by_intents(sample_message_event, intents) is False

    def test_dm_event_passes_with_direct_messages_intent(self, sample_dm_event):
        """Test DM event passes with DIRECT_MESSAGES intent."""
        intents = GatewayIntent.DIRECT_MESSAGES
        assert filter_by_intents(sample_dm_event, intents) is True

    def test_dm_event_fails_without_direct_messages_intent(self, sample_dm_event):
        """Test DM event fails without DIRECT_MESSAGES intent."""
        intents = GatewayIntent.GUILD_MESSAGES
        assert filter_by_intents(sample_dm_event, intents) is False

    def test_presence_event_passes_with_presences_intent(self, sample_presence_event):
        """Test presence event passes with GUILD_PRESENCES intent."""
        intents = GatewayIntent.GUILD_PRESENCES
        sample_presence_event.server_id = 123
        assert filter_by_intents(sample_presence_event, intents) is True

    def test_presence_event_fails_without_presences_intent(self, sample_presence_event):
        """Test presence event fails without GUILD_PRESENCES intent."""
        intents = GatewayIntent.GUILDS
        sample_presence_event.server_id = 123
        assert filter_by_intents(sample_presence_event, intents) is False

    def test_typing_event_passes_with_typing_intent(self, sample_typing_event):
        """Test typing event passes with GUILD_MESSAGE_TYPING intent."""
        intents = GatewayIntent.GUILD_MESSAGE_TYPING
        assert filter_by_intents(sample_typing_event, intents) is True

    def test_default_intents_allow_guild_messages(self, sample_message_event, default_intents):
        """Test default intents allow guild messages."""
        assert filter_by_intents(sample_message_event, default_intents) is True

    def test_default_intents_block_member_events(self):
        """Test default intents block member events."""
        event = events.create_guild_member_add(server_id=123, user_id=456)
        default = GatewayIntent.default_intents()
        assert filter_by_intents(event, default) is False


class TestEventRouter:
    """Tests for EventRouter class."""

    def test_router_initialization(self, event_router):
        """Test router initializes correctly."""
        assert event_router is not None

    def test_get_recipients_with_explicit_user_ids(self, event_router, sample_message_event):
        """Test get_recipients with explicit user IDs."""
        user_ids = [1, 2, 3]
        recipients = event_router.get_recipients(
            sample_message_event,
            user_ids=user_ids,
        )
        assert recipients == user_ids

    def test_get_recipients_excludes_specified_users(self, event_router, sample_message_event):
        """Test get_recipients excludes specified users."""
        user_ids = [1, 2, 3, 4, 5]
        exclude = [2, 4]
        recipients = event_router.get_recipients(
            sample_message_event,
            user_ids=user_ids,
            exclude_user_ids=exclude,
        )
        assert 2 not in recipients
        assert 4 not in recipients
        assert 1 in recipients
        assert 3 in recipients
        assert 5 in recipients

    def test_get_recipients_returns_empty_without_modules(self, event_router, sample_message_event):
        """Test get_recipients returns empty without routing modules."""
        recipients = event_router.get_recipients(sample_message_event)
        assert recipients == []

    def test_get_recipients_uses_event_server_id(self, event_router, sample_message_event):
        """Test get_recipients uses event's server_id."""
        recipients = event_router.get_recipients(
            sample_message_event,
            server_id=None,
        )
        assert recipients == []

    def test_get_recipients_uses_event_channel_id(self, event_router, sample_dm_event):
        """Test get_recipients uses event's channel_id for DMs."""
        recipients = event_router.get_recipients(
            sample_dm_event,
            channel_id=None,
        )
        assert recipients == []


class TestEventDispatch:
    """Tests for event dispatch functionality."""

    def test_dispatch_returns_zero_with_empty_recipients(self, sample_message_event):
        """Test dispatch returns 0 with empty recipients."""
        count = events.dispatch(sample_message_event, user_ids=[])
        assert count == 0

    def test_subscribe_and_dispatch(self, sample_message_event):
        """Test subscribing and dispatching events."""
        received_events = []
        received_users = []

        def callback(event, user_ids):
            received_events.append(event)
            received_users.extend(user_ids)

        events.subscribe(callback)
        try:
            count = events.dispatch(sample_message_event, user_ids=[1, 2, 3])
            assert count == 3
            assert len(received_events) == 1
            assert received_events[0] == sample_message_event
            assert set(received_users) == {1, 2, 3}
        finally:
            events.unsubscribe(callback)

    def test_unsubscribe_stops_callbacks(self, sample_message_event):
        """Test unsubscribing stops callbacks."""
        call_count = [0]

        def callback(event, user_ids):
            call_count[0] += 1

        events.subscribe(callback)
        events.dispatch(sample_message_event, user_ids=[1])
        assert call_count[0] == 1

        events.unsubscribe(callback)
        events.dispatch(sample_message_event, user_ids=[1])
        assert call_count[0] == 1

    def test_dispatch_with_exclude(self, sample_message_event):
        """Test dispatch excludes specified users."""
        received_users = []

        def callback(event, user_ids):
            received_users.extend(user_ids)

        events.subscribe(callback)
        try:
            events.dispatch(
                sample_message_event,
                user_ids=[1, 2, 3, 4, 5],
                exclude_user_ids=[2, 4],
            )
            assert 2 not in received_users
            assert 4 not in received_users
        finally:
            events.unsubscribe(callback)

    def test_dispatch_returns_zero_with_empty_user_ids(self, sample_message_event):
        """Test dispatch returns 0 with empty user_ids."""
        count = events.dispatch(sample_message_event, user_ids=[])
        assert count == 0

    def test_multiple_subscribers(self, sample_message_event):
        """Test multiple subscribers receive events."""
        calls = {"a": 0, "b": 0}

        def callback_a(event, user_ids):
            calls["a"] += 1

        def callback_b(event, user_ids):
            calls["b"] += 1

        events.subscribe(callback_a)
        events.subscribe(callback_b)
        try:
            events.dispatch(sample_message_event, user_ids=[1])
            assert calls["a"] == 1
            assert calls["b"] == 1
        finally:
            events.unsubscribe(callback_a)
            events.unsubscribe(callback_b)
