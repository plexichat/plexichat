"""
Mixin providing message retrieval route handlers.

Includes get-message-by-id and related read operations.
"""

from src.core.security.hardening import parse_id

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
from src.api.routes.messages.helpers import (
    _message_to_response,
    require_snowflake,
)
from .base import MessagesCRUDBase


def _not_found() -> HTTPException:
    """Same 404 as a missing message, so a restricted channel is not disclosed."""
    return HTTPException(
        status_code=404,
        detail={"error": {"code": 404, "message": "Message not found"}},
    )


def _require_channel_view(cid: int, user_id: int, servers_mod) -> None:
    """Fail closed when ``cid`` is a server channel the caller cannot view.

    ``get_message`` (and the message-bound channel check) only prove
    conversation *participation*. For server channels participation is granted
    to every member of the server (see ``ParticipantService.is_participant``),
    independent of ``channels.view``. Without this live gate a member denied
    view on a private channel could fetch any message in it by id -- including
    its attachments and viewer-bound signed URLs. The canonical ``get_channel``
    accessor enforces both membership and ``channels.view``; a channel that
    exists but cannot be resolved is indistinguishable from a missing message.
    """
    if not servers_mod:
        # The backing-channel/alias lookup cannot be evaluated without the
        # servers module. A private-channel alias can exist in the DB
        # independently of that module, so skipping this gate would let a
        # member denied channels.view fetch a private message by id -- including
        # its attachments and viewer-bound signed URLs. Deny instead; a wired
        # store with no backing channel still allows a genuine DM below.
        raise _not_found()

    get_channel = getattr(servers_mod, "get_channel", None)
    channel_exists = getattr(servers_mod, "channel_exists", None)
    reverse = getattr(servers_mod, "get_channel_by_conversation", None)

    def _require_alias_backing_channel() -> None:
        """Gate a server channel's linked conversation-id URL alias.

        Message routes accept either a channel id or the channel's linked
        conversation id (see ``_resolve_channel_scope``). A conversation id is
        NOT a channel id, so the direct ``get_channel``/``channel_exists``
        probes above both miss it and the alias looks like a DM. Resolve it
        membership-agnostically and require a live view grant on the real
        backing channel before a read/delete is allowed. Fail closed on any
        broken lookup; a genuine DM/group has no backing channel and is left
        to the messaging layer's participation check.
        """
        if not callable(reverse):
            # Legacy/stub stores without the reverse lookup cannot be checked
            # for the alias; the addressed-id gate above is all we can do.
            return
        try:
            backing = reverse(cid)
        except Exception:
            raise _not_found()
        if backing is None:
            return
        channel_id = (
            backing.get("id")
            if isinstance(backing, dict)
            else getattr(backing, "id", None)
        )
        if channel_id is None:
            raise _not_found()
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            raise _not_found()
        if channel_id == cid:
            return  # addressed-id gate already checked this exact channel
        if not callable(get_channel):
            raise _not_found()
        try:
            if get_channel(channel_id, user_id) is None:
                raise _not_found()
        except HTTPException:
            raise
        except Exception:
            raise _not_found()

    def _is_server_channel() -> bool:
        if callable(channel_exists):
            try:
                return bool(channel_exists(cid))
            except Exception:
                return True  # cannot prove it is NOT a server channel: deny
        getter = getattr(servers_mod, "get_channel_conversation_id", None)
        if callable(getter):
            try:
                return getter(cid) is not None
            except Exception:
                return True
        return False

    if not callable(get_channel):
        if _is_server_channel():
            raise _not_found()
        _require_alias_backing_channel()
        return
    try:
        if get_channel(cid, user_id) is not None:
            return
    except Exception:
        raise _not_found()
    if _is_server_channel():
        raise _not_found()
    _require_alias_backing_channel()


class RetrieveMixin(MessagesCRUDBase):
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
                cid = parse_id(channel_id, "channel_id")
                # A get-by-id cursor is an untrusted snowflake; bound its range
                # before it reaches the messaging lookup.
                mid = require_snowflake(message_id, "message_id", "Invalid ID format")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid ID format"}},
                )

            message = await run_in_threadpool(
                messaging.get_message, current_user.user_id, mid
            )
            if not message:
                # Visibility: distinguish "row missing" from "access denied"
                # without changing the client-visible 404 contract.
                try:
                    _dbg_row = None
                    _dbg_part = None
                    _dbg_db = api.get_db()
                    if _dbg_db is not None:
                        _dbg_row = _dbg_db.fetch_one(
                            "SELECT conversation_id, author_id FROM "
                            "msg_messages WHERE id = ?",
                            (mid,),
                        )
                        if _dbg_row is not None and messaging is not None:
                            _p_svc = getattr(
                                getattr(messaging, "_manager", messaging),
                                "_participant_svc",
                                None,
                            )
                            if _p_svc is None:
                                _mgr = getattr(messaging, "_message_svc", None)
                                _p_svc = getattr(_mgr, "_participant_svc", None)
                            if _p_svc is not None:
                                try:
                                    _dbg_part = bool(
                                        _p_svc.is_participant(
                                            _dbg_row["conversation_id"],
                                            current_user.user_id,
                                        )
                                    )
                                except Exception as _pe:
                                    _dbg_part = f"check-error: {_pe!r}"
                    logger.warning(
                        f"get_message {mid} unresolved for user "
                        f"{current_user.user_id} in channel {cid}: "
                        f"row={'missing' if _dbg_row is None else dict(_dbg_row)}, "
                        f"participant={_dbg_part}"
                    )
                except Exception as debug_error:
                    # Debug logging must never mask the HTTP error below.
                    logger.debug(f"message access debug log failed: {debug_error}")
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            # The URL channel must be the message's actual channel: a
            # mismatch returns the same 404 a nonexistent message produces
            # (no existence oracle for foreign conversations).
            self._ensure_message_in_channel(
                message,
                self._resolve_channel_scope(
                    cid, current_user.user_id, api.get_servers()
                ),
            )

            # Participation in a server channel is granted to every server
            # member, so it is NOT an authorization boundary for a by-id read.
            # Re-check the live per-channel view permission on every request,
            # including any cached message body, before serializing (which also
            # signs attachment URLs for this viewer).
            _require_channel_view(cid, current_user.user_id, api.get_servers())

            author_info = {"username": None, "avatar_url": None, "badges": []}
            auth = api.get_auth()
            if auth:
                try:
                    user = await run_in_threadpool(auth.get_user, message.author_id)
                    if user:
                        author_info["username"] = user.username
                        author_info["avatar_url"] = getattr(user, "avatar_url", None)
                        author_info["badges"] = getattr(user, "badges", [])
                except Exception as badge_error:
                    logger.debug(f"message author badge lookup failed: {badge_error}")

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
