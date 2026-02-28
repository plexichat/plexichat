"""
Emoji routes - Custom emoji management endpoints.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.core.database.cache import cached
from src.api.schemas.emojis import (
    EmojiResponse,
    EmojiCountsResponse,
    EmojiUpdateRequest,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse

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

        # Check if user is member of server
        if servers:
            try:
                member = servers.get_member(sid, current_user.user_id)
                if not member:
                    logger.warning(
                        f"User {current_user.user_id} attempted to access emojis for server {sid} they are not in"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Not a member of this server",
                            }
                        },
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error checking membership for server {sid}: {e}", exc_info=True
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/emojis/counts",
    response_model=EmojiCountsResponse,
    summary="Get emoji counts",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        403: {"model": ErrorResponse, "description": "Not a member of this server"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
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

        if servers:
            try:
                member = servers.get_member(sid, current_user.user_id)
                if not member:
                    logger.warning(
                        f"User {current_user.user_id} attempted to access emoji counts for server {sid} they are not in"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Not a member of this server",
                            }
                        },
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error checking membership for server {sid}: {e}", exc_info=True
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/emojis/{emoji_id}",
    response_model=EmojiResponse,
    summary="Get specific emoji",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid emoji ID"},
        404: {"model": ErrorResponse, "description": "Emoji not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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

        # Check permissions
        if servers_mod:
            perms = servers_mod.get_permissions(current_user.user_id, sid)
            from src.core.servers.permissions import has_permission

            if not (
                has_permission(perms, "emojis.manage")
                or has_permission(perms, "server.manage")
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Missing emojis.manage permission",
                        }
                    },
                )

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
            
            # Dispatch WebSocket event
            await _dispatch_emoji_update(sid)
            
            return _emoji_to_response(emoji)
        except Exception as e:
            exc_name = type(e).__name__
            if "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to create emoji in server {sid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )
            elif "Limit" in exc_name:
                logger.warning(f"Emoji limit reached for server {sid}")
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Name" in exc_name or "Invalid" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Size" in exc_name or "File" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Exists" in exc_name:
                raise HTTPException(
                    status_code=409, detail={"error": {"code": 409, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
    },
)
async def update_emoji(
    server_id: str,
    emoji_id: str,
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
        try:
            sid = int(server_id)
            eid = int(emoji_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid ID format for emoji update: {server_id}/{emoji_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        # Check permissions
        if servers_mod:
            perms = servers_mod.get_permissions(current_user.user_id, sid)
            from src.core.servers.permissions import has_permission

            if not (
                has_permission(perms, "emojis.manage")
                or has_permission(perms, "server.manage")
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Missing emojis.manage permission",
                        }
                    },
                )

        try:
            emoji = reactions.update_custom_emoji(
                user_id=current_user.user_id,
                emoji_id=eid,
                name=body.name,
            )
            
            # Dispatch WebSocket event
            await _dispatch_emoji_update(sid)
            
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
                    f"User {current_user.user_id} denied permission to update emoji {eid}"
                )
                raise HTTPException(
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
                )
            elif "Name" in exc_name or "Invalid" in exc_name:
                raise HTTPException(
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
                )
            elif "Exists" in exc_name:
                raise HTTPException(
                    status_code=409, detail={"error": {"code": 409, "message": str(e)}}
                )

            logger.error(f"Failed to update emoji {eid}: {e}", exc_info=True)
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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

        # Check permissions
        if servers_mod:
            perms = servers_mod.get_permissions(current_user.user_id, sid)
            from src.core.servers.permissions import has_permission

            if not (
                has_permission(perms, "emojis.manage")
                or has_permission(perms, "server.manage")
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Missing emojis.manage permission",
                        }
                    },
                )

        try:
            reactions.delete_custom_emoji(current_user.user_id, eid)
            
            # Dispatch WebSocket event
            await _dispatch_emoji_update(sid)
            
            return SuccessResponse(success=True)
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
                    status_code=403, detail={"error": {"code": 403, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
