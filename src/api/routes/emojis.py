"""
Emoji routes - Custom emoji management endpoints.
"""

import asyncio
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
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
)

router = APIRouter(tags=["Emojis"])


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
    """Invalidate cached emoji list/detail entries for all viewers of a server."""
    invalidate_pattern(
        f"{getattr(get_server_emojis, 'cache_key_prefix', '')}:str:{server_id}:*"
    )
    if emoji_id is not None:
        invalidate_pattern(
            f"{getattr(get_emoji, 'cache_key_prefix', '')}:str:{server_id}:str:{emoji_id}:*"
        )


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
@cached(ttl=300)
async def get_server_emojis(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[EmojiResponse]:
    """
    Get all custom emojis for a server.

    Returns a list of custom emojis available in the server.
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
            sid = int(server_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check if user is member of server (fail closed when unavailable)
        _require_server_membership(servers, current_user.user_id, sid, "emoji listing")

        try:
            emojis = reactions.get_server_custom_emojis(sid)
            return [_emoji_to_response(e) for e in emojis]
        except Exception as e:
            logger.error(f"Failed to fetch emojis for server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to fetch emojis: {str(e)}",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_server_emojis for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            sid = int(server_id)
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
                        "message": f"Failed to fetch emoji counts: {str(e)}",
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
@cached(ttl=3600)
async def get_emoji(
    server_id: str, emoji_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> EmojiResponse:
    """
    Get a specific custom emoji.

    Returns emoji details if found and user has access.
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
            eid = int(emoji_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid emoji ID format: {emoji_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid emoji ID"}},
            )

        try:
            emoji = reactions.get_custom_emoji(eid)
            if not emoji:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Emoji not found"}},
                )

            # Verify emoji belongs to the server
            if str(emoji.server_id) != server_id:
                logger.warning(f"Emoji {eid} does not belong to server {server_id}")
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Emoji not found"}},
                )

            # Fail closed on membership: this endpoint previously returned any
            # emoji in any server to any authenticated caller because it never
            # consulted the servers module at all.
            _require_server_membership(
                servers,
                current_user.user_id,
                int(server_id),
                "emoji detail",
            )

            return _emoji_to_response(emoji)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch emoji {emoji_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to fetch emoji: {str(e)}",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_emoji for emoji {emoji_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            sid = int(server_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format for emoji creation: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permissions (fail closed when the servers module is absent)
        _require_manage_emojis(servers_mod, current_user.user_id, sid)

        # Read image data
        try:
            image_data = await image.read()
        except Exception as e:
            logger.error(
                f"Failed to read image upload for server {sid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": f"Failed to read image: {str(e)}"}
                },
            )

        content_type = image.content_type or "application/octet-stream"

        try:
            emoji = reactions.create_custom_emoji(
                user_id=current_user.user_id,
                server_id=sid,
                name=name,
                image_data=image_data,
                content_type=content_type,
            )

            _invalidate_emoji_cache(sid, emoji.id)

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
                        "message": f"Failed to create emoji: {str(e)}",
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
        sid = int(server_id)

        # Check permissions (fail closed when the servers module is absent)
        _require_manage_emojis(servers_mod, current_user.user_id, sid)

        try:
            emoji = reactions.update_custom_emoji(
                user_id=current_user.user_id,
                emoji_id=emoji_id,
                name=body.name,
            )

            _invalidate_emoji_cache(sid, emoji_id)

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
                detail={"error": {"code": 500, "message": f"Update failed: {str(e)}"}},
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
            sid = int(server_id)
            eid = int(emoji_id)
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

        try:
            reactions.delete_custom_emoji(current_user.user_id, eid)

            _invalidate_emoji_cache(sid, eid)

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
                detail={
                    "error": {"code": 500, "message": f"Deletion failed: {str(e)}"}
                },
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
