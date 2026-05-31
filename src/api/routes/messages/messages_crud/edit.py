"""
Mixin providing message editing route handlers.

Includes edit-message and related update operations.
"""

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageUpdateRequest, MessageResponse
from src.core.messaging.exceptions import MessageNotFoundError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from src.api.routes.messages.helpers import _message_to_response
from src.api.routes.messages.messages import get_msg_id

from .broadcast import BroadcastMixin
from .base import MessagesCRUDBase


class EditMixin(BroadcastMixin, MessagesCRUDBase):
    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        body: MessageUpdateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessageResponse:
        """
        Edit a message.

        Updates the message content. Only the author can edit.
        """
        messaging = api.get_messaging()
        servers_mod = api.get_servers()
        auth = api.get_auth()

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
                cid = int(channel_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Invalid message or channel ID",
                        }
                    },
                )

            server_id = None
            if servers_mod:
                try:
                    channel = servers_mod.get_channel(cid, current_user.user_id)
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                        if server_id and hasattr(servers_mod, "is_timed_out"):
                            if servers_mod.is_timed_out(
                                current_user.user_id, server_id
                            ):
                                raise HTTPException(
                                    status_code=403,
                                    detail={
                                        "error": {
                                            "code": 403,
                                            "message": "You are currently timed out in this server",
                                        }
                                    },
                                )
                except Exception as e:
                    if (
                        isinstance(e, HTTPException)
                        and getattr(e, "status_code", None) == 403
                    ):
                        raise
                    server_id = None

            msg = messaging.edit_message(current_user.user_id, mid, body.content)

            if server_id:
                try:
                    from src.core import automod

                    content = getattr(msg, "content", None) or body.content or ""
                    result = automod.check_message(
                        server_id=server_id,
                        channel_id=cid,
                        user_id=current_user.user_id,
                        content=content,
                        message_id=get_msg_id(msg),
                        context={"source": "message_edit"},
                    )
                    if not result.passed:
                        for match in result.violations:
                            automod.process_violation(
                                server_id=server_id,
                                channel_id=cid,
                                user_id=current_user.user_id,
                                message_id=get_msg_id(msg),
                                match=match,
                                actions=result.actions_to_take,
                                context={"source": "message_edit"},
                            )
                except Exception as e:
                    logger.warning(f"Automod check failed for message edit: {e}")

            author_username, author_avatar_url, author_badges = (
                self._resolve_author_info(current_user, auth)
            )

            response = _message_to_response(
                msg,
                author_username,
                author_avatar_url,
                author_badges=author_badges,
                channel_id=cid,
                media_mod=api.get_media(),
                viewer_user_id=current_user.user_id,
            )

            await self._broadcast_message_update(
                response, cid, servers_mod, messaging, current_user
            )

            return response
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
                f"Error editing message {message_id} in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
