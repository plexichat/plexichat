"""
Mixin providing message-sending route handlers.

Includes create-message, reply, and poll creation logic.
"""

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageResponse,
)
from src.core.messaging.exceptions import (
    MessageNotFoundError,
)
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from src.core.polls import (
    PollResultsVisibility,
    PollNotFoundError,
    PollOptionNotFoundError,
    PollEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
)
from src.api.routes.messages.helpers import _message_to_response
from src.api.routes.messages.messages import get_msg_id

from .broadcast import BroadcastMixin
from .base import MessagesCRUDBase


class SendMixin(BroadcastMixin, MessagesCRUDBase):
    async def send_channel_message(
        self,
        channel_id: str,
        body: MessageCreateRequest,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> MessageResponse:
        """
        Send a message to a channel.

        Creates a new message in the specified channel.
        Works for both server channels and DM conversations.
        """
        servers_mod = api.get_servers()
        messaging = api.get_messaging()
        auth = api.get_auth()

        try:
            try:
                cid = int(channel_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            if (
                not body.content
                and not body.attachments
                and not body.embeds
                and not body.poll
            ):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Message must have content, attachments, embeds, or a poll",
                        }
                    },
                )

            reply_to = int(body.reply_to_id) if body.reply_to_id else None

            attachments = None
            if body.attachments:
                attachments = [
                    {
                        "filename": a.filename,
                        "content_type": a.content_type,
                        "size": a.size,
                        "url": a.url,
                        "checksum": a.hash,
                        "metadata": a.metadata,
                    }
                    for a in body.attachments
                ]

            content_value = body.content or ""
            if (not content_value or not content_value.strip()) and (
                attachments or body.poll
            ):
                content_value = "\u200b"

            msg, server_id = await self._send_message_with_fallback(
                cid,
                current_user.user_id,
                content_value,
                reply_to,
                attachments,
                body.embeds,
                servers_mod,
                messaging,
            )

            if msg is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )

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
                        context={"source": "message_create"},
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
                                context={"source": "message_create"},
                            )

                        if result.should_delete:
                            raise HTTPException(
                                status_code=400,
                                detail={
                                    "error": {
                                        "code": "MESSAGE_BLOCKED",
                                        "message": "Message blocked by auto-moderation",
                                        "violations": [
                                            v.rule_type.value for v in result.violations
                                        ],
                                    }
                                },
                            )
                except HTTPException:
                    raise
                except Exception as e:
                    logger.warning(f"Automod check failed for message create: {e}")

            if body.poll:
                polls_module = api.get_polls()
                if not polls_module:
                    if messaging:
                        try:
                            messaging.delete_message(
                                current_user.user_id,
                                get_msg_id(msg),
                                hard_delete=True,
                            )
                        except Exception:
                            pass
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": {
                                "code": 500,
                                "message": "Polls module not available",
                            }
                        },
                    )
                try:
                    msg_id = get_msg_id(msg)
                    poll = polls_module.create_poll(
                        user_id=current_user.user_id,
                        message_id=msg_id,
                        question=body.poll.question,
                        options=list(body.poll.options),
                        duration_hours=body.poll.duration_hours,
                        allow_multiple_choice=body.poll.allow_multiple_choice,
                        results_visibility=PollResultsVisibility(
                            body.poll.results_visibility
                        ),
                    )
                    if messaging and poll:
                        try:
                            msg = messaging.update_message_metadata(
                                msg_id, {"poll_id": poll.id}
                            )
                        except Exception:
                            pass
                except (
                    PollNotFoundError,
                    PollOptionNotFoundError,
                    PollEndedError,
                    InvalidPollQuestionError,
                    InvalidPollOptionError,
                    PollOptionLimitError,
                    InvalidPollDurationError,
                    AlreadyVotedError,
                    MultipleVoteNotAllowedError,
                    PermissionDeniedError,
                    MessageNotFoundError,
                ) as e:
                    if messaging:
                        try:
                            messaging.delete_message(
                                current_user.user_id,
                                get_msg_id(msg),
                                hard_delete=True,
                            )
                        except Exception:
                            pass
                    if isinstance(
                        e,
                        (
                            MessageNotFoundError,
                            ServerNotFoundError,
                            ChannelNotFoundError,
                        ),
                    ):
                        raise HTTPException(
                            status_code=404,
                            detail={"error": {"code": 404, "message": str(e)}},
                        )
                    if isinstance(e, (PermissionDeniedError, ChannelAccessDeniedError)):
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": str(e)}},
                        )
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                except Exception as e:
                    if messaging:
                        try:
                            messaging.delete_message(
                                current_user.user_id,
                                get_msg_id(msg),
                                hard_delete=True,
                            )
                        except Exception:
                            pass
                    logger.error(
                        f"Error creating poll for message {get_msg_id(msg)}: {e}",
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": {
                                "code": 500,
                                "message": "Internal server error",
                            }
                        },
                    )

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

            await self._broadcast_message_create(
                response, server_id, cid, servers_mod, messaging, current_user
            )

            return response
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to send message to channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
