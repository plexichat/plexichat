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
        
        if update_data:
            user = auth.update_user(current_user.user_id, **update_data)
        else:
            user = auth.get_user(current_user.user_id)
        
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
