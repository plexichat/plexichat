"""
Mixin providing message editing route handlers.

Includes edit-message and related update operations.
"""

from src.core.security.hardening import parse_id

from fastapi import HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SnowflakeIDParam
from src.api.schemas.messages import MessageUpdateRequest, MessageResponse
from src.core.messaging.exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    InvalidContentError,
    ContentTooLongError,
    MessageConflictError,
    MessageEditWindowExpiredError,
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


def _edit_not_found() -> HTTPException:
    """Same opaque 404 a missing message produces (no existence oracle)."""
    return HTTPException(
        status_code=404,
        detail={"error": {"code": 404, "message": "Message not found"}},
    )


def _require_edit_channel_view(
    url_cid: int, conversation_id: int, user_id: int, servers_mod
) -> None:
    """Fail closed when the message's backing server channel is not viewable.

    ``get_message`` plus the channel-scope check only prove *participation*,
    which every server member has regardless of ``channels.view``. Message
    routes also accept a server channel's linked ``conversation_id`` as a URL
    alias; ``get_channel``/``channel_exists`` only know channel ids and treat
    that alias as a DM. Resolve the authoritative conversation -> channel
    mapping and require a live ``get_channel`` grant on the real channel before
    the edit is applied, so a member denied view cannot edit in a private
    channel through either spelling of the id.
    """
    if not servers_mod:
        # The backing-channel lookup cannot be evaluated without the servers
        # module. A private-channel alias can exist in the DB independently of
        # that module, so skipping this gate would let a member denied
        # channels.view edit a message in a private channel through either
        # spelling of the id. Deny with the same opaque 404 a missing message
        # produces; a wired store with no backing channel still allows genuine
        # DMs below.
        raise _edit_not_found()

    get_channel = getattr(servers_mod, "get_channel", None)
    channel_exists = getattr(servers_mod, "channel_exists", None)
    reverse = getattr(servers_mod, "get_channel_by_conversation", None)

    # 1) URL id used directly as a channel id.
    if callable(get_channel):
        try:
            if get_channel(url_cid, user_id) is not None:
                return  # live view grant on the addressed channel
        except HTTPException:
            raise
        except Exception:
            raise _edit_not_found()

    # 2) The URL id or the message's authoritative conversation may be a server
    #    channel's linked conversation id (the URL alias). Resolve it
    #    membership-agnostically and require a live grant on the backing channel.
    for candidate in (conversation_id, url_cid):
        if not callable(reverse):
            break
        try:
            info = reverse(int(candidate))
        except Exception:
            raise _edit_not_found()
        if info is None:
            continue
        real_channel_id = (
            info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
        )
        if real_channel_id is None:
            raise _edit_not_found()
        try:
            real_channel_id = int(real_channel_id)
        except (TypeError, ValueError):
            raise _edit_not_found()
        if not callable(get_channel):
            raise _edit_not_found()
        try:
            channel = get_channel(real_channel_id, user_id)
        except Exception:
            raise _edit_not_found()
        if channel is None:
            raise _edit_not_found()
        return

    # 3) No backing server channel found. If the URL id is nevertheless a known
    #    server channel, the caller cannot view it -> same 404 as a missing
    #    message. Otherwise it is a genuine DM/group conversation, whose access
    #    is governed by the messaging layer's participation check.
    if callable(channel_exists):
        try:
            exists = channel_exists(url_cid)
        except Exception:
            exists = True  # unproven -> fail closed
        if exists:
            raise _edit_not_found()


class EditMixin(BroadcastMixin, MessagesCRUDBase):
    async def edit_message(
        self,
        channel_id: str,
        message_id: SnowflakeIDParam,
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
                cid = parse_id(channel_id, "channel_id")
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

            # Participation (checked by the messaging layer) is granted to
            # every server member and is NOT a channel-view boundary. The URL
            # id may be the channel's linked conversation id, which the channel
            # probes do not recognise as a channel. Gate both spellings before
            # any mutation runs so a member denied channels.view cannot edit a
            # message in a private channel via the conversation-id alias.
            _require_edit_channel_view(cid, real_cid, current_user.user_id, servers_mod)

            server_id = None
            # Share the resolved channel with the automod-context helper so it
            # does not re-resolve the conversation with a second lookup.
            _channel_ctx: dict = {}
            if servers_mod:
                try:
                    channel = servers_mod.get_channel(cid, current_user.user_id)
                    if channel:
                        _channel_ctx["channel"] = channel
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
            from src.core import automod

            if server_id:
                try:
                    automod_context = self._build_automod_context(
                        cid,
                        current_user.user_id,
                        body.content or "",
                        servers_mod,
                        messaging,
                        "message_edit",
                        _channel_ctx,
                    )
                    fast_result = automod.check_message_fast(
                        server_id=server_id,
                        channel_id=cid,
                        user_id=current_user.user_id,
                        content=body.content or "",
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
                    logger.error(f"Automod fast check failed for message edit: {e}")
                    automod.record_internal_error(
                        "fast_check:message_edit",
                        server_id,
                        cid,
                        current_user.user_id,
                        message_id,
                        e,
                    )
                    if automod.is_fail_closed():
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "code": "MESSAGE_BLOCKED",
                                    "message": (
                                        "Edit blocked: auto-moderation is "
                                        "temporarily unavailable"
                                    ),
                                    "violations": ["internal_error"],
                                }
                            },
                        )

            edited_msg = messaging.edit_message(
                current_user.user_id,
                message_id,
                body.content,
                expected_edited_at=body.expected_edited_at,
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
                    # Scheduling stays async, but the failure is recorded so
                    # the moderation gap is auditable rather than silent.
                    logger.error(f"Automod AI check scheduling failed: {e}")
                    try:
                        _failed_mid = get_msg_id(edited_msg)
                    except Exception:
                        _failed_mid = message_id
                    automod.record_internal_error(
                        "ai_schedule:message_edit",
                        server_id,
                        cid,
                        current_user.user_id,
                        _failed_mid,
                        e,
                    )

            return response
        except HTTPException:
            raise
        except MessageConflictError as e:
            logger.warning(f"Edit conflict for message {message_id}")
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {
                        "code": "EDIT_CONFLICT",
                        "message": (
                            "Message was modified by another request; refresh and retry"
                        ),
                        "expected_edited_at": e.expected,
                        "actual_edited_at": e.actual,
                    }
                },
            )
        except MessageEditWindowExpiredError:
            logger.warning(f"Edit window expired for message {message_id}")
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "EDIT_WINDOW_EXPIRED",
                        "message": "The edit window for this message has expired",
                    }
                },
            )
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
