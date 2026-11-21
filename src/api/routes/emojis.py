"""
Emoji routes - Custom emoji management endpoints.
"""

from src.api.request_guards import MAX_EMOJI_UPLOAD, read_upload_capped

import asyncio
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import authenticated_cache_key, require_user_token
from src.core.security.hardening import parse_id
from src.core.database.cache import cached, invalidate_pattern
from src.api.schemas.emojis import (
    EmojiResponse,
    EmojiCountsResponse,
    EmojiUpdateRequest,
)
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
    _reject_ui_text_chars,
)
from src.core.avatars.manager.upload_guard import (
    UploadContentError,
    verify_upload_content,
)

router = APIRouter(tags=["Emojis"])

# Read-path caches for server emoji lists and single emoji details. The old
# 300s/3600s TTLs let a deleted/renamed emoji (or a revoked membership) keep
# being served from cache for far too long; they are now short and bounded,
# with a 2s process-local tier that never outlives the shared Valkey entry.
_EMOJI_LIST_CACHE_PREFIX = "emoji_list_api"
_EMOJI_DETAIL_CACHE_PREFIX = "emoji_detail_api"
_EMOJI_CACHE_TTL = 10
_EMOJI_CACHE_L1_TTL = 2


def _cache_id(value):
    """Normalize a path id so reads and mutations build the same key segment."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _emoji_cache_key_builder(prefix: str):
    """Partition emoji cache keys by credential and by resource id.

    ``authenticated_cache_key`` keeps a bot token and its owner's user token in
    separate namespaces (both carry the same ``user_id``). The server id (and,
    for details, the emoji id) is placed in a fixed leading segment so a single
    invalidation glob per resource clears every credential's entry. FastAPI
    passes route params as kwargs, so the previous ``<prefix>:str:<sid>:*``
    glob never matched the real ``<prefix>:current_user:TokenInfo:<uid>:...``
    keys and mutations left permanently stale responses behind.
    """

    def _builder(*args, **kwargs):
        server_id = _cache_id(kwargs.get("server_id", args[0] if args else ""))
        emoji_id = _cache_id(kwargs.get("emoji_id", args[1] if len(args) > 1 else ""))
        resource = f"{prefix}:sid:{server_id}"
        if emoji_id != "" and emoji_id is not None:
            resource = f"{resource}:eid:{emoji_id}"
        return authenticated_cache_key(resource)(*args, **kwargs)

    return _builder


_emoji_list_cache_key = _emoji_cache_key_builder(_EMOJI_LIST_CACHE_PREFIX)
_emoji_detail_cache_key = _emoji_cache_key_builder(_EMOJI_DETAIL_CACHE_PREFIX)


def _bounded_id(value: object, field: str) -> int:
    """Parse a snowflake route id, refusing negatives and absurd values."""
    return parse_id(value, field)


def _emoji_to_response(emoji) -> EmojiResponse:
    """Convert emoji object to response model."""
    return EmojiResponse(
        id=str(emoji.id),
        server_id=str(emoji.server_id),
        name=emoji.name,
        animated=emoji.animated,
        url=emoji.url or "",
        available=emoji.available,
        created_by=str(emoji.created_by) if emoji.created_by else "0",
        uploader_username=emoji.uploader_username,
        created_at=emoji.created_at,
    )


def _invalidate_emoji_cache(server_id: int, emoji_id: Optional[int] = None) -> None:
    """Invalidate cached emoji list/detail entries for every credential.

    The glob must match the keys produced by ``_emoji_cache_key_builder`` —
    ``<prefix>:sid:<sid>:...`` and ``<prefix>:sid:<sid>:eid:<eid>:...`` — for
    both user and bot credentials. Create/update/delete all call this from
    their success path, so no mutation leaves a stale response behind.
    """
    invalidate_pattern(f"{_EMOJI_LIST_CACHE_PREFIX}:sid:{server_id}:*")
    if emoji_id is not None:
        invalidate_pattern(
            f"{_EMOJI_DETAIL_CACHE_PREFIX}:sid:{server_id}:eid:{emoji_id}:*"
        )


def _invalidate_reaction_views() -> None:
    """Drop cached reaction-list bodies after a custom-emoji mutation.

    Deleting an emoji also deletes every reaction that used it, and a rename
    changes the pill's emoji markup. Those bodies live in the separate
    ``reaction_read_api`` HTTP cache, which the reaction routes invalidate on
    their own writes but the emoji routes did not. The core L0 aggregation
    cache is already dropped by the manager, but that does not reach the HTTP
    tier, so a warm pill list could survive the mutation. Import lazily to
    avoid coupling the two route modules at import time.
    """
    from src.api.routes.reactions import _invalidate_reaction_read_cache

    _invalidate_reaction_read_cache()


def _require_servers_module(servers_module, action: str):
    """Return the servers module or fail closed with 503.

    The emoji routes delegate membership and permission checks to the servers
    module. Previously each call site guarded with ``if servers_module:``, so
    an unwired module silently *skipped* the check and every authenticated
    caller could read or mutate any server's emojis. A check that cannot be
    evaluated must deny: report the deployment problem (503) rather than
    granting access (see the soundboard ``W5`` route gate for the precedent).
    """
    if not servers_module:
        logger.error(f"Servers module not available for {action}")
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Servers module not available"}},
        )
    return servers_module


def _require_server_membership(
    servers_module, user_id: int, server_id: int, action: str
) -> None:
    """Fail-closed membership gate for server-scoped emoji reads."""
    _require_servers_module(servers_module, action)
    try:
        member = servers_module.get_member(server_id, user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error checking membership for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": "Failed to verify server membership",
                }
            },
        )
    if not member:
        logger.warning(
            f"User {user_id} attempted to access emojis for server "
            f"{server_id} they are not in"
        )
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Not a member of this server"}},
        )


def _require_manage_emojis(servers_module, user_id: int, server_id: int) -> None:
    """Fail-closed ``emojis.manage``/``server.manage`` gate for emoji writes."""
    _require_servers_module(servers_module, "emoji management")
    from src.core.auth.permissions import has_permission

    try:
        perms = servers_module.get_permissions(user_id, server_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to load permissions for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "Missing emojis.manage permission"}
            },
        )
    if not (
        has_permission(perms, "emojis.manage") or has_permission(perms, "server.manage")
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "Missing emojis.manage permission"}
            },
        )


async def _dispatch_emoji_update(server_id: int):
    """Helper to dispatch GUILD_EMOJIS_UPDATE via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()
        reactions = api.get_reactions()
        servers = api.get_servers()

        if not reactions or not servers:
            return

        # Get all server members
        user_ids = servers.get_member_user_ids(server_id)
        if not user_ids:
            return

        # Get current emojis for the payload
        emojis = reactions.get_server_custom_emojis(server_id)
        emoji_list = [_emoji_to_response(e).model_dump() for e in emojis]

        # Create and dispatch the event
        event = Event(
            event_type=EventType.GUILD_EMOJIS_UPDATE,
            data={
                "guild_id": str(server_id),
                "emojis": emoji_list,
            },
            server_id=server_id,
        )
        await dispatcher.dispatch_event(event, user_ids)

    except Exception as e:
        logger.debug(f"Failed to dispatch emoji update for server {server_id}: {e}")


@cached(
    ttl=_EMOJI_CACHE_TTL,
    prefix=_EMOJI_LIST_CACHE_PREFIX,
    key_builder=_emoji_list_cache_key,
    l1_ttl=_EMOJI_CACHE_L1_TTL,
)
async def _load_server_emojis_page(
    server_id: str, current_user: TokenInfo
) -> List[EmojiResponse]:
    """Data-only cold path for a server's emoji list.

    The membership gate runs in the route handler on EVERY request, including
    cache hits. The ``@cached`` decorator returns before this body on a warm
    entry, so leaving the gate here would let a member who lost access keep
    reading their still-warm list for the whole cache TTL.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        sid = _bounded_id(server_id, "server ID")
        emojis = reactions.get_server_custom_emojis(sid)
        return [_emoji_to_response(e) for e in emojis]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch emojis for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}/emojis",
    response_model=List[EmojiResponse],
    summary="Get server emojis",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        403: {"model": ErrorResponse, "description": "Not a member of this server"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def get_server_emojis(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[EmojiResponse]:
    """Get all custom emojis for a server (members only)."""
    servers = api.get_servers()
    try:
        sid = _bounded_id(server_id, "server ID")
    except (ValueError, TypeError):
        logger.warning(f"Invalid server ID format: {server_id}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )

    # LIVE GATE before the cached read. The membership check used to live inside
    # the @cached body, so a warm hit skipped it and a kicked member kept their
    # old list for the full TTL. Revocation also evicts this namespace, but that
    # is best-effort (other revocation paths / stale L1 copies can miss it), so
    # the per-request check is the authoritative one. Fail closed if it cannot
    # be evaluated.
    _require_server_membership(servers, current_user.user_id, sid, "emoji listing")

    return await _load_server_emojis_page(
        server_id=server_id, current_user=current_user
    )


@router.get(
    "/{server_id}/emojis/counts",
    response_model=EmojiCountsResponse,
    summary="Get emoji counts",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        403: {"model": ErrorResponse, "description": "Not a member of this server"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def get_emoji_counts(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> EmojiCountsResponse:
    """
    Get emoji counts and limits for a server.

    Returns current emoji counts and maximum limits.
    """
    reactions = api.get_reactions()
    servers = api.get_servers()

    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            sid = _bounded_id(server_id, "server ID")
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format for counts: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Fail closed when the servers module cannot answer the membership
        # question (see _require_server_membership).
        _require_server_membership(servers, current_user.user_id, sid, "emoji counts")

        try:
            counts = reactions.get_emoji_counts(sid)
            return EmojiCountsResponse(**counts)
        except Exception as e:
            logger.error(
                f"Failed to fetch emoji counts for server {sid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_emoji_counts for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@cached(
    ttl=_EMOJI_CACHE_TTL,
    prefix=_EMOJI_DETAIL_CACHE_PREFIX,
    key_builder=_emoji_detail_cache_key,
    l1_ttl=_EMOJI_CACHE_L1_TTL,
)
async def _load_emoji_detail(
    server_id: str, emoji_id: str, current_user: TokenInfo
) -> EmojiResponse:
    """Data-only cold path for one emoji detail.

    Membership is gated live by the route handler before this cached body is
    reached. On a cache hit the decorator returns without entering this
    function, which is why the gate cannot live here.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        eid = _bounded_id(emoji_id, "emoji ID")
        emoji = reactions.get_custom_emoji(eid)
        if not emoji:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Emoji not found"}},
            )

        # Verify emoji belongs to the server (IDOR defence in depth).
        if str(emoji.server_id) != server_id:
            logger.warning(f"Emoji {eid} does not belong to server {server_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Emoji not found"}},
            )

        return _emoji_to_response(emoji)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch emoji {emoji_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}/emojis/{emoji_id}",
    response_model=EmojiResponse,
    summary="Get specific emoji",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid emoji ID"},
        404: {"model": ErrorResponse, "description": "Emoji not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def get_emoji(
    server_id: str, emoji_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> EmojiResponse:
    """Get a specific custom emoji (members only)."""
    servers = api.get_servers()
    try:
        sid = _bounded_id(server_id, "server ID")
        _bounded_id(emoji_id, "emoji ID")
    except (ValueError, TypeError):
        logger.warning(
            f"Invalid id for emoji detail: server={server_id} emoji={emoji_id}"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid emoji ID"}},
        )

    # LIVE GATE before the cached read: a revoked member must not receive a
    # still-warm detail response. The check used to sit inside the @cached body
    # and was skipped on every cache hit; keep it here and fail closed.
    _require_server_membership(servers, current_user.user_id, sid, "emoji detail")

    return await _load_emoji_detail(
        server_id=server_id, emoji_id=emoji_id, current_user=current_user
    )


@router.post(
    "/{server_id}/emojis",
    response_model=EmojiResponse,
    summary="Create custom emoji",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input or limit reached"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        409: {"model": ErrorResponse, "description": "Emoji already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def create_emoji(
    server_id: str,
    name: str = Form(..., description="Emoji name (2-32 alphanumeric characters)"),
    image: UploadFile = File(
        ..., description="Emoji image (PNG, GIF, or WebP, max 256KB)"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> EmojiResponse:
    """
    Create a custom emoji for a server.

    Uploads an image and creates a new custom emoji.
    Requires emojis.manage or server.manage permission.
    """
    # A bot token's user_id is its owner; reject it before it can wield
    # the owner's server roles through _require_manage_emojis.
    require_user_token(current_user)
    reactions = api.get_reactions()
    servers_mod = api.get_servers()

    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            sid = _bounded_id(server_id, "server ID")
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format for emoji creation: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permissions (fail closed when the servers module is absent)
        _require_manage_emojis(servers_mod, current_user.user_id, sid)

        # Defence in depth: emoji names are rendered to every server member
        # via the emoji picker and message reactions, so reject HTML
        # metacharacters and control characters (stored XSS). The rejection is
        # a SecurityRejection, which the app-level handler maps to a 400 with
        # its stable reason code -- no per-route translation needed.
        name = _reject_ui_text_chars(name, "emoji name")

        # Read image data
        try:
            image_data = await read_upload_capped(image, MAX_EMOJI_UPLOAD)
        except Exception as e:
            logger.error(
                f"Failed to read image upload for server {sid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Failed to read image upload",
                    }
                },
            )

        content_type = image.content_type or "application/octet-stream"

        # DEEP: the declared emoji media type must match the actual magic
        # bytes, and the payload must not be a polyglot / carry active markup.
        # Without this, an SVG-with-script sent as image/png survives as a
        # stored-XSS vector in every emoji picker and reaction render.
        try:
            verify_upload_content(content_type, image_data)
        except UploadContentError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )

        try:
            emoji = reactions.create_custom_emoji(
                user_id=current_user.user_id,
                server_id=sid,
                name=name,
                image_data=image_data,
                content_type=content_type,
            )

            _invalidate_emoji_cache(sid, emoji.id)
            _invalidate_reaction_views()

            # Dispatch WebSocket event (fire-and-forget, errors are self-contained)
            asyncio.create_task(_dispatch_emoji_update(sid))

            return _emoji_to_response(emoji)
        except Exception as e:
            exc_name = type(e).__name__
            if "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to create emoji in server {sid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif "Limit" in exc_name:
                logger.warning(f"Emoji limit reached for server {sid}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif "Name" in exc_name or "Invalid" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif "Size" in exc_name or "File" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif "Exists" in exc_name:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {"code": 409, "message": "Resource already exists"}
                    },
                )

            logger.error(f"Failed to create emoji in server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_emoji for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{server_id}/emojis/{emoji_id}",
    response_model=EmojiResponse,
    summary="Update custom emoji",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Emoji not found"},
        409: {"model": ErrorResponse, "description": "Emoji name already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def update_emoji(
    server_id: SnowflakeIDParam,
    emoji_id: SnowflakeIDParam,
    body: EmojiUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> EmojiResponse:
    """
    Update a custom emoji.

    Currently only supports renaming the emoji.
    Requires emojis.manage or server.manage permission.
    """
    # A bot token's user_id is its owner; reject it before it can wield
    # the owner's server roles through _require_manage_emojis.
    require_user_token(current_user)
    reactions = api.get_reactions()
    servers_mod = api.get_servers()

    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        sid = _bounded_id(server_id, "server ID")

        # Check permissions (fail closed when the servers module is absent)
        _require_manage_emojis(servers_mod, current_user.user_id, sid)

        # Bind the emoji to the URL's server before mutating it, matching the
        # GET detail route. The core re-checks the owning server's permission,
        # but without this the route would authorize against A's URL while
        # mutating an emoji owned by B, then evict A's cache and broadcast A's
        # GUILD_EMOJIS_UPDATE for a change that happened in B.
        bound_emoji = reactions.get_custom_emoji(int(emoji_id))
        if bound_emoji is None or int(bound_emoji.server_id) != sid:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Emoji not found"}},
            )

        try:
            emoji = reactions.update_custom_emoji(
                user_id=current_user.user_id,
                emoji_id=emoji_id,
                name=body.name,
            )

            _invalidate_emoji_cache(sid, emoji_id)
            _invalidate_reaction_views()

            # Dispatch WebSocket event (fire-and-forget, errors are self-contained)
            asyncio.create_task(_dispatch_emoji_update(sid))

            return _emoji_to_response(emoji)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Emoji not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to update emoji {emoji_id}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif "Name" in exc_name or "Invalid" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif "Exists" in exc_name:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": {"code": 409, "message": "Resource already exists"}
                    },
                )

            logger.error(f"Failed to update emoji {emoji_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_emoji for emoji {emoji_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{server_id}/emojis/{emoji_id}",
    response_model=SuccessResponse,
    summary="Delete custom emoji",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid emoji ID"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Emoji not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Servers module not available"},
    },
)
async def delete_emoji(
    server_id: str, emoji_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete a custom emoji.

    Permanently removes the emoji from the server.
    Requires emojis.manage or server.manage permission.
    """
    # A bot token's user_id is its owner; reject it before it can wield
    # the owner's server roles through _require_manage_emojis.
    require_user_token(current_user)
    reactions = api.get_reactions()
    servers_mod = api.get_servers()

    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            sid = _bounded_id(server_id, "server ID")
            eid = _bounded_id(emoji_id, "emoji ID")
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid ID format for emoji deletion: {server_id}/{emoji_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        # Check permissions (fail closed when the servers module is absent)
        _require_manage_emojis(servers_mod, current_user.user_id, sid)

        # Same URL-server binding as get_emoji/update_emoji: never let a
        # mutation authorized for the URL's server act on another server's
        # emoji and then invalidate/announce the wrong server.
        bound_emoji = reactions.get_custom_emoji(eid)
        if bound_emoji is None or int(bound_emoji.server_id) != sid:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Emoji not found"}},
            )

        try:
            reactions.delete_custom_emoji(current_user.user_id, eid)

            _invalidate_emoji_cache(sid, eid)
            _invalidate_reaction_views()

            # Dispatch WebSocket event (fire-and-forget, errors are self-contained)
            asyncio.create_task(_dispatch_emoji_update(sid))

            return SuccessResponse(success=True, message=None)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Emoji not found"}},
                )
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to delete emoji {eid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )

            logger.error(f"Failed to delete emoji {eid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_emoji for emoji {emoji_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
