"""
User routes - User profile endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import UserResponse
from src.api.schemas.users import UserUpdateRequest, UserPublicResponse

router = APIRouter()


def _user_to_response(user, include_private: bool = False) -> UserResponse:
    """Convert user object to response model."""
    return UserResponse(
        id=str(user.id),
        username=user.username,
        email=getattr(user, "email", None) if include_private else None,
        avatar_url=getattr(user, "avatar_url", None),
        created_at=user.created_at,
        email_verified=getattr(user, "email_verified", False) if include_private else False,
        totp_enabled=getattr(user, "totp_enabled", False) if include_private else False,
    )


def _user_to_public_response(user) -> UserPublicResponse:
    """Convert user object to public response model."""
    return UserPublicResponse(
        id=str(user.id),
        username=user.username,
        avatar_url=getattr(user, "avatar_url", None),
        created_at=user.created_at,
    )


@router.get("/@me", response_model=UserResponse)
async def get_current_user_info(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get current user information.
    
    Returns the authenticated user's profile including private fields.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    try:
        user = auth.get_user(current_user.user_id)
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        
        return _user_to_response(user, include_private=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.patch("/@me", response_model=UserResponse)
async def update_current_user(
    body: UserUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update current user profile.
    
    Updates the authenticated user's profile fields.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    try:
        update_data = body.model_dump(exclude_unset=True)
        
        if "password" in update_data and update_data["password"]:
            if not update_data.get("current_password"):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Current password required to change password"}}
                )
            auth.change_password(
                current_user.user_id,
                update_data["current_password"],
                update_data["password"]
            )
            del update_data["password"]
            if "current_password" in update_data:
                del update_data["current_password"]
        
        if "current_password" in update_data:
            del update_data["current_password"]
        
        # Note: update_user not implemented yet, only password changes supported
        if update_data:
            # For now, just return current user - profile updates not yet supported
            pass
        
        user = auth.get_user(current_user.user_id)
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        
        return _user_to_response(user, include_private=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "Exists" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        elif "Invalid" in exc_name or "Weak" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


from fastapi import File, UploadFile

@router.post("/@me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Upload user avatar.
    
    Accepts image file upload and updates the user's avatar.
    """
    auth = api.get_auth()
    media = api.get_media()
    
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    if not media:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Media module not available. Please restart the server to initialize the media module."}})
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "File must be an image"}})
    
    # Read file data
    try:
        file_data = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"Failed to read file: {str(e)}"}})
    
    # Check file size (5MB max for avatars)
    if len(file_data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Avatar must be less than 5MB"}})
    
    try:
        # Upload the file
        result = media.upload_file(
            user_id=current_user.user_id,
            file_data=file_data,
            filename=file.filename or "avatar.png",
            content_type=file.content_type
        )
        
        # Update user's avatar_url in database
        db = api.get_db()
        if db and result.url:
            db.execute(
                "UPDATE auth_users SET avatar_url = ? WHERE id = ?",
                (result.url, current_user.user_id)
            )
        
        return {
            "success": True,
            "avatar_url": result.url,
            "file_id": str(result.file_id)
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "Size" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Type" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}})


@router.get("/@me/channels")
async def get_dm_channels(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all DM channels for the current user.
    """
    messaging = api.get_messaging()
    
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})
    
    try:
        # Try to get DM conversations if the method exists
        if hasattr(messaging, 'get_dm_channels'):
            channels = messaging.get_dm_channels(current_user.user_id)
        elif hasattr(messaging, 'get_conversations'):
            # Fallback to get_conversations and filter for DMs
            channels = messaging.get_conversations(current_user.user_id)
            channels = [c for c in (channels or []) if getattr(c, 'conversation_type', None) == 'dm']
        else:
            # DM channels not yet implemented
            return []
        
        auth = api.get_auth()
        result = []
        for ch in (channels or []):
            recipient_id = getattr(ch, "recipient_id", None)
            recipient_username = None
            if recipient_id and auth:
                try:
                    user = auth.get_user(recipient_id)
                    if user:
                        recipient_username = user.username
                except Exception:
                    pass
            
            result.append({
                "id": str(ch.id),
                "type": "dm",
                "recipient_id": str(recipient_id) if recipient_id else None,
                "recipient": {
                    "id": str(recipient_id),
                    "username": recipient_username or f"User {recipient_id}"
                } if recipient_id else None,
                "last_message_id": str(ch.last_message_id) if hasattr(ch, "last_message_id") and ch.last_message_id else None,
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/@me/channels")
async def create_dm_channel(body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """
    Create or get a DM channel with a user.
    """
    messaging = api.get_messaging()
    auth = api.get_auth()
    
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})
    
    recipient_id = body.get("recipient_id")
    if not recipient_id:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "recipient_id required"}})
    
    try:
        rid = int(recipient_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid recipient ID"}})
    
    try:
        # Try different method names
        if hasattr(messaging, 'get_or_create_dm'):
            channel = messaging.get_or_create_dm(current_user.user_id, rid)
        elif hasattr(messaging, 'create_dm'):
            channel = messaging.create_dm(current_user.user_id, rid)
        else:
            raise HTTPException(status_code=501, detail={"error": {"code": 501, "message": "DM creation not implemented"}})
        
        recipient_username = None
        if auth:
            try:
                user = auth.get_user(rid)
                if user:
                    recipient_username = user.username
            except Exception:
                pass
        
        return {
            "id": str(channel.id),
            "type": "dm",
            "recipient_id": str(rid),
            "recipient": {
                "id": str(rid),
                "username": recipient_username or f"User {rid}"
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        elif "Blocked" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Cannot message this user"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/search")
async def search_user_by_username(
    username: str = None,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Search for a user by username.
    
    Returns the user if found by exact username match.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    if not username:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Username required"}})
    
    try:
        # Try to find user by username
        if hasattr(auth, 'get_user_by_username'):
            user = auth.get_user_by_username(username)
        else:
            # Fallback: search in database directly
            db = api.get_db()
            if db:
                row = db.fetch_one(
                    "SELECT id, username, avatar_url, created_at FROM auth_users WHERE username = ? COLLATE NOCASE",
                    (username,)
                )
                if row:
                    return {
                        "id": str(row["id"]),
                        "username": row["username"],
                        "avatar_url": row.get("avatar_url"),
                        "created_at": row.get("created_at"),
                    }
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        
        return _user_to_public_response(user)
    except HTTPException:
        raise
    except Exception as e:
        if "NotFound" in type(e).__name__:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/{user_id}", response_model=UserPublicResponse)
async def get_user(user_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get user by ID.
    
    Returns public profile information for the specified user.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
    
    try:
        user = auth.get_user(uid)
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        
        return _user_to_public_response(user)
    except HTTPException:
        raise
    except Exception as e:
        if "NotFound" in type(e).__name__:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        raise
