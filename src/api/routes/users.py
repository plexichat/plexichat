"""
User routes - User profile endpoints.
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import UserResponse
from src.api.schemas.users import (
    UserUpdateRequest,
    UserPublicResponse,
    UserAvatarResponse,
)
from src.api.schemas.channels import (
    DMChannelResponse,
    RecipientResponse,
    DMChannelCreateRequest,
    NotesChannelResponse,
)
from src.api.schemas.messages import (
    MessagingSettingsResponse,
    MessagingSettingsUpdateRequest,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse
from src.core.database import cached, invalidate_pattern

router = APIRouter(tags=["Users"])


def _get_attr(obj, key, default=None):
    """Get attribute from object or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _user_to_response(user, include_private: bool = False) -> UserResponse:
    """Convert user object or dict to response model."""
    try:
        user_id = int(_get_attr(user, "id") or 0)
        
        # Get badges from user object or re-fetch if missing
        badges = _get_attr(user, "badges")
        if badges is None:
            badges = []
            try:
                from src.core import features
                if features._setup_complete:
                    badges = features.get_user_badges(user_id)
            except Exception:
                pass

        return UserResponse(
            id=SnowflakeID(user_id),
            username=str(_get_attr(user, "username") or ""),
            email=_get_attr(user, "email") if include_private else None,
            avatar_url=_get_attr(user, "avatar_url"),
            created_at=int(_get_attr(user, "created_at") or 0),
            email_verified=_get_attr(user, "email_verified", False)
            if include_private
            else False,
            totp_enabled=_get_attr(user, "totp_enabled", False)
            if include_private
            else False,
            badges=badges,
        )
    except Exception as e:
        logger.error(f"Error converting user object to response: {e}")
        raise e


def _user_to_public_response(user) -> UserPublicResponse:
    """Convert user object or dict to public response model."""
    try:
        user_id = _get_attr(user, "id")
        
        # Use badges from user object if available (already joined in AuthManager)
        # Fallback to empty list or re-fetch only if absolutely necessary
        badges = _get_attr(user, "badges")
        
        # If badges is None or empty list, double check features module if setup
        # (This is a safety fallback for any migration gap)
        if badges is None:
            badges = []
            try:
                from src.core import features
                if features._setup_complete:
                    badges = features.get_user_badges(user_id)
            except Exception as e:
                logger.debug(f"Failed to fetch badges for user {user_id}: {e}")

        return UserPublicResponse(
            id=SnowflakeID(user_id),
            username=str(_get_attr(user, "username") or ""),
            avatar_url=_get_attr(user, "avatar_url"),
            created_at=int(_get_attr(user, "created_at") or 0),
            badges=badges,
        )
    except Exception as e:
        logger.error(f"Error converting user object to public response: {e}")
        raise e


def _user_to_dict(user) -> dict:
    """Convert user object to JSON-serializable dict for caching."""
    try:
        account_type = getattr(user, "account_type", None)
        # Convert AccountType enum to string if needed
        if account_type is not None and hasattr(account_type, "value"):
            account_type = account_type.value
        return {
            "id": user.id,
            "username": user.username,
            "email": getattr(user, "email", None),
            "avatar_url": getattr(user, "avatar_url", None),
            "created_at": user.created_at,
            "email_verified": getattr(user, "email_verified", False),
            "totp_enabled": getattr(user, "totp_enabled", False),
            "account_type": account_type,
            "permissions": getattr(user, "permissions", {}),
            "badges": getattr(user, "badges", []),
        }
    except Exception as e:
        logger.error(f"Error converting user object to dict: {e}")
        raise e


def _get_user_cached(user_id: int):
    """Get user with caching - returns JSON-serializable dict."""
    try:
        auth = api.get_auth()
        if not auth:
            return None
        user = auth.get_user(user_id)
        return _user_to_dict(user) if user else None
    except Exception as e:
        logger.debug(f"Cache fetch failed for user {user_id}: {e}")
        return None


# Apply caching to the internal function (60s TTL for user data)
_get_user_cached = cached(ttl=60, prefix="user_api")(_get_user_cached)


@router.get(
    "/@me",
    response_model=UserResponse,
    summary="Get current user",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="current_user_api")
def get_current_user_info(
    current_user: TokenInfo = Depends(get_current_user),
) -> UserResponse:
    """
    Get current user information.

    Returns the authenticated user's profile including private fields.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        account_type = getattr(current_user, "account_type", None)
        if hasattr(account_type, "value"):
            account_type = account_type.value  # type: ignore

        lookup_id = current_user.user_id
        if account_type != "bot":
            lookup_id = current_user.account_id

        user = _get_user_cached(lookup_id)
        if not user:
            logger.warning(
                f"User profile not found for account {lookup_id}"
            )
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        return _user_to_response(user, include_private=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get info for user {current_user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/@me",
    response_model=UserResponse,
    summary="Update current user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid update data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Incorrect current password"},
        404: {"model": ErrorResponse, "description": "User not found"},
        409: {"model": ErrorResponse, "description": "User already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_current_user(
    body: UserUpdateRequest, current_user: TokenInfo = Depends(get_current_user)
) -> UserResponse:
    """
    Update current user profile.

    Updates the authenticated user's profile fields.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        update_data = body.model_dump(exclude_unset=True)

        if "password" in update_data and update_data["password"]:
            if not update_data.get("current_password"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Current password required to change password",
                        }
                    },
                )
            try:
                auth.change_password(
                    current_user.user_id,
                    update_data["current_password"],
                    update_data["password"],
                )
            except Exception as e:
                exc_name = type(e).__name__
                if "Password" in exc_name or "Auth" in exc_name:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Incorrect current password",
                            }
                        },
                    )
                raise

            del update_data["password"]
            if "current_password" in update_data:
                del update_data["current_password"]

        if "current_password" in update_data:
            del update_data["current_password"]

        # Update profile fields via auth module (replaces direct database access)
        if update_data:
            try:
                user = auth.update_user(
                    current_user.user_id,
                    username=update_data.get("username"),
                    email=update_data.get("email"),
                )
                # Invalidate user cache
                try:
                    invalidate_pattern(f"user_data:*{current_user.user_id}*")
                except Exception as ce:
                    logger.debug(
                        f"Cache invalidation failed for user {current_user.user_id}: {ce}"
                    )
                return _user_to_response(user, include_private=True)
            except Exception as e:
                exc_name = type(e).__name__
                if "Exists" in exc_name:
                    raise HTTPException(
                        status_code=409,
                        detail={"error": {"code": 409, "message": str(e)}},
                    )
                elif "Invalid" in exc_name or "Weak" in exc_name:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                raise

        user = auth.get_user(current_user.user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        return _user_to_response(user, include_private=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update user {current_user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/@me/avatar",
    response_model=UserAvatarResponse,
    summary="Upload avatar",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file or upload error"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_avatar(
    file: UploadFile = File(...), current_user: TokenInfo = Depends(get_current_user)
) -> UserAvatarResponse:
    """
    Upload user avatar.

    Accepts image file upload and stores it in the database.
    Uses the avatars module for processing and storage.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        # Validate file type
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "File must be an image"}},
            )

        # Read file data
        try:
            file_data = await file.read()
        except Exception as e:
            logger.warning(
                f"Failed to read upload file for user {current_user.user_id}: {e}"
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": f"Failed to read file: {str(e)}"}
                },
            )

        try:
            result = avatars.upload_user_avatar(
                user_id=current_user.user_id,
                image_data=file_data,
                content_type=file.content_type,
            )

            # Invalidate user cache so the new avatar_url is returned immediately
            try:
                invalidate_pattern(f"user:*{current_user.user_id}*")
            except Exception as ce:
                logger.debug(
                    f"Cache invalidation failed for user {current_user.user_id}: {ce}"
                )

            return UserAvatarResponse(
                success=True,
                avatar_url=result["url"],
                width=result["width"],
                height=result["height"],
                size=result["size"],
                animated=result["animated"],
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        except Exception as e:
            logger.error(
                f"Avatar upload failed for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": f"Upload failed: {str(e)}"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_avatar for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/@me/notes",
    response_model=NotesChannelResponse,
    summary="Get user notes channel",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        501: {"model": ErrorResponse, "description": "Notes not implemented"},
    },
)
async def get_notes_channel(
    current_user: TokenInfo = Depends(get_current_user),
) -> NotesChannelResponse:
    """
    Get or create the personal notes channel for the current user.

    Personal notes are a single-user conversation for storing private notes
    that sync across devices.
    """
    messaging = api.get_messaging()
    if not messaging:
        logger.error("Messaging module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        # Get or create notes conversation
        try:
            if hasattr(messaging, "get_or_create_notes"):
                channel = messaging.get_or_create_notes(current_user.user_id)
            else:
                raise HTTPException(
                    status_code=501,
                    detail={"error": {"code": 501, "message": "Notes not implemented"}},
                )

            return NotesChannelResponse(
                id=SnowflakeID(channel.id),
                channel_type="notes",
                name="Personal Notes",
                last_message_id=SnowflakeID(channel.last_message_id)
                if channel.last_message_id
                else None,
                last_message_at=channel.last_message_at,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to get/create notes for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notes_channel for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/@me/channels",
    response_model=List[DMChannelResponse],
    summary="List DM channels",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="user_dm_channels_api")
def get_dm_channels(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[DMChannelResponse]:
    """
    Get all DM channels for the current user.
    """
    messaging = api.get_messaging()
    if not messaging:
        logger.error("Messaging module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        # Try to get DM conversations if the method exists
        channels = []
        if hasattr(messaging, "get_dm_channels"):
            channels = messaging.get_dm_channels(current_user.user_id)
        elif hasattr(messaging, "get_conversations"):
            # Fallback to get_conversations and filter for DMs
            channels = messaging.get_conversations(current_user.user_id)
            channels = [
                c
                for c in (channels or [])
                if getattr(c, "conversation_type", None) == "dm"
            ]

        auth = api.get_auth()
        result = []
        
        # Optimize by bulk fetching users to avoid N+1 queries
        if channels and auth:
            recipient_ids = []
            for ch in channels:
                rid = getattr(ch, "recipient_id", None)
                if rid:
                    recipient_ids.append(rid)
            
            # Use profiles bulk which is safer for public info
            users_map = {}
            if recipient_ids:
                try:
                    users_map = auth.get_user_profiles_bulk(recipient_ids)
                except Exception:
                    pass

            for ch in channels:
                try:
                    rid = getattr(ch, "recipient_id", None)
                    recipient_username = None
                    if rid:
                        user_data = users_map.get(str(rid))
                        if user_data:
                            recipient_username = user_data.get("username")
                        else:
                            # Fallback if not in bulk (shouldn't happen often)
                            try:
                                user = auth.get_user(rid)
                                if user:
                                    recipient_username = user.username
                            except Exception:
                                pass

                    result.append(
                        DMChannelResponse(
                            id=SnowflakeID(ch.id),
                            channel_type="dm",
                            recipient_id=SnowflakeID(rid) if rid else None,
                            recipient=RecipientResponse(
                                id=SnowflakeID(rid),
                                username=recipient_username or f"User {rid}",
                            ) if rid else None,
                            last_message_id=SnowflakeID(ch.last_message_id)
                            if hasattr(ch, "last_message_id") and ch.last_message_id
                            else None,
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to process DM channel {getattr(ch, 'id', 'unknown')}: {e}")
                    continue
        
        return result
    except Exception as e:
        logger.error(
            f"Failed to get DM channels for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/@me/channels",
    response_model=DMChannelResponse,
    summary="Create DM channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid recipient ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Cannot message this user"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        501: {"model": ErrorResponse, "description": "DM creation not implemented"},
    },
)
async def create_dm_channel(
    body: DMChannelCreateRequest, current_user: TokenInfo = Depends(get_current_user)
) -> DMChannelResponse:
    """
    Create or get a DM channel with a user.
    """
    messaging = api.get_messaging()
    auth = api.get_auth()

    if not messaging:
        logger.error("Messaging module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            rid = int(body.recipient_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid recipient ID format for DM: {body.recipient_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid recipient ID"}},
            )

        try:
            # Try different method names
            if hasattr(messaging, "get_or_create_dm"):
                channel = messaging.get_or_create_dm(current_user.user_id, rid)
            elif hasattr(messaging, "create_dm"):
                channel = messaging.create_dm(current_user.user_id, rid)
            else:
                raise HTTPException(
                    status_code=501,
                    detail={
                        "error": {"code": 501, "message": "DM creation not implemented"}
                    },
                )

            recipient_username = None
            if auth:
                try:
                    user = auth.get_user(rid)
                    if user:
                        recipient_username = user.username
                except Exception:
                    pass

            return DMChannelResponse(
                id=SnowflakeID(channel.id),
                channel_type="dm",
                recipient_id=SnowflakeID(rid),
                recipient=RecipientResponse(
                    id=SnowflakeID(rid), username=recipient_username or f"User {rid}"
                ),
            )
        except HTTPException:
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            elif "Blocked" in exc_name or "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to DM user {rid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {"code": 403, "message": "Cannot message this user"}
                    },
                )

            logger.error(
                f"Failed to create DM channel with user {rid} for {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_dm_channel for recipient {body.recipient_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/search",
    response_model=UserPublicResponse,
    summary="Search user",
    responses={
        400: {"model": ErrorResponse, "description": "Username required"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def search_user_by_username(
    username: Optional[str] = None, current_user: TokenInfo = Depends(get_current_user)
) -> UserPublicResponse:
    """
    Search for a user by username.

    Returns the user if found by exact username match.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    if not username:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Username required"}},
        )

    try:
        # Try to find user by username
        try:
            user = auth.get_user_by_username(username)
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
            return _user_to_public_response(user)
        except HTTPException:
            raise
        except Exception as e:
            if "NotFound" in type(e).__name__:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            logger.error(f"Search failed for username '{username}': {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in search_user_by_username for '{username}': {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{user_id}",
    response_model=UserPublicResponse,
    summary="Get user by ID",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> UserPublicResponse:
    """
    Get user by ID.

    Returns public profile information for the specified user.
    """
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        try:
            uid = int(user_id)
            if uid > 2**63 - 1 or uid < -(2**63):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            user = auth.get_user(uid)
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            return _user_to_public_response(user)
        except HTTPException:
            raise
        except Exception as e:
            if "NotFound" in type(e).__name__:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            logger.error(f"Failed to get user {uid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_user for {user_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/@me/messaging-settings",
    response_model=MessagingSettingsResponse,
    summary="Get messaging settings",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="messaging_settings_api")
def get_messaging_settings(
    current_user: TokenInfo = Depends(get_current_user),
) -> MessagingSettingsResponse:
    """Get current user's messaging settings."""
    messaging = api.get_messaging()
    if not messaging:
        logger.error("Messaging module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        settings = messaging.get_user_message_settings(current_user.user_id)
        return settings
    except Exception as e:
        logger.error(
            f"Failed to get messaging settings for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/@me/messaging-settings",
    response_model=MessagingSettingsResponse,
    summary="Update messaging settings",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_messaging_settings(
    body: MessagingSettingsUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> MessagingSettingsResponse:
    """Update current user's messaging settings."""
    messaging = api.get_messaging()
    if not messaging:
        logger.error("Messaging module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        update_data = body.model_dump(exclude_unset=True)
        # Ensure we map the schema fields to the manager method arguments
        settings = messaging.update_user_message_settings(
            user_id=current_user.user_id, **update_data
        )
        return MessagingSettingsResponse.model_validate(settings)
    except Exception as e:
        logger.error(
            f"Failed to update messaging settings for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
