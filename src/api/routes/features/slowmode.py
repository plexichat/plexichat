from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import (
    parse_id,
    raise_bad_request,
    raise_forbidden,
    raise_internal,
    raise_not_found,
)

router = APIRouter()


class ThreadSlowmodeRequest(BaseModel):
    interval_ms: int = Field(
        ..., ge=0, description="Slowmode interval in ms (0 to disable)"
    )


@router.put(
    "/channels/{channel_id}/slowmode",
    summary="Set channel slowmode",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def set_channel_slowmode(
    channel_id: str,
    body: ThreadSlowmodeRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    cid = parse_id(channel_id, "channel ID")

    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    interval_seconds = max(0, body.interval_ms // 1000)

    db = api.get_db()
    if not db:
        raise_internal("Database not available")
    try:
        db.execute(
            "UPDATE srv_channels SET slowmode_seconds = ? WHERE id = ?",
            (interval_seconds, cid),
        )
        logger.info(
            f"Channel {cid} slowmode set to {interval_seconds}s by user {current_user.user_id}"
        )
        return {
            "success": True,
            "channel_id": cid,
            "slowmode_seconds": interval_seconds,
        }
    except Exception as e:
        logger.error(f"Failed to set channel slowmode: {e}")
        raise_internal("Internal server error")


@router.get(
    "/channels/{channel_id}/slowmode",
    summary="Get channel slowmode",
    responses={401: {"model": ErrorResponse}},
)
async def get_channel_slowmode(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    cid = parse_id(channel_id, "channel ID")

    db = api.get_db()
    if not db:
        raise_internal("Database not available")
    row = db.fetch_one(
        "SELECT id, slowmode_seconds FROM srv_channels WHERE id = ?",
        (cid,),
    )
    if not row:
        raise_not_found("Channel not found")

    return {"channel_id": cid, "slowmode_seconds": row["slowmode_seconds"] or 0}


@router.put(
    "/threads/{thread_id}/slowmode",
    summary="Set thread slowmode",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def set_thread_slowmode(
    thread_id: str,
    body: ThreadSlowmodeRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    tid = parse_id(thread_id, "thread ID")

    db = api.get_db()
    from src.core.threads.slowmode import ThreadSlowmode

    threads = api.get_threads()
    if not threads:
        raise_internal("Threads module not available")

    can_manage = threads.can_manage_thread(current_user.user_id, tid)

    svc = ThreadSlowmode(db)
    try:
        result = svc.set_slowmode(
            tid, body.interval_ms, current_user.user_id, can_manage
        )
        return {"success": True, "slowmode": result}
    except PermissionError as e:
        raise_forbidden(str(e))
    except ValueError as e:
        raise_bad_request(str(e))


@router.get(
    "/threads/{thread_id}/slowmode",
    summary="Get thread slowmode",
    responses={401: {"model": ErrorResponse}},
)
async def get_thread_slowmode(
    thread_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    tid = parse_id(thread_id, "thread ID")

    db = api.get_db()
    from src.core.threads.slowmode import ThreadSlowmode

    svc = ThreadSlowmode(db)
    result = svc.get_slowmode(tid)
    return {"slowmode": result}
