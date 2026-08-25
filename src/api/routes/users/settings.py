"""
Settings mixin - User messaging settings route handlers.
"""

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessagingSettingsResponse,
    MessagingSettingsUpdateRequest,
)
from src.core.database import cached
from src.core.database.cache.operations import invalidate_cached
from src.core.database.cache.serialization import generate_cache_key


class SettingsMixin:
    @cached(ttl=60, prefix="messaging_settings_api")
    def get_messaging_settings(
        self,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessagingSettingsResponse:
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

    async def update_messaging_settings(
        self,
        body: MessagingSettingsUpdateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessagingSettingsResponse:
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
            settings = messaging.update_user_message_settings(
                user_id=current_user.user_id, **update_data
            )
            # Mirror the routes/settings.py pattern: a write must invalidate
            # the cached GET for the same user or PATCH-then-GET serves stale
            # data for up to the cache TTL. The key layout matches how the
            # cached decorator builds it when FastAPI calls the bound GET
            # handler with keyword arguments (current_user=...).
            invalidate_cached(
                generate_cache_key("messaging_settings_api", current_user=current_user)
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
