"""Fixtures for events module tests."""

import pytest
import utils.logger as logger
from src.core import events
from src.core.events.types import GatewayIntent
from src.core.events.router import EventRouter


@pytest.fixture(autouse=True)
def setup_events(db, rel_manager, server_manager, messaging_manager):
    """Setup the events module with production-equivalent routing modules.

    Production wires relationships/servers/messaging into
    ``events.setup()`` (see ``src/server/initializer.py``); wiring NONE of
    them here made every recipient-resolution decision in these tests run
    through the fail-open "no routing module" bypass instead of the real
    router. The teardown clears the module globals so no stale manager
    (pinned to this test's deleted database) leaks into later suites.
    """
    if not logger._setup_called:
        logger.setup(log_dir="logs", level="WARNING")
    events.setup(
        relationships_module=rel_manager,
        servers_module=server_manager,
        messaging_module=messaging_manager,
    )
    yield
    events._manager = None
    events._setup_complete = False


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
