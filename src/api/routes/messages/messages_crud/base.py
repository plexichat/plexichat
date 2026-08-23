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
from src.core.messaging.exceptions import (
    AttachmentLimitError,
    ConversationAccessDeniedError,
    ConversationNotFoundError,
    InvalidContentError,
    ContentTooLongError,
)
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)


class MessagesCRUDBase:
    def _resolve_channel_scope(self, cid: int, user_id: int, servers_mod: Any) -> set:
        """Resolve a URL channel id to the conversation ids it can map to.

        A channel id is either a conversation id itself (DM/group) or a
        server channel id whose messages live in the linked conversation.
        Returns the set of both candidates for membership validation.
        """
        scope = {int(cid)}
        if servers_mod:
            getter = getattr(servers_mod, "get_channel_conversation_id", None)
            if getter is not None:
                try:
                    linked = getter(int(cid))
                    if linked:
                        scope.add(int(linked))
                except Exception as e:
                    logger.debug(
                        f"Failed to resolve conversation for channel {cid}: {e}"
                    )
        return scope

    def _ensure_message_in_channel(self, message: Any, scope: set) -> None:
        """404 when the message does not belong to the addressed channel.

        Uses the SAME error a nonexistent message produces so callers cannot
        probe for message existence in foreign conversations.
        """
        try:
            conversation_id = int(getattr(message, "conversation_id"))
        except (TypeError, ValueError):
            conversation_id = None
        if conversation_id is None or conversation_id not in scope:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )

    @staticmethod
    def _map_messaging_error(e: Exception, not_found_message: str) -> HTTPException:
        """Map core messaging/server exceptions onto HTTP errors.

        Client-visible messages are STATIC (never str(e)) so exception
        internals never leak; details are logged by the caller.
        """
        if isinstance(
            e, (ConversationNotFoundError, ChannelNotFoundError, ServerNotFoundError)
        ):
            return HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": not_found_message}},
            )
        if (
            isinstance(
                e,
                (
                    ConversationAccessDeniedError,
                    ChannelAccessDeniedError,
                    PermissionDeniedError,
                ),
            )
            or "Access" in type(e).__name__
        ):
            return HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        if isinstance(e, InvalidContentError):
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message content"}},
            )
        if isinstance(e, ContentTooLongError):
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Message content too long"}},
            )
        if "Content" in type(e).__name__ or "Invalid" in type(e).__name__:
            return HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message content"}},
            )
        if "NotFound" in type(e).__name__:
            return HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": not_found_message}},
            )
        raise ValueError(f"Unmapped messaging error: {type(e).__name__}")

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
                        detail={
                            "error": {"code": 400, "message": "Too many attachments"}
                        },
                    )
                if not isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
                    if isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": "Access denied"}},
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
                        detail={
                            "error": {"code": 400, "message": "Too many attachments"}
                        },
                    )
                if (
                    isinstance(e, InvalidContentError)
                    or "Content" in exc_name
                    or "Invalid" in exc_name
                ):
                    logger.warning(f"Message content rejected in channel {channel_id}")
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Invalid message content"}
                        },
                    )
                if (
                    isinstance(e, (ConversationNotFoundError,))
                    or "NotFound" in exc_name
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                if (
                    isinstance(
                        e,
                        (ConversationAccessDeniedError,),
                    )
                    or "Access" in exc_name
                ):
                    # Access-denied is a 403 everywhere else; never misreport
                    # it as not-found.
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": "Access denied"}},
                    )
                logger.error(
                    f"Error sending message in channel {channel_id}: {e}", exc_info=True
                )
                raise

        return msg, server_id

    def _resolve_server_id(
        self, channel_id: int, user_id: int, servers_mod: Any
    ) -> Optional[int]:
        """Return the server id for a channel, or None for DM conversations."""
        if not servers_mod:
            return None
        try:
            channel = servers_mod.get_channel(channel_id, user_id)
            if channel:
                return getattr(channel, "server_id", None)
        except Exception:
            return None
        return None

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
