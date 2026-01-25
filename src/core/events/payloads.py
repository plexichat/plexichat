"""
Event payload builders - Helper functions to create event payloads.
"""

from typing import Optional, Dict, Any, List
import time

from .types import EventType
from .models import (
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


def _get_timestamp() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def _str_id(value: Optional[int]) -> Optional[str]:
    """Convert int ID to string for JSON serialization."""
    return str(value) if value is not None else None


def create_ready_event(
    session_id: str,
    user: Dict[str, Any],
    guilds: List[Dict[str, Any]],
    resume_gateway_url: str = "",
) -> ReadyEvent:
    """Create a READY event."""
    return ReadyEvent(
        event_type=EventType.READY,
        data={
            "v": 10,
            "user": user,
            "guilds": guilds,
            "session_id": session_id,
            "resume_gateway_url": resume_gateway_url,
        },
        session_id=session_id,
        user=user,
        guilds=guilds,
        resume_gateway_url=resume_gateway_url,
    )


def create_message_create(
    message_id: int,
    channel_id: int,
    author_id: int,
    content: str,
    server_id: Optional[int] = None,
    author: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    embeds: Optional[List[Dict[str, Any]]] = None,
    mentions: Optional[List[Dict[str, Any]]] = None,
    pinned: bool = False,
    timestamp: Optional[int] = None,
) -> MessageEvent:
    """Create a MESSAGE_CREATE event."""
    ts = timestamp or _get_timestamp()
    data = {
        "id": _str_id(message_id),
        "channel_id": _str_id(channel_id),
        "author": author or {"id": _str_id(author_id)},
        "content": content,
        "timestamp": ts,
        "edited_timestamp": None,
        "tts": False,
        "mention_everyone": False,
        "mentions": mentions or [],
        "mention_roles": [],
        "attachments": attachments or [],
        "embeds": embeds or [],
        "pinned": pinned,
        "type": 0,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)

    return MessageEvent(
        event_type=EventType.MESSAGE_CREATE,
        data=data,
        timestamp=ts,
        server_id=server_id,
        channel_id=channel_id,
        user_id=author_id,
        message_id=message_id,
        content=content,
        author=author or {"id": _str_id(author_id)},
        attachments=attachments or [],
        embeds=embeds or [],
        mentions=mentions or [],
        pinned=pinned,
    )


def create_message_update(
    message_id: int,
    channel_id: int,
    content: Optional[str] = None,
    server_id: Optional[int] = None,
    author: Optional[Dict[str, Any]] = None,
    edited_timestamp: Optional[int] = None,
) -> MessageEvent:
    """Create a MESSAGE_UPDATE event."""
    ts = _get_timestamp()
    data = {
        "id": _str_id(message_id),
        "channel_id": _str_id(channel_id),
        "edited_timestamp": edited_timestamp or ts,
    }
    if content is not None:
        data["content"] = content
    if author:
        data["author"] = author
    if server_id:
        data["guild_id"] = _str_id(server_id)

    return MessageEvent(
        event_type=EventType.MESSAGE_UPDATE,
        data=data,
        timestamp=ts,
        server_id=server_id,
        channel_id=channel_id,
        message_id=message_id,
        content=content,
        author=author,
        edited_timestamp=edited_timestamp or ts,
    )


def create_message_delete(
    message_id: int,
    channel_id: int,
    server_id: Optional[int] = None,
) -> MessageEvent:
    """Create a MESSAGE_DELETE event."""
    data = {
        "id": _str_id(message_id),
        "channel_id": _str_id(channel_id),
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)

    return MessageEvent(
        event_type=EventType.MESSAGE_DELETE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        message_id=message_id,
    )


def create_presence_update(
    user_id: int,
    status: str,
    activities: Optional[List[Dict[str, Any]]] = None,
    client_status: Optional[Dict[str, str]] = None,
    server_id: Optional[int] = None,
    user: Optional[Dict[str, Any]] = None,
) -> PresenceEvent:
    """Create a PRESENCE_UPDATE event."""
    data = {
        "user": user or {"id": _str_id(user_id)},
        "status": status,
        "activities": activities or [],
        "client_status": client_status or {"desktop": status},
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)

    return PresenceEvent(
        event_type=EventType.PRESENCE_UPDATE,
        data=data,
        server_id=server_id,
        user_id=user_id,
        status=status,
        activities=activities or [],
        client_status=client_status,
    )


def create_typing_start(
    user_id: int,
    channel_id: int,
    server_id: Optional[int] = None,
    member: Optional[Dict[str, Any]] = None,
) -> TypingEvent:
    """Create a TYPING_START event."""
    ts = _get_timestamp()
    data = {
        "user_id": _str_id(user_id),
        "channel_id": _str_id(channel_id),
        "timestamp": ts // 1000,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if member:
        data["member"] = member

    return TypingEvent(
        event_type=EventType.TYPING_START,
        data=data,
        timestamp=ts,
        server_id=server_id,
        channel_id=channel_id,
        user_id=user_id,
    )


def create_channel_create(
    channel_id: int,
    channel_type: int,
    server_id: Optional[int] = None,
    name: Optional[str] = None,
    position: int = 0,
    topic: Optional[str] = None,
    nsfw: bool = False,
    parent_id: Optional[int] = None,
) -> ChannelEvent:
    """Create a CHANNEL_CREATE event."""
    data = {
        "id": _str_id(channel_id),
        "type": channel_type,
        "position": position,
        "nsfw": nsfw,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if name:
        data["name"] = name
    if topic:
        data["topic"] = topic
    if parent_id:
        data["parent_id"] = _str_id(parent_id)

    return ChannelEvent(
        event_type=EventType.CHANNEL_CREATE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        name=name,
        channel_type=channel_type,
        position=position,
        topic=topic,
        nsfw=nsfw,
        parent_id=parent_id,
    )


def create_channel_update(
    channel_id: int,
    channel_type: int,
    server_id: Optional[int] = None,
    name: Optional[str] = None,
    position: int = 0,
    topic: Optional[str] = None,
    nsfw: bool = False,
    parent_id: Optional[int] = None,
) -> ChannelEvent:
    """Create a CHANNEL_UPDATE event."""
    data = {
        "id": _str_id(channel_id),
        "type": channel_type,
        "position": position,
        "nsfw": nsfw,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if name:
        data["name"] = name
    if topic:
        data["topic"] = topic
    if parent_id:
        data["parent_id"] = _str_id(parent_id)

    return ChannelEvent(
        event_type=EventType.CHANNEL_UPDATE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        name=name,
        channel_type=channel_type,
        position=position,
        topic=topic,
        nsfw=nsfw,
        parent_id=parent_id,
    )


def create_channel_delete(
    channel_id: int,
    channel_type: int,
    server_id: Optional[int] = None,
    name: Optional[str] = None,
) -> ChannelEvent:
    """Create a CHANNEL_DELETE event."""
    data = {
        "id": _str_id(channel_id),
        "type": channel_type,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if name:
        data["name"] = name

    return ChannelEvent(
        event_type=EventType.CHANNEL_DELETE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        name=name,
        channel_type=channel_type,
    )


def create_guild_create(
    server_id: int,
    name: str,
    owner_id: int,
    icon: Optional[str] = None,
    member_count: int = 0,
    channels: Optional[List[Dict[str, Any]]] = None,
    roles: Optional[List[Dict[str, Any]]] = None,
    members: Optional[List[Dict[str, Any]]] = None,
) -> GuildEvent:
    """Create a GUILD_CREATE event."""
    data = {
        "id": _str_id(server_id),
        "name": name,
        "owner_id": _str_id(owner_id),
        "member_count": member_count,
        "channels": channels or [],
        "roles": roles or [],
        "members": members or [],
    }
    if icon:
        data["icon"] = icon

    return GuildEvent(
        event_type=EventType.GUILD_CREATE,
        data=data,
        server_id=server_id,
        name=name,
        icon=icon,
        owner_id=owner_id,
        member_count=member_count,
        channels=channels or [],
        roles=roles or [],
    )


def create_guild_update(
    server_id: int,
    name: Optional[str] = None,
    owner_id: Optional[int] = None,
    icon: Optional[str] = None,
) -> GuildEvent:
    """Create a GUILD_UPDATE event."""
    data = {"id": _str_id(server_id)}
    if name:
        data["name"] = name
    if owner_id:
        data["owner_id"] = _str_id(owner_id)
    if icon:
        data["icon"] = icon

    return GuildEvent(
        event_type=EventType.GUILD_UPDATE,
        data=data,
        server_id=server_id,
        name=name,
        icon=icon,
        owner_id=owner_id,
    )


def create_guild_delete(server_id: int) -> GuildEvent:
    """Create a GUILD_DELETE event."""
    return GuildEvent(
        event_type=EventType.GUILD_DELETE,
        data={"id": _str_id(server_id)},
        server_id=server_id,
    )


def create_guild_member_add(
    server_id: int,
    user_id: int,
    user: Optional[Dict[str, Any]] = None,
    nick: Optional[str] = None,
    roles: Optional[List[int]] = None,
    joined_at: Optional[int] = None,
) -> GuildMemberEvent:
    """Create a GUILD_MEMBER_ADD event."""
    ts = joined_at or _get_timestamp()
    data = {
        "guild_id": _str_id(server_id),
        "user": user or {"id": _str_id(user_id)},
        "roles": [_str_id(r) for r in (roles or [])],
        "joined_at": ts,
    }
    if nick:
        data["nick"] = nick

    return GuildMemberEvent(
        event_type=EventType.GUILD_MEMBER_ADD,
        data=data,
        server_id=server_id,
        user_id=user_id,
        member_user_id=user_id,
        nick=nick,
        roles=roles or [],
        joined_at=ts,
    )


def create_guild_member_remove(
    server_id: int,
    user_id: int,
    user: Optional[Dict[str, Any]] = None,
) -> GuildMemberEvent:
    """Create a GUILD_MEMBER_REMOVE event."""
    return GuildMemberEvent(
        event_type=EventType.GUILD_MEMBER_REMOVE,
        data={
            "guild_id": _str_id(server_id),
            "user": user or {"id": _str_id(user_id)},
        },
        server_id=server_id,
        user_id=user_id,
        member_user_id=user_id,
    )


def create_guild_member_update(
    server_id: int,
    user_id: int,
    user: Optional[Dict[str, Any]] = None,
    nick: Optional[str] = None,
    roles: Optional[List[int]] = None,
) -> GuildMemberEvent:
    """Create a GUILD_MEMBER_UPDATE event."""
    data = {
        "guild_id": _str_id(server_id),
        "user": user or {"id": _str_id(user_id)},
        "roles": [_str_id(r) for r in (roles or [])],
    }
    if nick is not None:
        data["nick"] = nick

    return GuildMemberEvent(
        event_type=EventType.GUILD_MEMBER_UPDATE,
        data=data,
        server_id=server_id,
        user_id=user_id,
        member_user_id=user_id,
        nick=nick,
        roles=roles or [],
    )


def create_voice_state_update(
    user_id: int,
    channel_id: Optional[int],
    server_id: Optional[int] = None,
    session_id: Optional[str] = None,
    self_mute: bool = False,
    self_deaf: bool = False,
    mute: bool = False,
    deaf: bool = False,
    member: Optional[Dict[str, Any]] = None,
) -> VoiceStateEvent:
    """Create a VOICE_STATE_UPDATE event."""
    data = {
        "user_id": _str_id(user_id),
        "channel_id": _str_id(channel_id),
        "self_mute": self_mute,
        "self_deaf": self_deaf,
        "mute": mute,
        "deaf": deaf,
        "suppress": False,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if session_id:
        data["session_id"] = session_id
    if member:
        data["member"] = member

    return VoiceStateEvent(
        event_type=EventType.VOICE_STATE_UPDATE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        user_id=user_id,
        voice_channel_id=channel_id,
        self_mute=self_mute,
        self_deaf=self_deaf,
        mute=mute,
        deaf=deaf,
    )


def create_reaction_add(
    user_id: int,
    message_id: int,
    channel_id: int,
    emoji: Dict[str, Any],
    server_id: Optional[int] = None,
    member: Optional[Dict[str, Any]] = None,
) -> ReactionEvent:
    """Create a MESSAGE_REACTION_ADD event."""
    data = {
        "user_id": _str_id(user_id),
        "message_id": _str_id(message_id),
        "channel_id": _str_id(channel_id),
        "emoji": emoji,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)
    if member:
        data["member"] = member

    return ReactionEvent(
        event_type=EventType.MESSAGE_REACTION_ADD,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        user_id=user_id,
        message_id=message_id,
        emoji=emoji,
        member=member,
    )


def create_reaction_remove(
    user_id: int,
    message_id: int,
    channel_id: int,
    emoji: Dict[str, Any],
    server_id: Optional[int] = None,
) -> ReactionEvent:
    """Create a MESSAGE_REACTION_REMOVE event."""
    data = {
        "user_id": _str_id(user_id),
        "message_id": _str_id(message_id),
        "channel_id": _str_id(channel_id),
        "emoji": emoji,
    }
    if server_id:
        data["guild_id"] = _str_id(server_id)

    return ReactionEvent(
        event_type=EventType.MESSAGE_REACTION_REMOVE,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
        user_id=user_id,
        message_id=message_id,
        emoji=emoji,
    )


def create_guild_members_chunk(
    server_id: int,
    members: List[Dict[str, Any]],
    chunk_index: int = 0,
    chunk_count: int = 1,
    not_found: Optional[List[int]] = None,
) -> GuildMemberEvent:
    """Create a GUILD_MEMBERS_CHUNK event."""
    return GuildMemberEvent(
        event_type=EventType.GUILD_MEMBERS_CHUNK,
        data={
            "guild_id": _str_id(server_id),
            "members": members,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "not_found": [_str_id(r) for r in (not_found or [])],
        },
        server_id=server_id,
    )
