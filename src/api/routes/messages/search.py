"""
Message search routes - Search messages in channels.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse
from .helpers import _message_to_response

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
    content: str = Query(..., description="Search query"),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """Search messages in a channel by content."""
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

        messages = []

        # Use messaging module's search (handles encryption via blind index)
        if messaging:
            try:
                # messaging.search_messages handles both DMs and server channels
                # as they are all backed by the same conversation system
                def _search_messages_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        return messaging.search_messages(
                            user_id=current_user.user_id,
                            conversation_id=cid,
                            query=content,
                            limit=limit,
                        )
                    finally:
                        if db:
                            db.close()

                messages = await run_in_threadpool(_search_messages_sync)
            except Exception as e:
                logger.debug(f"Messaging search failed: {e}")

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
            except Exception:
                pass

        media_mod = api.get_media()

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
