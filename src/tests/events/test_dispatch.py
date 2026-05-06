"""Tests for event dispatch and subscription."""

import pytest

from src.core.events.models import Event
from src.core.events.types import EventType


@pytest.mark.integration
class TestDispatch:
    """Tests for event dispatch and subscription."""

    def test_subscribe_callback(self, events_module):
        """Test subscribing to event dispatches."""
        received = []

        def callback(event, user_ids):
            received.append((event, user_ids))

        events_module._manager.subscribe(callback)
        assert callback in events_module._manager._subscribers

    def test_unsubscribe_callback(self, events_module):
        """Test unsubscribing from event dispatches."""
        received = []

        def callback(event, user_ids):
            received.append((event, user_ids))

        events_module._manager.subscribe(callback)
        events_module._manager.unsubscribe(callback)
        assert callback not in events_module._manager._subscribers

    def test_dispatch_to_subscribers(self, events_module):
        """Test that dispatched events reach subscribers."""
        received = []

        def callback(event, user_ids):
            received.append((event, user_ids))

        events_module._manager.subscribe(callback)

        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "test"},
            user_ids=[123],
        )
        events_module._manager.dispatch(event, user_ids=[123])

        assert len(received) == 1
        assert received[0][0].event_type == EventType.MESSAGE_CREATE

    def test_dispatch_with_no_subscribers(self, events_module):
        """Test dispatch with no subscribers does not raise."""
        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "test"},
        )
        # Should not raise
        count = events_module._manager.dispatch(event, user_ids=[123])
        assert isinstance(count, int)

    def test_dispatch_returns_recipient_count(self, events_module):
        """Test that dispatch returns number of recipients."""
        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "test"},
        )
        count = events_module._manager.dispatch(event, user_ids=[123, 456])
        assert count == 2

    def test_dispatch_empty_recipients(self, events_module):
        """Test dispatch with empty recipients returns 0."""
        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "test"},
        )
        count = events_module._manager.dispatch(event, user_ids=[])
        assert count == 0

    def test_event_to_dict(self):
        """Test Event.to_dict serialization."""
        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={"content": "hello"},
        )
        d = event.to_dict()
        assert "t" in d
        assert "d" in d
        assert d["t"] == "MESSAGE_CREATE"
        assert d["d"]["content"] == "hello"

    def test_event_has_timestamp(self):
        """Test that Event has a timestamp."""
        event = Event(
            event_type=EventType.MESSAGE_CREATE,
            data={},
        )
        assert event.timestamp > 0

    def test_subscriber_error_does_not_break_others(self, events_module):
        """Test that a failing subscriber doesn't prevent other subscribers from receiving."""
        received = []

        def bad_callback(event, user_ids):
            raise RuntimeError("Subscriber error")

        def good_callback(event, user_ids):
            received.append(event)

        events_module._manager.subscribe(bad_callback)
        events_module._manager.subscribe(good_callback)

        event = Event(event_type=EventType.MESSAGE_CREATE, data={})
        events_module._manager.dispatch(event, user_ids=[123])

        assert len(received) == 1
