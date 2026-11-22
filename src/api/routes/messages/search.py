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
from src.api.routes.users.helpers import authenticated_cache_key
from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse
from .helpers import (
    _message_to_response,
    batch_attachment_file_ids,
    batch_embed_suppressed_ids,
)

router = APIRouter(tags=["Messages"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": 404, "message": "Channel not found"}},
    )


# Partition search-result cache entries by credential: generate_cache_key()
# reduces TokenInfo to TokenInfo:<user_id>, which a bot shares with its owner.
# Without this, a bot's search hits can be served to the owner (or vice versa).
#
# Only the *data* work lives behind this decorator. The authorization gate stays
# in the route handler and runs on EVERY request, including cache hits: the
# decorator returns before this function is entered on a warm entry, so a viewer
# whose channel/conversation access was revoked must never reach a still-warm
# result body. This mirrors the gate-before-cache layout used by the presence
# and reaction read paths.
@cached(
    ttl=60,
    prefix="search_messages_api",
    key_builder=authenticated_cache_key("search_messages_api"),
)
async def _search_messages_cached(
    conv_id: int,
    content: str,
    limit: int,
    current_user: TokenInfo,
) -> List[MessageResponse]:
    """Data-only cold path for a channel message search.

    Callers must have already applied the live participant gate. The core
    search enforces it again on this cold path as defence in depth, but that
    check cannot protect a cache hit -- hence the route-level gate.
    """
    messaging = api.get_messaging()
    auth = api.get_auth()

    try:
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
            f"Failed to search messages in conversation {conv_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


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
    servers_mod = api.get_servers()
    messaging = api.get_messaging()

    try:
        try:
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Resolve channel_id to conversation_id for server channels.
        channel = None
        if servers_mod:
            channel = servers_mod.get_channel(cid, current_user.user_id)

        # Message routes also accept a server channel's linked conversation id
        # as the URL id. get_channel/channel_exists only know channel ids, so
        # they report "not a channel" for that alias and the code below would
        # treat it as a DM -- skipping the live messages.read gate that runs
        # before the cached read. Resolve the alias membership-agnostically and
        # re-gate on the REAL channel (see _require_edit_channel_view for the
        # same pattern). The authoritative service layer enforces this too, for
        # callers that bypass the route; this live check additionally protects
        # a warm search cache entry.
        alias_channel_id = None
        if channel is None and servers_mod:
            reverse = getattr(servers_mod, "get_channel_by_conversation", None)
            if callable(reverse):
                try:
                    backing = reverse(cid)
                except Exception:
                    backing = None
                if isinstance(backing, dict) and backing.get("id") is not None:
                    alias_channel_id = int(backing["id"])
                    channel = servers_mod.get_channel(
                        alias_channel_id, current_user.user_id
                    )

        # A server channel's messages require a per-channel read grant, not
        # merely membership of the parent server. The participant check below
        # (is_participant) only proves DM participation or server membership,
        # so on its own it lets a member read a channel whose ``messages.read``
        # is denied by a role/channel override -- the same hole the channel
        # message *listing* path closes with require_permission. Check the
        # channel grant live here, before the cached read, and fail closed when
        # the backing channel or its permission cannot be established.
        channel_exists = getattr(servers_mod, "channel_exists", None)
        is_server_channel = alias_channel_id is not None or (
            bool(channel_exists(cid))
            if callable(channel_exists)
            else channel is not None
        )
        if is_server_channel:
            if servers_mod is None or channel is None:
                raise _not_found()
            server_id = getattr(channel, "server_id", None)
            if not server_id:
                raise _not_found()
            # Gate the REAL backing channel id, not the URL alias, so the
            # permission lookup targets the id the channel table knows.
            gate_channel_id = alias_channel_id if alias_channel_id is not None else cid
            try:
                can_read = bool(
                    servers_mod.has_permission(
                        current_user.user_id,
                        server_id,
                        "messages.read",
                        gate_channel_id,
                    )
                )
            except Exception as perm_error:
                logger.debug(
                    f"message search read-permission check failed for channel "
                    f"{cid}: {perm_error}"
                )
                can_read = False
            if not can_read:
                raise _not_found()
            conv_id = getattr(channel, "conversation_id", None) or cid
        else:
            # DM / group conversation: no separate channel grant applies; the
            # participant gate below is the visibility boundary.
            conv_id = cid

        # LIVE GATE before the cached read. The participant check used to live
        # only inside the @cached handler (and inside core.search_messages), so
        # a viewer kicked from the channel/server kept receiving their old hits
        # for the whole 60s cache TTL. Run the same membership check on every
        # request, including cache hits, and fail closed if it cannot be
        # established. When there is no messaging module there is no data to
        # leak: the data-only helper returns an empty page as before.
        if messaging is not None:
            try:
                allowed = bool(messaging.is_participant(conv_id, current_user.user_id))
            except Exception as gate_error:
                logger.debug(
                    f"message search participant check failed for conv {conv_id}: "
                    f"{gate_error}"
                )
                allowed = False
            if not allowed:
                raise _not_found()

        return await _search_messages_cached(
            conv_id=conv_id,
            content=content,
            limit=limit,
            current_user=current_user,
        )
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
