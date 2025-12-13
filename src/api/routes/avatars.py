"""
Avatar routes - Endpoints for user avatars and server icons.
"""

from fastapi import APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.responses import Response

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()


@router.get("/users/{user_id}")
async def get_user_avatar(user_id: str):
    """
    Get user avatar image.
    
    Returns the avatar image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

    result = avatars.get_user_avatar_data(uid)
    if not result:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Avatar not found"}})

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
        }
    )


@router.post("/users/@me")
async def upload_my_avatar(
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Upload current user's avatar.
    
    Accepts image file upload and stores it in the database.
    """
    avatars = api.get_avatars()
    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "File must be an image"}})

    # Read file data
    try:
        file_data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"Failed to read file: {str(e)}"}})

    try:
        result = avatars.upload_user_avatar(
            user_id=current_user.user_id,
            image_data=file_data,
            content_type=file.content_type
        )

        # Invalidate user cache
        from src.core.database import invalidate_pattern
        invalidate_pattern(f"user:*{current_user.user_id}*")

        return {
            "success": True,
            "avatar_url": result["url"],
            "width": result["width"],
            "height": result["height"],
            "size": result["size"],
            "animated": result["animated"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}})


@router.delete("/users/@me")
async def delete_my_avatar(current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete current user's avatar.
    """
    avatars = api.get_avatars()
    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    deleted = avatars.delete_user_avatar(current_user.user_id)

    # Invalidate user cache
    from src.core.database import invalidate_pattern
    invalidate_pattern(f"user:*{current_user.user_id}*")

    return {"success": deleted}


@router.get("/servers/{server_id}")
async def get_server_icon(server_id: str):
    """
    Get server icon image.
    
    Returns the icon image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    result = avatars.get_server_icon_data(sid)
    if not result:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Icon not found"}})

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
        }
    )


@router.post("/servers/{server_id}")
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Upload server icon.
    
    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()

    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    if not servers:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    # Check permission
    server = servers.get_server(sid)
    if not server:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})

    # Check if user is owner or has permission
    is_owner = server.owner_id == current_user.user_id
    has_permission = False

    if not is_owner:
        member = servers.get_member(sid, current_user.user_id)
        if member:
            # Check for MANAGE_SERVER permission
            has_permission = servers.member_has_permission(sid, current_user.user_id, "MANAGE_SERVER")

    if not is_owner and not has_permission:
        raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Permission denied"}})

    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "File must be an image"}})

    # Read file data
    try:
        file_data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"Failed to read file: {str(e)}"}})

    try:
        result = avatars.upload_server_icon(
            server_id=sid,
            image_data=file_data,
            content_type=file.content_type
        )

        return {
            "success": True,
            "icon_url": result["url"],
            "width": result["width"],
            "height": result["height"],
            "size": result["size"],
            "animated": result["animated"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}})


@router.delete("/servers/{server_id}")
async def delete_server_icon(
    server_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Delete server icon.
    
    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()

    if not avatars:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Avatars module not available"}})

    if not servers:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    # Check permission
    server = servers.get_server(sid)
    if not server:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})

    is_owner = server.owner_id == current_user.user_id
    has_permission = False

    if not is_owner:
        member = servers.get_member(sid, current_user.user_id)
        if member:
            has_permission = servers.member_has_permission(sid, current_user.user_id, "MANAGE_SERVER")

    if not is_owner and not has_permission:
        raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Permission denied"}})

    deleted = avatars.delete_server_icon(sid)

    return {"success": deleted}
