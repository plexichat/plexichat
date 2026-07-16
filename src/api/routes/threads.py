"""
Thread routes - Channel thread endpoints.

Exposes the previously headless threads core module over HTTP and emits
THREAD_CREATE / THREAD_UPDATE / THREAD_DELETE gateway events so connected
clients update their thread lists live without a refresh.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Body

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.api.schemas.threads import (
    ThreadCreateRequest,
    ThreadUpdateRequest,
    ThreadResponse,
)
from src.core.events.gateway_emit import (
    emit_thread_create,
    emit_thread_update,
    emit_thread_delete,
)

import utils.logger as logger

router = APIRouter()


def _thread_to_response(thread) -> ThreadResponse:
    """Serialize a Thread dataclass into a response model."""
    thread_type = getattr(thread.thread_type, "value", thread.thread_type)
    state = getattr(thread.state, "value", thread.state)
    duration = getattr(
        thread.auto_archive_duration, "value", thread.auto_archive_duration
    )
    return ThreadResponse(
        id=SnowflakeID(thread.id),
        channel_id=SnowflakeID(thread.channel_id),
        server_id=SnowflakeID(thread.server_id),
        owner_id=SnowflakeID(thread.owner_id),
        name=thread.name,
        thread_type=thread_type,
        state=state,
        parent_message_id=(
            SnowflakeID(thread.parent_message_id)
            if thread.parent_message_id is not None
            else None
        ),
        auto_archive_duration=int(duration),
        message_count=thread.message_count,
        member_count=thread.member_count,
        created_at=thread.created_at,
        archived_at=getattr(thread, "archived_at", None),
        last_message_at=getattr(thread, "last_message_at", None),
        locked=getattr(thread, "locked", False),
    )


def _thread_to_dict(thread) -> dict:
    """Gateway-safe dict for THREAD_* dispatch payloads."""
    return {
        "id": thread.id,
        "channel_id": thread.channel_id,
        "parent_id": thread.channel_id,
        "guild_id": thread.server_id,
        "owner_id": thread.owner_id,
        "name": thread.name,
        "thread_type": getattr(thread.thread_type, "value", thread.thread_type),
        "state": getattr(thread.state, "value", thread.state),
        "parent_message_id": thread.parent_message_id,
        "message_count": thread.message_count,
        "member_count": thread.member_count,
        "created_at": thread.created_at,
        "last_message_at": getattr(thread, "last_message_at", None),
        "locked": getattr(thread, "locked", False),
    }


def _get_threads():
    threads_mod = api.get_threads()
    if not threads_mod:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Threads module not available"}},
        )
    return threads_mod


def _parse_type(threads_mod, value: str):
    try:
        return threads_mod.ThreadType(value)
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread type"}},
        )


def _parse_duration(threads_mod, value: int):
    try:
        return threads_mod.AutoArchiveDuration(int(value))
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid auto-archive duration"}},
        )


def _map_thread_exception(e: Exception) -> Optional[HTTPException]:
    name = type(e).__name__
    if name == "ThreadNotFoundError":
        return HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Thread not found"}},
        )
    if name in ("ThreadAccessDeniedError",):
        return HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": str(e) or "Access denied"}},
        )
    if name in ("ThreadNameError",):
        return HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e) or "Invalid thread name"}},
        )
    if name in ("ThreadArchivedError", "ThreadLockedError"):
        return HTTPException(
            status_code=409,
            detail={"error": {"code": 409, "message": str(e)}},
        )
    if name in ("ChannelNotFoundError",):
        return HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )
    if name == "MessageNotFoundError":
        return HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Message not found"}},
        )
    return None


@router.get(
    "/channels/{channel_id}/threads",
    response_model=List[ThreadResponse],
    summary="List active threads in a channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        503: {"model": ErrorResponse, "description": "Threads module not available"},
    },
    tags=["Threads"],
)
async def list_channel_threads(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ThreadResponse]:
    threads_mod = _get_threads()
    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )
    try:
        threads = threads_mod.get_active_threads(current_user.user_id, cid)
        return [_thread_to_response(t) for t in (threads or [])]
    except HTTPException:
        raise
    except Exception as e:
        mapped = _map_thread_exception(e)
        if mapped:
            raise mapped
        logger.error(
            f"Error listing threads in channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/channels/{channel_id}/threads",
    response_model=ThreadResponse,
    summary="Create a thread in a channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel or message not found"},
        503: {"model": ErrorResponse, "description": "Threads module not available"},
    },
    tags=["Threads"],
)
async def create_channel_thread(
    channel_id: str,
    body: ThreadCreateRequest = Body(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> ThreadResponse:
    threads_mod = _get_threads()
    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    thread_type = _parse_type(threads_mod, body.thread_type)
    duration = _parse_duration(threads_mod, body.auto_archive_duration)

    try:
        if body.parent_message_id is not None:
            thread = threads_mod.create_thread_from_message(
                current_user.user_id,
                int(body.parent_message_id),
                body.name,
                thread_type,
                duration,
            )
        else:
            thread = threads_mod.create_thread(
                current_user.user_id,
                cid,
                body.name,
                thread_type,
                duration,
            )
    except HTTPException:
        raise
    except Exception as e:
        mapped = _map_thread_exception(e)
        if mapped:
            raise mapped
        logger.error(
            f"Error creating thread in channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    emit_thread_create(_thread_to_dict(thread))
    return _thread_to_response(thread)


@router.get(
    "/threads/{thread_id}",
    response_model=ThreadResponse,
    summary="Get a thread",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid thread ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Thread not found"},
        503: {"model": ErrorResponse, "description": "Threads module not available"},
    },
    tags=["Threads"],
)
async def get_thread(
    thread_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> ThreadResponse:
    threads_mod = _get_threads()
    try:
        tid = int(thread_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread ID"}},
        )
    try:
        thread = threads_mod.get_thread(current_user.user_id, tid)
    except HTTPException:
        raise
    except Exception as e:
        mapped = _map_thread_exception(e)
        if mapped:
            raise mapped
        logger.error(f"Error fetching thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    if not thread:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Thread not found"}},
        )
    return _thread_to_response(thread)


@router.patch(
    "/threads/{thread_id}",
    response_model=ThreadResponse,
    summary="Update a thread",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Thread not found"},
        503: {"model": ErrorResponse, "description": "Threads module not available"},
    },
    tags=["Threads"],
)
async def update_thread(
    thread_id: str,
    body: ThreadUpdateRequest = Body(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> ThreadResponse:
    threads_mod = _get_threads()
    try:
        tid = int(thread_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread ID"}},
        )

    duration = None
    if body.auto_archive_duration is not None:
        duration = _parse_duration(threads_mod, body.auto_archive_duration)

    try:
        thread = threads_mod.update_thread(
            current_user.user_id, tid, body.name, duration
        )
    except HTTPException:
        raise
    except Exception as e:
        mapped = _map_thread_exception(e)
        if mapped:
            raise mapped
        logger.error(f"Error updating thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    emit_thread_update(_thread_to_dict(thread))
    return _thread_to_response(thread)


@router.delete(
    "/threads/{thread_id}",
    response_model=SuccessResponse,
    summary="Delete a thread",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid thread ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Thread not found"},
        503: {"model": ErrorResponse, "description": "Threads module not available"},
    },
    tags=["Threads"],
)
async def delete_thread(
    thread_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    threads_mod = _get_threads()
    try:
        tid = int(thread_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread ID"}},
        )

    thread = None
    try:
        thread = threads_mod.get_thread(current_user.user_id, tid)
    except Exception:
        thread = None

    try:
        threads_mod.delete_thread(current_user.user_id, tid)
    except HTTPException:
        raise
    except Exception as e:
        mapped = _map_thread_exception(e)
        if mapped:
            raise mapped
        logger.error(f"Error deleting thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    if thread is not None:
        emit_thread_delete(
            tid,
            guild_id=getattr(thread, "server_id", None),
            parent_id=getattr(thread, "channel_id", None),
        )
    else:
        emit_thread_delete(tid)
    return SuccessResponse(success=True, message=None)
