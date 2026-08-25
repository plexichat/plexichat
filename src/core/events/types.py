"""
Event types - Enums for event types and gateway intents.
"""

from enum import Enum, IntFlag


class EventType(Enum):
    """Gateway event types."""

    READY = "READY"
    RESUMED = "RESUMED"

    # Server -> client error frame for a specific request that could not
    # be fulfilled (denied permission, internal failure, ...). Carries a
    # stable machine code so clients can distinguish outcomes instead of
    # guessing from silence.
    ERROR = "ERROR"

    MESSAGE_CREATE = "MESSAGE_CREATE"
    MESSAGE_UPDATE = "MESSAGE_UPDATE"
    MESSAGE_DELETE = "MESSAGE_DELETE"
    MESSAGE_DELETE_BULK = "MESSAGE_DELETE_BULK"
    MESSAGE_ACK = "MESSAGE_ACK"
    MESSAGE_REACTION_ADD = "MESSAGE_REACTION_ADD"
    MESSAGE_REACTION_REMOVE = "MESSAGE_REACTION_REMOVE"
    MESSAGE_REACTION_REMOVE_ALL = "MESSAGE_REACTION_REMOVE_ALL"
    MESSAGE_REACTION_REMOVE_EMOJI = "MESSAGE_REACTION_REMOVE_EMOJI"

    POLL_UPDATE = "POLL_UPDATE"

    PRESENCE_UPDATE = "PRESENCE_UPDATE"
    TYPING_START = "TYPING_START"
    TYPING_STOP = "TYPING_STOP"
    USER_UPDATE = "USER_UPDATE"
    SECURITY_LOGOUT = "SECURITY_LOGOUT"
    SECURITY_ALERT = "SECURITY_ALERT"

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
    GUILD_STICKERS_UPDATE = "GUILD_STICKERS_UPDATE"
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

    INTERACTION_CREATE = "INTERACTION_CREATE"

    NOTIFICATION_CREATE = "NOTIFICATION_CREATE"
    NOTIFICATION_UPDATE = "NOTIFICATION_UPDATE"
    NOTIFICATION_DELETE = "NOTIFICATION_DELETE"

    RATCHET_UPDATE = "RATCHET_UPDATE"

    MESSAGE_BOOKMARK_ADD = "MESSAGE_BOOKMARK_ADD"
    MESSAGE_BOOKMARK_REMOVE = "MESSAGE_BOOKMARK_REMOVE"

    ARTIFACT_CREATE = "ARTIFACT_CREATE"
    ARTIFACT_UPDATE = "ARTIFACT_UPDATE"
    ARTIFACT_DELETE = "ARTIFACT_DELETE"
    ARTIFACT_OP = "ARTIFACT_OP"

    # PlexiSum workbook mutations (cells, structure, comments, formats...).
    WORKBOOK_UPDATE = "WORKBOOK_UPDATE"
    SUM_OPERATION = "SUM_OPERATION"
    SUM_PRESENCE = "SUM_PRESENCE"


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
    SCHEDULED_EVENTS = 1 << 16
    THREADS = 1 << 17
    AUTOMOD = 1 << 18
    AUDIT_LOG = 1 << 19
    ARTIFACTS = 1 << 20
    SUMS = 1 << 21

    @classmethod
    def all_intents(cls) -> int:
        """Get all intents combined.

        Derived from the enum itself so the mask can never drift from the
        declared intent set (a previous hand-written list silently dropped
        SCHEDULED_EVENTS and THREADS).
        """
        value = 0
        for member in cls:
            value |= int(member)
        return value

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
            | cls.ARTIFACTS
            | cls.SUMS
        )

    @classmethod
    def privileged_intents(cls) -> int:
        """Get privileged intents (require approval)."""
        return cls.GUILD_MEMBERS | cls.GUILD_PRESENCES | cls.MESSAGE_CONTENT
