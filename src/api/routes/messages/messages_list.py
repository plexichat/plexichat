"""
Message list routes - Fetching lists of messages (channel messages, pinned messages).
"""

from src.core.security.hardening import parse_id

from typing import List, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.core.base import SnowflakeID
from src.core.database.cache.request_local import coalesced_cache_get
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    ChannelTypeError,
    PermissionDeniedError,
)
from src.core.messaging.exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
)
from src.core.messaging.render_generation import get as get_render_generation
from src.api.routes.users.helpers import authenticated_cache_key
from .render_cache import get_render_cache
from .helpers import (
    _message_to_response,
    batch_attachment_file_ids,
    batch_embed_suppressed_ids,
    require_snowflake,
)

router = APIRouter(tags=["Messages"])


def _channel_messages_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": 404, "message": "Channel not found"}},
    )


def _require_channel_messages_view(
    cid: SnowflakeID, user_id: SnowflakeID, servers_mod: Any
) -> None:
    """Fail closed when ``cid`` addresses a server channel the caller can't view.

    ``get_channel`` returns ``None`` (rather than raising) when ``channels.view``
    is denied, which the servers message reader surfaces as
    ``ChannelNotFoundError``. The list route treats that as "not a server
    channel" and falls back to the messaging service, where participation is
    granted to every server member -- independent of ``channels.view``. The URL
    id may also be the channel's linked *conversation* id, so gate both spellings
    membership-agnostically before any private page can be served.
    """
    if not servers_mod:
        # The backing-channel/alias lookup cannot be evaluated without the
        # servers module. A private-channel alias can exist in the DB
        # independently of that module, so skipping this gate would let a
        # member denied channels.view page through private-channel messages.
        # Deny instead; a wired store with no backing channel still allows a
        # genuine DM below.
        raise _channel_messages_not_found()
    get_channel = getattr(servers_mod, "get_channel", None)
    channel_exists = getattr(servers_mod, "channel_exists", None)
    reverse = getattr(servers_mod, "get_channel_by_conversation", None)

    # 1) cid used directly as a channel id.
    if callable(get_channel):
        try:
            if get_channel(cid, user_id) is not None:
                return  # live view grant on the addressed channel
        except Exception:
            raise _channel_messages_not_found()
    if callable(channel_exists):
        try:
            if channel_exists(cid):
                raise _channel_messages_not_found()
        except HTTPException:
            raise
        except Exception:
            # Cannot prove it is NOT a server channel; fall through to the
            # reverse lookup rather than serving a possibly private page.
            pass

    # 2) cid used as the linked conversation id (server-channel alias).
    if callable(reverse):
        try:
            channel_row = reverse(cid)
        except Exception:
            raise _channel_messages_not_found()
        if channel_row is not None:
            channel_id = (
                channel_row.get("id") if isinstance(channel_row, dict) else None
            )
            if channel_id is None or not callable(get_channel):
                raise _channel_messages_not_found()
            try:
                if get_channel(int(channel_id), user_id) is None:
                    raise _channel_messages_not_found()
            except HTTPException:
                raise
            except Exception:
                raise _channel_messages_not_found()
    # No server channel backs this id: a genuine DM/group conversation whose
    # access is gated by messaging participation.


async def _verify_channel_messages_access(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    """Live view gate that runs before the cached message-list body."""
    try:
        cid = parse_id(channel_id, "channel_id")
    except ValueError:
        return  # the endpoint returns the 400 for a malformed id
    await run_in_threadpool(
        _require_channel_messages_view, cid, current_user.user_id, api.get_servers()
    )


@router.get(
    "/channels/{channel_id}/messages",
    response_model=List[MessageResponse],
    summary="Get channel messages",
    dependencies=[Depends(_verify_channel_messages_access)],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
# Cache entries for private channel content MUST be partitioned by credential,
# not just by user_id: generate_cache_key() collapses a TokenInfo to
# ``TokenInfo:<user_id>``, and bot tokens carry their owner's user_id. Without
# authenticated_cache_key a bot and its owner collide on the same key, so one
# credential's message page is replayed to the other (cross-credential leak).
@cached(
    ttl=10,
    prefix="messages_api",
    key_builder=authenticated_cache_key("messages_api"),
)
async def get_channel_messages(
    channel_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: Any = Query(default=None),
    after: Any = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """
    Get messages in a channel.

    Returns messages with pagination support.
    Works for both server channels and DM conversations.
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    auth = api.get_auth()

    try:

        def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        try:
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Pagination cursors are message snowflakes: range-check them like
        # every other id rather than letting bare int() accept negatives or
        # arbitrarily large values into the storage lookup.
        before_id = (
            require_snowflake(before, "before", "Invalid before ID") if before else None
        )
        after_id = (
            require_snowflake(after, "after", "Invalid after ID") if after else None
        )

        # Bounded in-process LRU of fully normalized pages. The conversation's
        # generation is part of the key, so any message write in this
        # conversation (which bumps it) makes every cached page miss at once.
        render_key = (
            "messages_api",
            current_user.user_id,
            cid,
            limit,
            before_id,
            after_id,
            get_render_generation(cid),
        )
        render_cache = get_render_cache()
        cached_page = render_cache.get(render_key)
        if cached_page is not None:
            return list(cached_page)

        messages = None

        # Try server channel first (more common)
        if servers_mod:
            try:
                # Use run_in_threadpool for synchronous manager call
                messages = await run_in_threadpool(
                    servers_mod.get_channel_messages,
                    user_id=current_user.user_id,
                    channel_id=cid,
                    limit=limit,
                    before_id=before_id,
                    after_id=after_id,
                )
            except (
                ServerNotFoundError,
                ChannelNotFoundError,
                ChannelTypeError,
            ) as e:
                # Expected for any id that is NOT a server text channel (a
                # DM/group conversation id lands here as well), so fall back
                # to the conversation lookup below. Access/permission errors
                # are deliberately NOT caught here: a server channel the
                # caller cannot view must never be re-read through the DM
                # fallback with the channel id used as a conversation id —
                # that bypasses the channel-view permission check. Those
                # errors propagate to the 404 mapping below.
                logger.debug(
                    f"Channel {cid} is not a server text channel "
                    f"({type(e).__name__}); trying conversation lookup"
                )
                messages = None

        # Fallback to DM/Conversation
        if messages is None and messaging:
            # The servers reader's ChannelNotFoundError is ambiguous: it is
            # raised both for "not a server channel" and for a channel the
            # caller cannot view (get_channel returns None on a channels.view
            # deny). Before using the URL id as a conversation id, fail closed
            # if it is a server channel (or its linked-conversation alias) the
            # caller cannot view — messaging participation alone is not enough.
            await run_in_threadpool(
                _require_channel_messages_view, cid, current_user.user_id, servers_mod
            )
            try:
                messages = await run_in_threadpool(
                    messaging.get_messages,
                    user_id=current_user.user_id,
                    conversation_id=cid,
                    limit=limit,
                    before_id=before_id,
                    after_id=after_id,
                )

            except Exception as e:
                if isinstance(
                    e,
                    (
                        ServerNotFoundError,
                        ChannelNotFoundError,
                        ConversationNotFoundError,
                        ConversationAccessDeniedError,
                    ),
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                raise

        if messages is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

        # Defense in depth: a storage backend that ignores the LIMIT (or a
        # compromised/corrupt row set) must not turn into an unbounded
        # enrichment fan-out or response. The route already caps ``limit`` at
        # 100; slice again so the invariant holds even for a misbehaving
        # manager. Valid callers see byte-identical output.
        messages = messages[: min(limit, 100)]

        # Bulk fetch all author usernames and avatars in single query (avoids N+1)
        author_ids = list(
            {
                int(author_id)
                for author_id in (_get_attr(m, "author_id") for m in messages)
                if author_id is not None
            }
        )
        author_cache = {}  # {str(user_id): {"username": str, "avatar_url": str, "badges": list}}
        if auth and author_ids:
            try:
                users = await run_in_threadpool(auth.get_user_profiles_bulk, author_ids)
                # Ensure all keys in author_cache are strings for consistent lookup
                author_cache = {
                    str(uid): {
                        "username": u["username"],
                        "avatar_url": u.get("avatar_url"),
                        "badges": u.get("badges", []),
                    }
                    for uid, u in users.items()
                }
            except Exception as user_error:
                logger.debug(f"message list author enrichment failed: {user_error}")

        # Get media module once (URL signing + attachment hydration below).
        media_mod = api.get_media()

        # Resolve attachment file ids in one batched lookup so serialization
        # never falls back to a per-attachment get_file_by_filename() query.
        # Shared with the pins/search routes, which had the same N+1.
        await run_in_threadpool(
            batch_attachment_file_ids, messages, media_mod=media_mod
        )

        # Fetch reactions for all messages in a single batch query (avoids N+1)
        reactions_module = api.get_reactions()
        reactions_cache = {}
        if reactions_module and messages:
            try:
                message_ids = [
                    mid
                    for mid in (_get_attr(m, "id") for m in messages)
                    if mid is not None
                ]
                reactions_cache = reactions_module.get_reactions_batch(
                    current_user.user_id, message_ids
                )
            except Exception:
                # Fallback to empty reactions if batch fails
                reactions_cache = {
                    mid: []
                    for mid in (_get_attr(m, "id") for m in messages)
                    if mid is not None
                }

        # Bulk fetch reader information for messages authored by current user (sender only)
        # This eliminates the N+1 problem in _message_to_response
        readers_cache = {}  # {str(message_id): [username, ...]}
        if messaging and auth and messages:
            try:
                # Only check messages authored by current user
                own_message_ids = []
                for m in messages:
                    mid = _get_attr(m, "id")
                    author_id = _get_attr(m, "author_id")
                    if mid is None or author_id is None:
                        continue
                    if int(author_id) == int(current_user.user_id):
                        own_message_ids.append(mid)

                if own_message_ids:
                    reader_ids_map = await run_in_threadpool(
                        messaging.get_batch_reader_ids,
                        current_user.user_id,
                        own_message_ids,
                    )

                    # Collect all unique reader IDs to fetch usernames in bulk
                    all_reader_ids = set()
                    for r_ids in reader_ids_map.values():
                        all_reader_ids.update(r_ids)

                    if all_reader_ids:
                        reader_users = await run_in_threadpool(
                            auth.get_user_profiles_bulk, list(all_reader_ids)
                        )
                        # Use string keys for robust lookup
                        reader_users_str = {
                            str(uid): u for uid, u in reader_users.items()
                        }

                        # Build the readers cache with ReaderInfo objects
                        for mid, r_ids in reader_ids_map.items():
                            readers_cache[str(mid)] = [
                                {
                                    "id": str(rid),
                                    "username": reader_users_str[str(rid)]["username"],
                                    "avatar_url": reader_users_str[str(rid)].get(
                                        "avatar_url"
                                    ),
                                }
                                for rid in r_ids
                                if str(rid) in reader_users_str
                            ]
            except Exception as e:
                logger.warning(f"Failed to bulk fetch reader info: {e}")

        # Resolve embed suppression for the whole page in one query instead of
        # one are_embeds_suppressed() call per message during serialization.
        suppressed_ids = batch_embed_suppressed_ids(
            [mid for mid in (_get_attr(m, "id") for m in messages) if mid is not None],
            embeds_mod=api.get_embeds(),
        )

        result = []
        for m in messages:
            try:
                # Robust lookup using string keys
                author_id = _get_attr(m, "author_id")
                mid = _get_attr(m, "id")
                author_info = author_cache.get(str(author_id)) or {}

                result.append(
                    _message_to_response(
                        m,
                        author_username=author_info.get("username"),
                        author_avatar_url=author_info.get("avatar_url"),
                        author_badges=author_info.get("badges"),
                        reactions_data=reactions_cache.get(mid, []),
                        read_by_data=readers_cache.get(str(mid)),
                        media_mod=media_mod,
                        viewer_user_id=current_user.user_id,
                        suppress_embeds=mid in suppressed_ids,
                    )
                )
            except Exception as message_error:
                logger.warning(
                    f"Skipping malformed message in channel {channel_id}: {message_error}"
                )

        render_cache.put(render_key, result)
        return result
    except HTTPException:
        raise
    except (ChannelAccessDeniedError, PermissionDeniedError):
        # A server channel the caller may not view is indistinguishable from
        # a missing one at this boundary — never disclose its existence or
        # fall back to the DM path with the channel id.
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )
    except Exception as e:
        logger.error(
            f"Failed to get messages for channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


def _pins_channel_cache_key(channel_id: SnowflakeID, user_id: SnowflakeID) -> str:
    """Request-local memo key for the pins channel-resolution decision."""
    return f"pins:channel:{int(channel_id)}:{int(user_id)}"


def _resolve_pins_channel(
    servers_mod: Any, channel_id: SnowflakeID, user_id: SnowflakeID
) -> Any:
    """Resolve ``get_channel`` at most once per request for the pins route.

    The live access gate (``_verify_pinned_channel_access``) and the cached
    endpoint body both need the *same* ``get_channel(channel_id, user_id)``
    decision. Outside the memo they each pay a Valkey channel-row GET, a DB
    membership query and the ``channels.view`` permission walk. The
    request-local store coalesces the second lookup into the first; it lives
    only for this ASGI request (see ``request_local``), never crosses the
    request boundary, and memoises the exact value the authoritative resolver
    returned -- including ``None`` and exceptions -- so it never widens a grant.
    """
    return coalesced_cache_get(
        _pins_channel_cache_key(channel_id, user_id),
        loader=lambda: servers_mod.get_channel(channel_id, user_id),
    )


async def _verify_pinned_channel_access(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    """Re-check channel view access on *every* pins request, before the cache.

    ``get_pinned_messages`` is wrapped in a 120s ``@cached`` body. Previously
    the only access check lived inside that cached function, so a viewer whose
    channel/server access was revoked kept receiving the warm private pin list
    until the entry expired. FastAPI resolves route dependencies before calling
    the endpoint, so this gate runs on cache hits and misses alike. It denies
    any server channel the caller can no longer resolve through ``get_channel``
    while leaving genuine DM conversation ids (which are not server channels)
    to the messaging service's participation check in the cached loader.
    """
    try:
        cid = parse_id(channel_id, "channel_id")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        ) from None

    servers_mod = api.get_servers()
    if not servers_mod:
        # The reverse lookup cannot be evaluated without the servers module.
        # A private-channel alias can exist in the DB independently of that
        # module, so skipping this cached-body gate would keep serving a warm
        # private pin list to a viewer whose channel access was revoked. Deny
        # instead; a wired store with no backing channel still allows a genuine
        # DM to the messaging participation check in the cached loader.
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )

    try:
        channel = await run_in_threadpool(
            _resolve_pins_channel, servers_mod, cid, current_user.user_id
        )
    except (ChannelAccessDeniedError, PermissionDeniedError) as exc:
        logger.debug(f"pins channel access denied for {cid}: {exc}")
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        ) from None
    except (ServerNotFoundError, ChannelNotFoundError):
        # The lookup itself says the id is not a server channel; the DM path
        # below remains available.
        return
    except Exception as exc:
        # An unproven lookup must never fall through to a cached private body.
        logger.debug(f"pins channel access probe failed for {cid}: {exc}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        ) from None

    if channel is not None:
        return  # live access proven for this credential

    # The URL id may be the channel's *conversation* id: message routes accept
    # that alias, but get_channel/channel_exists only recognise channel ids and
    # would treat the alias as a DM. Resolve it membership-agnostically; if it
    # backs a server channel, require a live view grant on THAT channel before
    # any cached pin body can be served. Mirrors the pin/unpin route fix.
    reverse = getattr(servers_mod, "get_channel_by_conversation", None)
    if callable(reverse):
        try:
            backing = await run_in_threadpool(reverse, cid)
        except Exception as exc:
            logger.debug(f"pins conversation reverse lookup failed for {cid}: {exc}")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            ) from None
        if backing is not None:
            try:
                real_channel_id = int(backing["id"])
            except (KeyError, TypeError, ValueError, IndexError):
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Internal server error"}},
                ) from None
            try:
                real_channel = await run_in_threadpool(
                    _resolve_pins_channel,
                    servers_mod,
                    real_channel_id,
                    current_user.user_id,
                )
            except (ChannelAccessDeniedError, PermissionDeniedError) as exc:
                logger.debug(f"pins backing channel access denied for {cid}: {exc}")
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                ) from None
            except Exception as exc:
                logger.debug(f"pins backing channel probe failed for {cid}: {exc}")
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Internal server error"}},
                ) from None
            if real_channel is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            return  # live access proven on the real backing channel

    # get_channel returns None both for "not a server channel" and for "user
    # lost access". Only a membership-agnostic existence probe may tell them
    # apart; if it cannot, fail closed instead of serving a warm private page.
    try:
        exists = await run_in_threadpool(servers_mod.channel_exists, cid)
    except Exception as exc:
        logger.debug(f"pins channel existence probe failed for {cid}: {exc}")
        exists = True
    if exists:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )


@router.get(
    "/channels/{channel_id}/pins",
    response_model=List[MessageResponse],
    summary="Get pinned messages",
    dependencies=[Depends(_verify_pinned_channel_access)],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(
    ttl=120,
    prefix="channel_pins_api",
    key_builder=authenticated_cache_key("channel_pins_api"),
)
async def get_pinned_messages(
    channel_id: str,
    # Pins used to be returned in full with no bound. A channel with a very
    # large pin set produced an unbounded response plus unbounded author/
    # reaction enrichment work. Cap the list at the route before any of that
    # fan-out; callers that need more can page with the channel message list.
    limit: int = Query(default=100, ge=1, le=200),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """Get pinned messages in a channel (bounded)."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    try:
        try:
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        messages = []
        resolved_server_channel = False

        if servers_mod:

            def _get_channel_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    # Reuse the resolution already performed by the live gate
                    # earlier in this request instead of paying for get_channel
                    # a second time (Valkey row GET + membership query + perm).
                    return _resolve_pins_channel(servers_mod, cid, current_user.user_id)
                finally:
                    if db:
                        db.close()

            channel = await run_in_threadpool(_get_channel_sync)
            conversation_id = (
                getattr(channel, "conversation_id", None) if channel else None
            )
            if channel:
                # This is a server channel: pins come exclusively from its
                # linked conversation. An EMPTY pin list must stay empty —
                # never re-query with the server channel id as a conversation
                # id (that pseudo-conversation lookup raises and 500s).
                resolved_server_channel = True
                if conversation_id and messaging:

                    def _get_pins_sync():
                        import src.api as api_module

                        db = api_module.get_db()
                        try:
                            return messaging.get_pinned_messages(
                                current_user.user_id, conversation_id
                            )
                        finally:
                            if db:
                                db.close()

                    messages = await run_in_threadpool(_get_pins_sync) or []

        if not messages and not resolved_server_channel and messaging:

            def _get_pins_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    return messaging.get_pinned_messages(current_user.user_id, cid)
                finally:
                    if db:
                        db.close()

            messages = await run_in_threadpool(_get_pins_sync) or []

        # Bound the fan-out (author profiles, reactions) and the response to
        # at most ``limit`` pins regardless of what the storage layer returns.
        messages = messages[:limit]

        # One media lookup for every attachment on the page instead of one
        # get_file_by_filename() query per attachment in the serializer.
        await run_in_threadpool(
            batch_attachment_file_ids, messages, media_mod=api.get_media()
        )

        author_cache = {}
        author_ids = {m.author_id for m in messages}
        if auth and author_ids:

            def _get_users_bulk_sync():
                import src.api as api_module

                db = api_module.get_db()
                try:
                    return auth.get_user_profiles_bulk(list(author_ids))
                finally:
                    if db:
                        db.close()

            users = await run_in_threadpool(_get_users_bulk_sync)
            author_cache = {
                int(uid): {
                    "username": u.get("username"),
                    "avatar_url": u.get("avatar_url"),
                    "badges": u.get("badges", []),
                }
                for uid, u in users.items()
            }
        suppressed_ids = batch_embed_suppressed_ids(
            [m.id for m in messages if getattr(m, "id", None) is not None],
            embeds_mod=api.get_embeds(),
        )
        result = []
        for m in messages:
            author_id = m.author_id
            info = author_cache.get(author_id, {})
            result.append(
                _message_to_response(
                    m,
                    info.get("username"),
                    info.get("avatar_url"),
                    author_badges=info.get("badges"),
                    media_mod=api.get_media(),
                    viewer_user_id=current_user.user_id,
                    suppress_embeds=m.id in suppressed_ids,
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pinned messages for channel {channel_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
