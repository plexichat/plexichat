"""
Avatar routes - Endpoints for user avatars and server icons.
"""

from fastapi import APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.responses import Response

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.avatars import AvatarUploadResponse, IconUploadResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse
import utils.logger as logger

router = APIRouter(tags=["Avatars"])


@router.get(
    "/users/{user_id}",
    summary="Get user avatar",
    responses={
        200: {
            "description": "The avatar image file",
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/gif": {},
                "image/webp": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        404: {"model": ErrorResponse, "description": "Avatar not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_avatar(user_id: str):
    """
    Get user avatar image.

    Returns the avatar image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for avatar request: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            result = avatars.get_user_avatar_data(uid)
            if not result:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Avatar not found"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch avatar for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch avatar"}},
            )

        avatar_data, content_type = result

        return Response(
            content=avatar_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                "Content-Length": str(len(avatar_data)),
                # CORS headers to prevent OpaqueResponseBlocking
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_user_avatar for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/users/@me",
    response_model=AvatarUploadResponse,
    summary="Upload my avatar",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or read error"},
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_my_avatar(
    file: UploadFile = File(...), current_user: TokenInfo = Depends(get_current_user)
) -> AvatarUploadResponse:
    """
    Upload current user's avatar.

    Accepts image file upload and stores it in the database.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        logger.warning(
            f"User {current_user.user_id} attempted to upload non-image file: {file.content_type}"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "File must be an image"}},
        )

    # Read file data
    try:
        file_data = await file.read()
    except Exception as e:
        logger.error(
            f"Failed to read avatar file upload from user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": f"Failed to read file: {str(e)}"}
            },
        )

    try:
        try:
            result = avatars.upload_user_avatar(
                user_id=current_user.user_id,
                image_data=file_data,
                content_type=file.content_type,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        except Exception as e:
            logger.error(
                f"Failed to upload avatar for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}},
            )

        # Invalidate user cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"user:*{current_user.user_id}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for user {current_user.user_id} after avatar upload: {e}"
            )

        return AvatarUploadResponse(
            success=True,
            avatar_url=result["url"],
            width=result["width"],
            height=result["height"],
            size=result["size"],
            animated=result["animated"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_my_avatar for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/users/@me",
    response_model=SuccessResponse,
    summary="Delete my avatar",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_my_avatar(
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Delete current user's avatar.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        try:
            deleted = avatars.delete_user_avatar(current_user.user_id)
        except Exception as e:
            logger.error(
                f"Failed to delete avatar for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to delete avatar"}},
            )

        # Invalidate user cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"user:*{current_user.user_id}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for user {current_user.user_id} after avatar deletion: {e}"
            )

        return SuccessResponse(success=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_my_avatar for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/servers/{server_id}",
    summary="Get server icon",
    responses={
        200: {
            "description": "The server icon image file",
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/gif": {},
                "image/webp": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        404: {"model": ErrorResponse, "description": "Icon not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_icon(server_id: str):
    """
    Get server icon image.

    Returns the icon image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format for icon request: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        try:
            result = avatars.get_server_icon_data(sid)
            if not result:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Icon not found"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch icon for server {server_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch icon"}},
            )

        icon_data, content_type = result

        return Response(
            content=icon_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                "Content-Length": str(len(icon_data)),
                # CORS headers to prevent OpaqueResponseBlocking
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_server_icon for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/servers/{server_id}",
    response_model=IconUploadResponse,
    summary="Upload server icon",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or read error"},
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> IconUploadResponse:
    """
    Upload server icon.

    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()

    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    if not servers:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            logger.warning(
                f"User {current_user.user_id} provided invalid server ID for icon upload: {server_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permission
        try:
            server = servers.get_server(sid, current_user.user_id)
            if not server:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Server not found"}},
                )

            # Check if user is owner or has permission
            is_owner = server.owner_id == current_user.user_id
            has_permission = False

            if not is_owner:
                member = servers.get_member(sid, current_user.user_id)
                if member:
                    # Check for server.manage permission
                    has_permission = servers.has_permission(
                        current_user.user_id, sid, "server.manage"
                    )

            if not is_owner and not has_permission:
                logger.warning(
                    f"User {current_user.user_id} denied MANAGE_SERVER for server {sid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Permission denied"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error checking permissions for server {sid} icon upload: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Failed to verify permissions"}
                },
            )

        # Validate file type
        if not file.content_type or not file.content_type.startswith("image/"):
            logger.warning(
                f"User {current_user.user_id} attempted to upload non-image icon for server {sid}: {file.content_type}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "File must be an image"}},
            )

        # Read file data
        try:
            file_data = await file.read()
        except Exception as e:
            logger.error(
                f"Failed to read icon file upload for server {sid} from user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": f"Failed to read file: {str(e)}"}
                },
            )

        try:
            result = avatars.upload_server_icon(
                server_id=sid, image_data=file_data, content_type=file.content_type
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        except Exception as e:
            logger.error(f"Failed to upload icon for server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}},
            )

        return IconUploadResponse(
            success=True,
            icon_url=result["url"],
            width=result["width"],
            height=result["height"],
            size=result["size"],
            animated=result["animated"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_server_icon for server {server_id} by user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/servers/{server_id}",
    response_model=SuccessResponse,
    summary="Delete server icon",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_server_icon(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete server icon.

    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()

    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    if not servers:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid server ID format for icon deletion: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permission
        try:
            server = servers.get_server(sid, current_user.user_id)
            if not server:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Server not found"}},
                )

            is_owner = server.owner_id == current_user.user_id
            has_permission = False

            if not is_owner:
                member = servers.get_member(sid, current_user.user_id)
                if member:
                    has_permission = servers.has_permission(
                        current_user.user_id, sid, "server.manage"
                    )

            if not is_owner and not has_permission:
                logger.warning(
                    f"User {current_user.user_id} denied MANAGE_SERVER for server {sid} icon deletion"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Permission denied"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error checking permissions for server {sid} icon deletion: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Failed to verify permissions"}
                },
            )

        try:
            deleted = avatars.delete_server_icon(sid)
        except Exception as e:
            logger.error(f"Failed to delete icon for server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": f"Deletion failed: {str(e)}"}
                },
            )

        # Invalidate server cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"server:*{sid}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for server {sid} after icon deletion: {e}"
            )

        return SuccessResponse(success=deleted)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_server_icon for server {server_id} by user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
