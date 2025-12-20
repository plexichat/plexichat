"""
Emoji routes - Custom emoji management endpoints.
"""

from typing import List, Optional, Any, Dict
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field, ConfigDict, field_serializer

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo

router = APIRouter()


class EmojiResponse(BaseModel):
    """Custom emoji response model."""
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Emoji ID")
    server_id: str = Field(..., description="Server ID")
    name: str = Field(..., description="Emoji name")
    animated: bool = Field(False, description="Whether emoji is animated")
    url: str = Field("", description="Emoji image URL")
    available: bool = Field(True, description="Whether emoji is available for use")
    created_by: str = Field(..., description="User ID who created the emoji")
    created_at: int = Field(..., description="Creation timestamp")

    @field_serializer("id", "server_id", "created_by")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None


class EmojiCountsResponse(BaseModel):
    """Emoji counts response model."""
    static: int = Field(..., description="Number of static emojis")
    animated: int = Field(..., description="Number of animated emojis")
    max_static: int = Field(..., description="Maximum static emojis allowed")
    max_animated: int = Field(..., description="Maximum animated emojis allowed")


class EmojiUpdateRequest(BaseModel):
    """Emoji update request model."""
    name: Optional[str] = Field(None, description="New emoji name")


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
        created_at=emoji.created_at,
    )


@router.get("/{server_id}/emojis", response_model=List[EmojiResponse])
async def get_server_emojis(
    server_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get all custom emojis for a server.
    
    Returns a list of custom emojis available in the server.
    """
    reactions = api.get_reactions()
    servers = api.get_servers()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    # Check if user is member of server
    if servers:
        member = servers.get_member(sid, current_user.user_id)
        if not member:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Not a member of this server"}})

    try:
        emojis = reactions.get_server_custom_emojis(sid)
        return [_emoji_to_response(e) for e in emojis]
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/{server_id}/emojis/counts", response_model=EmojiCountsResponse)
async def get_emoji_counts(
    server_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get emoji counts and limits for a server.
    
    Returns current emoji counts and maximum limits.
    """
    reactions = api.get_reactions()
    servers = api.get_servers()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    if servers:
        member = servers.get_member(sid, current_user.user_id)
        if not member:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Not a member of this server"}})

    try:
        counts = reactions.get_emoji_counts(sid)
        return EmojiCountsResponse(**counts)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/{server_id}/emojis/{emoji_id}", response_model=EmojiResponse)
async def get_emoji(
    server_id: str,
    emoji_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get a specific custom emoji.
    
    Returns emoji details if found and user has access.
    """
    reactions = api.get_reactions()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        eid = int(emoji_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid emoji ID"}})

    try:
        emoji = reactions.get_custom_emoji(eid)
        if not emoji:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Emoji not found"}})

        # Verify emoji belongs to the server
        if str(emoji.server_id) != server_id:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Emoji not found"}})

        return _emoji_to_response(emoji)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/{server_id}/emojis", response_model=EmojiResponse)
async def create_emoji(
    server_id: str,
    name: str = Form(..., description="Emoji name (2-32 alphanumeric characters)"),
    image: UploadFile = File(..., description="Emoji image (PNG, GIF, or WebP, max 256KB)"),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Create a custom emoji for a server.
    
    Uploads an image and creates a new custom emoji.
    Requires server.manage permission.
    """
    reactions = api.get_reactions()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    # Read image data
    try:
        image_data = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"Failed to read image: {str(e)}"}})

    content_type = image.content_type or "application/octet-stream"

    try:
        emoji = reactions.create_custom_emoji(
            user_id=current_user.user_id,
            server_id=sid,
            name=name,
            image_data=image_data,
            content_type=content_type,
        )
        return _emoji_to_response(emoji)
    except Exception as e:
        exc_name = type(e).__name__
        if "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Limit" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Name" in exc_name or "Invalid" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Size" in exc_name or "File" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Exists" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.patch("/{server_id}/emojis/{emoji_id}", response_model=EmojiResponse)
async def update_emoji(
    server_id: str,
    emoji_id: str,
    body: EmojiUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update a custom emoji.
    
    Currently only supports renaming the emoji.
    Requires server.manage permission.
    """
    reactions = api.get_reactions()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        eid = int(emoji_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid emoji ID"}})

    try:
        emoji = reactions.update_custom_emoji(
            user_id=current_user.user_id,
            emoji_id=eid,
            name=body.name,
        )
        return _emoji_to_response(emoji)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Emoji not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Name" in exc_name or "Invalid" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Exists" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/{server_id}/emojis/{emoji_id}")
async def delete_emoji(
    server_id: str,
    emoji_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """
    Delete a custom emoji.
    
    Permanently removes the emoji from the server.
    Requires server.manage permission.
    """
    reactions = api.get_reactions()

    if not reactions:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Reactions module not available"}})

    try:
        eid = int(emoji_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid emoji ID"}})

    try:
        reactions.delete_custom_emoji(current_user.user_id, eid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Emoji not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})
