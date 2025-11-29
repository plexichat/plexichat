"""
Events Module - Zero-friction API for event types and dispatching.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import events
    events.setup()

    # In any other file (use directly)
    from src.core import events
    event = events.create_message_create(message)
    events.dispatch(event, user_ids=[1, 2, 3])
"""

from typing import Optional, List, Callable, Any, Dict

from .types import EventType, GatewayIntent
from .models import (
    Event,
    ReadyEvent,
    MessageEvent,
    PresenceEvent,
    TypingEvent,
    ChannelEvent,
    GuildEvent,
    GuildMemberEvent,
    VoiceStateEvent,
    ReactionEvent,
)
from .payloads import (
    create_ready_event,
    create_message_create,
    create_message_update,
    create_message_delete,
    create_presence_update,
    create_typing_start,
    create_channel_create,
    create_channel_update,
    create_channel_delete,
    create_guild_create,
    create_guild_update,
    create_guild_delete,
    create_guild_member_add,
    create_guild_member_remove,
    create_guild_member_update,
    create_voice_state_update,
    create_reaction_add,
    create_reaction_remove,
)

__all__ = [
    "setup",
    "dispatch",
    "subscribe",
    "unsubscribe",
    "get_required_intent",
    "filter_by_intents",
    "is_setup",
    "EventType",
    "GatewayIntent",
    "Event",
    "ReadyEvent",
    "MessageEvent",
    "PresenceEvent",
    "TypingEvent",
    "ChannelEvent",
    "GuildEvent",
    "GuildMemberEvent",
    "VoiceStateEvent",
    "ReactionEvent",
    "create_ready_event",
    "create_message_create",
    "create_message_update",
    "create_message_delete",
    "create_presence_update",
    "create_typing_start",
    "create_channel_create",
    "create_channel_update",
    "create_channel_delete",
    "create_guild_create",
    "create_guild_update",
    "create_guild_delete",
    "create_guild_member_add",
    "create_guild_member_remove",
    "create_guild_member_update",
    "create_voice_state_update",
    "create_reaction_add",
    "create_reaction_remove",
]

_manager = None
_setup_complete = False


def setup(
    relationships_module=None,
    servers_module=None,
    messaging_module=None,
) -> None:
    """
    Initialize the events module.

    Args:
        relationships_module: Optional relationships module for routing
        servers_module: Optional servers module for routing
        messaging_module: Optional messaging module for routing
    """
    global _manager, _setup_complete

    from .manager import EventManager

    _manager = EventManager(
        relationships_module=relationships_module,
        servers_module=servers_module,
        messaging_module=messaging_module,
    )
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Events module not initialized. Call events.setup() first."
        )
    return _manager


def dispatch(
    event: Event,
    user_ids: Optional[List[int]] = None,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    exclude_user_ids: Optional[List[int]] = None,
) -> int:
    """
    Dispatch an event to connected users.

    Args:
        event: Event to dispatch
        user_ids: Specific user IDs to send to (if None, uses routing)
        server_id: Server ID for server-scoped events
        channel_id: Channel ID for channel-scoped events
        exclude_user_ids: User IDs to exclude from dispatch

    Returns:
        Number of users the event was dispatched to
    """
    return _get_manager().dispatch(
        event=event,
        user_ids=user_ids,
        server_id=server_id,
        channel_id=channel_id,
        exclude_user_ids=exclude_user_ids,
    )


def subscribe(callback: Callable[[Event, List[int]], None]) -> None:
    """
    Subscribe to event dispatches.

    Args:
        callback: Function called with (event, user_ids) when events dispatch
    """
    _get_manager().subscribe(callback)


def unsubscribe(callback: Callable[[Event, List[int]], None]) -> None:
    """
    Unsubscribe from event dispatches.

    Args:
        callback: Previously subscribed callback
    """
    _get_manager().unsubscribe(callback)


def get_required_intent(event_type: EventType) -> Optional[GatewayIntent]:
    """
    Get the required intent for an event type.

    Args:
        event_type: The event type

    Returns:
        Required intent or None if no intent required
    """
    from .router import get_required_intent as _get_intent
    return _get_intent(event_type)


def filter_by_intents(event: Event, intents: int) -> bool:
    """
    Check if an event passes intent filtering.

    Args:
        event: Event to check
        intents: User's intent flags

    Returns:
        True if event should be sent to user
    """
    from .router import filter_by_intents as _filter
    return _filter(event, intents)


def is_setup() -> bool:
    """Check if the events module is initialized."""
    return _setup_complete
