"""
Mixin providing message editing route handlers.

Includes edit-message and related update operations.
"""

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageUpdateRequest, MessageResponse
from src.core.messaging.exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    InvalidContentError,
    ContentTooLongError,
)
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
        message_id: int,
        body: MessageUpdateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessageResponse:
        """
        Edit a message.

        Updates the message content. Only the author can edit. The URL
        channel must be the message's actual channel: a mismatch is
        indistinguishable from a nonexistent message (404).
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

            # Resolve the message FIRST and reconcile it against the URL
            # channel before any moderation or mutation runs, so automod and
            # broadcasts always operate on the message's REAL conversation.
            msg = messaging.get_message(current_user.user_id, message_id)
            if not msg:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            self._ensure_message_in_channel(
                msg, self._resolve_channel_scope(cid, current_user.user_id, servers_mod)
            )

            real_cid = int(getattr(msg, "conversation_id"))

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

            # Phase 1 (synchronous): cheap rules run BEFORE the edit is
            # applied, so a match blocks the edit entirely. AI moderation is
            # excluded here and runs async (phase 2).
            if server_id:
                try:
                    from src.core import automod

                    fast_result = automod.check_message_fast(
                        server_id=server_id,
                        channel_id=cid,
                        user_id=current_user.user_id,
                        content=body.content or "",
                        message_id=None,
                        context={"source": "message_edit"},
                    )
                    if not fast_result.passed:
                        for match in fast_result.violations:
                            automod.process_violation(
                                server_id=server_id,
                                channel_id=cid,
                                user_id=current_user.user_id,
                                message_id=None,
                                match=match,
                                actions=fast_result.actions_to_take,
                                context={"source": "message_edit"},
                            )
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "code": "MESSAGE_BLOCKED",
                                    "message": "Edit blocked by auto-moderation",
                                    "violations": [
                                        v.rule_type.value
                                        for v in fast_result.violations
                                    ],
                                }
                            },
                        )
                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning(f"Automod fast check failed for message edit: {e}")

            edited_msg = messaging.edit_message(
                current_user.user_id, message_id, body.content
            )

            author_username, author_avatar_url, author_badges = (
                self._resolve_author_info(current_user, auth)
            )

            response = _message_to_response(
                edited_msg,
                author_username,
                author_avatar_url,
                author_badges=author_badges,
                channel_id=cid,
                media_mod=api.get_media(),
                viewer_user_id=current_user.user_id,
            )

            await self._broadcast_message_update(
                response,
                cid,
                servers_mod,
                messaging,
                current_user,
                conversation_id=real_cid,
            )

            # Phase 2 (asynchronous): AI moderation on the edited content.
            if server_id:
                try:
                    from src.core import automod

                    msg_id = get_msg_id(edited_msg)
                    if msg_id is None:
                        logger.warning(
                            f"Automod AI check skipped for edited message {message_id}: no id"
                        )
                    else:
                        automod.schedule_ai_check(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=msg_id,
                            content=body.content or "",
                            context={"source": "message_edit"},
                        )
                except Exception as e:
                    logger.warning(f"Automod AI check scheduling failed: {e}")

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
            elif isinstance(e, MessageAccessDeniedError):
                logger.warning(f"Edit denied for message {message_id}")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif isinstance(e, (InvalidContentError, ContentTooLongError)):
                logger.warning(f"Edited content rejected for message {message_id}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {"code": 400, "message": "Invalid message content"}
                    },
                )
            elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                logger.warning(f"Edit denied for message {message_id}")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            logger.error(
                f"Error editing message {message_id} in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
