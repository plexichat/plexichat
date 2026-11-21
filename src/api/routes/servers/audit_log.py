from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.servers import AuditLogEntryResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse
from src.core.security.hardening import parse_id

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
    server_id: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[AuditLogEntryResponse]:
    # A bot token carries its owner's user_id (its own id lives in account_id).
    # The core audit read treats that id as the acting moderator and checks
    # ``server.view_audit_log`` against it, so without this gate a narrow-scope
    # bot could read the moderation/audit history of any server its owner
    # manages -- the same owner-identity confusion guarded on the ban list.
    require_user_token(current_user)
    servers_mod = api.get_servers()
    auth_mod = api.get_auth()

    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = parse_id(server_id, "server ID")
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

        # Deferred on purpose (avoids a module-level circular import), but it
        # only needs resolving once per request, not once per distinct user id.
        from ..users import _user_to_public_response

        users_map = {}
        if auth_mod:
            users_dict = auth_mod.get_users_bulk(list(user_ids))
            for uid, u in users_dict.items():
                users_map[int(uid)] = _user_to_public_response(u)

        # Role and channel target names used to be resolved with one get_role /
        # get_channel call per distinct target id — an N+1 fan-out that grew
        # with the page size (up to 100 extra lookups for a full page). Both
        # bulk APIs are permission-aware, cached and scoped to this server, so
        # a single call each resolves every name in the page. A failed bulk
        # lookup (e.g. the viewer lacks the higher list permission) fails closed
        # to an empty map: the entry is still returned, just without a name —
        # identical to the old behaviour when an individual lookup failed.
        role_map = {}
        try:
            roles = servers_mod.get_roles(current_user.user_id, sid)
            if roles:
                role_map = {int(role.id): role.name for role in roles}
        except Exception:
            role_map = {}

        channel_map = {}
        try:
            channels = servers_mod.get_channels(current_user.user_id, sid)
            if channels:
                channel_map = {int(channel.id): channel.name for channel in channels}
        except Exception:
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
                target_name = role_map.get(int(e.target_id))
            elif e.target_type == "channel" and e.target_id:
                target_name = channel_map.get(int(e.target_id))

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
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to get audit log for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
