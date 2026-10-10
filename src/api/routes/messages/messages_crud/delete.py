"""
Mixin providing message deletion route handlers.

Includes delete-message and related removal operations.
"""

import json

from fastapi import HTTPException, Depends, Body

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SuccessResponse
from src.api.schemas.messages import BulkDeleteRequest
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


from .base import MessagesCRUDBase
from .broadcast import BroadcastMixin


class DeleteMixin(BroadcastMixin, MessagesCRUDBase):
    async def delete_message(
        self,
        channel_id: str,
        message_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        """
        Delete a message.

        Deletes the message. The author, a conversation OWNER/ADMIN, or a
        server moderator (``messages.manage``) can delete. The URL channel
        must be the message's actual channel: a mismatch returns the same
        404 a nonexistent message produces.
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
                url_cid = int(channel_id)
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
            self._ensure_message_in_channel(
                msg,
                self._resolve_channel_scope(
                    url_cid, current_user.user_id, api.get_servers()
                ),
            )

            cid = int(getattr(msg, "conversation_id"))

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

            # Contract alignment (parity #3): the repository soft-delete
            # keeps a tombstone row that REFETCHES will render, while
            # MESSAGE_DELETE makes live clients remove the element. Both
            # views must agree, so a MESSAGE_UPDATE tombstone
            # (deleted=true, content blanked) is broadcast IN ADDITION to
            # MESSAGE_DELETE. Clients tolerate both events.
            try:
                from src.api.routes.messages.helpers import _message_to_response

                auth = api.get_auth()
                author_username = None
                author_avatar_url = None
                author_badges = []
                if auth:
                    try:
                        user = auth.get_user(int(getattr(msg, "author_id")))
                        if user:
                            author_username = user.username
                            author_avatar_url = getattr(user, "avatar_url", None)
                            author_badges = getattr(user, "badges", []) or []
                    except Exception:
                        pass

                reactions_data = []
                reactions_module = api.get_reactions()
                if reactions_module:
                    try:
                        reactions_data = reactions_module.get_reactions_batch(
                            0, [mid]
                        ).get(mid, [])
                    except Exception:
                        reactions_data = []

                tombstone = _message_to_response(
                    msg,
                    author_username=author_username,
                    author_avatar_url=author_avatar_url,
                    author_badges=author_badges,
                    channel_id=url_cid,
                    reactions_data=reactions_data,
                    # Fan-out broadcast: no per-viewer media signing (the
                    # deleter-bound signatures would be useless to every
                    # other recipient and leak who deleted the message);
                    # attachment URLs stay auth+per-file protected. The
                    # poll is hydrated with the AUTHOR as context viewer
                    # so AFTER_VOTE/AFTER_END results are not leaked to
                    # the deleter's visibility window.
                    poll_viewer_user_id=int(getattr(msg, "author_id"))
                    if getattr(msg, "author_id", None)
                    else None,
                )
                tombstone.content = ""
                tombstone.deleted = True

                await self._broadcast_message_update(
                    tombstone,
                    url_cid,
                    servers_mod,
                    messaging,
                    current_user,
                    conversation_id=cid,
                )
            except Exception as e:
                logger.debug(
                    f"Tombstone MESSAGE_UPDATE broadcast skipped for {mid}: {e}"
                )

            await self._broadcast_message_delete(
                mid,
                url_cid,
                cid,
                servers_mod,
                messaging,
                current_user,
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
            elif isinstance(e, MessageAccessDeniedError):
                logger.warning(f"Delete denied for message {message_id}")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif isinstance(e, (InvalidContentError, ContentTooLongError)):
                logger.warning(f"Delete rejected for message {message_id}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                logger.warning(f"Delete denied for message {message_id}")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            logger.error(
                f"Error deleting message {message_id} in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )

    async def bulk_delete_messages(
        self,
        channel_id: str,
        body: BulkDeleteRequest = Body(...),
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        """
        Bulk-delete messages from a channel.

        Only messages that belong to the addressed channel AND that the
        caller may delete (author, conversation OWNER/ADMIN, or server
        ``messages.manage`` moderator) are removed. Emits MESSAGE_DELETE_BULK
        so connected clients remove the messages from the view without a
        refresh.
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
                cid = int(channel_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            raw_ids = list(body.message_ids or [])
            if len(raw_ids) > 100:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot bulk-delete more than 100 messages",
                        }
                    },
                )
            try:
                message_ids = [int(m) for m in raw_ids]
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid message ID"}},
                )
            if not message_ids:
                return SuccessResponse(success=True, message=None)

            deleted = messaging.delete_messages_bulk(
                current_user.user_id, cid, message_ids
            )

            if deleted:
                from src.core.events.gateway_emit import emit_message_delete_bulk

                servers_mod = api.get_servers()
                guild_id = None
                if servers_mod:
                    try:
                        channel = servers_mod.get_channel(cid, current_user.user_id)
                        guild_id = getattr(channel, "server_id", None)
                    except Exception:
                        guild_id = None
                emit_message_delete_bulk(
                    channel_id=cid,
                    message_ids=deleted,
                    guild_id=guild_id,
                    actor_id=current_user.user_id,
                )

            if not deleted:
                return SuccessResponse(
                    success=False,
                    message="No eligible messages were deleted",
                )
            if len(deleted) < len(message_ids):
                return SuccessResponse(
                    success=False,
                    message=(
                        f"Deleted {len(deleted)} of {len(message_ids)} messages; "
                        "the rest were not eligible"
                    ),
                )
            return SuccessResponse(success=True, message=None)
        except HTTPException:
            raise
        except Exception as e:
            if isinstance(e, MessageNotFoundError):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            if isinstance(e, MessageAccessDeniedError):
                logger.warning("Bulk delete denied")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            if isinstance(e, (InvalidContentError, ContentTooLongError)):
                logger.warning("Bulk delete rejected: batch limit exceeded")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Cannot bulk-delete more than 100 messages",
                        }
                    },
                )
            if isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                logger.warning("Bulk delete denied")
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            logger.error(
                f"Error bulk-deleting messages in channel {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
