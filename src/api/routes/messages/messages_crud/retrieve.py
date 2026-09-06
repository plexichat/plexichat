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
from .base import MessagesCRUDBase


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
                except Exception:
                    pass
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
