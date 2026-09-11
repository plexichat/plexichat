"""
Avatar mixin - User avatar upload/management route handler.
"""

from fastapi import HTTPException, Depends, File, UploadFile

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.users import UserAvatarResponse
from src.core.database import invalidate_pattern


class AvatarMixin:
    async def upload_avatar(
        self,
        file: UploadFile = File(...),
        current_user: TokenInfo = Depends(get_current_user),
    ) -> UserAvatarResponse:
        avatars = api.get_avatars()
        if not avatars:
            logger.error("Avatars module not available")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Avatars module not available"}
                },
            )

        try:
            if not file.content_type or not file.content_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "File must be an image"}},
                )

            try:
                file_data = await file.read()
            except Exception as e:
                logger.warning(
                    f"Failed to read upload file for user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Failed to read file",
                        }
                    },
                )

            try:
                result = avatars.upload_user_avatar(
                    user_id=current_user.user_id,
                    image_data=file_data,
                    content_type=file.content_type,
                )

                try:
                    invalidate_pattern(f"user:*{current_user.user_id}*")
                except Exception as ce:
                    logger.debug(
                        f"Cache invalidation failed for user {current_user.user_id}: {ce}"
                    )

                # H1: also drop the avatars route's in-process checksum cache so
                # clients stop receiving 304 for the old avatar after re-upload
                # (the Valkey invalidation above never touches that local dict).
                try:
                    from src.api.routes.avatars import (
                        _invalidate_user_avatar_cache,
                    )

                    _invalidate_user_avatar_cache(current_user.user_id)
                except Exception as ie:
                    logger.debug(
                        f"Local avatar checksum invalidation failed "
                        f"for user {current_user.user_id}: {ie}"
                    )

                try:
                    from src.core.events.gateway_emit import emit_user_update

                    emit_user_update(
                        {
                            "id": current_user.user_id,
                            "username": "",
                            "avatar_url": result["url"],
                        },
                        exclude_user_id=current_user.user_id,
                    )
                except Exception as ge:
                    logger.debug(f"emit_user_update (avatar) failed: {ge}")

                return UserAvatarResponse(
                    success=True,
                    avatar_url=result["url"],
                    width=result["width"],
                    height=result["height"],
                    size=result["size"],
                    animated=result["animated"],
                )
            except (ValueError, TypeError):
                # M1: invalid image data is a client error; use the same
                # message as the sibling /avatars/users/@me endpoint so the
                # two upload paths behave identically.
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid image file"}},
                )
            except Exception as e:
                logger.error(
                    f"Avatar upload failed for user {current_user.user_id}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Upload failed"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in upload_avatar for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
