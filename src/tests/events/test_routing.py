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
        assert (
            get_required_intent(EventType.GUILD_MEMBER_ADD)
            == GatewayIntent.GUILD_MEMBERS
        )
        assert (
            get_required_intent(EventType.GUILD_MEMBER_REMOVE)
            == GatewayIntent.GUILD_MEMBERS
        )
        assert (
            get_required_intent(EventType.GUILD_MEMBER_UPDATE)
            == GatewayIntent.GUILD_MEMBERS
        )

    def test_presence_events_require_guild_presences_intent(self):
        """Test presence events require GUILD_PRESENCES intent."""
        assert (
            get_required_intent(EventType.PRESENCE_UPDATE)
            == GatewayIntent.GUILD_PRESENCES
        )

    def test_voice_events_require_voice_states_intent(self):
        """Test voice events require GUILD_VOICE_STATES intent."""
        assert (
            get_required_intent(EventType.VOICE_STATE_UPDATE)
            == GatewayIntent.GUILD_VOICE_STATES
        )

    def test_reaction_events_require_reactions_intent(self):
        """Test reaction events require GUILD_MESSAGE_REACTIONS intent."""
        assert (
            get_required_intent(EventType.MESSAGE_REACTION_ADD)
            == GatewayIntent.GUILD_MESSAGE_REACTIONS
        )
        assert (
            get_required_intent(EventType.MESSAGE_REACTION_REMOVE)
            == GatewayIntent.GUILD_MESSAGE_REACTIONS
        )

    def test_typing_events_require_typing_intent(self):
        """Test typing events require GUILD_MESSAGE_TYPING intent."""
        assert (
            get_required_intent(EventType.TYPING_START)
            == GatewayIntent.GUILD_MESSAGE_TYPING
        )

    def test_dm_message_events_require_dm_intent(self):
        """Test DM message events require DIRECT_MESSAGES intent."""
        assert get_dm_intent(EventType.MESSAGE_CREATE) == GatewayIntent.DIRECT_MESSAGES
        assert get_dm_intent(EventType.MESSAGE_UPDATE) == GatewayIntent.DIRECT_MESSAGES
        assert get_dm_intent(EventType.MESSAGE_DELETE) == GatewayIntent.DIRECT_MESSAGES

    def test_dm_typing_requires_dm_typing_intent(self):
        """Test DM typing requires DIRECT_MESSAGE_TYPING intent."""
        assert (
            get_dm_intent(EventType.TYPING_START) == GatewayIntent.DIRECT_MESSAGE_TYPING
        )

    def test_ready_event_no_intent_required(self):
        """Test READY event has no intent requirement."""
        assert get_required_intent(EventType.READY) is None


class TestIntentFiltering:
    """Tests for intent-based event filtering."""

    def test_guild_event_passes_with_guilds_intent(self):
        """Test guild event passes with GUILDS intent."""
        event = events.create_guild_create(
            server_id=123, owner_id=456, name="Test Guild"
        )
        all_intents = GatewayIntent.GUILDS
        assert filter_by_intents(event, all_intents) is True

    def test_guild_event_fails_without_guilds_intent(self):
        """Test guild event fails without GUILDS intent."""
        event = events.create_guild_create(
            server_id=123, owner_id=456, name="Test Guild"
        )
        no_intents = GatewayIntent(0)
        assert filter_by_intents(event, no_intents) is False

    def test_message_event_passes_with_guild_messages_intent(self):
        """Test message event passes with GUILD_MESSAGES intent."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=111,
        )
        intents = GatewayIntent.GUILD_MESSAGES
        assert filter_by_intents(event, intents) is True

    def test_message_event_fails_without_guild_messages_intent(self):
        """Test message event fails without GUILD_MESSAGES intent."""
        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=111,
        )
        intents = GatewayIntent.GUILDS
        assert filter_by_intents(event, intents) is False

    def test_dm_event_passes_with_direct_messages_intent(self):
        """Test DM event passes with DIRECT_MESSAGES intent."""
        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        intents = GatewayIntent.DIRECT_MESSAGES
        assert filter_by_intents(event, intents) is True

    def test_dm_event_fails_without_direct_messages_intent(self):
        """Test DM event fails without DIRECT_MESSAGES intent."""
        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        intents = GatewayIntent.GUILD_MESSAGES
        assert filter_by_intents(event, intents) is False

    def test_presence_event_passes_with_presences_intent(self):
        """Test presence event passes with GUILD_PRESENCES intent."""
        event = events.create_presence_update(user_id=123, status="online")
        event.server_id = 123
        intents = GatewayIntent.GUILD_PRESENCES
        assert filter_by_intents(event, intents) is True

    def test_presence_event_fails_without_presences_intent(self):
        """Test presence event fails without GUILD_PRESENCES intent."""
        event = events.create_presence_update(user_id=123, status="online")
        event.server_id = 123
        intents = GatewayIntent.GUILDS
        assert filter_by_intents(event, intents) is False

    def test_typing_event_passes_with_typing_intent(self):
        """Test typing event passes with GUILD_MESSAGE_TYPING intent."""
        event = events.create_typing_start(user_id=123, channel_id=456, server_id=789)
        intents = GatewayIntent.GUILD_MESSAGE_TYPING
        assert filter_by_intents(event, intents) is True


class TestDefaultIntents:
    """Tests for default intent behavior."""

    def test_default_intents_block_member_events(self):
        """Test default intents block member events."""
        event = events.create_guild_member_add(server_id=123, user_id=456)
        default = GatewayIntent.default_intents()
        assert filter_by_intents(event, default) is False


class TestEventRouter:
    """Tests for EventRouter class."""

    def test_router_initialization(self):
        """Test router initializes correctly."""
        from src.core.events.router import EventRouter

        router = EventRouter()
        assert router is not None

    def test_get_recipients_with_explicit_user_ids(self):
        """Test get_recipients with explicit user IDs."""
        from src.core.events.router import EventRouter

        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        router = EventRouter()
        user_ids = [1, 2, 3]
        recipients = router.get_recipients(
            event,
            user_ids=user_ids,
        )
        assert recipients == user_ids

    def test_get_recipients_excludes_specified_users(self):
        """Test get_recipients excludes specified users."""
        from src.core.events.router import EventRouter

        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        router = EventRouter()
        user_ids = [1, 2, 3, 4, 5]
        exclude = [2, 4]
        recipients = router.get_recipients(
            event,
            user_ids=user_ids,
            exclude_user_ids=exclude,
        )
        assert 2 not in recipients
        assert 4 not in recipients
        assert 1 in recipients
        assert 3 in recipients
        assert 5 in recipients

    def test_get_recipients_returns_empty_without_modules(self):
        """Test get_recipients returns empty without routing modules."""
        from src.core.events.router import EventRouter

        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        router = EventRouter()
        recipients = router.get_recipients(event)
        assert recipients == []

    def test_get_recipients_uses_event_server_id(self):
        """Test get_recipients uses event's server_id."""
        from src.core.events.router import EventRouter

        event = events.create_message_create(
            message_id=123,
            channel_id=456,
            author_id=789,
            content="Hello!",
            server_id=111,
        )
        router = EventRouter()
        recipients = router.get_recipients(
            event,
            server_id=None,
        )
        assert recipients == []

    def test_get_recipients_uses_event_channel_id(self):
        """Test get_recipients uses event's channel_id for DMs."""
        from src.core.events.router import EventRouter

        event = events.create_message_create(
            message_id=123, channel_id=456, author_id=789, content="Hello!"
        )
        router = EventRouter()
        recipients = router.get_recipients(
            event,
            channel_id=None,
        )
        assert recipients == []


class TestEventDispatch:
    """Tests for event dispatch functionality."""

    def test_dispatch_returns_zero_with_empty_recipients(self, events_module):
        """Test dispatch returns 0 with empty recipients."""
        from src.core.events import Event, EventType

        event = Event(EventType.READY, {})
        count = events.dispatch(event, user_ids=[])
        assert count == 0

    def test_subscribe_and_dispatch(self, events_module):
        """Test subscribing and dispatching events."""
        from src.core.events import Event, EventType

        callback_called = []

        def callback(event, user_ids):
            callback_called.append((event, user_ids))

        events.subscribe(callback)

        event = Event(EventType.READY, {"data": "test"})
        count = events.dispatch(event, user_ids=[1, 2, 3])

        assert count == 3
        assert len(callback_called) == 1
        assert callback_called[0][0] == event
        assert callback_called[0][1] == [1, 2, 3]

    def test_unsubscribe_stops_callbacks(self, events_module):
        """Test unsubscribing stops callbacks."""
        from src.core.events import Event, EventType

        callback_called = []

        def callback(event, user_ids):
            callback_called.append((event, user_ids))

        events.subscribe(callback)
        events.unsubscribe(callback)

        event = Event(EventType.READY, {"data": "test"})
        count = events.dispatch(event, user_ids=[1, 2, 3])

        assert count == 3  # Still dispatches to users, but no callbacks
        assert len(callback_called) == 0

    def test_dispatch_with_exclude(self, events_module):
        """Test dispatch excludes specified users."""
        from src.core.events import Event, EventType

        callback_called = []

        def callback(event, user_ids):
            callback_called.append((event, user_ids))

        events.subscribe(callback)

        event = Event(EventType.READY, {"data": "test"})
        count = events.dispatch(
            event, user_ids=[1, 2, 3, 4, 5], exclude_user_ids=[2, 4]
        )

        assert count == 3  # Only 1, 3, 5 receive it
        assert len(callback_called) == 1
        assert callback_called[0][1] == [1, 3, 5]

    def test_dispatch_returns_zero_with_empty_user_ids(self, events_module):
        """Test dispatch returns 0 with empty user_ids."""
        from src.core.events import Event, EventType

        callback_called = []

        def callback(event, user_ids):
            callback_called.append((event, user_ids))

        events.subscribe(callback)

        event = Event(EventType.READY, {"data": "test"})
        count = events.dispatch(event, user_ids=[])

        assert count == 0
        assert len(callback_called) == 0

    def test_multiple_subscribers(self, events_module):
        """Test multiple subscribers receive events."""
        from src.core.events import Event, EventType

        callback1_called = []
        callback2_called = []

        def callback1(event, user_ids):
            callback1_called.append((event, user_ids))

        def callback2(event, user_ids):
            callback2_called.append((event, user_ids))

        events.subscribe(callback1)
        events.subscribe(callback2)

        event = Event(EventType.READY, {"data": "test"})
        count = events.dispatch(event, user_ids=[1, 2])

        assert count == 2
        assert len(callback1_called) == 1
        assert len(callback2_called) == 1
        assert callback1_called[0][1] == [1, 2]
        assert callback2_called[0][1] == [1, 2]
