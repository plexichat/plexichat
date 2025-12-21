"""Fixtures for events module tests."""

import pytest
import utils.logger as logger
from src.core import events
from src.core.events.types import GatewayIntent
from src.core.events.router import EventRouter


@pytest.fixture(autouse=True)
def setup_events():
    """Setup events module for each test."""
    if not logger._setup_called:
        logger.setup(log_dir="logs", level="WARNING")
    events.setup()
    yield


@pytest.fixture
def event_router():
    """Create an event router instance."""
    return EventRouter()


@pytest.fixture
def sample_message_event():
    """Create a sample message event."""
    return events.create_message_create(
        message_id=123456789,
        channel_id=987654321,
        author_id=111222333,
        content="Hello, world!",
        server_id=444555666,
    )


@pytest.fixture
def sample_dm_event():
    """Create a sample DM message event."""
    return events.create_message_create(
        message_id=123456789,
        channel_id=987654321,
        author_id=111222333,
        content="Hello via DM!",
        server_id=None,
    )


@pytest.fixture
def sample_presence_event():
    """Create a sample presence event."""
    return events.create_presence_update(
        user_id=111222333,
        status="online",
        activities=[{"type": 0, "name": "Testing"}],
    )


@pytest.fixture
def sample_typing_event():
    """Create a sample typing event."""
    return events.create_typing_start(
        user_id=111222333,
        channel_id=987654321,
        server_id=444555666,
    )


@pytest.fixture
def sample_guild_event():
    """Create a sample guild event."""
    return events.create_guild_create(
        server_id=444555666,
        name="Test Server",
        owner_id=111222333,
        member_count=10,
    )


@pytest.fixture
def all_intents():
    """Get all intents combined."""
    return GatewayIntent.all_intents()


@pytest.fixture
def default_intents():
    """Get default intents."""
    return GatewayIntent.default_intents()


@pytest.fixture
def no_intents():
    """Get no intents."""
    return 0
