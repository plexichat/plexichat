"""
Mixin providing message deletion route handlers.

Includes delete-message and related removal operations.
"""

import json

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SuccessResponse
from src.core.messaging.exceptions import MessageNotFoundError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)


from .broadcast import BroadcastMixin


class DeleteMixin(BroadcastMixin):
    async def delete_message(
        self,
        channel_id: str,
        message_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        """
        Delete a message.

        Deletes the message. Author or moderators can delete.
        """
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
                mid = int(message_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid message ID"}},
                )

            msg = messaging.get_message(current_user.user_id, mid)
            if not msg:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            cid = msg.conversation_id

            messaging.delete_message(current_user.user_id, mid)

            metadata = getattr(msg, "metadata", None)
            if metadata and isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = None

            if isinstance(metadata, dict) and metadata.get("poll_id"):
                polls_module = api.get_polls()
                if polls_module:
                    try:
                        polls_module.delete_poll(
                            current_user.user_id, int(metadata["poll_id"])
                        )
                    except Exception:
                        pass

            servers_mod = api.get_servers()

            await self._broadcast_message_delete(
                mid, channel_id, cid, servers_mod, messaging, current_user
            )

            return SuccessResponse(success=True, message=None)
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
                    detail={"error": {"code": 403, "message": str(e)}},
                )
            logger.error(
                f"Error deleting message {message_id} in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
