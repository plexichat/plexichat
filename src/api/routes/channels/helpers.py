from typing import Any, Optional

from fastapi import HTTPException

import src.api as api
import utils.config as config
import utils.logger as logger
from src.api.schemas.servers import ChannelResponse
from src.api.schemas.common import SnowflakeID
from src.core.security.hardening import bounded_int, parse_id

DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024


def require_channel_snowflake(value: Any, field: str = "channel_id") -> int:
    """Parse an untrusted channel id with the shared snowflake range guard.

    A bare ``int()`` accepted negative ids and ids past the 64-bit snowflake
    ceiling, which then flowed into lookups, cache keys and ratchet key
    derivation. Reusing the shared parser rejects those before any of that
    work, while the route keeps its documented 400 contract.
    """
    try:
        return parse_id(value, field)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        ) from exc


MAX_PAGE_LIMIT = 200
MAX_PAGE_OFFSET = 100_000


def require_pagination(limit: Any, offset: Any) -> tuple[int, int]:
    """Bound ``limit``/``offset`` for a channel list endpoint.

    The two list endpoints here previously had no page size at all, so a
    single request could materialise every webhook or ratchet interval for a
    channel. Both values now pass through the shared integer range guard and
    an abusive value is a 400 rather than an unbounded scan.
    """
    try:
        safe_limit = bounded_int(
            limit, "limit", minimum=1, maximum=MAX_PAGE_LIMIT, default=100
        )
        safe_offset = bounded_int(
            offset, "offset", minimum=0, maximum=MAX_PAGE_OFFSET, default=0
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid pagination parameters"}},
        ) from exc
    return safe_limit, safe_offset


def _channel_to_response(
    channel, current_user_id: Optional[int] = None
) -> ChannelResponse:
    """Convert channel object to response model."""
    try:
        channel_type = getattr(channel, "channel_type", None)
        if channel_type is None:
            channel_type = getattr(channel, "conversation_type", None)

        if channel_type is not None and hasattr(channel_type, "value"):
            channel_type = channel_type.value

        name = getattr(channel, "name", None)
        server_id = getattr(channel, "server_id", 0)

        recipient_id = None
        recipient = None

        if channel_type == "dm" or channel_type == "group_dm":
            messaging_mod = api.get_messaging()
            if messaging_mod:
                try:
                    recipient_id = getattr(channel, "recipient_id", None)

                    if not recipient_id:
                        manager = messaging_mod.get_manager()
                        participants = manager.get_participant_ids(channel.id)
                        if len(participants) == 2 and current_user_id:
                            recipient_id = next(
                                (
                                    p
                                    for p in participants
                                    if int(p) != int(current_user_id)
                                ),
                                participants[0],
                            )
                        elif participants:
                            recipient_id = participants[0]

                    if recipient_id:
                        from ..users import _get_user_cached, _user_to_public_response

                        user_data = _get_user_cached(int(recipient_id))
                        if user_data:
                            recipient = _user_to_public_response(user_data)
                            if channel_type == "dm":
                                name = recipient.username
                except Exception as de:
                    logger.debug(
                        f"Failed to populate DM recipient for channel {channel.id}: {de}"
                    )

        if not name or (isinstance(name, str) and "pyc" in name):
            if channel_type == "dm" and recipient:
                name = recipient.username
            else:
                name = f"Conversation {channel.id}"

        return ChannelResponse(
            id=SnowflakeID(channel.id),
            server_id=SnowflakeID(server_id),
            name=name,
            channel_type=channel_type or "text",
            topic=getattr(channel, "topic", None),
            position=getattr(channel, "position", 0),
            category_id=SnowflakeID(channel.category_id)
            if getattr(channel, "category_id", None)
            else None,
            nsfw=getattr(channel, "nsfw", False),
            slowmode_seconds=getattr(channel, "slowmode_seconds", 0),
            read_receipts_enabled=bool(getattr(channel, "read_receipts_enabled", True)),
            created_at=channel.created_at,
            recipient_id=SnowflakeID(recipient_id) if recipient_id else None,
            recipient=recipient,
        )
    except Exception as e:
        logger.error(f"Error converting channel object to response: {e}")
        raise e


def _get_upload_limit(user_id: Optional[int] = None) -> int:
    """Get the upload size limit based on user tier or config default."""
    try:
        if user_id:
            try:
                from src.core import features

                if features.is_setup():
                    tier_limits = features.get_user_tier_limits(user_id)
                    if tier_limits and tier_limits.max_file_size_mb:
                        return tier_limits.max_file_size_mb * 1024 * 1024
            except Exception as tier_error:
                # Fall through to the configured global limit.
                logger.debug(f"channel tier file limit lookup failed: {tier_error}")

        media_config = config.get("media", {})
        size_limits = media_config.get("size_limits", {})
        return size_limits.get("other", DEFAULT_UPLOAD_LIMIT)
    except Exception:
        return DEFAULT_UPLOAD_LIMIT
