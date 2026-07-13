"""
Channel mixin - DM channel and notes route handlers.
"""

from typing import List

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.channels import (
    DMChannelResponse,
    RecipientResponse,
    DMChannelCreateRequest,
    NotesChannelResponse,
)
from src.api.schemas.common import SnowflakeID
from src.core.database import cached


class ChannelMixin:
    async def get_notes_channel(
        self,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> NotesChannelResponse:
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
            try:
                if hasattr(messaging, "get_or_create_notes"):
                    channel = messaging.get_or_create_notes(current_user.user_id)
                else:
                    raise HTTPException(
                        status_code=501,
                        detail={
                            "error": {"code": 501, "message": "Notes not implemented"}
                        },
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

    @cached(ttl=5, prefix="user_dm_channels_api")
    def get_dm_channels(
        self,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> List[DMChannelResponse]:
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
            channels = []
            if hasattr(messaging, "get_dm_channels"):
                channels = messaging.get_dm_channels(current_user.user_id)
            elif hasattr(messaging, "get_conversations"):
                channels = messaging.get_conversations(current_user.user_id)
                channels = [
                    c
                    for c in (channels or [])
                    if getattr(c, "conversation_type", None) == "dm"
                ]

            auth = api.get_auth()
            result = []

            if channels and auth:
                recipient_ids = []
                for ch in channels:
                    rid = getattr(ch, "recipient_id", None)
                    if rid:
                        recipient_ids.append(rid)

                users_map = {}
                if recipient_ids:
                    try:
                        users_map = auth.get_user_profiles_bulk(recipient_ids)
                    except Exception as e:
                        logger.debug(
                            f"Bulk profile fetch failed for DM recipients: {e}"
                        )

                for ch in channels:
                    try:
                        rid = getattr(ch, "recipient_id", None)
                        recipient_username = None
                        if rid:
                            user_data = users_map.get(str(rid))
                            if user_data:
                                recipient_username = user_data.get("username")
                            else:
                                try:
                                    user = auth.get_user(rid)
                                    if user:
                                        recipient_username = user.username
                                except Exception as e:
                                    logger.debug(
                                        f"Failed to fetch user profile for {rid}: {e}"
                                    )

                        result.append(
                            DMChannelResponse(
                                id=SnowflakeID(ch.id),
                                channel_type="dm",
                                recipient_id=SnowflakeID(rid) if rid else None,
                                recipient=RecipientResponse(
                                    id=SnowflakeID(rid),
                                    username=recipient_username or f"User {rid}",
                                )
                                if rid
                                else None,
                                last_message_id=SnowflakeID(ch.last_message_id)
                                if hasattr(ch, "last_message_id") and ch.last_message_id
                                else None,
                            )
                        )
                    except Exception as e:
                        logger.debug(
                            f"Failed to process DM channel {getattr(ch, 'id', 'unknown')}: {e}"
                        )
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

    async def create_dm_channel(
        self,
        body: DMChannelCreateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> DMChannelResponse:
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
                logger.warning(
                    f"Invalid recipient ID format for DM: {body.recipient_id}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid recipient ID"}},
                )

            try:
                if hasattr(messaging, "get_or_create_dm"):
                    channel = messaging.get_or_create_dm(current_user.user_id, rid)
                elif hasattr(messaging, "create_dm"):
                    channel = messaging.create_dm(current_user.user_id, rid)
                else:
                    raise HTTPException(
                        status_code=501,
                        detail={
                            "error": {
                                "code": 501,
                                "message": "DM creation not implemented",
                            }
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
                        id=SnowflakeID(rid),
                        username=recipient_username or f"User {rid}",
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
                            "error": {
                                "code": 403,
                                "message": "Cannot message this user",
                            }
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
