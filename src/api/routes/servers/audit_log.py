from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import AuditLogEntryResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse

import utils.logger as logger

router = APIRouter()


@router.get(
    "/{server_id}/audit-logs",
    response_model=List[AuditLogEntryResponse],
    summary="Get audit logs",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_audit_log(
    server_id: str, limit: int = 50, current_user: TokenInfo = Depends(get_current_user)
) -> List[AuditLogEntryResponse]:
    servers_mod = api.get_servers()
    auth_mod = api.get_auth()

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

        entries = servers_mod.get_audit_log(current_user.user_id, sid, limit=limit)
        if not entries:
            return []

        user_ids = set()
        for e in entries:
            user_ids.add(e.user_id)
            if e.target_type == "member" and e.target_id:
                user_ids.add(int(e.target_id))

        users_map = {}
        if auth_mod:
            users_dict = auth_mod.get_users_bulk(list(user_ids))
            for uid, u in users_dict.items():
                from ..users import _user_to_public_response

                users_map[int(uid)] = _user_to_public_response(u)

        role_map = {}
        channel_map = {}

        result = []
        for e in entries:
            target_name = None
            if e.target_type == "member" and e.target_id:
                target_user = users_map.get(int(e.target_id))
                target_name = (
                    target_user.username if target_user else f"User {e.target_id}"
                )
            elif e.target_type == "role" and e.target_id:
                rid = int(e.target_id)
                if rid not in role_map:
                    try:
                        role = servers_mod.get_role(rid, current_user.user_id)
                        role_map[rid] = role.name if role else None
                    except Exception:
                        role_map[rid] = None
                target_name = role_map[rid]
            elif e.target_type == "channel" and e.target_id:
                cid = int(e.target_id)
                if cid not in channel_map:
                    try:
                        channel = servers_mod.get_channel(cid, current_user.user_id)
                        channel_map[cid] = channel.name if channel else None
                    except Exception:
                        channel_map[cid] = None
                target_name = channel_map[cid]

            result.append(
                AuditLogEntryResponse(
                    id=SnowflakeID(e.id),
                    server_id=SnowflakeID(e.server_id),
                    user_id=SnowflakeID(e.user_id),
                    user=users_map.get(int(e.user_id)),
                    action=e.action_type.value
                    if hasattr(e.action_type, "value")
                    else str(e.action_type),
                    target_type=getattr(e, "target_type", None),
                    target_id=SnowflakeID(e.target_id)
                    if getattr(e, "target_id", None)
                    else None,
                    target_name=target_name,
                    changes=getattr(e, "changes", None),
                    reason=getattr(e, "reason", None),
                    created_at=getattr(e, "created_at", None),
                )
            )
        return result
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

        logger.error(
            f"Failed to get audit log for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
