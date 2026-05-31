"""
Base class with shared helpers for message CRUD mixins.

Provides message validation, channel fallback logic, and utility methods
used across multiple mixins.
"""

from typing import Optional, Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.messaging.exceptions import AttachmentLimitError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)


class MessagesCRUDBase:
    async def _send_message_with_fallback(
        self,
        channel_id: int,
        user_id: int,
        content: str,
        reply_to_id: Optional[int],
        attachments: Optional[list],
        embeds: Optional[list],
        servers_mod: Any,
        messaging: Any,
    ) -> tuple:
        """
        Send a message with fallback logic: try server channel first, then DM conversation.

        Returns:
            Tuple of (message, server_id)
        """
        msg = None
        server_id = None

        if servers_mod:
            try:
                channel = servers_mod.get_channel(channel_id, user_id)
                if channel:
                    server_id = getattr(channel, "server_id", None)
                    conversation_id = getattr(channel, "conversation_id", None)

                    if server_id and hasattr(servers_mod, "is_timed_out"):
                        if servers_mod.is_timed_out(user_id, server_id):
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "You are currently timed out in this server",
                                    }
                                },
                            )

                    if conversation_id and messaging:
                        msg = await run_in_threadpool(
                            messaging.send_message,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            content=content,
                            reply_to_id=reply_to_id,
                            attachments=attachments,
                            embeds=embeds,
                        )
                    elif messaging:
                        logger.warning(
                            f"Server channel {channel_id} has no conversation_id linked"
                        )
                        try:
                            msg = await run_in_threadpool(
                                messaging.send_message,
                                user_id=user_id,
                                conversation_id=channel_id,
                                content=content,
                                reply_to_id=reply_to_id,
                                attachments=attachments,
                                embeds=embeds,
                            )
                        except Exception:
                            msg = None
            except HTTPException:
                raise
            except Exception as e:
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                if not isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
                    if isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": str(e)}},
                        )
                    logger.error(
                        f"Error sending message in server channel {channel_id}: {e}",
                        exc_info=True,
                    )
                    raise
                msg = None

        if msg is None and messaging:
            try:
                msg = messaging.send_message(
                    user_id=user_id,
                    conversation_id=channel_id,
                    content=content,
                    reply_to_id=reply_to_id,
                    attachments=attachments,
                    embeds=embeds,
                )
            except Exception as e:
                exc_name = type(e).__name__
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                if "NotFound" in exc_name or "Access" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Content" in exc_name or "Invalid" in exc_name:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                logger.error(
                    f"Error sending message in channel {channel_id}: {e}", exc_info=True
                )
                raise

        return msg, server_id

    def _resolve_author_info(self, current_user, auth=None):
        author_username = current_user.username
        author_avatar_url = getattr(current_user, "avatar_url", None)
        author_badges = getattr(current_user, "badges", [])

        if not author_avatar_url or not author_badges:
            resolved_auth = auth or api.get_auth()
            if resolved_auth:
                try:
                    user = resolved_auth.get_user(current_user.user_id)
                    if user:
                        if not author_avatar_url:
                            author_avatar_url = getattr(user, "avatar_url", None)
                        if not author_badges:
                            author_badges = getattr(user, "badges", [])
                except Exception:
                    pass

        return author_username, author_avatar_url, author_badges
