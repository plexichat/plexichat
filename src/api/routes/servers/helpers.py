import utils.config as config
from src.api.schemas.servers import (
    ServerResponse,
    ChannelResponse,
    AutomodRuleResponse,
    AutomodRuleAction,
)
from src.api.schemas.common import SnowflakeID

DEFAULT_ICON_SIZE_LIMIT = 2 * 1024 * 1024


def _server_to_response(server) -> ServerResponse:
    default_channel_id = getattr(server, "default_channel_id", None)
    icon_url = getattr(server, "icon_url", None)
    return ServerResponse(
        id=SnowflakeID(server.id),
        name=server.name,
        description=getattr(server, "description", None),
        icon_url=icon_url,
        owner_id=SnowflakeID(server.owner_id),
        member_count=getattr(server, "member_count", 0),
        default_channel_id=SnowflakeID(default_channel_id)
        if default_channel_id
        else None,
        verification_level=getattr(server, "verification_level", 0),
        default_message_notifications=getattr(
            server, "default_message_notifications", 0
        ),
        created_at=server.created_at,
    )


def _channel_to_response(channel) -> ChannelResponse:
    channel_type = getattr(channel, "channel_type", None)
    if channel_type is not None and hasattr(channel_type, "value"):
        channel_type = channel_type.value

    return ChannelResponse(
        id=SnowflakeID(channel.id),
        server_id=SnowflakeID(channel.server_id),
        name=channel.name,
        channel_type=channel_type or "text",
        topic=getattr(channel, "topic", None),
        position=getattr(channel, "position", 0),
        category_id=SnowflakeID(channel.category_id)
        if getattr(channel, "category_id", None)
        else None,
        nsfw=getattr(channel, "nsfw", False),
        slowmode_seconds=getattr(channel, "slowmode_seconds", 0),
        read_receipts_enabled=getattr(channel, "read_receipts_enabled", True),
        created_at=channel.created_at,
        recipient_id=None,
        recipient=None,
    )


def _server_to_dict(server) -> dict:
    default_channel_id = getattr(server, "default_channel_id", None)
    return {
        "id": server.id,
        "name": server.name,
        "description": getattr(server, "description", None),
        "icon_url": getattr(server, "icon_url", None),
        "owner_id": server.owner_id,
        "member_count": getattr(server, "member_count", 0),
        "default_channel_id": default_channel_id,
        "created_at": server.created_at,
    }


def _channel_to_dict(channel) -> dict:
    channel_type = getattr(channel, "channel_type", None)
    if channel_type is not None and hasattr(channel_type, "value"):
        channel_type = channel_type.value
    return {
        "id": channel.id,
        "server_id": channel.server_id,
        "name": channel.name,
        "channel_type": channel_type or "text",
        "topic": getattr(channel, "topic", None),
        "position": getattr(channel, "position", 0),
        "category_id": getattr(channel, "category_id", None),
        "nsfw": getattr(channel, "nsfw", False),
        "slowmode_seconds": getattr(channel, "slowmode_seconds", 0),
        "created_at": channel.created_at,
    }


def _automod_rule_to_response(rule) -> AutomodRuleResponse:
    return AutomodRuleResponse(
        id=SnowflakeID(rule.id),
        server_id=SnowflakeID(rule.server_id),
        name=rule.name,
        rule_type=rule.rule_type.value
        if hasattr(rule.rule_type, "value")
        else str(rule.rule_type),
        enabled=bool(rule.enabled),
        config=rule.config,
        actions=[
            AutomodRuleAction(
                action_type=a.action_type.value
                if hasattr(a.action_type, "value")
                else str(a.action_type),
                duration_seconds=a.duration_seconds,
                reason=a.reason,
                notify_user=a.notify_user,
                metadata=a.metadata or {},
            )
            for a in (rule.actions or [])
        ],
        exempt_roles=[SnowflakeID(r) for r in (rule.exempt_roles or [])],
        applied_roles=[
            SnowflakeID(r) for r in (getattr(rule, "applied_roles", []) or [])
        ],
        exempt_channels=[SnowflakeID(c) for c in (rule.exempt_channels or [])],
        priority=rule.priority,
        check_all=bool(rule.check_all),
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        created_by=SnowflakeID(rule.created_by),
    )


def _get_icon_size_limit() -> int:
    try:
        media_config = config.get("media", {})
        size_limits = media_config.get("size_limits", {})
        return size_limits.get("icon", DEFAULT_ICON_SIZE_LIMIT)
    except Exception:
        return DEFAULT_ICON_SIZE_LIMIT
