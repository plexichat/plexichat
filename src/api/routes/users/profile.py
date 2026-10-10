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
from src.core.auth.exceptions import (
    InvalidCredentialsError,
    InvalidEmailError,
    InvalidUsernameError,
    UserExistsError,
    UserNotFoundError,
    WeakPasswordError,
)
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
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
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
            password_challenge_verified = False

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
                except (
                    InvalidCredentialsError,
                    WeakPasswordError,
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Incorrect current password",
                            }
                        },
                    )

                password_challenge_verified = True
                del update_data["password"]
                if "current_password" in update_data:
                    del update_data["current_password"]

            # M2 FIX: an email change is a sensitive account operation. It
            # requires a current-password challenge (like password change) and
            # the manager forces email_verified=0 so the new address must
            # complete the verification flow before it is trusted. A current-
            # password challenge already satisfied by a password change in the
            # same request counts.
            if update_data.get("email") and not password_challenge_verified:
                if not update_data.get("current_password"):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {
                                "code": 400,
                                "message": (
                                    "Current password required to change email"
                                ),
                            }
                        },
                    )
                try:
                    verified = auth.verify_user_password(
                        current_user.user_id, update_data["current_password"]
                    )
                except Exception as e:
                    logger.warning(
                        f"Password re-check failed for user "
                        f"{current_user.user_id} during email change: {e}"
                    )
                    verified = False
                if not verified:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Incorrect current password",
                            }
                        },
                    )

            if "current_password" in update_data:
                del update_data["current_password"]

            # Profile-manager fields are handled below, not by auth.update_user
            # (which only accepts username/email/permissions).
            profile_fields: dict = {}
            for key in (
                "bio",
                "pronouns",
                "location",
                "timezone",
                "banner_url",
                "social_links",
            ):
                if key in update_data:
                    profile_fields[key] = update_data.pop(key)
            avatar_value = update_data.pop("avatar_url", None)

            if update_data:
                try:
                    user = auth.update_user(
                        current_user.user_id,
                        username=update_data.get("username"),
                        email=update_data.get("email"),
                    )
                except UserExistsError:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": {
                                "code": 409,
                                "message": "Username or email already in use",
                            }
                        },
                    )
                except (
                    InvalidUsernameError,
                    InvalidEmailError,
                    WeakPasswordError,
                ):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Invalid update data"}
                        },
                    )
            else:
                user = auth.get_user(current_user.user_id)
                if not user:
                    logger.warning(
                        f"User profile not found for account {current_user.user_id}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "User not found"}},
                    )

            if profile_fields:
                db = api.get_db()
                from src.core.profiles.manager import ProfileManager

                try:
                    ProfileManager(db).update_profile(
                        current_user.user_id, **profile_fields
                    )
                except ValueError as ve:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(ve)}},
                    )

            if avatar_value is not None:
                self._apply_avatar_url_update(current_user.user_id, avatar_value)

            # Re-fetch so the response (and gateway event) reflect any
            # avatar/profile changes made above.
            user = auth.get_user(current_user.user_id)
            if not user:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
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
                        "id": str(user.id),
                        "username": user.username,
                        "avatar_url": getattr(user, "avatar_url", None),
                        "email": getattr(user, "email", None),
                    },
                    exclude_user_id=current_user.user_id,
                )
            except Exception as ge:
                logger.debug(f"emit_user_update failed: {ge}")
            return _user_to_response(user, include_private=True)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to update user {current_user.user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    def _apply_avatar_url_update(self, user_id: int, avatar_value: object) -> None:
        """Validate and apply an ``avatar_url`` from PATCH /users/@me.

        Only a reference to the user's *uploaded* avatar is accepted — the
        canonical ``/api/v1/avatars/users/{user_id}`` path backed by an actual
        ``user_avatars`` row. Arbitrary external URLs are rejected (use the
        avatar upload endpoint to change the image itself).
        """
        cleaned = str(avatar_value).strip().rstrip("/")
        canonical = f"/api/v1/avatars/users/{user_id}"
        avatars = api.get_avatars()
        has_upload = False
        if avatars:
            try:
                has_upload = bool(avatars.get_user_avatar(user_id))
            except Exception as ae:
                logger.debug(f"Avatar lookup failed for user {user_id}: {ae}")
        if cleaned != canonical or not has_upload:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": (
                            "avatar_url must reference your uploaded avatar "
                            f"({canonical}); upload via POST /users/@me/avatar"
                        ),
                    }
                },
            )

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=503,
                detail={"error": {"code": 503, "message": "Database unavailable"}},
            )
        db.execute(
            "UPDATE auth_users SET avatar_url = ? WHERE id = ?", (canonical, user_id)
        )
        try:
            invalidate_pattern(f"user:{user_id}*")
            invalidate_pattern(f"user_avatar*{user_id}*")
        except Exception as ce:
            logger.debug(f"Cache invalidation failed for user {user_id}: {ce}")

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
            except UserNotFoundError:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "User not found"}},
                )

            logger.error(f"Failed to get user {uid}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in get_user for {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
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

        except (InvalidCredentialsError, TwoFactorInvalidError):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid password or 2FA code"}
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to schedule deletion for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
