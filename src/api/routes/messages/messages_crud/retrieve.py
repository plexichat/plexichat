"""
Mixin providing message retrieval route handlers.

Includes get-message-by-id and related read operations.
"""

from fastapi import HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageResponse
from src.core.messaging.exceptions import MessageNotFoundError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from src.api.routes.messages.helpers import _message_to_response


class RetrieveMixin:
    async def get_message(
        self,
        channel_id: str,
        message_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessageResponse:
        """Get a specific message by ID."""
        messaging = api.get_messaging()
        if not messaging:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Messaging module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
                mid = int(message_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid ID format"}},
                )

            message = await run_in_threadpool(
                messaging.get_message, current_user.user_id, mid
            )
            if not message:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            author_info = {"username": None, "avatar_url": None, "badges": []}
            auth = api.get_auth()
            if auth:
                try:
                    user = await run_in_threadpool(auth.get_user, message.author_id)
                    if user:
                        author_info["username"] = user.username
                        author_info["avatar_url"] = getattr(user, "avatar_url", None)
                        author_info["badges"] = getattr(user, "badges", [])
                except Exception:
                    pass

            return _message_to_response(
                message,
                author_username=author_info["username"],
                author_avatar_url=author_info["avatar_url"],
                author_badges=author_info["badges"],
                channel_id=cid,
                media_mod=api.get_media(),
                viewer_user_id=current_user.user_id,
            )
        except HTTPException:
            raise
        except Exception as e:
            if isinstance(
                e, (MessageNotFoundError, ServerNotFoundError, ChannelNotFoundError)
            ):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            logger.error(
                f"Error getting message {message_id} in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
