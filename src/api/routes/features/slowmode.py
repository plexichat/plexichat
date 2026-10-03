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

# Matches ThreadSlowmode.MAX_INTERVAL_MS (6 hours).
MAX_SLOWMODE_SECONDS = 21600


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

    interval_seconds = max(0, body.interval_ms // 1000)
    if interval_seconds > MAX_SLOWMODE_SECONDS:
        raise_bad_request(
            f"Slowmode interval cannot exceed {MAX_SLOWMODE_SECONDS} seconds"
        )

    db = api.get_db()
    if not db:
        raise_internal("Database not available")

    channel_row = db.fetch_one(
        "SELECT server_id FROM srv_channels WHERE id = ?",
        (cid,),
    )
    if not channel_row:
        raise_not_found("Channel not found")
    server_id_value = (
        channel_row["server_id"] if isinstance(channel_row, dict) else channel_row[0]
    )
    if not server_id_value:
        raise_not_found("Channel not found")
    server_id = int(server_id_value)

    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    can_manage = False
    for permission in ("channels.manage", "administrator"):
        try:
            if servers_mod.has_permission(
                current_user.user_id, server_id, permission, cid
            ):
                can_manage = True
                break
        except Exception:
            continue
    if not can_manage:
        raise_forbidden("Missing permission to manage this channel")

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
            # Snowflake ids are emitted as strings: a JS Number cannot
            # hold a 64-bit id exactly, so a numeric id would be rounded
            # by the client and subsequent lookups would miss.
            "channel_id": str(cid),
            "slowmode_seconds": interval_seconds,
        }
    except Exception as e:
        logger.error(f"Failed to set channel slowmode: {e}")
        raise_internal("Internal server error")


@router.get(
    "/channels/{channel_id}/slowmode",
    summary="Get channel slowmode",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_channel_slowmode(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    cid = parse_id(channel_id, "channel ID")

    db = api.get_db()
    if not db:
        raise_internal("Database not available")
    row = db.fetch_one(
        "SELECT id, server_id, slowmode_seconds FROM srv_channels WHERE id = ?",
        (cid,),
    )
    if not row:
        raise_not_found("Channel not found")

    # M4 FIX: reads are gated behind channel view access so an authenticated
    # user cannot confirm the existence of private cross-server channels.
    # Fail closed when the servers module is unavailable: without it the gate
    # cannot be evaluated, and skipping it leaked channel existence/slowmode
    # for every channel id to any authenticated caller.
    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    can_view = False
    try:
        can_view = servers_mod.get_channel(cid, current_user.user_id) is not None
    except Exception:
        can_view = False
    if not can_view:
        exists = False
        try:
            exists = bool(servers_mod.channel_exists(cid))
        except Exception:
            exists = False
        if not exists:
            raise_not_found("Channel not found")
        raise_forbidden("Channel access denied")

    slowmode_seconds = row["slowmode_seconds"] if isinstance(row, dict) else row[2]
    return {"channel_id": str(cid), "slowmode_seconds": slowmode_seconds or 0}


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
    except PermissionError:
        raise_forbidden("Access denied")
    except ValueError:
        raise_bad_request("Invalid slowmode configuration")


@router.get(
    "/threads/{thread_id}/slowmode",
    summary="Get thread slowmode",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_thread_slowmode(
    thread_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    tid = parse_id(thread_id, "thread ID")

    db = api.get_db()
    from src.core.threads.slowmode import ThreadSlowmode

    # M4 FIX: gate the read behind thread view access, mirroring the write
    # path's can_manage_thread gate, so cross-server threads cannot be probed.
    threads = api.get_threads()
    if threads is not None:
        try:
            can_view = threads.can_view_thread(current_user.user_id, tid)
        except Exception:
            can_view = False
        if not can_view:
            raise_not_found("Thread not found")

    svc = ThreadSlowmode(db)
    result = svc.get_slowmode(tid)
    if not result:
        raise_not_found("Thread not found")
    return {"slowmode": result}
