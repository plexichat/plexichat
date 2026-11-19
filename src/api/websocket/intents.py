"""
Gateway intents - Intent validation and filtering.
"""

from src.core.events.types import GatewayIntent
from src.core.events.models import Event
from src.core.events.router import (
    filter_by_intents as _filter_by_intents,
    get_dm_intent as _get_dm_intent,
    get_required_intent as _get_required_intent,
)

from .intent_decision_cache import IntentDecisionCache

# Shared fan-out memo. A single event is filtered once per recipient
# connection (and once per worker on the cross-worker replay path), but the
# answer depends only on (event type, scope class, intent mask). See
# ``intent_decision_cache`` for the identity/bounding/fail-closed rationale.
_intent_decision_cache = IntentDecisionCache()


def get_intent_decision_cache_stats() -> dict:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _intent_decision_cache.stats()


def clear_intent_decision_cache() -> None:
    """Invalidate every cached decision (intent-rule change / test isolation)."""
    _intent_decision_cache.clear()


DEFAULT_INTENTS = GatewayIntent.default_intents()
ALL_INTENTS = GatewayIntent.all_intents()
PRIVILEGED_INTENTS = GatewayIntent.privileged_intents()
#: The exact set of bits the protocol defines. Any other bit is unknown
#: and must be rejected rather than silently ignored.
ALLOWED_INTENTS = GatewayIntent.all_intents()


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
    if intents & ~ALLOWED_INTENTS:
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


def _compute_filter_decision(event: Event, intents: int) -> bool:
    """Evaluate the full intent gate (uncached). Caller has validated ``intents``."""
    # The upstream router classifies any event with no ``server_id`` but a
    # ``channel_id`` as a DM and consults only the DM intent map. Event types
    # that exist only in the guild map (artifacts, PlexiSum workbooks,
    # presence, ...) then fall through to "no intent required" and leak to
    # clients that never requested that intent. Enforce the guild-scope
    # requirement here as well, but skip event types that have a legitimate
    # DM counterpart (typing, messages, reactions) — those are correctly
    # gated by the DM map and must not additionally require a guild intent.
    if _get_dm_intent(event.event_type) is None:
        required = _get_required_intent(event.event_type)
        if required is not None and not (intents & required):
            return False

    return bool(_filter_by_intents(event, intents))


def filter_event_by_intents(event: Event, intents: int) -> bool:
    """
    Check if an event should be sent based on intents.

    Args:
        event: Event to check
        intents: User's intent flags

    Returns:
        True if event should be sent
    """
    # Fail closed at the point of use. IDENTIFY validates the mask when a
    # session is created, but a resumed/persisted session can carry a stale or
    # corrupted value; a negative, boolean or out-of-range mask must never be
    # treated as "all intents granted" by the bitwise checks downstream.
    # This check runs BEFORE any cache lookup and is never memoised away.
    if not validate_intents(intents):
        return False

    # Scope class is part of the cache key: the gateway keeps *separate*
    # guild/DM intent tables, so two events of the same type but different
    # scope must never share a decision. Omitting this from the key would let
    # a DM decision be served for a guild event (or vice versa) -- an
    # audience/intent-gate leak.
    is_dm = event.server_id is None and event.channel_id is not None
    cached = _intent_decision_cache.get(event.event_type, is_dm, intents)
    if cached is not None:
        return cached

    decision = _compute_filter_decision(event, intents)
    _intent_decision_cache.put(event.event_type, is_dm, intents, decision)
    return decision


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
