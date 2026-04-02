"""
Event routing - Logic for determining which users receive which events.
"""

from typing import Optional, List, Set

from .types import EventType, GatewayIntent
from .models import Event


EVENT_INTENT_MAP = {
    EventType.GUILD_CREATE: GatewayIntent.GUILDS,
    EventType.GUILD_UPDATE: GatewayIntent.GUILDS,
    EventType.GUILD_DELETE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_CREATE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_UPDATE: GatewayIntent.GUILDS,
    EventType.GUILD_ROLE_DELETE: GatewayIntent.GUILDS,
    EventType.CHANNEL_CREATE: GatewayIntent.GUILDS,
    EventType.CHANNEL_UPDATE: GatewayIntent.GUILDS,
    EventType.CHANNEL_DELETE: GatewayIntent.GUILDS,
    EventType.CHANNEL_PINS_UPDATE: GatewayIntent.GUILDS,
    EventType.THREAD_CREATE: GatewayIntent.GUILDS,
    EventType.THREAD_UPDATE: GatewayIntent.GUILDS,
    EventType.THREAD_DELETE: GatewayIntent.GUILDS,
    EventType.GUILD_MEMBER_ADD: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBER_REMOVE: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBER_UPDATE: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_MEMBERS_CHUNK: GatewayIntent.GUILD_MEMBERS,
    EventType.GUILD_BAN_ADD: GatewayIntent.GUILD_BANS,
    EventType.GUILD_BAN_REMOVE: GatewayIntent.GUILD_BANS,
    EventType.GUILD_EMOJIS_UPDATE: GatewayIntent.GUILD_EMOJIS,
    EventType.GUILD_STICKERS_UPDATE: GatewayIntent.GUILD_EMOJIS,
    EventType.WEBHOOKS_UPDATE: GatewayIntent.GUILD_WEBHOOKS,
    EventType.INVITE_CREATE: GatewayIntent.GUILD_INVITES,
    EventType.INVITE_DELETE: GatewayIntent.GUILD_INVITES,
    EventType.VOICE_STATE_UPDATE: GatewayIntent.GUILD_VOICE_STATES,
    EventType.PRESENCE_UPDATE: GatewayIntent.GUILD_PRESENCES,
    EventType.MESSAGE_REACTION_ADD: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE_ALL: GatewayIntent.GUILD_MESSAGE_REACTIONS,
    EventType.TYPING_START: GatewayIntent.GUILD_MESSAGE_TYPING,
}

DM_EVENT_INTENT_MAP = {
    EventType.MESSAGE_CREATE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_UPDATE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_DELETE: GatewayIntent.DIRECT_MESSAGES,
    EventType.MESSAGE_REACTION_ADD: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    EventType.MESSAGE_REACTION_REMOVE: GatewayIntent.DIRECT_MESSAGE_REACTIONS,
    EventType.TYPING_START: GatewayIntent.DIRECT_MESSAGE_TYPING,
}

GUILD_MESSAGE_EVENTS = {
    EventType.MESSAGE_CREATE,
    EventType.MESSAGE_UPDATE,
    EventType.MESSAGE_DELETE,
    EventType.MESSAGE_DELETE_BULK,
}


def get_required_intent(event_type: EventType) -> Optional[GatewayIntent]:
    """
    Get the required intent for an event type.

    Args:
        event_type: The event type

    Returns:
        Required intent or None if no intent required
    """
    return EVENT_INTENT_MAP.get(event_type)


def get_dm_intent(event_type: EventType) -> Optional[GatewayIntent]:
    """
    Get the required intent for a DM event type.

    Args:
        event_type: The event type

    Returns:
        Required DM intent or None
    """
    return DM_EVENT_INTENT_MAP.get(event_type)


def filter_by_intents(event: Event, intents: int) -> bool:
    """
    Check if an event passes intent filtering.

    Args:
        event: Event to check
        intents: User's intent flags

    Returns:
        True if event should be sent to user
    """
    is_dm = event.server_id is None and event.channel_id is not None

    if is_dm:
        required = get_dm_intent(event.event_type)
        if required is not None:
            return bool(intents & required)
        return True

    if event.event_type in GUILD_MESSAGE_EVENTS:
        if not (intents & GatewayIntent.GUILD_MESSAGES):
            return False
        return True

    required = get_required_intent(event.event_type)
    if required is not None:
        return bool(intents & required)

    return True


class EventRouter:
    """Routes events to appropriate users based on context."""

    def __init__(
        self,
        relationships_module=None,
        servers_module=None,
        messaging_module=None,
    ):
        """
        Initialize the event router.

        Args:
            relationships_module: For friend/block relationships
            servers_module: For server membership
            messaging_module: For DM participants
        """
        self._relationships = relationships_module
        self._servers = servers_module
        self._messaging = messaging_module

    def get_recipients(
        self,
        event: Event,
        user_ids: Optional[List[int]] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        exclude_user_ids: Optional[List[int]] = None,
    ) -> List[int]:
        """
        Determine which users should receive an event.

        Args:
            event: Event to route
            user_ids: Explicit user IDs (overrides routing)
            server_id: Server ID for server events
            channel_id: Channel ID for channel events
            exclude_user_ids: Users to exclude

        Returns:
            List of user IDs to receive the event
        """
        exclude_set: Set[int] = set(exclude_user_ids or [])

        if user_ids is not None:
            return [uid for uid in user_ids if uid not in exclude_set]

        recipients: Set[int] = set()

        effective_server_id = server_id or event.server_id
        effective_channel_id = channel_id or event.channel_id

        if effective_server_id and self._servers:
            recipients.update(self._get_server_member_ids(effective_server_id))
        elif effective_channel_id and self._messaging:
            recipients.update(self._get_dm_participant_ids(effective_channel_id))

        if event.event_type == EventType.PRESENCE_UPDATE:
            if event.user_id and self._relationships:
                recipients.update(self._get_presence_recipients(event.user_id))

        return [uid for uid in recipients if uid not in exclude_set]

    def _get_server_member_ids(self, server_id: int) -> List[int]:
        """Get all member user IDs for a server."""
        if not self._servers:
            return []
        try:
            # ServerManager has get_member_user_ids
            members = self._servers.get_member_user_ids(server_id)
            return members if members else []
        except Exception:
            return []

    def _get_dm_participant_ids(self, channel_id: int) -> List[int]:
        """Get participant user IDs for a DM/group conversation."""
        if not self._messaging:
            return []
        try:
            participants = self._messaging.get_participant_ids(channel_id)
            return participants if participants else []
        except Exception:
            return []

    def _get_presence_recipients(self, user_id: int) -> List[int]:
        """Get users who should receive presence updates for a user."""
        recipients: Set[int] = set()

        if self._relationships:
            try:
                friend_ids = self._relationships.get_friend_ids(user_id)
                if friend_ids:
                    recipients.update(friend_ids)
            except Exception:
                pass

        if self._servers:
            try:
                server_ids = self._servers.get_user_server_ids(user_id)
                for sid in server_ids or []:
                    member_ids = self._get_server_member_ids(sid)
                    recipients.update(member_ids)
            except Exception:
                pass

        recipients.discard(user_id)

        # Filter out blocked users
        if self._relationships and recipients:
            try:
                blocked_ids = self._relationships.get_all_blocked_ids(user_id)
                for bid in blocked_ids:
                    recipients.discard(bid)
            except Exception:
                pass

        return list(recipients)
