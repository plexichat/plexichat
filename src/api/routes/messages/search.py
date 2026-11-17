"""
Message search routes - Search messages in channels.
"""

from src.core.security.hardening import parse_id

from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse
from .helpers import (
    _message_to_response,
    batch_attachment_file_ids,
    batch_embed_suppressed_ids,
)

router = APIRouter(tags=["Messages"])


@router.get(
    "/channels/{channel_id}/messages/search",
    response_model=List[MessageResponse],
    summary="Search messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="search_messages_api")
async def search_messages(
    channel_id: str,
    # A search term is fed to the messaging engine's (blind-index) query
    # builder; an unbounded string lets a caller force an arbitrarily large
    # index scan / allocation. Real search terms are short, so cap at the
    # boundary rather than trusting the engine to bound its own input.
    content: str = Query(..., max_length=200, description="Search query"),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """Search messages in a channel by content."""
    messaging = api.get_messaging()
    auth = api.get_auth()
    servers_mod = api.get_servers()

    try:
        try:
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Resolve channel_id to conversation_id for server channels
        conv_id = cid
        if servers_mod:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if (
                channel
                and hasattr(channel, "conversation_id")
                and channel.conversation_id
            ):
                conv_id = channel.conversation_id

        messages = []

        # Use messaging module's search (handles encryption via blind index)
        if messaging:

            def _search_messages_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    return messaging.search_messages(
                        user_id=current_user.user_id,
                        conversation_id=conv_id,
                        query=content,
                        limit=limit,
                    )
                finally:
                    if db:
                        db.close()

            messages = await run_in_threadpool(_search_messages_sync)

        # Bulk fetch all author info
        author_ids = list(set(m.author_id for m in messages))
        author_cache = {}
        if auth and author_ids:
            try:

                def _get_users_bulk_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        return auth.get_user_profiles_bulk(author_ids)
                    finally:
                        if db:
                            db.close()

                users = await run_in_threadpool(_get_users_bulk_sync)
                author_cache = {
                    str(uid): {
                        "username": u["username"],
                        "avatar_url": u.get("avatar_url"),
                        "badges": u.get("badges", []),
                    }
                    for uid, u in users.items()
                }
            except Exception as user_error:
                logger.debug(f"search author enrichment failed: {user_error}")

        media_mod = api.get_media()

        # BEFORE: ``_message_to_response`` resolved embed suppression with one
        # are_embeds_suppressed() query per hit, and each media attachment
        # without a file_id triggered its own get_file_by_filename() query.
        # Both are now resolved for the whole result set in one query each.
        await run_in_threadpool(
            batch_attachment_file_ids, messages, media_mod=media_mod
        )
        suppressed_ids = await run_in_threadpool(
            batch_embed_suppressed_ids,
            [
                mid
                for mid in (getattr(m, "id", None) for m in messages)
                if mid is not None
            ],
            embeds_mod=api.get_embeds(),
        )

        result = []
        for m in messages:
            author_id = m.author_id
            author_info = author_cache.get(str(author_id)) or {}
            result.append(
                _message_to_response(
                    m,
                    author_username=author_info.get("username"),
                    author_avatar_url=author_info.get("avatar_url"),
                    author_badges=author_info.get("badges"),
                    media_mod=media_mod,
                    viewer_user_id=current_user.user_id,
                    suppress_embeds=getattr(m, "id", None) in suppressed_ids,
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to search messages in channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
