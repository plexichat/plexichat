from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    MemberResponse,
    PresenceResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.database import cached

import utils.logger as logger

router = APIRouter()


@router.get(
    "/{server_id}/members",
    response_model=List[MemberResponse],
    summary="List server members",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="server_members_api")
def get_server_members(
    server_id: str,
    limit: int = 100,
    after: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MemberResponse]:
    servers_mod = api.get_servers()
    auth = api.get_auth()
    presence = api.get_presence()

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

        curr_user_id = (
            current_user.user_id
            if hasattr(current_user, "user_id")
            else getattr(current_user, "get", lambda k: None)("user_id")
        )

        members = servers_mod.get_members(curr_user_id, sid)
        if members is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch members"}},
            )
        if len(members) == 0:
            return []

        user_ids = []
        for m in members:
            uid = getattr(m, "user_id", None) or m.get("user_id")
            if uid:
                user_ids.append(uid)

        users_map = {}
        if auth and user_ids:
            users_map = auth.get_users_bulk(user_ids)

        presence_map = {}
        if presence and user_ids:
            try:
                presence_map = presence.get_visible_presences_bulk(
                    curr_user_id, user_ids
                )
            except Exception as e:
                logger.warning(f"Failed to get bulk presence for server {sid}: {e}")

        result = []
        for m in members:
            if isinstance(m, dict):
                raw_member_id = m.get("id") or m.get("member_id")
                raw_user_id = m.get("user_id", 0)
                nickname = m.get("nickname")
                joined_at = m.get("joined_at")
                roles = m.get("roles", [])
                timeout_until = m.get("timeout_until")
                timeout_reason = m.get("timeout_reason")
            else:
                raw_member_id = getattr(m, "id", None)
                raw_user_id = getattr(m, "user_id", 0)
                nickname = getattr(m, "nickname", None)
                joined_at = getattr(m, "joined_at", None)
                roles = getattr(m, "roles", [])
                timeout_until = getattr(m, "timeout_until", None)
                timeout_reason = getattr(m, "timeout_reason", None)

            user_id = int(raw_user_id)
            user = users_map.get(user_id) or users_map.get(str(user_id))

            pres = presence_map.get(user_id) or presence_map.get(str(user_id))
            presence_data = PresenceResponse(status="offline")
            if pres:
                status = getattr(pres, "status", None)
                if status and hasattr(status, "value"):
                    status = status.value
                presence_data = PresenceResponse(
                    status=str(status) if status else "offline"
                )

            result.append(
                MemberResponse(
                    member_id=SnowflakeID(int(raw_member_id))
                    if raw_member_id
                    else None,
                    user_id=SnowflakeID(user_id),
                    username=str(user.username) if user else f"User {user_id}",
                    nickname=nickname,
                    avatar_url=getattr(user, "avatar_url", None),
                    joined_at=joined_at,
                    roles=[SnowflakeID(r) for r in (roles or [])],
                    presence=presence_data,
                    badges=getattr(user, "badges", []),
                    timeout_until=timeout_until,
                    timeout_reason=timeout_reason,
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
        elif "Access" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to fetch members for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/members/{member_id}",
    response_model=SuccessResponse,
    summary="Kick member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def kick_member(
    server_id: str, member_id: str, current_user: TokenInfo = Depends(get_current_user)
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
            mid = int(member_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )
        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]
        servers_mod.kick_member(current_user.user_id, sid, target_user_id)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member not found"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name or "Owner" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to kick member {member_id} from server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/{server_id}/members/{member_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Add role to member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member or role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def assign_role_to_member(
    server_id: str,
    member_id: str,
    role_id: str,
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
            mid = int(member_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.assign_role(current_user.user_id, sid, target_user_id, rid)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member or role not found"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "Exists" in exc_name:
            return SuccessResponse(success=True, message=None)

        logger.error(
            f"Failed to assign role {role_id} to member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/members/{member_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Remove role from member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member or role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_role_from_member(
    server_id: str,
    member_id: str,
    role_id: str,
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
            mid = int(member_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.remove_role(current_user.user_id, sid, target_user_id, rid)
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member or role not found"}},
            )
        elif (
            "Permission" in exc_name or "Hierarchy" in exc_name or "Default" in exc_name
        ):
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to remove role {role_id} from member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
