import re
from typing import Any, Optional

from fastapi import HTTPException

import src.api as api
import utils.config as config
import utils.logger as logger
from src.api.schemas.servers import ChannelResponse
from src.api.schemas.common import SnowflakeID
from src.core.security.hardening import (
    bounded_int,
    parse_id,
    reject_control_characters,
    require_http_url,
)
from src.core.security.hardening.text import strip_control_characters

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


CHANNEL_TYPE_ALLOWLIST = frozenset(
    {"text", "voice", "announcement", "stage", "category", "dm", "group_dm", "notes"}
)
MAX_CHANNEL_NAME_LENGTH = 100
MAX_CHANNEL_TOPIC_LENGTH = 1024


def validate_channel_fields(update_data: dict) -> None:
    """Reject out-of-allowlist types and overlength names/topics.

    The request schema enforces some of these, but the route must not rely on a
    schema edit elsewhere to keep the boundary: an unexpected field or a
    caller that bypasses normal validation still reaches this check before any
    channel mutation.
    """
    if "channel_type" in update_data:
        value = update_data["channel_type"]
        value = getattr(value, "value", value)
        if str(value) not in CHANNEL_TYPE_ALLOWLIST:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel type"}},
            )
    for field, ceiling in (
        ("name", MAX_CHANNEL_NAME_LENGTH),
        ("topic", MAX_CHANNEL_TOPIC_LENGTH),
    ):
        value = update_data.get(field)
        if value is None:
            continue
        value = str(value)
        try:
            reject_control_characters(value, field)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": f"Invalid {field}"}},
            ) from exc
        if len(value) > ceiling:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": f"Invalid {field}"}},
            )


MAX_INVITE_AGE_SECONDS = 30 * 24 * 3600
MAX_INVITE_USES = 10_000
INVITE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def require_invite_code(value: Any) -> str:
    """Validate an invite code before it reaches a storage lookup."""
    code = str(value or "")
    if not INVITE_CODE_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid invite code"}},
        )
    return code


def require_invite_limits(max_age: Any, max_uses: Any) -> tuple[int, int]:
    """Bound invite lifetime and use count with the shared numeric guard."""
    try:
        age = bounded_int(
            max_age, "max_age", minimum=0, maximum=MAX_INVITE_AGE_SECONDS, default=86400
        )
        uses = bounded_int(
            max_uses, "max_uses", minimum=0, maximum=MAX_INVITE_USES, default=0
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid invite parameters"}},
        ) from exc
    return age, uses


MAX_WEBHOOK_NAME_LENGTH = 100
MAX_WEBHOOK_URL_LENGTH = 2048


def sanitize_webhook_fields(name: Any, avatar_url: Any) -> tuple[str, Optional[str]]:
    """Bound webhook response fields and keep only HTTP(S) avatar URLs.

    A stored webhook record is trusted only for identity, not for the shape of
    the values echoed to clients. Control characters are stripped, the name is
    capped, and an avatar URL with a non-HTTP scheme or embedded credentials
    is dropped rather than reflected into the API response.
    """
    safe_name = strip_control_characters(name)[:MAX_WEBHOOK_NAME_LENGTH]
    safe_url = None
    if avatar_url:
        url = str(avatar_url).strip()
        if len(url) <= MAX_WEBHOOK_URL_LENGTH:
            try:
                safe_url = require_http_url(url, "webhook avatar URL")
            except ValueError:
                safe_url = None
    return safe_name, safe_url


MAX_BULK_BATCH = 1000


def bounded_bulk_batch(values: Any, field: str, maximum: int = MAX_BULK_BATCH) -> list:
    """Cap a bulk collection before any per-item fan-out.

    Join/update events build channels, roles and member-id lists from storage
    and then do work for every entry. Without a ceiling, one large server can
    turn a single channel request into an unbounded loop and event payload.
    The cap itself goes through the shared numeric guard, and the collection is
    truncated (not rejected) so ordinary large servers keep working.
    """
    if values is None:
        return []
    try:
        items = list(values)
    except TypeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        ) from exc
    try:
        safe_maximum = bounded_int(
            maximum,
            f"{field} limit",
            minimum=1,
            maximum=MAX_BULK_BATCH,
            default=MAX_BULK_BATCH,
        )
    except ValueError:
        safe_maximum = MAX_BULK_BATCH
    return items[:safe_maximum]


WEBHOOK_SORT_FIELDS = frozenset({"created_at", "name", "id"})
RATCHET_SORT_FIELDS = frozenset(
    {"interval_id", "start_message_id", "end_message_id", "created_at"}
)
SORT_ORDERS = frozenset({"asc", "desc"})


def require_sort(value: Any, allowed: frozenset, field: str = "sort") -> Optional[str]:
    """Return a client-chosen sort key only if it is on the allowlist."""
    if value is None:
        return None
    key = str(value).strip().lower()
    if key not in allowed:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        )
    return key


def require_order(value: Any, field: str = "order") -> Optional[str]:
    """Return a normalised ``asc``/``desc`` order or reject the value."""
    if value is None:
        return None
    order = str(value).strip().lower()
    if order not in SORT_ORDERS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {field}"}},
        )
    return order


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
        if channel_type is not None and channel_type not in CHANNEL_TYPE_ALLOWLIST:
            logger.warning(f"Unknown channel type {channel_type!r}; using text")
            channel_type = "text"
        if channel_type is not None and channel_type not in CHANNEL_TYPE_ALLOWLIST:
            logger.warning(f"Unknown channel type {channel_type!r}; using text")
            channel_type = "text"
        if channel_type is not None and channel_type not in CHANNEL_TYPE_ALLOWLIST:
            logger.warning(f"Unknown channel type {channel_type!r}; using text")
            channel_type = "text"

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
