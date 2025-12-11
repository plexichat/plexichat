"""Tests for event dispatch to connections."""

from src.core import events
from src.core.events.manager import EventManager
from src.core.events.types import EventType


class TestEventManager:
    """Tests for EventManager class."""

    def test_manager_initialization(self):
        """Test manager initializes correctly."""
        manager = EventManager()
        assert manager is not None

    def test_subscribe_callback(self):
        """Test subscribing a callback."""
        manager = EventManager()
        calls = []

        def callback(event, user_ids):
            calls.append((event, user_ids))

        manager.subscribe(callback)
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        manager.dispatch(event, user_ids=[1, 2])

        assert len(calls) == 1
        assert calls[0][0] == event
        assert calls[0][1] == [1, 2]

    def test_unsubscribe_callback(self):
        """Test unsubscribing a callback."""
        manager = EventManager()
        calls = []

        def callback(event, user_ids):
            calls.append(1)

        manager.subscribe(callback)
        manager.unsubscribe(callback)

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        manager.dispatch(event, user_ids=[1])

        assert len(calls) == 0

    def test_dispatch_returns_recipient_count(self):
        """Test dispatch returns number of recipients."""
        manager = EventManager()

        def callback(event, user_ids):
            pass

        manager.subscribe(callback)
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        count = manager.dispatch(event, user_ids=[1, 2, 3])

        assert count == 3

    def test_dispatch_with_no_recipients(self):
        """Test dispatch with no recipients returns 0."""
        manager = EventManager()
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        count = manager.dispatch(event, user_ids=[])

        assert count == 0

    def test_dispatch_excludes_users(self):
        """Test dispatch excludes specified users."""
        manager = EventManager()
        received_users = []

        def callback(event, user_ids):
            received_users.extend(user_ids)

        manager.subscribe(callback)
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        manager.dispatch(
            event,
            user_ids=[1, 2, 3, 4, 5],
            exclude_user_ids=[2, 4],
        )

        assert 2 not in received_users
        assert 4 not in received_users
        assert len(received_users) == 3

    def test_callback_exception_does_not_break_dispatch(self):
        """Test callback exception does not break other callbacks."""
        manager = EventManager()
        calls = []

        def bad_callback(event, user_ids):
            raise ValueError("Test error")

        def good_callback(event, user_ids):
            calls.append(1)

        manager.subscribe(bad_callback)
        manager.subscribe(good_callback)

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        manager.dispatch(event, user_ids=[1])

        assert len(calls) == 1

    def test_duplicate_subscribe_ignored(self):
        """Test subscribing same callback twice is ignored."""
        manager = EventManager()
        calls = []

        def callback(event, user_ids):
            calls.append(1)

        manager.subscribe(callback)
        manager.subscribe(callback)

        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        manager.dispatch(event, user_ids=[1])

        assert len(calls) == 1

    def test_unsubscribe_nonexistent_callback(self):
        """Test unsubscribing non-existent callback does not error."""
        manager = EventManager()

        def callback(event, user_ids):
            pass

        manager.unsubscribe(callback)


class TestModuleLevelDispatch:
    """Tests for module-level dispatch functions."""

    def test_is_setup_returns_true_after_setup(self):
        """Test is_setup returns True after setup."""
        assert events.is_setup() is True

    def test_dispatch_without_user_ids_returns_zero(self):
        """Test dispatch without user_ids returns 0."""
        event = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="test"
        )
        count = events.dispatch(event)
        assert count == 0

    def test_get_required_intent(self):
        """Test get_required_intent function."""
        from src.core.events.types import GatewayIntent

        intent = events.get_required_intent(EventType.GUILD_CREATE)
        assert intent == GatewayIntent.GUILDS

    def test_filter_by_intents(self):
        """Test filter_by_intents function."""
        from src.core.events.types import GatewayIntent

        event = events.create_guild_create(
            server_id=1, name="Test", owner_id=2
        )
        assert events.filter_by_intents(event, GatewayIntent.GUILDS) is True
        assert events.filter_by_intents(event, 0) is False


class TestEventCreationAndDispatch:
    """Tests for creating and dispatching various event types."""

    def test_message_create_dispatch(self):
        """Test MESSAGE_CREATE event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_message_create(
                message_id=1, channel_id=2, author_id=3, content="Hello"
            )
            events.dispatch(event, user_ids=[1])
            assert EventType.MESSAGE_CREATE in received
        finally:
            events.unsubscribe(callback)

    def test_presence_update_dispatch(self):
        """Test PRESENCE_UPDATE event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_presence_update(user_id=1, status="online")
            events.dispatch(event, user_ids=[1])
            assert EventType.PRESENCE_UPDATE in received
        finally:
            events.unsubscribe(callback)

    def test_guild_create_dispatch(self):
        """Test GUILD_CREATE event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_guild_create(
                server_id=1, name="Test", owner_id=2
            )
            events.dispatch(event, user_ids=[1])
            assert EventType.GUILD_CREATE in received
        finally:
            events.unsubscribe(callback)

    def test_channel_create_dispatch(self):
        """Test CHANNEL_CREATE event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_channel_create(
                channel_id=1, channel_type=0, name="general"
            )
            events.dispatch(event, user_ids=[1])
            assert EventType.CHANNEL_CREATE in received
        finally:
            events.unsubscribe(callback)

    def test_voice_state_update_dispatch(self):
        """Test VOICE_STATE_UPDATE event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_voice_state_update(
                user_id=1, channel_id=2
            )
            events.dispatch(event, user_ids=[1])
            assert EventType.VOICE_STATE_UPDATE in received
        finally:
            events.unsubscribe(callback)

    def test_reaction_add_dispatch(self):
        """Test MESSAGE_REACTION_ADD event dispatch."""
        received = []

        def callback(event, user_ids):
            received.append(event.event_type)

        events.subscribe(callback)
        try:
            event = events.create_reaction_add(
                user_id=1, message_id=2, channel_id=3,
                emoji={"name": "thumbsup"}
            )
            events.dispatch(event, user_ids=[1])
            assert EventType.MESSAGE_REACTION_ADD in received
        finally:
            events.unsubscribe(callback)
