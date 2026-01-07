"""
Event types - Enums for event types and gateway intents.
"""

from enum import Enum, IntFlag


class EventType(Enum):
    """Gateway event types."""

    READY = "READY"
    RESUMED = "RESUMED"

    MESSAGE_CREATE = "MESSAGE_CREATE"
    MESSAGE_UPDATE = "MESSAGE_UPDATE"
    MESSAGE_DELETE = "MESSAGE_DELETE"
    MESSAGE_DELETE_BULK = "MESSAGE_DELETE_BULK"
    MESSAGE_ACK = "MESSAGE_ACK"
    MESSAGE_REACTION_ADD = "MESSAGE_REACTION_ADD"
    MESSAGE_REACTION_REMOVE = "MESSAGE_REACTION_REMOVE"
    MESSAGE_REACTION_REMOVE_ALL = "MESSAGE_REACTION_REMOVE_ALL"

    PRESENCE_UPDATE = "PRESENCE_UPDATE"
    TYPING_START = "TYPING_START"
    TYPING_STOP = "TYPING_STOP"
    USER_UPDATE = "USER_UPDATE"

    CHANNEL_CREATE = "CHANNEL_CREATE"
    CHANNEL_UPDATE = "CHANNEL_UPDATE"
    CHANNEL_DELETE = "CHANNEL_DELETE"
    CHANNEL_PINS_UPDATE = "CHANNEL_PINS_UPDATE"

    GUILD_CREATE = "GUILD_CREATE"
    GUILD_UPDATE = "GUILD_UPDATE"
    GUILD_DELETE = "GUILD_DELETE"
    GUILD_BAN_ADD = "GUILD_BAN_ADD"
    GUILD_BAN_REMOVE = "GUILD_BAN_REMOVE"
    GUILD_EMOJIS_UPDATE = "GUILD_EMOJIS_UPDATE"
    GUILD_ROLE_CREATE = "GUILD_ROLE_CREATE"
    GUILD_ROLE_UPDATE = "GUILD_ROLE_UPDATE"
    GUILD_ROLE_DELETE = "GUILD_ROLE_DELETE"

    GUILD_MEMBER_ADD = "GUILD_MEMBER_ADD"
    GUILD_MEMBER_REMOVE = "GUILD_MEMBER_REMOVE"
    GUILD_MEMBER_UPDATE = "GUILD_MEMBER_UPDATE"
    GUILD_MEMBERS_CHUNK = "GUILD_MEMBERS_CHUNK"

    VOICE_STATE_UPDATE = "VOICE_STATE_UPDATE"
    VOICE_SERVER_UPDATE = "VOICE_SERVER_UPDATE"

    WEBHOOKS_UPDATE = "WEBHOOKS_UPDATE"
    INVITE_CREATE = "INVITE_CREATE"
    INVITE_DELETE = "INVITE_DELETE"

    THREAD_CREATE = "THREAD_CREATE"
    THREAD_UPDATE = "THREAD_UPDATE"
    THREAD_DELETE = "THREAD_DELETE"
    THREAD_MEMBER_UPDATE = "THREAD_MEMBER_UPDATE"

    RELATIONSHIP_ADD = "RELATIONSHIP_ADD"
    RELATIONSHIP_REMOVE = "RELATIONSHIP_REMOVE"


class GatewayIntent(IntFlag):
    """Gateway intents for filtering events."""

    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    GUILD_BANS = 1 << 2
    GUILD_EMOJIS = 1 << 3
    GUILD_INTEGRATIONS = 1 << 4
    GUILD_WEBHOOKS = 1 << 5
    GUILD_INVITES = 1 << 6
    GUILD_VOICE_STATES = 1 << 7
    GUILD_PRESENCES = 1 << 8
    GUILD_MESSAGES = 1 << 9
    GUILD_MESSAGE_REACTIONS = 1 << 10
    GUILD_MESSAGE_TYPING = 1 << 11
    DIRECT_MESSAGES = 1 << 12
    DIRECT_MESSAGE_REACTIONS = 1 << 13
    DIRECT_MESSAGE_TYPING = 1 << 14
    MESSAGE_CONTENT = 1 << 15

    @classmethod
    def all_intents(cls) -> int:
        """Get all intents combined."""
        return (
            cls.GUILDS
            | cls.GUILD_MEMBERS
            | cls.GUILD_BANS
            | cls.GUILD_EMOJIS
            | cls.GUILD_INTEGRATIONS
            | cls.GUILD_WEBHOOKS
            | cls.GUILD_INVITES
            | cls.GUILD_VOICE_STATES
            | cls.GUILD_PRESENCES
            | cls.GUILD_MESSAGES
            | cls.GUILD_MESSAGE_REACTIONS
            | cls.GUILD_MESSAGE_TYPING
            | cls.DIRECT_MESSAGES
            | cls.DIRECT_MESSAGE_REACTIONS
            | cls.DIRECT_MESSAGE_TYPING
            | cls.MESSAGE_CONTENT
        )

    @classmethod
    def default_intents(cls) -> int:
        """Get default intents (non-privileged)."""
        return (
            cls.GUILDS
            | cls.GUILD_BANS
            | cls.GUILD_EMOJIS
            | cls.GUILD_INTEGRATIONS
            | cls.GUILD_WEBHOOKS
            | cls.GUILD_INVITES
            | cls.GUILD_VOICE_STATES
            | cls.GUILD_MESSAGES
            | cls.GUILD_MESSAGE_REACTIONS
            | cls.GUILD_MESSAGE_TYPING
            | cls.DIRECT_MESSAGES
            | cls.DIRECT_MESSAGE_REACTIONS
            | cls.DIRECT_MESSAGE_TYPING
        )

    @classmethod
    def privileged_intents(cls) -> int:
        """Get privileged intents (require approval)."""
        return cls.GUILD_MEMBERS | cls.GUILD_PRESENCES | cls.MESSAGE_CONTENT
