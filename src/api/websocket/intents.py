"""
Gateway intents - Intent validation and filtering.
"""

from src.core.events.types import GatewayIntent
from src.core.events.models import Event
from src.core.events.router import filter_by_intents as _filter_by_intents


DEFAULT_INTENTS = GatewayIntent.default_intents()
ALL_INTENTS = GatewayIntent.all_intents()
PRIVILEGED_INTENTS = GatewayIntent.privileged_intents()


def validate_intents(intents: int) -> bool:
    """
    Validate that intents value is valid.

    Args:
        intents: Intent flags to validate

    Returns:
        True if valid
    """
    # Non-integer payloads (strings, lists, dicts, None) must be rejected
    # outright; comparing them against the bitmask would raise instead of
    # returning the documented INVALID_INTENTS close code. Booleans are
    # ints in Python but are never a valid intent mask.
    if not isinstance(intents, int) or isinstance(intents, bool):
        return False
    if intents < 0:
        return False
    if intents > ALL_INTENTS:
        return False
    return True


def has_privileged_intents(intents: int) -> bool:
    """
    Check if intents include privileged intents.

    Args:
        intents: Intent flags to check

    Returns:
        True if privileged intents are requested
    """
    return bool(intents & PRIVILEGED_INTENTS)


def get_privileged_intents_requested(intents: int) -> list:
    """
    Get list of privileged intents requested.

    Args:
        intents: Intent flags

    Returns:
        List of privileged intent names
    """
    requested = []
    if intents & GatewayIntent.GUILD_MEMBERS:
        requested.append("GUILD_MEMBERS")
    if intents & GatewayIntent.GUILD_PRESENCES:
        requested.append("GUILD_PRESENCES")
    if intents & GatewayIntent.MESSAGE_CONTENT:
        requested.append("MESSAGE_CONTENT")
    return requested


def filter_event_by_intents(event: Event, intents: int) -> bool:
    """
    Check if an event should be sent based on intents.

    Args:
        event: Event to check
        intents: User's intent flags

    Returns:
        True if event should be sent
    """
    return _filter_by_intents(event, intents)


def should_include_message_content(intents: int, is_dm: bool) -> bool:
    """
    Check if message content should be included.

    Args:
        intents: User's intent flags
        is_dm: Whether this is a DM

    Returns:
        True if content should be included
    """
    if is_dm:
        return bool(intents & GatewayIntent.DIRECT_MESSAGES)
    return bool(intents & GatewayIntent.MESSAGE_CONTENT)


def get_intent_description(intent: GatewayIntent) -> str:
    """Get human-readable description of an intent."""
    descriptions = {
        GatewayIntent.GUILDS: "Guild events (create, update, delete, channels, roles)",
        GatewayIntent.GUILD_MEMBERS: "Guild member events (privileged)",
        GatewayIntent.GUILD_BANS: "Guild ban events",
        GatewayIntent.GUILD_EMOJIS: "Guild emoji events",
        GatewayIntent.GUILD_INTEGRATIONS: "Guild integration events",
        GatewayIntent.GUILD_WEBHOOKS: "Guild webhook events",
        GatewayIntent.GUILD_INVITES: "Guild invite events",
        GatewayIntent.GUILD_VOICE_STATES: "Voice state events",
        GatewayIntent.GUILD_PRESENCES: "Presence events (privileged)",
        GatewayIntent.GUILD_MESSAGES: "Guild message events",
        GatewayIntent.GUILD_MESSAGE_REACTIONS: "Guild reaction events",
        GatewayIntent.GUILD_MESSAGE_TYPING: "Guild typing events",
        GatewayIntent.DIRECT_MESSAGES: "Direct message events",
        GatewayIntent.DIRECT_MESSAGE_REACTIONS: "DM reaction events",
        GatewayIntent.DIRECT_MESSAGE_TYPING: "DM typing events",
        GatewayIntent.MESSAGE_CONTENT: "Message content (privileged)",
        GatewayIntent.SCHEDULED_EVENTS: "Scheduled event events",
        GatewayIntent.THREADS: "Thread events",
        GatewayIntent.AUTOMOD: "Auto-moderation events",
        GatewayIntent.AUDIT_LOG: "Audit log events",
        GatewayIntent.ARTIFACTS: "Artifact collaboration events (whiteboards, live editors)",
        GatewayIntent.SUMS: "PlexiSum workbook collaboration events",
    }
    return descriptions.get(intent, "Unknown intent")
