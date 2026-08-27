from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    BanResponse,
    BanCreateRequest,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse

import utils.logger as logger
from src.core.events.gateway_emit import emit_guild_ban_add, emit_guild_ban_remove

router = APIRouter()


@router.get(
    "/{server_id}/bans",
    response_model=List[BanResponse],
    summary="List server bans",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_bans(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[BanResponse]:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        bans = servers_mod.get_bans(current_user.user_id, sid)

        # Enrich bans with the banned user's public info so clients can render
        # real usernames instead of "Unknown User".
        users_map = {}
        user_ids = {
            int(b.user_id)
            for b in (bans or [])
            if getattr(b, "user_id", None) is not None
        }
        if user_ids:
            auth_mod = api.get_auth()
            if auth_mod and hasattr(auth_mod, "get_users_bulk"):
                try:
                    from ..users import _user_to_public_response

                    for uid, u in (
                        auth_mod.get_users_bulk(list(user_ids)) or {}
                    ).items():
                        users_map[int(uid)] = _user_to_public_response(u)
                except Exception as ue:
                    logger.debug(f"Failed to enrich ban users: {ue}")

        return [
            BanResponse(
                user_id=SnowflakeID(b.user_id),
                user=users_map.get(int(b.user_id)),
                reason=getattr(b, "reason", None),
                banned_by=SnowflakeID(b.banned_by) if hasattr(b, "banned_by") else None,
                banned_at=getattr(b, "created_at", None),
            )
            for b in (bans or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(f"Failed to get bans for server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/{server_id}/bans/{user_id}",
    response_model=SuccessResponse,
    summary="Ban user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server or user not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def ban_member(
    server_id: str,
    user_id: str,
    body: Optional[BanCreateRequest] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        reason = body.reason if body else None
        delete_message_days = body.delete_message_days if body else 0

        servers_mod.ban_member(
            current_user.user_id,
            sid,
            uid,
            reason=reason,
            delete_message_days=delete_message_days,
        )
        emit_guild_ban_add(sid, uid, reason=reason)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server or user not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "NotSupported" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}},
            )

        logger.error(
            f"Failed to ban user {user_id} from server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/bans/{user_id}",
    response_model=SuccessResponse,
    summary="Unban user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server or ban not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unban_member(
    server_id: str, user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.unban_member(current_user.user_id, sid, uid)
        emit_guild_ban_remove(sid, uid)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Ban not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to unban user {user_id} from server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
