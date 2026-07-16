"""
Profile mixin - User profile CRUD route handlers.
"""

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import UserResponse
from src.api.schemas.users import (
    UserUpdateRequest,
    UserPublicResponse,
    UserDeletionRequest,
)
from src.api.schemas.common import SnowflakeID, SuccessResponse
from src.core.database import cached, invalidate_pattern
from .helpers import _user_to_response, _user_to_public_response, _get_user_cached


class ProfileMixin:
    @cached(ttl=60, prefix="current_user_api")
    def get_current_user_info(
        self,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> UserResponse:
        auth = api.get_auth()
        if not auth:
            logger.error("Auth module not available")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )

        try:
            if getattr(current_user, "token_type", None) == "bot":
                bot = auth.get_bot(current_user.account_id)
                if not bot:
                    logger.warning(
                        f"Bot not found for account {current_user.account_id}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "User not found"}},
                    )

                return UserResponse(
                    id=SnowflakeID(int(bot.id)),
                    username=str(bot.username),
                    email=None,
                    avatar_url=None,
                    created_at=int(bot.created_at),
                    email_verified=False,
                    totp_enabled=False,
                    age_verified=False,
                    badges=[],
                    deletion_status="active",
                    deletion_at=None,
                )

            user = _get_user_cached(current_user.user_id)
            if not user:
                logger.warning(
                    f"User profile not found for account {current_user.user_id}"
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
                f"Failed to get info for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def update_current_user(
        self,
        body: UserUpdateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> UserResponse:
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

            if update_data:
                try:
                    user = auth.update_user(
                        current_user.user_id,
                        username=update_data.get("username"),
                        email=update_data.get("email"),
                    )
                    try:
                        invalidate_pattern(f"user_data:*{current_user.user_id}*")
                    except Exception as ce:
                        logger.debug(
                            f"Cache invalidation failed for user {current_user.user_id}: {ce}"
                        )
                    try:
                        from src.core.events.gateway_emit import emit_user_update

                        emit_user_update(
                            {
                                "id": user.id,
                                "username": user.username,
                                "avatar_url": getattr(user, "avatar_url", None),
                                "email": getattr(user, "email", None),
                            },
                            exclude_user_id=current_user.user_id,
                        )
                    except Exception as ge:
                        logger.debug(f"emit_user_update failed: {ge}")
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

    @cached(ttl=60, prefix="user_profile_api")
    async def get_user(
        self, user_id: str, current_user: TokenInfo = Depends(get_current_user)
    ) -> UserPublicResponse:
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
                profiles = auth.get_user_profiles_bulk([uid])
                user = profiles.get(str(uid)) if profiles else None
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
            logger.error(
                f"Unexpected error in get_user for {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )

    async def schedule_account_deletion(
        self,
        body: UserDeletionRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        auth = api.get_auth()
        if not auth:
            logger.error("Auth module not available")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )

        from src.core.auth.exceptions import (
            InvalidCredentialsError,
            TwoFactorInvalidError,
        )

        try:
            auth.schedule_account_deletion(
                user_id=current_user.user_id,
                password=body.password,
                totp_code=body.code,
            )

            logger.info(f"User {current_user.user_id} scheduled account deletion")
            return SuccessResponse(success=True, message=None)

        except (InvalidCredentialsError, TwoFactorInvalidError) as e:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}},
            )
        except Exception as e:
            logger.error(
                f"Failed to schedule deletion for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
