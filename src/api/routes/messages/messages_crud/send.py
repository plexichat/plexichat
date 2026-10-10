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
from src.core.embeds.exceptions import EmbedError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
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
    PollAlreadyExistsError,
)

# ``src.core.polls`` ships its own ``PermissionDeniedError`` /
# ``MessageNotFoundError``.  They share a name with the servers/messaging
# ones but descend from a different base (``PollError`` vs ``ServerError``),
# so they are aliased here: the poll handler below must catch the classes
# ``polls.create_poll`` actually raises, otherwise every poll authorization
# or message-missing failure falls through to a generic 500.
from src.core.polls import (
    PermissionDeniedError as PollPermissionDeniedError,
)
from src.core.polls import (
    MessageNotFoundError as PollMessageNotFoundError,
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

            try:
                reply_to = int(body.reply_to_id) if body.reply_to_id else None
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid reply_to ID"}},
                )

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
                attachments or body.poll or body.embeds
            ):
                # Embeds-only messages (e.g. sticker sends) carry their
                # payload in the embeds slot; substitute the zero-width
                # space so the messaging layer's non-empty-content
                # validation passes without inventing visible text.
                content_value = "\u200b"

            # Aggregate ALL user-controlled text surfaces (message content,
            # attachment filenames/URLs, poll question/options) so keyword /
            # regex filters cannot be evaded via attachments or polls.
            automod_content = content_value
            _extra_parts = []
            for _attachment in attachments or []:
                if _attachment.get("filename"):
                    _extra_parts.append(_attachment["filename"])
                if _attachment.get("url"):
                    _extra_parts.append(_attachment["url"])
            if body.poll:
                if body.poll.question:
                    _extra_parts.append(body.poll.question)
                _extra_parts.extend(option for option in body.poll.options if option)
            if _extra_parts:
                automod_content = "\n".join([automod_content] + _extra_parts)

            from src.core import automod

            # Phase 1 (synchronous): cheap rules (keyword/regex/spam/etc.) run
            # BEFORE the message is created or broadcast. A match blocks the
            # send entirely, so other clients never see the message. AI
            # moderation is intentionally excluded here and runs async (phase 2).
            server_id = self._resolve_server_id(cid, current_user.user_id, servers_mod)
            if server_id:
                try:
                    automod_context = self._build_automod_context(
                        cid,
                        current_user.user_id,
                        automod_content,
                        servers_mod,
                        messaging,
                        "message_create",
                    )
                    fast_result = automod.check_message_fast(
                        server_id=server_id,
                        channel_id=cid,
                        user_id=current_user.user_id,
                        content=automod_content,
                        message_id=None,
                        context=automod_context,
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
                                context=automod_context,
                            )
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "code": "MESSAGE_BLOCKED",
                                    "message": "Message blocked by auto-moderation",
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
                    logger.error(f"Automod fast check failed for message create: {e}")
                    automod.record_internal_error(
                        "fast_check:message_create",
                        server_id,
                        cid,
                        current_user.user_id,
                        None,
                        e,
                    )
                    if automod.is_fail_closed():
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "code": "MESSAGE_BLOCKED",
                                    "message": (
                                        "Message blocked: auto-moderation is "
                                        "temporarily unavailable"
                                    ),
                                    "violations": ["internal_error"],
                                }
                            },
                        )

            else:
                # DM/group conversation: server automod does not apply, but
                # DM spam protection MUST (rate/duplicate/pattern checks).
                self._enforce_dm_spam_protection(
                    cid, current_user.user_id, content_value, messaging
                )

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
                                current_user.user_id, msg_id, {"poll_id": poll.id}
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
                    PollAlreadyExistsError,
                    PollPermissionDeniedError,
                    PollMessageNotFoundError,
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
                            PollMessageNotFoundError,
                            ServerNotFoundError,
                            ChannelNotFoundError,
                        ),
                    ):
                        raise HTTPException(
                            status_code=404,
                            detail={
                                "error": {"code": 404, "message": "Message not found"}
                            },
                        )
                    if isinstance(
                        e,
                        (PollPermissionDeniedError, ChannelAccessDeniedError),
                    ):
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": "Access denied"}},
                        )
                    if isinstance(e, PollAlreadyExistsError):
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": {
                                    "code": 409,
                                    "message": "Resource already exists",
                                }
                            },
                        )
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {"code": 400, "message": "Invalid poll request"}
                        },
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
                response,
                server_id,
                cid,
                servers_mod,
                messaging,
                current_user,
                conversation_id=int(getattr(msg, "conversation_id", cid) or cid),
            )

            # Phase 2 (asynchronous): AI moderation runs in the background
            # after the message has been broadcast. A match replaces the
            # message with an AutoMod tombstone (flag-driven rendering) and
            # pushes a MESSAGE_UPDATE so clients re-render it.
            if server_id:
                try:
                    msg_id = get_msg_id(msg)
                    if msg_id is None:
                        logger.warning(
                            f"Automod AI check skipped for message in channel {channel_id}: no id"
                        )
                    else:
                        automod.schedule_ai_check(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=msg_id,
                            content=automod_content,
                            context={"source": "message_create"},
                        )
                except Exception as e:
                    # Scheduling stays async, but the failure is recorded so
                    # the moderation gap is auditable rather than silent.
                    logger.error(f"Automod AI check scheduling failed: {e}")
                    try:
                        _failed_mid = get_msg_id(msg)
                    except Exception:
                        _failed_mid = None
                    automod.record_internal_error(
                        "ai_schedule:message_create",
                        server_id,
                        cid,
                        current_user.user_id,
                        _failed_mid,
                        e,
                    )

            return response
        except HTTPException:
            raise
        except EmbedError as e:
            issues = getattr(e, "issues", [])
            logger.warning(f"Embed validation failed: {issues or e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid embeds"},
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to send message to channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
