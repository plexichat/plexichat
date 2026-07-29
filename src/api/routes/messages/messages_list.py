"""
Message list routes - Fetching lists of messages (channel messages, pinned messages).
"""

from typing import List, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import MessageResponse
from src.api.schemas.common import ErrorResponse
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
)
from src.core.messaging.exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
)
from .helpers import _message_to_response

router = APIRouter(tags=["Messages"])


@router.get(
    "/channels/{channel_id}/messages",
    response_model=List[MessageResponse],
    summary="Get channel messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=10, prefix="messages_api")
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

        def _set_attr(obj: Any, name: str, value: Any) -> None:
            if isinstance(obj, dict):
                obj[name] = value
            else:
                setattr(obj, name, value)

        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        before_id = int(before) if before else None
        after_id = int(after) if after else None

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
            except Exception:
                # If not a server channel or no access, fall back to messaging
                messages = None

        # Fallback to DM/Conversation
        if messages is None and messaging:
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
            except Exception:
                pass

        # OPTIMIZATION: Pre-fetch file IDs for attachments to avoid N+1 in _message_to_response
        media = api.get_media()
        if media and messages:
            filenames_to_fetch = set()
            for msg in messages:
                attachments = _get_attr(msg, "attachments") or []
                if attachments:
                    for att in attachments:
                        # Check if file_id is missing in metadata
                        has_file_id = False
                        metadata = _get_attr(att, "metadata")
                        if metadata:
                            if isinstance(metadata, dict):
                                has_file_id = "file_id" in metadata
                            elif isinstance(metadata, str):
                                import json

                                try:
                                    m = json.loads(metadata)
                                    has_file_id = "file_id" in m
                                except json.JSONDecodeError:
                                    pass

                        att_url = _get_attr(att, "url")
                        if not has_file_id and att_url:
                            # Extract filename from URL
                            parts = att_url.split("/")
                            if parts:
                                filenames_to_fetch.add(parts[-1])

            if filenames_to_fetch:
                try:
                    file_map = await run_in_threadpool(
                        media.get_files_by_filenames, list(filenames_to_fetch)
                    )
                    # Update attachment metadata in-memory
                    for msg in messages:
                        attachments = _get_attr(msg, "attachments") or []
                        if attachments:
                            for att in attachments:
                                att_url = _get_attr(att, "url")
                                if att_url:
                                    fname = att_url.split("/")[-1]
                                    if fname in file_map:
                                        metadata = _get_attr(att, "metadata")
                                        if not metadata:
                                            metadata = {}
                                            _set_attr(att, "metadata", metadata)
                                        if isinstance(metadata, dict):
                                            metadata["file_id"] = file_map[fname].id
                except Exception as e:
                    logger.debug(f"Failed to bulk fetch media files: {e}")

        # Get media module for URL signing
        media_mod = api.get_media()

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
                    )
                )
            except Exception as message_error:
                logger.warning(
                    f"Skipping malformed message in channel {channel_id}: {message_error}"
                )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get messages for channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/channels/{channel_id}/pins",
    response_model=List[MessageResponse],
    summary="Get pinned messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=120, prefix="channel_pins_api")
async def get_pinned_messages(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[MessageResponse]:
    """Get all pinned messages in a channel."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
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

        if servers_mod:
            try:

                def _get_channel_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        return servers_mod.get_channel(cid, current_user.user_id)
                    finally:
                        if db:
                            db.close()

                channel = await run_in_threadpool(_get_channel_sync)
                conversation_id = (
                    getattr(channel, "conversation_id", None) if channel else None
                )
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
            except Exception:
                pass

        if not messages and messaging:
            try:

                def _get_pins_sync():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        return messaging.get_pinned_messages(current_user.user_id, cid)
                    finally:
                        if db:
                            db.close()

                messages = await run_in_threadpool(_get_pins_sync) or []
            except Exception:
                pass

        author_cache = {}
        author_ids = {m.author_id for m in messages}
        if auth and author_ids:
            try:

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
            except Exception:
                author_cache = {}
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
