"""
Bot gateway intents - Controls which gateway events a bot receives.

Extends the base GatewayIntent from src.core.events.types with
bot-specific intent management, privilege classification, and
intent validation for the bot application framework.

Intents follow a privileged/unprivileged model:
- Unprivileged intents: Available to all bots by default
- Privileged intents: Require explicit approval or verification
  (e.g., message content, server member lists)
"""

from typing import Dict, Any, List, Set


# Import the canonical GatewayIntent definition from the events module
from src.core.events.types import GatewayIntent

# Intent flags are defined on GatewayIntent in src.core.events.types


# Privileged intents that require explicit approval
PRIVILEGED_INTENTS: Set[GatewayIntent] = {
    GatewayIntent.GUILD_MEMBERS,
    GatewayIntent.GUILD_PRESENCES,
    GatewayIntent.MESSAGE_CONTENT,
}

# Default intents granted to all bots
DEFAULT_BOT_INTENTS: Set[GatewayIntent] = {
    GatewayIntent.GUILDS,
    GatewayIntent.GUILD_BANS,
    GatewayIntent.GUILD_EMOJIS,
    GatewayIntent.GUILD_WEBHOOKS,
    GatewayIntent.GUILD_INVITES,
    GatewayIntent.GUILD_VOICE_STATES,
    GatewayIntent.GUILD_MESSAGES,
    GatewayIntent.GUILD_MESSAGE_REACTIONS,
    GatewayIntent.GUILD_MESSAGE_TYPING,
    GatewayIntent.DIRECT_MESSAGES,
    GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    GatewayIntent.DIRECT_MESSAGE_TYPING,
}
DEFAULT_BOT_INTENTS.add(GatewayIntent.SCHEDULED_EVENTS)
DEFAULT_BOT_INTENTS.add(GatewayIntent.THREADS)


def is_privileged(intent: GatewayIntent) -> bool:
    """Check if an intent requires privileged approval."""
    return intent in PRIVILEGED_INTENTS


def validate_intent_request(
    requested: GatewayIntent,
    is_verified_bot: bool = False,
) -> Dict[str, Any]:
    """
    Validate a bot's intent request.

    Returns a dict with:
    - allowed: The intents that are approved
    - denied: The privileged intents that require verification
    - warnings: List of warning messages
    """
    warnings = []
    denied = GatewayIntent(0)
    allowed = requested

    for priv_intent in PRIVILEGED_INTENTS:
        if requested & priv_intent:
            if not is_verified_bot:
                allowed &= ~priv_intent
                denied |= priv_intent
                warnings.append(f"Intent {priv_intent.name} requires bot verification")

    return {
        "allowed": allowed,
        "denied": denied,
        "warnings": warnings,
    }


def get_intent_description(intent: GatewayIntent) -> str:
    """Get human-readable description for a gateway intent."""
    descriptions = {
        GatewayIntent.GUILDS: "Server create/update/delete events",
        GatewayIntent.GUILD_MEMBERS: "Member join/leave/update events (privileged)",
        GatewayIntent.GUILD_BANS: "Ban add/remove events",
        GatewayIntent.GUILD_EMOJIS: "Emoji create/update/delete events",
        GatewayIntent.GUILD_WEBHOOKS: "Webhook create/update/delete events",
        GatewayIntent.GUILD_INVITES: "Invite create/delete events",
        GatewayIntent.GUILD_VOICE_STATES: "Voice state update events",
        GatewayIntent.GUILD_PRESENCES: "Presence update events (privileged)",
        GatewayIntent.GUILD_MESSAGES: "Message create/update/delete events",
        GatewayIntent.GUILD_MESSAGE_REACTIONS: "Reaction add/remove events",
        GatewayIntent.GUILD_MESSAGE_TYPING: "Typing start events",
        GatewayIntent.MESSAGE_CONTENT: "Access to message content (privileged)",
        GatewayIntent.DIRECT_MESSAGES: "DM message events",
        GatewayIntent.DIRECT_MESSAGE_REACTIONS: "DM reaction events",
        GatewayIntent.DIRECT_MESSAGE_TYPING: "DM typing events",
    }
    descriptions[GatewayIntent.SCHEDULED_EVENTS] = (
        "Scheduled event create/update/delete events"
    )
    descriptions[GatewayIntent.AUTOMOD] = "Auto-moderation action events"
    descriptions[GatewayIntent.THREADS] = "Thread create/update/delete events"
    descriptions[GatewayIntent.AUDIT_LOG] = "Audit log entry events"
    return descriptions.get(intent, "Unknown intent")


def compute_intent_value(intents: List[GatewayIntent]) -> int:
    """Compute the integer bitmask value from a list of intents."""
    result = GatewayIntent(0)
    for intent in intents:
        result |= intent
    return int(result)


def parse_intent_value(value: int) -> Set[GatewayIntent]:
    """Parse an integer bitmask into a set of GatewayIntent flags."""
    result = set()
    for intent in GatewayIntent:
        if value & intent:
            result.add(intent)
    return result


def intent_to_event_map() -> Dict[GatewayIntent, List[str]]:
    """Map gateway intents to their corresponding event names."""
    mapping: Dict[GatewayIntent, List[str]] = {
        GatewayIntent.GUILDS: [
            "GUILD_CREATE",
            "GUILD_UPDATE",
            "GUILD_DELETE",
            "CHANNEL_CREATE",
            "CHANNEL_UPDATE",
            "CHANNEL_DELETE",
        ],
        GatewayIntent.GUILD_MEMBERS: [
            "GUILD_MEMBER_ADD",
            "GUILD_MEMBER_REMOVE",
            "GUILD_MEMBER_UPDATE",
        ],
        GatewayIntent.GUILD_BANS: [
            "GUILD_BAN_ADD",
            "GUILD_BAN_REMOVE",
        ],
        GatewayIntent.GUILD_EMOJIS: [
            "GUILD_EMOJIS_UPDATE",
            "GUILD_STICKERS_UPDATE",
        ],
        GatewayIntent.GUILD_WEBHOOKS: [
            "WEBHOOKS_UPDATE",
        ],
        GatewayIntent.GUILD_INVITES: [
            "INVITE_CREATE",
            "INVITE_DELETE",
        ],
        GatewayIntent.GUILD_VOICE_STATES: [
            "VOICE_STATE_UPDATE",
        ],
        GatewayIntent.GUILD_PRESENCES: [
            "PRESENCE_UPDATE",
        ],
        GatewayIntent.GUILD_MESSAGES: [
            "MESSAGE_CREATE",
            "MESSAGE_UPDATE",
            "MESSAGE_DELETE",
            "MESSAGE_DELETE_BULK",
        ],
        GatewayIntent.GUILD_MESSAGE_REACTIONS: [
            "MESSAGE_REACTION_ADD",
            "MESSAGE_REACTION_REMOVE",
            "MESSAGE_REACTION_REMOVE_ALL",
        ],
        GatewayIntent.GUILD_MESSAGE_TYPING: [
            "TYPING_START",
        ],
        GatewayIntent.MESSAGE_CONTENT: [],  # Not an event - filters message content
        GatewayIntent.DIRECT_MESSAGES: [
            "DM_MESSAGE_CREATE",
            "DM_MESSAGE_UPDATE",
            "DM_MESSAGE_DELETE",
        ],
        GatewayIntent.DIRECT_MESSAGE_REACTIONS: [
            "DM_REACTION_ADD",
            "DM_REACTION_REMOVE",
        ],
        GatewayIntent.DIRECT_MESSAGE_TYPING: [
            "DM_TYPING_START",
        ],
    }
    mapping[GatewayIntent.SCHEDULED_EVENTS] = [
        "EVENT_CREATE",
        "EVENT_UPDATE",
        "EVENT_DELETE",
    ]
    mapping[GatewayIntent.AUTOMOD] = [
        "AUTOMOD_ACTION_CREATE",
    ]
    mapping[GatewayIntent.THREADS] = [
        "THREAD_CREATE",
        "THREAD_UPDATE",
        "THREAD_DELETE",
    ]
    mapping[GatewayIntent.AUDIT_LOG] = [
        "AUDIT_LOG_ENTRY_CREATE",
    ]
    return mapping


def get_events_for_intents(intents: GatewayIntent) -> List[str]:
    """Get all event names that should be dispatched for the given intents."""
    event_map = intent_to_event_map()
    events = []
    for intent, event_names in event_map.items():
        if intents & intent:
            events.extend(event_names)
    return events
